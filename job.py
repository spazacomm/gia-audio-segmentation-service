import os
import sys
import logging
import tempfile
import asyncio
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timedelta, timezone

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings
from inaSpeechSegmenter import Segmenter
from google.cloud import storage
from supabase import create_client, Client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ============================================================
# CONFIGURATION
# ============================================================

class Settings(BaseSettings):
    # Required
    SOURCE_ID: str
    
    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    
    # GCS
    BUCKET_NAME: str = "spaza-recordings"
    
    # Processing
    MAX_CONCURRENT: int = 3  # Lower for CPU-intensive segmentation
    MAX_FILES_PER_RUN: int = 20  # Limit files per run to avoid timeout
    MAX_FILE_SIZE_MB: int = 500  # Skip files larger than this
    MAX_TIMEOUT_MINUTES: int = 8  # Leave buffer for 10min Cloud Run timeout
    SUPPORTED_EXTENSIONS: List[str] = [".mp3", ".wav", ".mp4"]
    
    class Config:
        env_file = ".env"
    
    @field_validator('SOURCE_ID', 'SUPABASE_URL', 'SUPABASE_KEY')
    @classmethod
    def validate_required(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} is required")
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
    total_segments: int = 0
    failed_files: List[str] = []
    start_time: datetime
    end_time: Optional[datetime] = None

# ============================================================
# SUPABASE CLIENT
# ============================================================

class SupabaseClient:
    def __init__(self):
        self.client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        logger.info("Supabase client initialized")
    
    def get_source(self, source_id: str) -> Optional[Dict[str, Any]]:
        """Get source details from database"""
        try:
            response = self.client.table('sources').select(
                'id, name, countries(code,name), platforms(name)'
            ).eq('id', source_id).single().execute()
            return response.data
        except Exception as e:
            logger.error(f"Failed to fetch source {source_id}: {e}")
            return None
    
    def is_file_processed(self, recording_url: str) -> bool:
        """Check if file has already been processed"""
        try:
            response = self.client.table('broadcast_timeline')\
                .select('id')\
                .eq('recording_url', recording_url)\
                .eq('segmentation_processed', True)\
                .execute()
            return len(response.data) > 0
        except Exception as e:
            logger.error(f"Failed to check if file processed: {e}")
            return False
    
    def batch_check_processed(self, recording_urls: List[str]) -> set:
        """Batch check which files are already processed"""
        try:
            response = self.client.table('broadcast_timeline')\
                .select('recording_url')\
                .in_('recording_url', recording_urls)\
                .eq('segmentation_processed', True)\
                .execute()
            return {item['recording_url'] for item in response.data}
        except Exception as e:
            logger.error(f"Failed to batch check processed files: {e}")
            return set()
    
    def create_broadcast(self, broadcast_data: Dict[str, Any]) -> Optional[str]:
        """Create broadcast_timeline record"""
        try:
            response = self.client.table('broadcast_timeline').insert(broadcast_data).execute()
            if response.data:
                broadcast_id = response.data[0]['id']
                logger.debug(f"Created broadcast record: {broadcast_id}")
                return broadcast_id
            return None
        except Exception as e:
            logger.error(f"Failed to create broadcast: {e}")
            return None
    
    def insert_segments(self, segments: List[Dict[str, Any]]) -> bool:
        """Bulk insert timeline_labels"""
        try:
            self.client.table('timeline_labels').insert(segments).execute()
            logger.debug(f"Inserted {len(segments)} segments")
            return True
        except Exception as e:
            logger.error(f"Failed to insert segments: {e}")
            return False
    
    def update_broadcast_status(self, broadcast_id: int, status: str, 
                                segmentation_processed: bool = False) -> bool:
        """Update broadcast status"""
        try:
            update_data = {
                'status': status,
                'updated_at': datetime.now(timezone.utc).isoformat()
            }
            if segmentation_processed:
                update_data['segmentation_processed'] = True
                update_data['processed_at'] = datetime.now(timezone.utc).isoformat()
            
            self.client.table('broadcast_timeline').update(update_data).eq('id', broadcast_id).execute()
            return True
        except Exception as e:
            logger.error(f"Failed to update broadcast {broadcast_id}: {e}")
            return False

# ============================================================
# GCS CLIENT
# ============================================================

class GCSClient:
    def __init__(self):
        self.storage_client = storage.Client()
        self.bucket = self.storage_client.bucket(settings.BUCKET_NAME)
        logger.info(f"GCS client initialized for bucket: {settings.BUCKET_NAME}")
    
    def list_audio_files(self, prefix: str) -> List[storage.Blob]:
        """List all audio files in GCS path recursively"""
        blobs = self.storage_client.list_blobs(settings.BUCKET_NAME, prefix=prefix)
        audio_blobs = [
            blob for blob in blobs 
            if any(blob.name.lower().endswith(ext) for ext in settings.SUPPORTED_EXTENSIONS)
        ]
        return sorted(audio_blobs, key=lambda b: b.name)
    
    def download_to_temp(self, blob: storage.Blob) -> Optional[str]:
        """Download blob to temporary file"""
        try:
            suffix = Path(blob.name).suffix
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                tmp_path = tmp_file.name
            blob.download_to_filename(tmp_path)
            return tmp_path
        except Exception as e:
            logger.error(f"Failed to download {blob.name}: {e}")
            return None

# ============================================================
# AUDIO PROCESSOR
# ============================================================

class AudioProcessor:
    def __init__(self):
        self.segmenter = Segmenter()
        self.gcs_client = GCSClient()
        self.db_client = SupabaseClient()
        logger.info("Audio processor initialized")
    
    def parse_datetime_from_filename(self, filename: str) -> Optional[datetime]:
    
        patterns = [
            # YYYY-MM-DD_HH-MM-SS
            (r'(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})',
            "%Y-%m-%d %H:%M:%S",
            lambda d, t: f"{d} {t.replace('-', ':')}"),

            # YYYYMMDD_HHMMSS
            (r'(\d{8})_(\d{6})',
            "%Y%m%d%H%M%S",
            lambda d, t: f"{d}{t}")
        ]

        for pattern, fmt, builder in patterns:
            match = re.search(pattern, filename)
            if match:
                try:
                    return datetime.strptime(builder(*match.groups()), fmt)
                except ValueError:
                    continue

        return None
    
    def build_gcs_prefix(self, source: Dict[str, Any]) -> str:
        """Build GCS path from source metadata (without date for recursive scanning)"""
        country = source['countries']['name']
        platform = source['platforms']['name']
        station = source['name']
        
        # Normalize names (lowercase, remove spaces)
        country = country.lower().strip()
        platform = platform.lower().strip().replace(' ', '')
        station = station.lower().strip().replace(' ', '-')
        
        return f"{country}/{platform}/{station}/"
    
    async def segment_audio_file(self, file_path: str) -> List[SegmentData]:
        """Run segmentation on audio file (in thread pool)"""
        def _segment():
            segments = self.segmenter(file_path)
            return [
                SegmentData(
                    label=seg[0],
                    start_time=float(seg[1]),
                    end_time=float(seg[2])
                )
                for seg in segments
            ]
        
        return await asyncio.to_thread(_segment)
    
    async def process_single_file(self, blob: storage.Blob, source_id: str) -> tuple[bool, bool]:
        """
        Process a single audio file
        Returns: (success, was_skipped)
        """
        filename = Path(blob.name).name
        tmp_path = None
        recording_url = f"gs://{settings.BUCKET_NAME}/{blob.name}"
        
        try:
            # Skip files that are too large
            file_size_mb = blob.size / 1024 / 1024
            if file_size_mb > settings.MAX_FILE_SIZE_MB:
                logger.warning(f"Skipping {filename}: file too large ({file_size_mb:.0f}MB)")
                return True, True
            
            # Parse datetime from filename
            broadcast_datetime = self.parse_datetime_from_filename(filename)
            if not broadcast_datetime:
                logger.error(f"Could not parse datetime from filename: {filename}")
                return False, False
            
            # Create broadcast record
            broadcast_data = {
                'broadcast_datetime': broadcast_datetime.isoformat(),
                'source_id': source_id,
                'status': 'processing',
                'recording_url': recording_url,
                'metadata': {
                    'filename': filename,
                    'gcs_path': blob.name
                }
            }
            
            broadcast_id = self.db_client.create_broadcast(broadcast_data)
            if not broadcast_id:
                logger.error(f"Failed to create broadcast record for {filename}")
                return False, False
            
            # Download file
            tmp_path = await asyncio.to_thread(self.gcs_client.download_to_temp, blob)
            if not tmp_path:
                self.db_client.update_broadcast_status(broadcast_id, 'failed')
                return False, False
            
            # Segment audio
            segments = await self.segment_audio_file(tmp_path)
            
            if not segments:
                logger.warning(f"No segments found in {filename}")
                self.db_client.update_broadcast_status(broadcast_id, 'completed', True)
                return True, False
            
            broadcast_dt = broadcast_datetime.replace(tzinfo=timezone.utc)

            # Prepare segments for insertion
            segment_records = [
                {
                    'timeline_id': broadcast_id,
                    'label': seg.label,
                    'start_time': (broadcast_dt + timedelta(seconds=seg.start_time)).isoformat(),
                    'end_time': (broadcast_dt + timedelta(seconds=seg.end_time)).isoformat(),
                    'start_offset_seconds':seg.start_time,
                    'end_offset_seconds': seg.end_time,
                    'fingerprint_processed': False,
                    'transcription_processed': False
                }
                for seg in segments
            ]
            
            # Insert segments
            if not self.db_client.insert_segments(segment_records):
                self.db_client.update_broadcast_status(broadcast_id, 'failed')
                return False, False
            
            # Mark complete
            self.db_client.update_broadcast_status(broadcast_id, 'completed', True)
            
            logger.info(f"✓ Processed {filename}: {len(segments)} segments")
            return True, False
            
        except Exception as e:
            logger.error(f"Error processing {filename}: {e}", exc_info=True)
            return False, False
            
        finally:
            # Cleanup temp file
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception as e:
                    logger.warning(f"Failed to cleanup temp file: {e}")

# ============================================================
# MAIN JOB
# ============================================================

async def run_job():
    """Main job execution"""
    stats = ProcessingStats(start_time=datetime.now(timezone.utc))
    
    logger.info("=" * 60)
    logger.info("Starting Recursive Audio Segmentation Job")
    logger.info("=" * 60)
    logger.info(f"Source ID: {settings.SOURCE_ID}")
    logger.info(f"Max Concurrent: {settings.MAX_CONCURRENT}")
    logger.info("")
    
    # Initialize processor
    processor = AudioProcessor()
    
    # Get source details
    logger.info("Fetching source details from database...")
    source = processor.db_client.get_source(settings.SOURCE_ID)
    if not source:
        logger.error(f"Source {settings.SOURCE_ID} not found in database")
        sys.exit(1)
    
    logger.info(f"Source: {source['name']}")
    
    # Build GCS prefix and list files recursively
    gcs_prefix = processor.build_gcs_prefix(source)
    logger.info(f"GCS Path (recursive): gs://{settings.BUCKET_NAME}/{gcs_prefix}")
    logger.info("")
    
    logger.info("Listing audio files recursively...")
    audio_files = await asyncio.to_thread(processor.gcs_client.list_audio_files, gcs_prefix)
    
    if not audio_files:
        logger.warning("No audio files found")
        return
    
    logger.info(f"Found {len(audio_files)} audio files")
    
    # Batch check which files are already processed
    logger.info("Checking which files are already processed...")
    all_recording_urls = [f"gs://{settings.BUCKET_NAME}/{blob.name}" for blob in audio_files]
    processed_urls = await asyncio.to_thread(
        processor.db_client.batch_check_processed,
        all_recording_urls
    )
    
    # Filter out already-processed files
    unprocessed_files = [
        blob for blob in audio_files 
        if f"gs://{settings.BUCKET_NAME}/{blob.name}" not in processed_urls
    ]
    
    logger.info(f"Already processed: {len(processed_urls)}, Remaining: {len(unprocessed_files)}")
    
    if not unprocessed_files:
        logger.info("All files already processed!")
        return
    
    # Limit files per run to avoid timeout
    if len(unprocessed_files) > settings.MAX_FILES_PER_RUN:
        logger.info(f"Limiting to first {settings.MAX_FILES_PER_RUN} of {len(unprocessed_files)} unprocessed files")
        unprocessed_files = unprocessed_files[:settings.MAX_FILES_PER_RUN]
    
    # Estimate time and further limit if needed
    estimated_time_minutes = len(unprocessed_files) * 2  # ~2min per file average
    if estimated_time_minutes > settings.MAX_TIMEOUT_MINUTES:
        safe_limit = settings.MAX_TIMEOUT_MINUTES // 2
        logger.warning(f"Estimated time {estimated_time_minutes}min exceeds timeout. Reducing to {safe_limit} files")
        unprocessed_files = unprocessed_files[:safe_limit]
    
    audio_files = unprocessed_files
    stats.total_files = len(audio_files)
    logger.info(f"Found {stats.total_files} audio files")
    logger.info(f"Processing with {settings.MAX_CONCURRENT} concurrent workers...")
    logger.info("")
    
    # Process files with concurrency limit
    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT)
    
    async def process_with_limit(blob: storage.Blob) -> tuple[storage.Blob, bool, bool]:
        async with semaphore:
            success, skipped = await processor.process_single_file(blob, settings.SOURCE_ID)
            return blob, success, skipped
    
    # Create all tasks
    tasks = [process_with_limit(blob) for blob in audio_files]
    
    # Process with progress tracking
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
            stats.failed_files.append(Path(blob.name).name)
        
        # Progress indicator every 10% or every 50 files
        if completed_count % max(1, stats.total_files // 10) == 0 or completed_count % 50 == 0:
            progress_pct = (completed_count / stats.total_files) * 100
            logger.info(f"Progress: {completed_count}/{stats.total_files} ({progress_pct:.1f}%) - "
                       f"Completed: {stats.completed}, Skipped: {stats.skipped}, Failed: {stats.failed}")
    
    stats.end_time = datetime.now(timezone.utc)
    
    # Print summary
    print_summary(stats)
    
    # Exit with appropriate code
    sys.exit(0 if stats.failed == 0 else 1)

def print_summary(stats: ProcessingStats):
    """Print job summary"""
    duration = (stats.end_time - stats.start_time).total_seconds()
    duration_str = f"{int(duration // 60)}m {int(duration % 60)}s"
    
    logger.info("")
    logger.info("=" * 60)
    logger.info("Job Completed")
    logger.info("=" * 60)
    logger.info(f"Total Files Found: {stats.total_files}")
    logger.info(f"Successfully Processed: {stats.completed}")
    logger.info(f"Skipped (Already Processed): {stats.skipped}")
    logger.info(f"Failed: {stats.failed}")
    logger.info(f"Duration: {duration_str}")
    logger.info("")
    
    if stats.failed_files:
        logger.info("Failed Files:")
        for filename in stats.failed_files[:10]:  # Show first 10
            logger.info(f"  - {filename}")
        if len(stats.failed_files) > 10:
            logger.info(f"  ... and {len(stats.failed_files) - 10} more")
        logger.info("")
    
    logger.info("Check Supabase for detailed results:")
    logger.info(f"  SELECT * FROM broadcast_timeline WHERE source_id = '{settings.SOURCE_ID}'")
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