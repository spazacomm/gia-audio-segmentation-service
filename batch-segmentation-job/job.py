import os
import logging
import sys
from pathlib import Path
from typing import List, Optional
from datetime import datetime

import httpx
from pydantic import BaseModel
from pydantic_settings import BaseSettings
from inaSpeechSegmenter import Segmenter

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# --- Configuration ---
class Settings(BaseSettings):
    # Mount point for GCS bucket
    MOUNT_PATH: str = "/mnt/gcs"
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
    OUTPUT_PATH: Optional[str] = None  # If None, sends to webhook only

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
        logger.info("Initialized InaSpeech Segmenter")
    
    def get_base_directory(self) -> Path:
        """Construct the base directory path from environment variables"""
        base_path = Path(settings.MOUNT_PATH) / settings.BUCKET_NAME  / settings.COUNTRY / settings.PLATFORM / settings.STATION
        
        if settings.DATE:
            base_path = base_path / settings.DATE
        
        logger.info(f"Base directory: {base_path}")
        return base_path
    
    def find_audio_files(self, base_dir: Path) -> List[Path]:
        """Recursively find all audio files in the directory"""
        audio_files = []
        
        if not base_dir.exists():
            logger.error(f"Directory does not exist: {base_dir}")
            return audio_files
        
        for ext in settings.SUPPORTED_EXTENSIONS:
            # Use rglob for recursive search
            files = list(base_dir.rglob(f"*{ext}"))
            audio_files.extend(files)
            logger.info(f"Found {len(files)} {ext} files")
        
        logger.info(f"Total audio files found: {len(audio_files)}")
        return sorted(audio_files)
    
    def process_file(self, file_path: Path, base_dir: Path) -> FileProcessingResult:
        """Process a single audio file"""
        start_time = datetime.now()
        relative_path = str(file_path.relative_to(base_dir))
        
        logger.info(f"Processing: {relative_path}")
        
        try:
            # Run segmentation
            segments = self.segmenter(str(file_path))
            
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
                file_path=str(file_path),
                relative_path=relative_path,
                status="success",
                segments=results,
                total_segments=len(results),
                processing_time=processing_time
            )
            
        except Exception as e:
            processing_time = (datetime.now() - start_time).total_seconds()
            logger.error(f"Failed to process {relative_path}: {e}", exc_info=True)
            
            return FileProcessingResult(
                file_path=str(file_path),
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
        """Save results to file if OUTPUT_PATH is configured"""
        if not settings.OUTPUT_PATH:
            logger.info("No output path configured, skipping file save")
            return
        
        output_path = Path(settings.OUTPUT_PATH)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        
        if settings.OUTPUT_FORMAT == "json":
            import json
            with open(output_path, 'w') as f:
                json.dump(results.dict(), f, indent=2)
            logger.info(f"Results saved to {output_path}")
        
        elif settings.OUTPUT_FORMAT == "csv":
            import csv
            with open(output_path, 'w', newline='') as f:
                writer = csv.writer(f)
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
            logger.info(f"Results saved to {output_path}")

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
    base_dir = processor.get_base_directory()
    audio_files = processor.find_audio_files(base_dir)
    
    if not audio_files:
        logger.warning("No audio files found to process")
        return
    
    # Process files
    results = []
    successful = 0
    failed = 0
    
    for file_path in audio_files:
        result = processor.process_file(file_path, base_dir)
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
        total_files=len(audio_files),
        successful=successful,
        failed=failed,
        files=results
    )
    
    logger.info(f"Job completed: {successful} successful, {failed} failed out of {len(audio_files)} files")
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