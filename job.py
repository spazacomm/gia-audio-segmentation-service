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

    LOOKBACK_DAYS: int = 3

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
        """Query ALL processed recordings under a prefix (optionally date-limited)"""
        try:
            query = (
                self.client
                .table("broadcast_timeline")
                .select("recording_url")
                .like("recording_url", f"gs://{bucket}/{gcs_prefix}%")
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
        """
        Create or return existing broadcast record.
        Checks by unique constraint: (broadcast_datetime, source_id)
        """
        try:
            # Check if already exists by unique constraint
            existing = (
                self.client
                .table("broadcast_timeline")
                .select("id, status, segmentation_processed, recording_url")
                .eq("broadcast_datetime", data["broadcast_datetime"])
                .eq("source_id", data["source_id"])
                .maybe_single()
                .execute()
            )
            
            if existing.data:
                record = existing.data
                broadcast_id = int(record["id"])
                
                logger.info(
                    f"Found existing broadcast {broadcast_id}: "
                    f"status={record['status']}, "
                    f"processed={record.get('segmentation_processed')}"
                )
                
                # If already completed successfully, skip
                if record.get("segmentation_processed") == True:
                    logger.info(f"Broadcast {broadcast_id} already processed, skipping")
                    return broadcast_id
                
                # If failed or stuck in processing, reset and retry
                if record.get("status") in ["failed", "processing"]:
                    logger.warning(
                        f"Resetting broadcast {broadcast_id} from "
                        f"status '{record['status']}' to retry"
                    )
                    self.client.table("broadcast_timeline").update({
                        "status": "processing",
                        "updated_at": datetime.now(timezone.utc).isoformat()
                    }).eq("id", broadcast_id).execute()
                
                return broadcast_id
            
            # No existing record, create new
            res = self.client.table("broadcast_timeline").insert(data).execute()
            
            if not res.data or len(res.data) == 0:
                logger.error(
                    f"Insert returned no data for "
                    f"broadcast_datetime={data.get('broadcast_datetime')}, "
                    f"source_id={data.get('source_id')}"
                )
                return None
            
            broadcast_id = int(res.data[0]["id"])
            logger.debug(f"Created new broadcast {broadcast_id}")
            return broadcast_id
            
        except Exception as e:
            logger.error(f"Failed to create broadcast: {e}", exc_info=True)
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
    ) -> bool:
        """Update broadcast status. Returns True on success."""
        try:
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
            return True
        except Exception as e:
            logger.error(f"Failed to update broadcast {broadcast_id}: {e}")
            return False

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
        broadcast_id = None  # Initialize to None

        try:
            # Skip large files
            if blob.size / 1024 / 1024 > settings.MAX_FILE_SIZE_MB:
                logger.warning(f"Skipping {filename}: file too large ({blob.size / 1024 / 1024:.0f}MB)")
                return True, True

            # Parse datetime from filename
            dt = self.parse_datetime_from_filename(filename)
            if not dt:
                logger.error(f"Cannot parse datetime from {filename}")
                return False, False

            # Create or get broadcast record
            broadcast_id = self.db.create_broadcast({
                "source_id": source_id,
                "broadcast_datetime": dt.isoformat(),
                "recording_url": recording_url,
                "status": "processing",
                "metadata": {"filename": filename},
            })

            # Check if broadcast_id is valid
            if broadcast_id is None:
                logger.error(f"Failed to create/get broadcast record for {filename}")
                return False, False
            
            # Verify it's an integer
            if not isinstance(broadcast_id, int):
                logger.error(
                    f"Invalid broadcast_id type for {filename}: "
                    f"type={type(broadcast_id)}, value={broadcast_id}"
                )
                return False, False

            # Check if already processed (create_broadcast returns existing if completed)
            check = self.db.client.table("broadcast_timeline")\
                .select("segmentation_processed")\
                .eq("id", broadcast_id)\
                .single()\
                .execute()
            
            if check.data and check.data.get("segmentation_processed"):
                logger.info(f"Skipping {filename}: already processed (ID: {broadcast_id})")
                return True, True

            # Download file
            tmp = await asyncio.to_thread(self.gcs.download_to_temp, blob)
            if not tmp:
                logger.error(f"Failed to download {filename}")
                self.db.update_broadcast_status(broadcast_id, "failed")
                return False, False

            # Segment audio
            segments = await self.segment_audio(tmp)

            # Insert segments if any
            if segments:
                base_dt = dt.replace(tzinfo=timezone.utc)
                rows = [
                    {
                        "timeline_id": broadcast_id,
                        "label": s.label,
                        "start_time": (base_dt + timedelta(seconds=s.start_time)).isoformat(),
                        "end_time": (base_dt + timedelta(seconds=s.end_time)).isoformat(),
                        "start_offset_seconds": str(s.start_time),  # TEXT type in schema
                        "end_offset_seconds": str(s.end_time),      # TEXT type in schema
                        "fingerprint_processed": False,
                        "transcription_processed": False,
                    }
                    for s in segments
                ]
                
                if not self.db.insert_segments(rows):
                    logger.error(f"Failed to insert segments for {filename}")
                    self.db.update_broadcast_status(broadcast_id, "failed")
                    return False, False

            # Mark as completed
            if not self.db.update_broadcast_status(broadcast_id, "completed", True):
                logger.error(f"Failed to mark {filename} as completed")
                return False, False
            
            logger.info(f"✓ Processed {filename}: {len(segments)} segments (ID: {broadcast_id})")
            return True, False

        except Exception as e:
            logger.error(f"Failed processing {filename}: {e}", exc_info=True)
            
            # Only update status if we have a valid broadcast_id
            if broadcast_id is not None and isinstance(broadcast_id, int):
                try:
                    self.db.update_broadcast_status(broadcast_id, "failed")
                except Exception as update_error:
                    logger.error(f"Failed to mark broadcast {broadcast_id} as failed: {update_error}")
            
            return False, False

        finally:
            # Cleanup temp file
            if tmp and os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except Exception as cleanup_error:
                    logger.warning(f"Failed to cleanup temp file {tmp}: {cleanup_error}")

# ============================================================
# MAIN JOB
# ============================================================

async def run_job():
    stats = ProcessingStats(start_time=datetime.now(timezone.utc))
    processor = AudioProcessor()

    logger.info("=" * 60)
    logger.info("Starting Audio Segmentation Job")
    logger.info("=" * 60)
    logger.info(f"Source ID: {settings.SOURCE_ID}")
    logger.info(f"Max concurrent: {settings.MAX_CONCURRENT}")
    logger.info(f"Max files per run: {settings.MAX_FILES_PER_RUN}")
    logger.info(f"Lookback days: {settings.LOOKBACK_DAYS}")
    logger.info("")

    # Get source details
    source = processor.db.get_source(settings.SOURCE_ID)
    if not source:
        logger.error(f"Source {settings.SOURCE_ID} not found")
        raise RuntimeError("Source not found")

    logger.info(f"Source: {source['name']}")
    
    # Build GCS prefix
    gcs_prefix = processor.build_gcs_prefix(source)
    logger.info(f"GCS prefix: gs://{settings.BUCKET_NAME}/{gcs_prefix}")

    # List audio files
    blobs = await asyncio.to_thread(processor.gcs.list_audio_files, gcs_prefix)
    if not blobs:
        logger.info("No audio files found")
        return

    logger.info(f"Found {len(blobs)} audio files")

    # Get processed URLs
    start_dt = datetime.now(timezone.utc) - timedelta(days=settings.LOOKBACK_DAYS)
    processed_urls = await asyncio.to_thread(
        processor.db.get_processed_urls_by_prefix,
        settings.BUCKET_NAME,
        gcs_prefix,
        start_dt,
        None,
    )

    logger.info(f"Already processed: {len(processed_urls)} files")

    # Filter unprocessed
    unprocessed = [
        b for b in blobs
        if f"gs://{settings.BUCKET_NAME}/{b.name}" not in processed_urls
    ]

    logger.info(f"Unprocessed: {len(unprocessed)} files")

    # Limit files per run
    if len(unprocessed) > settings.MAX_FILES_PER_RUN:
        logger.info(f"Limiting to first {settings.MAX_FILES_PER_RUN} files")
        unprocessed = unprocessed[:settings.MAX_FILES_PER_RUN]

    stats.total_files = len(unprocessed)

    if stats.total_files == 0:
        logger.info("No files to process")
        return

    logger.info(f"Processing {stats.total_files} files...")
    logger.info("")

    # Process files with concurrency control
    sem = asyncio.Semaphore(settings.MAX_CONCURRENT)

    async def worker(blob):
        async with sem:
            return blob, *await processor.process_file(blob, settings.SOURCE_ID)

    tasks = [worker(b) for b in unprocessed]

    # Process and track progress
    completed_count = 0
    for coro in asyncio.as_completed(tasks):
        blob, success, skipped = await coro
        completed_count += 1
        
        if skipped:
            stats.skipped += 1
        elif success:
            stats.completed += 1
        else:
            stats.failed += 1
            stats.failed_files.append(blob.name)
        
        # Progress update every 5 files or 20%
        if completed_count % 5 == 0 or completed_count % max(1, stats.total_files // 5) == 0:
            progress_pct = (completed_count / stats.total_files) * 100
            logger.info(
                f"Progress: {completed_count}/{stats.total_files} ({progress_pct:.1f}%) - "
                f"Completed: {stats.completed}, Skipped: {stats.skipped}, Failed: {stats.failed}"
            )

    stats.end_time = datetime.now(timezone.utc)
    
    # Print summary
    duration = (stats.end_time - stats.start_time).total_seconds()
    duration_str = f"{int(duration // 60)}m {int(duration % 60)}s"
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("Job Summary")
    logger.info("=" * 60)
    logger.info(f"Total files: {stats.total_files}")
    logger.info(f"Completed: {stats.completed}")
    logger.info(f"Skipped: {stats.skipped}")
    logger.info(f"Failed: {stats.failed}")
    logger.info(f"Duration: {duration_str}")
    
    if stats.failed_files:
        logger.info("")
        logger.info(f"Failed files ({len(stats.failed_files)}):")
        for filename in stats.failed_files[:10]:
            logger.info(f"  - {Path(filename).name}")
        if len(stats.failed_files) > 10:
            logger.info(f"  ... and {len(stats.failed_files) - 10} more")
    
    logger.info("=" * 60)

# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        asyncio.run(run_job())
    except KeyboardInterrupt:
        logger.info("Job interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Job failed: {e}", exc_info=True)
        sys.exit(1)