import os
import sys
import re
import asyncio
import logging
import tempfile
from pathlib import Path
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Any, Optional, Tuple
from concurrent.futures import ProcessPoolExecutor

from pydantic import BaseModel
from pydantic_settings import BaseSettings
from inaSpeechSegmenter import Segmenter
from google.cloud import storage
from supabase import create_client, Client

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s level=%(levelname)s %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ============================================================
# SETTINGS
# ============================================================

class Settings(BaseSettings):
    SOURCE_ID: str
    GCS_PREFIX: str
    BUCKET_NAME: str

    SUPABASE_URL: str
    SUPABASE_KEY: str

    MAX_SEGMENT_WORKERS: int = 2
    FILE_TIMEOUT_SECONDS: int = 8 * 60

    SUPPORTED_EXTENSIONS: List[str] = [".mp3", ".wav", ".mp4"]

settings = Settings()

# ============================================================
# MODELS
# ============================================================

class Segment(BaseModel):
    label: str
    start: float
    end: float

# ============================================================
# SUPABASE REPOSITORY
# ============================================================

class SupabaseRepo:
    def __init__(self):
        self.client: Client = create_client(
            settings.SUPABASE_URL,
            settings.SUPABASE_KEY,
        )

    def create_or_get_broadcast(
        self,
        source_id: str,
        broadcast_dt: datetime,
        recording_url: str,
        filename: str,
    ) -> Tuple[int, bool]:
        """
        Returns:
          (broadcast_id, already_processed)
        """
        res = (
            self.client
            .table("broadcast_timeline")
            .select("id, segmentation_processed")
            .eq("source_id", source_id)
            .eq("broadcast_datetime", broadcast_dt.isoformat())
            .maybe_single()
            .execute()
        )

        if res.data:
            return int(res.data["id"]), bool(res.data["segmentation_processed"])

        insert = (
            self.client
            .table("broadcast_timeline")
            .insert({
                "source_id": source_id,
                "broadcast_datetime": broadcast_dt.isoformat(),
                "recording_url": recording_url,
                "status": "processing",
                "metadata": {"filename": filename},
            })
            .execute()
        )

        return int(insert.data[0]["id"]), False

    def mark_completed(self, broadcast_id: int):
        self.client.table("broadcast_timeline").update({
            "status": "completed",
            "segmentation_processed": True,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", broadcast_id).execute()

    def mark_failed(self, broadcast_id: int):
        self.client.table("broadcast_timeline").update({
            "status": "failed",
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }).eq("id", broadcast_id).execute()

    def insert_segments_chunked(
        self,
        rows: List[Dict[str, Any]],
        chunk_size: int = 500,
    ):
        for i in range(0, len(rows), chunk_size):
            self.client.table("timeline_labels").insert(
                rows[i:i + chunk_size]
            ).execute()

# ============================================================
# GCS
# ============================================================

class GCSRepo:
    def __init__(self):
        self.client = storage.Client()

    def list_audio(self, bucket: str, prefix: str):
        blobs = self.client.list_blobs(bucket, prefix=prefix)
        return [
            b for b in blobs
            if any(b.name.lower().endswith(ext) for ext in settings.SUPPORTED_EXTENSIONS)
        ]

    def download(self, blob) -> str:
        fd, path = tempfile.mkstemp(suffix=Path(blob.name).suffix)
        os.close(fd)
        blob.download_to_filename(path)
        return path

# ============================================================
# SEGMENTATION (PROCESS SAFE)
# ============================================================

def segment_file(path: str) -> List[Segment]:
    segmenter = Segmenter()
    return [
        Segment(label=s[0], start=float(s[1]), end=float(s[2]))
        for s in segmenter(path)
    ]

# ============================================================
# PROCESSOR
# ============================================================

class Processor:
    def __init__(self):
        self.db = SupabaseRepo()
        self.gcs = GCSRepo()
        self.pool = ProcessPoolExecutor(
            max_workers=settings.MAX_SEGMENT_WORKERS
        )

    def parse_datetime(self, filename: str) -> Optional[datetime]:
        """
        Supported patterns:
          1) YYYYMMDD_HHMMSS        -> 20251217_195932
          2) YYYY-MM-DD_HH-MM-SS    -> 2025-12-17_19-59-32
        """
        patterns = [
            # YYYYMMDD_HHMMSS
            (
                r"(\d{8})_(\d{6})",
                "%Y%m%d%H%M%S",
                lambda m: "".join(m.groups()),
            ),
            # YYYY-MM-DD_HH-MM-SS
            (
                r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})",
                "%Y-%m-%d%H-%M-%S",
                lambda m: m.group(1) + m.group(2),
            ),
        ]

        for regex, fmt, builder in patterns:
            m = re.search(regex, filename)
            if not m:
                continue
            try:
                dt = datetime.strptime(builder(m), fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    async def process_blob(self, blob):
        filename = Path(blob.name).name
        recording_url = f"gs://{settings.BUCKET_NAME}/{blob.name}"

        broadcast_dt = self.parse_datetime(filename)
        if not broadcast_dt:
            logger.warning(f"skip=no_datetime file={filename}")
            return

        broadcast_id, done = self.db.create_or_get_broadcast(
            settings.SOURCE_ID,
            broadcast_dt,
            recording_url,
            filename,
        )

        if done:
            logger.info(f"skip=already_processed file={filename}")
            return

        tmp = None
        try:
            tmp = self.gcs.download(blob)

            segments = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(
                    self.pool, segment_file, tmp
                ),
                timeout=settings.FILE_TIMEOUT_SECONDS,
            )

            rows = [{
                "timeline_id": broadcast_id,
                "label": s.label,
                "start_time": (broadcast_dt + timedelta(seconds=s.start)).isoformat(),
                "end_time": (broadcast_dt + timedelta(seconds=s.end)).isoformat(),
                "start_offset_seconds": s.start,
                "end_offset_seconds": s.end,
                "fingerprint_processed": False,
                "transcription_processed": False,
            } for s in segments]

            if rows:
                self.db.insert_segments_chunked(rows)

            self.db.mark_completed(broadcast_id)

            logger.info(
                f"status=completed file={filename} "
                f"segments={len(rows)} broadcast_id={broadcast_id}"
            )

        except Exception as e:
            logger.error(
                f"status=failed file={filename} "
                f"broadcast_id={broadcast_id} error={e}",
                exc_info=True,
            )
            self.db.mark_failed(broadcast_id)

        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

# ============================================================
# ENTRYPOINT (Cloud Run Job)
# ============================================================

async def main():
    processor = Processor()

    blobs = processor.gcs.list_audio(
        settings.BUCKET_NAME,
        settings.GCS_PREFIX,
    )

    logger.info(
        f"job_start source={settings.SOURCE_ID} "
        f"files_found={len(blobs)}"
    )

    await asyncio.gather(
        *(processor.process_blob(blob) for blob in blobs)
    )

    logger.info(
        f"job_complete source={settings.SOURCE_ID} "
        f"files_total={len(blobs)}"
    )

if __name__ == "__main__":
    asyncio.run(main())
