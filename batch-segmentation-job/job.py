import os
import sys
import logging
import tempfile
import asyncio
import re
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

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
    DATE: str  # Format: YYYY-MM-DD
    
    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    
    # GCS
    BUCKET_NAME: str = "spaza-recordings"
    
    # Processing
    MAX_CONCURRENT: int = 10
    SUPPORTED_EXTENSIONS: List[str] = [".mp3", ".wav", ".mp4"]
    
    class Config:
        env_file = ".env"
    
    @field_validator('SOURCE_ID', 'DATE', 'SUPABASE_URL', 'SUPABASE_KEY')
    @classmethod
    def validate_required(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} is required")
        return v.strip()
    
    @field_validator('DATE')
    @classmethod
    def validate_date_format(cls, v: str) -> str:
        try:
            datetime.strptime(v, '%Y-%m-%d')
            return v
        except ValueError:
            raise ValueError("DATE must be in format YYYY-MM-DD")

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
                'id, name, countries(code), platforms(name)'
            ).eq('id', source_id).single().execute()
            return response.data
        except Exception as e:
            logger.error(f"Failed to fetch source {source_id}: {e}")
            return None
    
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
        """List all audio files in GCS path"""
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
        """Extract datetime from filename pattern YYYYMMDD_HHMMSS"""
        match = re.search(r'(\d{8})_(\d{6})', filename)
        if match:
            date_str, time_str = match.groups()
            try:
                return datetime.strptime(f"{date_str}{time_str}", "%Y%m%d%H%M%S")
            except ValueError:
                pass
        return None
    
    def build_gcs_prefix(self, source: Dict[str, Any]) -> str:
        """Build GCS path from source metadata"""
        country = source['countries']['code']
        platform = source['platforms']['name']
        station = source['name']
        
        # Normalize names (lowercase, remove spaces)
        country = country.lower().strip()
        platform = platform.lower().strip().replace(' ', '')
        station = station.lower().strip().replace(' ', '')
        
        return f"{country}/{platform}/{station}/{settings.DATE}/"
    
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
    
    async def process_single_file(self, blob: storage.Blob, source_id: str) -> bool:
        """Process a single audio file"""
        filename = Path(blob.name).name
        tmp_path = None
        
        try:
            # Parse datetime from filename
            broadcast_datetime = self.parse_datetime_from_filename(filename)
            if not broadcast_datetime:
                logger.error(f"Could not parse datetime from filename: {filename}")
                return False
            
            # Create broadcast record
            broadcast_data = {
                'broadcast_datetime': broadcast_datetime.isoformat(),
                'source_id': source_id,
                'status': 'processing',
                'recording_url': f"gs://{settings.BUCKET_NAME}/{blob.name}",
                'metadata': {
                    'filename': filename,
                    'gcs_path': blob.name
                }
            }
            
            broadcast_id = self.db_client.create_broadcast(broadcast_data)
            if not broadcast_id:
                logger.error(f"Failed to create broadcast record for {filename}")
                return False
            
            # Download file
            tmp_path = await asyncio.to_thread(self.gcs_client.download_to_temp, blob)
            if not tmp_path:
                self.db_client.update_broadcast_status(broadcast_id, 'failed')
                return False
            
            # Segment audio
            segments = await self.segment_audio_file(tmp_path)
            
            if not segments:
                logger.warning(f"No segments found in {filename}")
                self.db_client.update_broadcast_status(broadcast_id, 'completed', True)
                return True
            
            # Prepare segments for insertion
            segment_records = [
                {
                    'timeline_id': broadcast_id,
                    'label': seg.label,
                    'start_time': seg.start_time,
                    'end_time': seg.end_time,
                    'fingerprint_processed': False,
                    'transcription_processed': False
                }
                for seg in segments
            ]
            
            # Insert segments
            if not self.db_client.insert_segments(segment_records):
                self.db_client.update_broadcast_status(broadcast_id, 'failed')
                return False
            
            # Mark complete
            self.db_client.update_broadcast_status(broadcast_id, 'completed', True)
            
            logger.info(f"✓ Processed {filename}: {len(segments)} segments")
            return True
            
        except Exception as e:
            logger.error(f"Error processing {filename}: {e}", exc_info=True)
            return False
            
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
    logger.info("Starting Audio Segmentation Job")
    logger.info("=" * 60)
    logger.info(f"Source ID: {settings.SOURCE_ID}")
    logger.info(f"Date: {settings.DATE}")
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
    
    # Build GCS prefix and list files
    gcs_prefix = processor.build_gcs_prefix(source)
    logger.info(f"GCS Path: gs://{settings.BUCKET_NAME}/{gcs_prefix}")
    logger.info("")
    
    logger.info("Listing audio files...")
    audio_files = await asyncio.to_thread(processor.gcs_client.list_audio_files, gcs_prefix)
    
    if not audio_files:
        logger.warning("No audio files found")
        return
    
    stats.total_files = len(audio_files)
    logger.info(f"Found {stats.total_files} audio files")
    logger.info(f"Processing with {settings.MAX_CONCURRENT} concurrent workers...")
    logger.info("")
    
    # Process files with concurrency limit
    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT)
    
    async def process_with_limit(blob: storage.Blob) -> tuple[storage.Blob, bool]:
        async with semaphore:
            success = await processor.process_single_file(blob, settings.SOURCE_ID)
            return blob, success
    
    # Create all tasks
    tasks = [process_with_limit(blob) for blob in audio_files]
    
    # Process with progress tracking
    completed_count = 0
    for coro in asyncio.as_completed(tasks):
        blob, success = await coro
        completed_count += 1
        
        if success:
            stats.completed += 1
        else:
            stats.failed += 1
            stats.failed_files.append(Path(blob.name).name)
        
        # Progress indicator every 10% or every 50 files
        if completed_count % max(1, stats.total_files // 10) == 0 or completed_count % 50 == 0:
            progress_pct = (completed_count / stats.total_files) * 100
            logger.info(f"Progress: {completed_count}/{stats.total_files} ({progress_pct:.1f}%)")
    
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
    logger.info(f"Total Files: {stats.total_files}")
    logger.info(f"Successfully Processed: {stats.completed}")
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
