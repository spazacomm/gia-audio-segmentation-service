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
# SETTINGS & LOGGING
# ============================================================

class Settings(BaseSettings):
    SOURCE_ID: str
    GCS_PREFIX: str
    BUCKET_NAME: str
    SUPABASE_URL: str
    SUPABASE_KEY: str
    MAX_SEGMENT_WORKERS: int = 2
    # Limit parallel processing to avoid OOM/CPU thrashing
    MAX_CONCURRENT_TASKS: int = 2 
    FILE_TIMEOUT_SECONDS: int = 8 * 60
    SUPPORTED_EXTENSIONS: List[str] = [".mp3", ".wav", ".mp4"]

settings = Settings()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)

# ============================================================
# MODELS & REPOS
# ============================================================

class Segment(BaseModel):
    label: str
    start: float
    end: float

class SupabaseRepo:
    def __init__(self):
        self.client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)

    def upsert_broadcast(self, source_id: str, recording_url: str, broadcast_dt: Optional[datetime], filename: str) -> Tuple[int, bool]:
        """Atomic operation to get or create a broadcast record."""
        payload = {
            "source_id": source_id,
            "recording_url": recording_url,
            "broadcast_datetime": broadcast_dt.isoformat() if broadcast_dt else None,
            "status": "processing",
            "metadata": {"filename": filename},
        }
        
        # .upsert handles the "already exists" logic via the unique constraint
        # on_conflict specifies the columns that define a 'duplicate'
        res = self.client.table("broadcast_timeline").upsert(
            payload, on_conflict="source_id, recording_url"
        ).execute()

        if not res.data:
            raise RuntimeError(f"Upsert failed for {recording_url}")
            
        row = res.data[0]
        return int(row["id"]), bool(row.get("segmentation_processed", False))

    def mark_status(self, broadcast_id: int, status: str, processed: bool = False):
        now = datetime.now(timezone.utc).isoformat()
        update_data = {"status": status, "updated_at": now}
        if processed:
            update_data.update({"segmentation_processed": True, "processed_at": now})
        
        self.client.table("broadcast_timeline").update(update_data).eq("id", broadcast_id).execute()

    def insert_segments_chunked(self, rows: List[Dict[str, Any]]):
        for i in range(0, len(rows), 500):
            self.client.table("timeline_labels").insert(rows[i:i + 500]).execute()

# ============================================================
# LOGIC & PROCESSING
# ============================================================

def segment_file(path: str) -> List[Segment]:
    """Runs in ProcessPoolExecutor to avoid blocking the event loop."""
    segmenter = Segmenter() # Initialize inside worker to avoid pickle issues
    return [Segment(label=s[0], start=float(s[1]), end=float(s[2])) for s in segmenter(path)]

class Processor:
    def __init__(self):
        self.db = SupabaseRepo()
        self.storage = storage.Client()
        self.pool = ProcessPoolExecutor(max_workers=settings.MAX_SEGMENT_WORKERS)
        self.semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_TASKS)

    def parse_datetime(self, filename: str) -> Optional[datetime]:
        # Optimized regex matching
        patterns = [(r"(\d{8})_(\d{6})", "%Y%m%d%H%M%S"), 
                    (r"(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})", "%Y-%m-%d%H-%M-%S")]
        for regex, fmt in patterns:
            m = re.search(regex, filename)
            if m:
                try:
                    ts_str = "".join(m.groups()).replace("-", "") if "-" in m.group(1) else "".join(m.groups())
                    return datetime.strptime(ts_str, "%Y%m%d%H%M%S" if "_" in regex else "%Y%m%d%H%M%S").replace(tzinfo=timezone.utc)
                except: continue
        return None

    async def process_blob(self, blob):
        async with self.semaphore: # Limit active workers
            filename = Path(blob.name).name
            recording_url = f"gs://{settings.BUCKET_NAME}/{blob.name}"
            broadcast_dt = self.parse_datetime(filename)

            try:
                broadcast_id, done = self.db.upsert_broadcast(settings.SOURCE_ID, recording_url, broadcast_dt, filename)
                if done:
                    logger.info(f"SKIP: {filename} already processed.")
                    return

                # Download and Segment
                with tempfile.NamedTemporaryFile(suffix=Path(blob.name).suffix, delete=True) as tmp:
                    blob.download_to_filename(tmp.name)
                    
                    segments = await asyncio.wait_for(
                        asyncio.get_running_loop().run_in_executor(self.pool, segment_file, tmp.name),
                        timeout=settings.FILE_TIMEOUT_SECONDS
                    )

                # Prepare Rows
                rows = [{
                    "timeline_id": broadcast_id,
                    "label": s.label,
                    "start_time": (broadcast_dt + timedelta(seconds=s.start)).isoformat() if broadcast_dt else None,
                    "end_time": (broadcast_dt + timedelta(seconds=s.end)).isoformat() if broadcast_dt else None,
                    "start_offset_seconds": s.start,
                    "end_offset_seconds": s.end,
                } for s in segments]

                if rows:
                    self.db.insert_segments_chunked(rows)
                
                self.db.mark_status(broadcast_id, "completed", processed=True)
                logger.info(f"SUCCESS: {filename} (ID: {broadcast_id})")

            except Exception as e:
                logger.error(f"FAILED: {filename} | Error: {str(e)}")
                # We don't have broadcast_id if upsert fails
                if 'broadcast_id' in locals():
                    self.db.mark_status(broadcast_id, "failed")

# ============================================================
# ENTRYPOINT
# ============================================================

async def main():
    processor = Processor()
    blobs = [b for b in processor.storage.list_blobs(settings.BUCKET_NAME, prefix=settings.GCS_PREFIX)
             if any(b.name.lower().endswith(ext) for ext in settings.SUPPORTED_EXTENSIONS)]

    logger.info(f"Starting Job: {len(blobs)} files found.")
    await asyncio.gather(*(processor.process_blob(b) for b in blobs))

if __name__ == "__main__":
    asyncio.run(main())