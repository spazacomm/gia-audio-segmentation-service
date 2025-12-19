import os
import logging
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone
from contextlib import asynccontextmanager

import httpx
from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings
from inaSpeechSegmenter import Segmenter
from google.cloud import storage
from tenacity import retry, stop_after_attempt, wait_exponential, retry_if_exception_type
import asyncio

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- Configuration ---
class Settings(BaseSettings):
    BUCKET_NAME: str = "spaza-recordings"
    
    # Directory structure parameters (required)
    COUNTRY: str
    PLATFORM: str
    STATION: str
    DATE: Optional[str] = None
    
    # Processing settings
    SUPPORTED_EXTENSIONS: List[str] = [".mp3", ".wav", ".mp4"]
    MAX_CONCURRENT_FILES: int = 3
    
    # Supabase configuration
    SUPABASE_URL: str
    SUPABASE_KEY: str  # Service role key for backend operations
    SUPABASE_BATCH_SIZE: int = 100  # Insert segments in batches
    
    # Source mapping (to find the correct source_id)
    SOURCE_ID: Optional[str] = None  # If known, provide the UUID directly
    
    # Output settings (optional - for backup/debugging)
    OUTPUT_FORMAT: str = "json"
    OUTPUT_PATH: Optional[str] = None

    class Config:
        env_file = ".env"
    
    @field_validator('COUNTRY', 'PLATFORM', 'STATION', 'SUPABASE_URL', 'SUPABASE_KEY')
    @classmethod
    def validate_required_fields(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} is required and cannot be empty")
        return v.strip()
    
    @field_validator('SUPPORTED_EXTENSIONS')
    @classmethod
    def normalize_extensions(cls, v: List[str]) -> List[str]:
        return [ext.lower() if ext.startswith('.') else f'.{ext}'.lower() for ext in v]

settings = Settings()

# --- Models ---
class SegmentResult(BaseModel):
    label: str
    start_time: float
    end_time: float

class FileProcessingResult(BaseModel):
    file_path: str
    relative_path: str
    broadcast_datetime: datetime
    status: str
    segments: List[SegmentResult]
    total_segments: int
    processing_time: float
    duration_seconds: Optional[float] = None
    error: Optional[str] = None
    processed_at: str
    timeline_id: Optional[int] = None  # Set after DB insert

class JobResult(BaseModel):
    job_id: str
    start_time: str
    end_time: str
    total_files: int
    successful: int
    failed: int
    db_inserts: int
    db_failures: int
    files: List[FileProcessingResult]

# --- Supabase Client ---
class SupabaseClient:
    def __init__(self):
        self.base_url = settings.SUPABASE_URL.rstrip('/')
        self.headers = {
            "apikey": settings.SUPABASE_KEY,
            "Authorization": f"Bearer {settings.SUPABASE_KEY}",
            "Content-Type": "application/json",
            "Prefer": "return=representation"
        }
        self.http_client: Optional[httpx.AsyncClient] = None
    
    async def __aenter__(self):
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.http_client:
            await self.http_client.aclose()
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def get_source_id(self) -> Optional[str]:
        """Get or verify the source_id based on configuration"""
        if settings.SOURCE_ID:
            logger.info(f"Using provided SOURCE_ID: {settings.SOURCE_ID}")
            return settings.SOURCE_ID
        
        # Query sources table to find matching source
        url = f"{self.base_url}/rest/v1/sources"
        params = {
            "select": "id,name",
            "name": f"eq.{settings.STATION}",
            "limit": 1
        }
        
        response = await self.http_client.get(url, headers=self.headers, params=params)
        response.raise_for_status()
        
        sources = response.json()
        if sources:
            source_id = sources[0]['id']
            logger.info(f"Found source_id: {source_id} for station: {settings.STATION}")
            return source_id
        
        logger.warning(f"No source found for station: {settings.STATION}")
        return None
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def create_broadcast_timeline(self, result: FileProcessingResult, source_id: str) -> Optional[int]:
        """Create a broadcast_timeline record"""
        url = f"{self.base_url}/rest/v1/broadcast_timeline"
        
        payload = {
            "broadcast_datetime": result.broadcast_datetime.isoformat(),
            "source_id": source_id,
            "status": "completed" if result.status == "success" else "failed",
            "recording_url": result.file_path,
            "duration_seconds": result.duration_seconds,
            "processed_at": result.processed_at,
            "segmentation_processed": True,
            "metadata": {
                "relative_path": result.relative_path,
                "processing_time": result.processing_time,
                "country": settings.COUNTRY,
                "platform": settings.PLATFORM,
                "station": settings.STATION
            }
        }
        
        try:
            response = await self.http_client.post(
                url,
                headers=self.headers,
                json=payload
            )
            response.raise_for_status()
            
            data = response.json()
            if data and len(data) > 0:
                timeline_id = data[0]['id']
                logger.info(f"Created broadcast_timeline {timeline_id} for {result.relative_path}")
                return timeline_id
            
            logger.error(f"No data returned when creating broadcast_timeline for {result.relative_path}")
            return None
            
        except httpx.HTTPStatusError as e:
            logger.error(f"Failed to create broadcast_timeline: {e.response.status_code} - {e.response.text}")
            raise
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=True
    )
    async def create_timeline_labels_batch(self, timeline_id: int, segments: List[SegmentResult]) -> int:
        """Create timeline_labels records in batch"""
        if not segments:
            return 0
        
        url = f"{self.base_url}/rest/v1/timeline_labels"
        
        # Process in batches
        total_inserted = 0
        
        for i in range(0, len(segments), settings.SUPABASE_BATCH_SIZE):
            batch = segments[i:i + settings.SUPABASE_BATCH_SIZE]
            
            payload = [
                {
                    "timeline_id": timeline_id,
                    "label": seg.label,
                    "start_time": seg.start_time,
                    "end_time": seg.end_time,
                    "fingerprint_processed": False,
                    "transcription_processed": False,
                    "fingerprint_matched": False
                }
                for seg in batch
            ]
            
            try:
                response = await self.http_client.post(
                    url,
                    headers=self.headers,
                    json=payload
                )
                response.raise_for_status()
                
                data = response.json()
                inserted = len(data) if data else 0
                total_inserted += inserted
                
                logger.info(f"Inserted batch {i//settings.SUPABASE_BATCH_SIZE + 1}: {inserted} segments")
                
            except httpx.HTTPStatusError as e:
                logger.error(f"Failed to insert segment batch: {e.response.status_code} - {e.response.text}")
                raise
        
        logger.info(f"Total segments inserted for timeline {timeline_id}: {total_inserted}")
        return total_inserted

# --- Processing Logic ---
class AudioProcessor:
    def __init__(self):
        self.segmenter = Segmenter()
        self.storage_client = storage.Client()
        self.bucket = self.storage_client.bucket(settings.BUCKET_NAME)
        self.supabase: Optional[SupabaseClient] = None
        logger.info("Initialized InaSpeech Segmenter and GCS client")
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.supabase = SupabaseClient()
        await self.supabase.__aenter__()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.supabase:
            await self.supabase.__aexit__(exc_type, exc_val, exc_tb)
    
    def get_prefix(self) -> str:
        """Construct the GCS prefix from environment variables"""
        prefix = f"{settings.COUNTRY}/{settings.PLATFORM}/{settings.STATION}"
        if settings.DATE:
            prefix = f"{prefix}/{settings.DATE}"
        logger.info(f"GCS prefix: {prefix}")
        return prefix
    
    def extract_broadcast_datetime(self, blob_name: str) -> datetime:
        """Extract broadcast datetime from filename or path"""
        # Expected format: COUNTRY/PLATFORM/STATION/YYYY-MM-DD/filename.ext
        # or filename contains datetime like: station_2024-01-15_14-30-00.mp3
        
        try:
            parts = blob_name.split('/')
            
            # Try to find date in path
            for part in parts:
                # Try YYYY-MM-DD format
                if len(part) == 10 and part.count('-') == 2:
                    try:
                        date_obj = datetime.strptime(part, '%Y-%m-%d')
                        # If we have time in filename, extract it
                        filename = parts[-1]
                        # Look for time patterns like HH-MM-SS or HH_MM_SS
                        if '_' in filename or '-' in filename:
                            time_parts = filename.replace('.', '_').split('_')
                            for tp in time_parts:
                                if len(tp) >= 6 and tp.replace('-', '').isdigit():
                                    time_str = tp.replace('-', ':')[:8]
                                    try:
                                        time_obj = datetime.strptime(time_str, '%H:%M:%S').time()
                                        return datetime.combine(date_obj.date(), time_obj, tzinfo=timezone.utc)
                                    except:
                                        pass
                        # Default to start of day if no time found
                        return date_obj.replace(tzinfo=timezone.utc)
                    except ValueError:
                        continue
            
            # Fallback: use file modification time or current time
            logger.warning(f"Could not extract datetime from {blob_name}, using current time")
            return datetime.now(timezone.utc)
            
        except Exception as e:
            logger.warning(f"Error extracting datetime from {blob_name}: {e}, using current time")
            return datetime.now(timezone.utc)
    
    def find_audio_files(self) -> List[storage.Blob]:
        """List all audio files in the GCS bucket with the given prefix"""
        prefix = self.get_prefix()
        audio_blobs = []
        
        blobs = self.storage_client.list_blobs(settings.BUCKET_NAME, prefix=prefix)
        
        for blob in blobs:
            if any(blob.name.lower().endswith(ext) for ext in settings.SUPPORTED_EXTENSIONS):
                audio_blobs.append(blob)
        
        logger.info(f"Total audio files found: {len(audio_blobs)}")
        return sorted(audio_blobs, key=lambda b: b.name)
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        retry=retry_if_exception_type((IOError, ConnectionError)),
        reraise=True
    )
    async def process_file(self, blob: storage.Blob, prefix: str) -> FileProcessingResult:
        """Download and process a single audio file with retry logic"""
        start_time = datetime.now(timezone.utc)
        relative_path = blob.name[len(prefix):].lstrip('/')
        broadcast_datetime = self.extract_broadcast_datetime(blob.name)
        
        logger.info(f"Processing: {relative_path} (broadcast: {broadcast_datetime})")
        
        try:
            # Run blocking I/O in thread pool
            result = await asyncio.to_thread(
                self._process_file_sync, 
                blob, 
                prefix, 
                relative_path, 
                broadcast_datetime,
                start_time
            )
            return result
                
        except Exception as e:
            processing_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.error(f"Failed to process {relative_path}: {e}", exc_info=True)
            
            return FileProcessingResult(
                file_path=f"gs://{settings.BUCKET_NAME}/{blob.name}",
                relative_path=relative_path,
                broadcast_datetime=broadcast_datetime,
                status="failed",
                segments=[],
                total_segments=0,
                processing_time=processing_time,
                error=str(e),
                processed_at=datetime.now(timezone.utc).isoformat()
            )
    
    def _process_file_sync(
        self, 
        blob: storage.Blob, 
        prefix: str, 
        relative_path: str,
        broadcast_datetime: datetime,
        start_time: datetime
    ) -> FileProcessingResult:
        """Synchronous file processing logic"""
        suffix = Path(blob.name).suffix
        tmp_path = None
        
        try:
            # Create temp file
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                tmp_path = tmp_file.name
            
            # Download file
            blob.download_to_filename(tmp_path)
            
            # Run segmentation
            segments = self.segmenter(tmp_path)
            
            results = [
                SegmentResult(
                    label=seg[0],
                    start_time=float(seg[1]),
                    end_time=float(seg[2])
                )
                for seg in segments
            ]
            
            # Calculate duration from segments
            duration = max([seg.end_time for seg in results]) if results else None
            
            processing_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            
            logger.info(f"Completed {relative_path}: {len(results)} segments in {processing_time:.2f}s")
            
            return FileProcessingResult(
                file_path=f"gs://{settings.BUCKET_NAME}/{blob.name}",
                relative_path=relative_path,
                broadcast_datetime=broadcast_datetime,
                status="success",
                segments=results,
                total_segments=len(results),
                duration_seconds=duration,
                processing_time=processing_time,
                processed_at=datetime.now(timezone.utc).isoformat()
            )
        finally:
            # Clean up temp file
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception as e:
                    logger.warning(f"Failed to clean up temp file {tmp_path}: {e}")
    
    async def save_to_database(self, result: FileProcessingResult, source_id: str) -> bool:
        """Save processing result to Supabase database"""
        try:
            # Create broadcast_timeline record
            timeline_id = await self.supabase.create_broadcast_timeline(result, source_id)
            
            if not timeline_id:
                logger.error(f"Failed to create timeline for {result.relative_path}")
                return False
            
            result.timeline_id = timeline_id
            
            # Create timeline_labels records
            if result.segments:
                await self.supabase.create_timeline_labels_batch(timeline_id, result.segments)
            
            return True
            
        except Exception as e:
            logger.error(f"Database error for {result.relative_path}: {e}", exc_info=True)
            return False
    
    async def save_results(self, results: JobResult):
        """Save results to GCS if OUTPUT_PATH is configured (for backup/debugging)"""
        if not settings.OUTPUT_PATH:
            return
        
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False) as tmp_file:
                tmp_path = tmp_file.name
                import json
                json.dump(results.model_dump(), tmp_file, indent=2)
            
            # Upload to GCS in thread pool
            await asyncio.to_thread(self._upload_to_gcs, tmp_path)
            
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception as e:
                    logger.warning(f"Failed to clean up temp file: {e}")
    
    def _upload_to_gcs(self, tmp_path: str):
        """Synchronous GCS upload"""
        output_blob = self.bucket.blob(settings.OUTPUT_PATH)
        output_blob.upload_from_filename(tmp_path)
        logger.info(f"Results saved to gs://{settings.BUCKET_NAME}/{settings.OUTPUT_PATH}")

# --- Main Job Logic ---
async def run_job():
    """Main job execution logic with concurrent processing"""
    job_start = datetime.now(timezone.utc)
    job_id = f"job_{job_start.strftime('%Y%m%d_%H%M%S')}"
    
    logger.info(f"Starting job {job_id}")
    logger.info(f"Configuration: country={settings.COUNTRY}, platform={settings.PLATFORM}, "
                f"station={settings.STATION}, date={settings.DATE or 'all'}")
    
    async with AudioProcessor() as processor:
        # Get source_id
        source_id = await processor.supabase.get_source_id()
        if not source_id:
            logger.error("Could not determine source_id. Please set SOURCE_ID or ensure station exists in database.")
            sys.exit(1)
        
        # Find files
        prefix = processor.get_prefix()
        audio_blobs = await asyncio.to_thread(processor.find_audio_files)
        
        if not audio_blobs:
            logger.warning("No audio files found to process")
            return
        
        # Process files concurrently with semaphore to limit concurrency
        semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_FILES)
        
        async def process_with_limit(blob):
            async with semaphore:
                return await processor.process_file(blob, prefix)
        
        # Process all files concurrently
        logger.info(f"Processing {len(audio_blobs)} files with max {settings.MAX_CONCURRENT_FILES} concurrent")
        tasks = [process_with_limit(blob) for blob in audio_blobs]
        
        results = []
        for i, task in enumerate(asyncio.as_completed(tasks)):
            result = await task
            results.append(result)
            
            # Progress indicator
            if (i + 1) % 10 == 0 or (i + 1) == len(audio_blobs):
                logger.info(f"Progress: {i + 1}/{len(audio_blobs)} files processed")
        
        # Count successes and failures
        successful = sum(1 for r in results if r.status == "success")
        failed = sum(1 for r in results if r.status == "failed")
        
        # Save to database
        logger.info("Saving results to Supabase database...")
        db_successes = 0
        db_failures = 0
        
        for result in results:
            if result.status == "success":
                if await processor.save_to_database(result, source_id):
                    db_successes += 1
                else:
                    db_failures += 1
        
        logger.info(f"Database operations: {db_successes} successful, {db_failures} failed")
        
        job_end = datetime.now(timezone.utc)
        
        # Create job result
        job_result = JobResult(
            job_id=job_id,
            start_time=job_start.isoformat(),
            end_time=job_end.isoformat(),
            total_files=len(audio_blobs),
            successful=successful,
            failed=failed,
            db_inserts=db_successes,
            db_failures=db_failures,
            files=results
        )
        
        logger.info(f"Job completed: {successful} successful, {failed} failed out of {len(audio_blobs)} files")
        logger.info(f"Total time: {(job_end - job_start).total_seconds():.2f}s")
        
        # Save results to GCS if configured (optional backup)
        await processor.save_results(job_result)
        
        # Exit with appropriate code
        sys.exit(0 if (failed == 0 and db_failures == 0) else 1)

# --- Entry Point ---
if __name__ == "__main__":
    try:
        asyncio.run(run_job())
    except KeyboardInterrupt:
        logger.info("Job interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Job failed with error: {e}", exc_info=True)
        sys.exit(1)
