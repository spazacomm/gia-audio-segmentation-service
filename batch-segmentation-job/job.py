import os
import logging
import sys
import tempfile
from pathlib import Path
from typing import List, Optional
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
    
    # Webhook configuration
    WEBHOOK_URL: Optional[str] = None
    WEBHOOK_TOKEN: Optional[str] = None
    WEBHOOK_BATCH_SIZE: int = 10  # Send webhooks in batches
    WEBHOOK_TIMEOUT: int = 30
    
    # Output settings
    OUTPUT_FORMAT: str = "json"
    OUTPUT_PATH: Optional[str] = None

    class Config:
        env_file = ".env"
    
    @field_validator('COUNTRY', 'PLATFORM', 'STATION')
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
    status: str
    segments: List[SegmentResult]
    total_segments: int
    processing_time: float
    error: Optional[str] = None
    processed_at: str

class WebhookDeliveryResult(BaseModel):
    success: bool
    status_code: Optional[int] = None
    error: Optional[str] = None
    attempt: int

class JobResult(BaseModel):
    job_id: str
    start_time: str
    end_time: str
    total_files: int
    successful: int
    failed: int
    webhook_deliveries: int
    webhook_failures: int
    files: List[FileProcessingResult]

# --- Processing Logic ---
class AudioProcessor:
    def __init__(self):
        self.segmenter = Segmenter()
        self.storage_client = storage.Client()
        self.bucket = self.storage_client.bucket(settings.BUCKET_NAME)
        self.http_client: Optional[httpx.AsyncClient] = None
        logger.info("Initialized InaSpeech Segmenter and GCS client")
    
    async def __aenter__(self):
        """Async context manager entry"""
        self.http_client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.WEBHOOK_TIMEOUT),
            limits=httpx.Limits(max_keepalive_connections=5, max_connections=10)
        )
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit"""
        if self.http_client:
            await self.http_client.aclose()
    
    def get_prefix(self) -> str:
        """Construct the GCS prefix from environment variables"""
        prefix = f"{settings.COUNTRY}/{settings.PLATFORM}/{settings.STATION}"
        if settings.DATE:
            prefix = f"{prefix}/{settings.DATE}"
        logger.info(f"GCS prefix: {prefix}")
        return prefix
    
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
        
        logger.info(f"Processing: {relative_path}")
        
        try:
            # Run blocking I/O in thread pool
            result = await asyncio.to_thread(self._process_file_sync, blob, prefix, relative_path, start_time)
            return result
                
        except Exception as e:
            processing_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            logger.error(f"Failed to process {relative_path}: {e}", exc_info=True)
            
            return FileProcessingResult(
                file_path=f"gs://{settings.BUCKET_NAME}/{blob.name}",
                relative_path=relative_path,
                status="failed",
                segments=[],
                total_segments=0,
                processing_time=processing_time,
                error=str(e),
                processed_at=datetime.now(timezone.utc).isoformat()
            )
    
    def _process_file_sync(self, blob: storage.Blob, prefix: str, relative_path: str, start_time: datetime) -> FileProcessingResult:
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
            
            processing_time = (datetime.now(timezone.utc) - start_time).total_seconds()
            
            logger.info(f"Completed {relative_path}: {len(results)} segments in {processing_time:.2f}s")
            
            return FileProcessingResult(
                file_path=f"gs://{settings.BUCKET_NAME}/{blob.name}",
                relative_path=relative_path,
                status="success",
                segments=results,
                total_segments=len(results),
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
    
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=1, max=5),
        retry=retry_if_exception_type((httpx.HTTPError, httpx.TimeoutException)),
        reraise=False
    )
    async def send_webhook(self, data: dict, attempt: int = 1) -> WebhookDeliveryResult:
        """Send results to configured webhook with retry logic"""
        if not settings.WEBHOOK_URL or not self.http_client:
            return WebhookDeliveryResult(success=True, attempt=attempt)
        
        headers = {"Content-Type": "application/json"}
        if settings.WEBHOOK_TOKEN:
            headers["Authorization"] = f"Bearer {settings.WEBHOOK_TOKEN}"
        
        try:
            res = await self.http_client.post(
                settings.WEBHOOK_URL,
                json=data,
                headers=headers
            )
            res.raise_for_status()
            logger.info(f"Webhook delivered successfully (attempt {attempt}): {res.status_code}")
            return WebhookDeliveryResult(
                success=True,
                status_code=res.status_code,
                attempt=attempt
            )
        except httpx.HTTPStatusError as e:
            logger.error(f"Webhook failed with status {e.response.status_code} (attempt {attempt}): {e}")
            return WebhookDeliveryResult(
                success=False,
                status_code=e.response.status_code,
                error=str(e),
                attempt=attempt
            )
        except Exception as e:
            logger.error(f"Webhook failed (attempt {attempt}): {e}")
            return WebhookDeliveryResult(
                success=False,
                error=str(e),
                attempt=attempt
            )
    
    async def send_batch_webhook(self, results: List[FileProcessingResult]) -> WebhookDeliveryResult:
        """Send a batch of results to webhook"""
        batch_data = {
            "batch": True,
            "count": len(results),
            "results": [r.model_dump() for r in results]
        }
        return await self.send_webhook(batch_data)
    
    async def save_results(self, results: JobResult):
        """Save results to GCS if OUTPUT_PATH is configured"""
        if not settings.OUTPUT_PATH:
            logger.info("No output path configured, skipping file save")
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
        
        # Send webhooks in batches
        webhook_successes = 0
        webhook_failures = 0
        
        if settings.WEBHOOK_URL:
            logger.info(f"Sending results to webhook in batches of {settings.WEBHOOK_BATCH_SIZE}")
            
            for i in range(0, len(results), settings.WEBHOOK_BATCH_SIZE):
                batch = results[i:i + settings.WEBHOOK_BATCH_SIZE]
                webhook_result = await processor.send_batch_webhook(batch)
                
                if webhook_result.success:
                    webhook_successes += 1
                else:
                    webhook_failures += 1
                
                # Small delay between batches to avoid overwhelming webhook
                if i + settings.WEBHOOK_BATCH_SIZE < len(results):
                    await asyncio.sleep(0.5)
            
            logger.info(f"Webhook delivery: {webhook_successes} successful, {webhook_failures} failed batches")
        
        job_end = datetime.now(timezone.utc)
        
        # Create job result
        job_result = JobResult(
            job_id=job_id,
            start_time=job_start.isoformat(),
            end_time=job_end.isoformat(),
            total_files=len(audio_blobs),
            successful=successful,
            failed=failed,
            webhook_deliveries=webhook_successes,
            webhook_failures=webhook_failures,
            files=results
        )
        
        logger.info(f"Job completed: {successful} successful, {failed} failed out of {len(audio_blobs)} files")
        logger.info(f"Total time: {(job_end - job_start).total_seconds():.2f}s")
        
        # Save results to GCS if configured
        await processor.save_results(job_result)
        
        # Exit with appropriate code
        sys.exit(0 if failed == 0 else 1)

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