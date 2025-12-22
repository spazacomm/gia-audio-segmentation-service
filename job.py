import os
import sys
import logging
import tempfile
import asyncio
import re
from pathlib import Path
from typing import List, Optional, Dict, Any, Set
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings
from inaSpeechSegmenter import Segmenter
from google.cloud import storage
from supabase import create_client, Client

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger(__name__)

# ============================================================
# SETTINGS
# ============================================================

class Settings(BaseSettings):
    SOURCE_ID: str

    SUPABASE_URL: str
    SUPABASE_KEY: str

    BUCKET_NAME: str = "spaza-recordings"

    MAX_CONCURRENT: int = 3
    MAX_FILES_PER_RUN: int = 20
    MAX_FILE_SIZE_MB: int = 500
    MAX_TIMEOUT_MINUTES: int = 8

    LOOKBACK_DAYS: int = 3  # 🔥 limits DB scan by date

    SUPPORTED_EXTENSIONS: List[str] = [".mp3", ".wav", ".mp4"]

    class Config:
        env_file = ".env"

    @field_validator("SOURCE_ID", "SUPABASE_URL", "SUPABASE_KEY")
    @classmethod
    def validate_required(cls, v: str):
        if not v or not v.strip():
            raise ValueError("Required setting missing")
        return v.strip()

settings = Settings()

# ============================================================
# DATA MODELS
# ============================================================

class SegmentData(BaseModel):
    label: str
    start_time: float
    end_time: float

class ProcessingStats(BaseModel):
    total_files: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    failed_files: List[str] = []
    start_time: datetime
    end_time: Optional[datetime] = None

# ============================================================
# SUPABASE CLIENT
# ============================================================

class SupabaseClient:
    def __init__(self):
        self.client: Client = create_client(
            settings.SUPABASE_URL, settings.SUPABASE_KEY
        )
        logger.info("Supabase client initialized")

    def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        try:
            res = (
                self.client
                .table("sources")
                .select("id,name,countries(name),platforms(name)")
                .eq("id", source_id)
                .single()
                .execute()
            )
            return res.data
        except Exception as e:
            logger.error(f"Failed to fetch source: {e}")
            return None

    def get_processed_urls_by_prefix(
        self,
        bucket: str,
        gcs_prefix: str,
        start_dt: Optional[datetime],
        end_dt: Optional[datetime],
    ) -> Set[str]:
        """
        🔥 KEY CHANGE:
        Query ALL processed recordings under a prefix (optionally date-limited)
        """
        try:
            query = (
                self.client
                .table("broadcast_timeline")
                .select("recording_url")
                .like(
                    "recording_url",
                    f"gs://{bucket}/{gcs_prefix}%"
                )
                .eq("segmentation_processed", True)
            )

            if start_dt:
                query = query.gte("broadcast_datetime", start_dt.isoformat())

            if end_dt:
                query = query.lt("broadcast_datetime", end_dt.isoformat())

            res = query.execute()
            return {row["recording_url"] for row in res.data}

        except Exception as e:
            logger.error(f"Failed to fetch processed URLs: {e}")
            return set()

    def create_broadcast(self, data: Dict[str, Any]) -> Optional[int]:
        try:
            res = self.client.table("broadcast_timeline").insert(data).execute()
            return res.data[0]["id"] if res.data else None
        except Exception as e:
            logger.error(f"Failed to create broadcast: {e}")
            return None

    def insert_segments(self, segments: List[Dict[str, Any]]) -> bool:
        try:
            self.client.table("timeline_labels").insert(segments).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to insert segments: {e}")
            return False

    def update_broadcast_status(
        self,
        broadcast_id: int,
        status: str,
        segmentation_processed: bool = False,
    ) -> None:
        data = {
            "status": status,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        if segmentation_processed:
            data["segmentation_processed"] = True
            data["processed_at"] = datetime.now(timezone.utc).isoformat()

        self.client.table("broadcast_timeline").update(data).eq(
            "id", broadcast_id
        ).execute()

# ============================================================
# GCS CLIENT
# ============================================================

class GCSClient:
    def __init__(self):
        self.client = storage.Client()
        self.bucket = self.client.bucket(settings.BUCKET_NAME)

    def list_audio_files(self, prefix: str) -> List[storage.Blob]:
        blobs = self.client.list_blobs(settings.BUCKET_NAME, prefix=prefix)
        return sorted(
            [
                b for b in blobs
                if any(b.name.lower().endswith(ext) for ext in settings.SUPPORTED_EXTENSIONS)
            ],
            key=lambda b: b.name,
        )

    def download_to_temp(self, blob: storage.Blob) -> Optional[str]:
        try:
            suffix = Path(blob.name).suffix
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as f:
                path = f.name
            blob.download_to_filename(path)
            return path
        except Exception as e:
            logger.error(f"Failed to download {blob.name}: {e}")
            return None

# ============================================================
# AUDIO PROCESSOR
# ============================================================

class AudioProcessor:
    def __init__(self):
        self.segmenter = Segmenter()
        self.db = SupabaseClient()
        self.gcs = GCSClient()

    def parse_datetime_from_filename(self, filename: str) -> Optional[datetime]:
        patterns = [
            (r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", "%Y-%m-%d %H:%M:%S",
             lambda d, t: f"{d} {t.replace('-', ':')}"),
            (r"(\d{8})_(\d{6})", "%Y%m%d%H%M%S", lambda d, t: f"{d}{t}"),
        ]

        for pattern, fmt, build in patterns:
            m = re.search(pattern, filename)
            if m:
                try:
                    return datetime.strptime(build(*m.groups()), fmt)
                except ValueError:
                    pass
        return None

    def build_gcs_prefix(self, source: Dict[str, Any]) -> str:
        country = source["countries"]["name"].lower().strip()
        platform = source["platforms"]["name"].lower().replace(" ", "")
        station = source["name"].lower().replace(" ", "-")
        return f"{country}/{platform}/{station}/"

    async def segment_audio(self, path: str) -> List[SegmentData]:
        def _run():
            return [
                SegmentData(label=s[0], start_time=float(s[1]), end_time=float(s[2]))
                for s in self.segmenter(path)
            ]
        return await asyncio.to_thread(_run)

    async def process_file(self, blob: storage.Blob, source_id: str):
        filename = Path(blob.name).name
        recording_url = f"gs://{settings.BUCKET_NAME}/{blob.name}"
        tmp = None

        try:
            if blob.size / 1024 / 1024 > settings.MAX_FILE_SIZE_MB:
                return True, True

            dt = self.parse_datetime_from_filename(filename)
            if not dt:
                raise ValueError("Cannot parse datetime")

            broadcast_id = self.db.create_broadcast({
                "source_id": source_id,
                "broadcast_datetime": dt.isoformat(),
                "recording_url": recording_url,
                "status": "processing",
                "metadata": {"filename": filename},
            })

            tmp = await asyncio.to_thread(self.gcs.download_to_temp, blob)
            segments = await self.segment_audio(tmp)

            if segments:
                base_dt = dt.replace(tzinfo=timezone.utc)
                rows = [
                    {
                        "timeline_id": broadcast_id,
                        "label": s.label,
                        "start_time": (base_dt + timedelta(seconds=s.start_time)).isoformat(),
                        "end_time": (base_dt + timedelta(seconds=s.end_time)).isoformat(),
                        "start_offset_seconds": s.start_time,
                        "end_offset_seconds": s.end_time,
                        "fingerprint_processed": False,
                        "transcription_processed": False,
                    }
                    for s in segments
                ]
                self.db.insert_segments(rows)

            self.db.update_broadcast_status(broadcast_id, "completed", True)
            return True, False

        except Exception as e:
            logger.error(f"Failed processing {filename}: {e}", exc_info=True)
            return False, False

        finally:
            if tmp and os.path.exists(tmp):
                os.unlink(tmp)

# ============================================================
# MAIN JOB
# ============================================================

async def run_job():
    stats = ProcessingStats(start_time=datetime.now(timezone.utc))
    processor = AudioProcessor()

    source = processor.db.get_source(settings.SOURCE_ID)
    if not source:
        raise RuntimeError("Source not found")

    gcs_prefix = processor.build_gcs_prefix(source)

    blobs = await asyncio.to_thread(processor.gcs.list_audio_files, gcs_prefix)
    if not blobs:
        logger.info("No audio files found")
        return

    start_dt = datetime.now(timezone.utc) - timedelta(days=settings.LOOKBACK_DAYS)

    processed_urls = await asyncio.to_thread(
        processor.db.get_processed_urls_by_prefix,
        settings.BUCKET_NAME,
        gcs_prefix,
        start_dt,
        None,
    )

    unprocessed = [
        b for b in blobs
        if f"gs://{settings.BUCKET_NAME}/{b.name}" not in processed_urls
    ]

    unprocessed = unprocessed[:settings.MAX_FILES_PER_RUN]
    stats.total_files = len(unprocessed)

    sem = asyncio.Semaphore(settings.MAX_CONCURRENT)

    async def worker(blob):
        async with sem:
            return blob, *await processor.process_file(blob, settings.SOURCE_ID)

    tasks = [worker(b) for b in unprocessed]

    for coro in asyncio.as_completed(tasks):
        blob, success, skipped = await coro
        if skipped:
            stats.skipped += 1
        elif success:
            stats.completed += 1
        else:
            stats.failed += 1
            stats.failed_files.append(blob.name)

    stats.end_time = datetime.now(timezone.utc)
    logger.info(f"Completed: {stats.completed}, Failed: {stats.failed}")

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    asyncio.run(run_job())
