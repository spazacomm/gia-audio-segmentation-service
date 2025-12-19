import os
import logging
import sys
import tempfile
from pathlib import Path
from typing import List, Optional
from datetime import datetime

import httpx
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from inaSpeechSegmenter import Segmenter
from google.cloud import storage

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- Configuration ---
class Settings(BaseSettings):
    BUCKET_NAME: str = "spaza-recordings"
    
    # Directory structure parameters
    COUNTRY: str  # e.g., "kenya"
    PLATFORM: str  # e.g., "radio"
    STATION: str  # e.g., "capital-fm"
    DATE: Optional[str] = None  # e.g., "2025-12-16" or None for all dates
    
    # Processing settings
    SUPPORTED_EXTENSIONS: List[str] = [".mp3", ".wav", ".mp4"]
    
    # Webhook configuration
    WEBHOOK_URL: Optional[str] = None
    WEBHOOK_TOKEN: Optional[str] = None
    
    # Output settings
    OUTPUT_FORMAT: str = "json"  # json or csv
    OUTPUT_PATH: Optional[str] = None  # GCS path for results (e.g., "results/output.json")

    class Config:
        env_file = ".env"

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

class JobResult(BaseModel):
    job_id: str
    start_time: str
    end_time: str
    total_files: int
    successful: int
    failed: int
    files: List[FileProcessingResult]

# --- Processing Logic ---
class AudioProcessor:
    def __init__(self):
        self.segmenter = Segmenter()
        self.storage_client = storage.Client()
        self.bucket = self.storage_client.bucket(settings.BUCKET_NAME)
        logger.info("Initialized InaSpeech Segmenter and GCS client")
    
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
    
    def process_file(self, blob: storage.Blob, prefix: str) -> FileProcessingResult:
        """Download and process a single audio file"""
        start_time = datetime.now()
        relative_path = blob.name[len(prefix):].lstrip('/')
        
        logger.info(f"Processing: {relative_path}")
        
        try:
            # Download to temp file
            suffix = Path(blob.name).suffix
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                tmp_path = tmp_file.name
                blob.download_to_filename(tmp_path)
            
            try:
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
                
                processing_time = (datetime.now() - start_time).total_seconds()
                
                logger.info(f"Completed {relative_path}: {len(results)} segments in {processing_time:.2f}s")
                
                return FileProcessingResult(
                    file_path=f"gs://{settings.BUCKET_NAME}/{blob.name}",
                    relative_path=relative_path,
                    status="success",
                    segments=results,
                    total_segments=len(results),
                    processing_time=processing_time
                )
            finally:
                # Clean up temp file
                os.unlink(tmp_path)
                
        except Exception as e:
            processing_time = (datetime.now() - start_time).total_seconds()
            logger.error(f"Failed to process {relative_path}: {e}", exc_info=True)
            
            return FileProcessingResult(
                file_path=f"gs://{settings.BUCKET_NAME}/{blob.name}",
                relative_path=relative_path,
                status="failed",
                segments=[],
                total_segments=0,
                processing_time=processing_time,
                error=str(e)
            )
    
    async def send_webhook(self, data: dict):
        """Send results to configured webhook"""
        if not settings.WEBHOOK_URL:
            logger.info("No webhook URL configured, skipping")
            return
        
        headers = {"Content-Type": "application/json"}
        if settings.WEBHOOK_TOKEN:
            headers["Authorization"] = f"Bearer {settings.WEBHOOK_TOKEN}"
        
        async with httpx.AsyncClient(timeout=60) as client:
            try:
                res = await client.post(
                    settings.WEBHOOK_URL,
                    json=data,
                    headers=headers
                )
                res.raise_for_status()
                logger.info(f"Webhook sent successfully: {res.status_code}")
            except Exception as e:
                logger.error(f"Failed to send webhook: {e}")
    
    def save_results(self, results: JobResult):
        """Save results to GCS if OUTPUT_PATH is configured"""
        if not settings.OUTPUT_PATH:
            logger.info("No output path configured, skipping file save")
            return
        
        with tempfile.NamedTemporaryFile(mode='w', suffix='.tmp', delete=False) as tmp_file:
            tmp_path = tmp_file.name
            
            if settings.OUTPUT_FORMAT == "json":
                import json
                json.dump(results.dict(), tmp_file, indent=2)
            
            elif settings.OUTPUT_FORMAT == "csv":
                import csv
                writer = csv.writer(tmp_file)
                writer.writerow([
                    "file_path", "relative_path", "status", 
                    "total_segments", "processing_time", "error"
                ])
                for file_result in results.files:
                    writer.writerow([
                        file_result.file_path,
                        file_result.relative_path,
                        file_result.status,
                        file_result.total_segments,
                        file_result.processing_time,
                        file_result.error or ""
                    ])
        
        try:
            # Upload to GCS
            output_blob = self.bucket.blob(settings.OUTPUT_PATH)
            output_blob.upload_from_filename(tmp_path)
            logger.info(f"Results saved to gs://{settings.BUCKET_NAME}/{settings.OUTPUT_PATH}")
        finally:
            os.unlink(tmp_path)

# --- Main Job Logic ---
async def run_job():
    """Main job execution logic"""
    job_start = datetime.now()
    job_id = f"job_{job_start.strftime('%Y%m%d_%H%M%S')}"
    
    logger.info(f"Starting job {job_id}")
    logger.info(f"Configuration: country={settings.COUNTRY}, platform={settings.PLATFORM}, "
                f"station={settings.STATION}, date={settings.DATE or 'all'}")
    
    processor = AudioProcessor()
    
    # Find files
    prefix = processor.get_prefix()
    audio_blobs = processor.find_audio_files()
    
    if not audio_blobs:
        logger.warning("No audio files found to process")
        return
    
    # Process files
    results = []
    successful = 0
    failed = 0
    
    for blob in audio_blobs:
        result = processor.process_file(blob, prefix)
        results.append(result)
        
        if result.status == "success":
            successful += 1
        else:
            failed += 1
    
    job_end = datetime.now()
    
    # Create job result
    job_result = JobResult(
        job_id=job_id,
        start_time=job_start.isoformat(),
        end_time=job_end.isoformat(),
        total_files=len(audio_blobs),
        successful=successful,
        failed=failed,
        files=results
    )
    
    logger.info(f"Job completed: {successful} successful, {failed} failed out of {len(audio_blobs)} files")
    logger.info(f"Total time: {(job_end - job_start).total_seconds():.2f}s")
    
    # Save results
    processor.save_results(job_result)
    
    # Send webhook
    await processor.send_webhook(job_result.dict())
    
    # Exit with appropriate code
    sys.exit(0 if failed == 0 else 1)

# --- Entry Point ---
if __name__ == "__main__":
    import asyncio
    
    try:
        asyncio.run(run_job())
    except KeyboardInterrupt:
        logger.info("Job interrupted by user")
        sys.exit(1)
    except Exception as e:
        logger.error(f"Job failed with error: {e}", exc_info=True)
        sys.exit(1)
