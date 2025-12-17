"""
Optimized FastAPI service for broadcast audio segmentation using inaSpeechSegmenter
Designed for 2 vCPU, 4GB RAM systems with CPU-only processing
"""

import os
import gc
import logging
import atexit
import shutil
import psutil
import time
import threading
from datetime import datetime, timedelta
from typing import List, Optional, Dict, Any
from pathlib import Path
import tempfile
import re
import json
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# Audio processing (CPU-optimized)
from inaSpeechSegmenter import seg
import librosa
import soundfile as sf
import numpy as np

# Cloud storage
from google.cloud import storage
import io

# Database
from supabase import create_client, Client

# Environment setup
from dotenv import load_dotenv
load_dotenv()

# Configure logging (reduced verbosity for low-resource)
logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)

# Resource limits for 2 vCPU, 4GB RAM system
MAX_CONCURRENT_JOBS = 1  # Process only one job at a time
MAX_MEMORY_MB = 3072     # Use max 3GB of 4GB RAM (reserve 1GB for system)
AUDIO_CHUNK_SIZE_SECONDS = 300  # 5 minutes max per audio file
MAX_TEMP_FILES = 5       # Limit temporary files
PROCESSING_TIMEOUT_SECONDS = 1800  # 30 minutes max per job

app = FastAPI(
    title="Broadcast Audio Segmentation Service (Optimized)",
    description="Optimized service for 2 vCPU, 4GB RAM systems",
    version="1.0.0-optimized"
)

# Initialize clients with optimizations
storage_client = storage.Client()
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

# Pydantic models (optimized)
class SegmentationRequest(BaseModel):
    source_id: str  # UUID
    gcs_prefix: str  # Directory containing audio chunks

class SegmentResponse(BaseModel):
    timeline_id: int
    segments_processed: int
    message: str
    job_id: Optional[str] = None

class TimelineResponse(BaseModel):
    id: int
    broadcast_datetime: datetime
    status: str
    segments: List[Dict[str, Any]]

class BroadcastsResponse(BaseModel):
    broadcasts: List[Dict[str, Any]]
    total: int

# Resource monitoring (lightweight)
class LowResourceMonitor:
    def __init__(self):
        self.start_time = time.time()
        self.request_count = 0
        self.error_count = 0
        self.last_gc = time.time()
        self.gc_interval = 300  # Garbage collect every 5 minutes
    
    def check_memory_usage(self) -> float:
        """Check current memory usage percentage"""
        try:
            memory = psutil.virtual_memory()
            return memory.percent
        except:
            return 0.0
    
    def force_gc_if_needed(self):
        """Force garbage collection if memory usage is high"""
        current_time = time.time()
        memory_percent = self.check_memory_usage()
        
        if memory_percent > 75 or (current_time - self.last_gc) > self.gc_interval:
            gc.collect()
            self.last_gc = current_time
            logger.debug(f"Garbage collection triggered. Memory: {memory_percent:.1f}%")
    
    def get_optimized_metrics(self) -> Dict[str, Any]:
        """Get lightweight system metrics"""
        try:
            memory = psutil.virtual_memory()
            return {
                "memory_percent": round(memory.percent, 1),
                "memory_used_gb": round(memory.used / (1024**3), 2),
                "memory_total_gb": round(memory.total / (1024**3), 2),
                "uptime_hours": round((time.time() - self.start_time) / 3600, 2),
                "total_requests": self.request_count,
                "error_count": self.error_count
            }
        except Exception as e:
            logger.error(f"Failed to get metrics: {str(e)}")
            return {}

# Initialize low-resource monitor
monitor = LowResourceMonitor()

# Global job tracking (limited for low resources)
PROCESSING_JOBS = {}
JOB_COUNTER = 0
MAX_JOBS_TRACKED = 10  # Limit job tracking to prevent memory buildup

def track_job(source_id: str, gcs_prefix: str) -> str:
    """Create a new job tracking entry (optimized)"""
    global JOB_COUNTER
    JOB_COUNTER += 1
    job_id = f"job_{JOB_COUNTER}_{int(time.time())}"
    
    # Limit job tracking to prevent memory buildup
    if len(PROCESSING_JOBS) >= MAX_JOBS_TRACKED:
        # Remove oldest completed/failed jobs
        completed_jobs = [jid for jid, job in PROCESSING_JOBS.items() 
                         if job.get("status") in ["completed", "failed"]]
        for jid in completed_jobs[:5]:  # Remove 5 oldest
            del PROCESSING_JOBS[jid]
    
    PROCESSING_JOBS[job_id] = {
        "source_id": source_id,
        "gcs_prefix": gcs_prefix,
        "status": "started",
        "start_time": datetime.utcnow().isoformat(),
        "files_found": 0,
        "files_processed": 0,
        "segments_created": 0,
        "current_file": None,
        "progress_percent": 0
    }
    
    return job_id

def update_job_progress(job_id: str, **kwargs):
    """Update job progress (optimized)"""
    if job_id in PROCESSING_JOBS:
        PROCESSING_JOBS[job_id].update(kwargs)

def complete_job(job_id: str, success: bool = True):
    """Mark job as completed (optimized)"""
    if job_id in PROCESSING_JOBS:
        PROCESSING_JOBS[job_id].update({
            "status": "completed" if success else "failed",
            "end_time": datetime.utcnow().isoformat()
        })

# Optimized utility functions
def download_audio_chunk(gcs_bucket: str, gcs_path: str, local_path: str) -> bool:
    """Download audio chunk from GCS to local file (optimized)"""
    try:
        bucket = storage_client.bucket(gcs_bucket)
        blob = bucket.blob(gcs_path)
        blob.download_to_filename(local_path)
        logger.info(f"Downloaded {gcs_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to download {gcs_path}: {str(e)}")
        return False

def segment_audio_file(file_path: str) -> List[Dict[str, Any]]:
    """Segment audio file using inaSpeechSegmenter (optimized for CPU)"""
    try:
        logger.info(f"Segmenting {os.path.basename(file_path)}")
        
        # CPU-optimized segmentation parameters
        segments = seg(
            file_path, 
            fmt='json',
            vad_engine='sm',  # Use simpler VAD engine for CPU
            detect_gender=True  # Keep gender detection as it's lightweight
        )
        
        processed_segments = []
        for segment in segments:
            # inaSpeechSegmenter returns: [label, start_time, end_time]
            label, start_time, end_time = segment
            
            processed_segments.append({
                'label': label,
                'start_time': float(start_time),
                'end_time': float(end_time),
                'confidence': 1.0,
                'duration': float(end_time - start_time)
            })
        
        logger.info(f"Found {len(processed_segments)} segments")
        return processed_segments
        
    except Exception as e:
        logger.error(f"Segmentation failed for {file_path}: {str(e)}")
        return []

def extract_broadcast_datetime(gcs_path: str, gcs_prefix: str) -> datetime:
    """Extract broadcast datetime from GCS path (optimized)"""
    try:
        relative_path = gcs_path.replace(gcs_prefix, '')
        
        # Simplified datetime extraction patterns
        patterns = [
            r'(\d{4}-\d{2}-\d{2})[T_](\d{2}:\d{2})',  # 2025-12-16T08:00
            r'(\d{8})[T_](\d{4})',                      # 20251216_0800
        ]
        
        for pattern in patterns:
            match = re.search(pattern, relative_path)
            if match:
                date_str = match.group(1)
                time_str = match.group(2)
                
                if len(date_str) == 8:  # YYYYMMDD format
                    year, month, day = int(date_str[:4]), int(date_str[4:6]), int(date_str[6:8])
                else:  # YYYY-MM-DD format
                    year, month, day = map(int, date_str.split('-'))
                
                if len(time_str) == 4:  # HHMM format
                    hour, minute = int(time_str[:2]), int(time_str[2:4])
                else:  # HH:MM format
                    hour, minute = map(int, time_str.split(':'))
                
                return datetime(year, month, day, hour, minute, 0)
        
        # Fallback
        logger.warning(f"Could not extract datetime from {gcs_path}")
        return datetime.utcnow()
        
    except Exception as e:
        logger.error(f"Failed to extract datetime: {str(e)}")
        return datetime.utcnow()

# Cleanup utilities (optimized)
def safe_cleanup_file(file_path: str, description: str = "temporary file"):
    """Safely cleanup a single file with logging"""
    try:
        if file_path and os.path.exists(file_path):
            os.unlink(file_path)
            logger.debug(f"Cleaned up {description}")
            return True
    except Exception as e:
        logger.warning(f"Failed to cleanup {description}: {str(e)}")
    return False

def cleanup_temp_files():
    """Cleanup temporary files (optimized for low resources)"""
    try:
        temp_base_dir = tempfile.gettempdir()
        cleaned_count = 0
        
        for item in os.listdir(temp_base_dir):
            if item.startswith("segmentation_"):
                item_path = os.path.join(temp_base_dir, item)
                try:
                    if os.path.isfile(item_path):
                        safe_cleanup_file(item_path, "temp file")
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                    cleaned_count += 1
                except Exception as e:
                    logger.warning(f"Failed to cleanup {item_path}: {str(e)}")
        
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} temporary files")
            
    except Exception as e:
        logger.warning(f"Error during cleanup: {str(e)}")

# Register cleanup
atexit.register(cleanup_temp_files)

# Database functions (optimized)
async def get_or_create_timeline(broadcast_datetime: datetime, source_id: str, audio_file_path: str) -> Optional[int]:
    """Get existing timeline or create new one (optimized)"""
    try:
        # Check if timeline exists (simplified query)
        response = supabase.table('broadcast_timeline').select('id').eq('broadcast_datetime', broadcast_datetime.isoformat()).eq('source_id', source_id).execute()
        
        if response.data:
            return response.data[0]['id']
        
        # Create new timeline
        timeline_data = {
            'broadcast_datetime': broadcast_datetime.isoformat(),
            'source_id': source_id,
            'status': 'processing',
            'recording_url': f"gs://{os.getenv('GCS_BUCKET')}/{audio_file_path}",
            'segmentation_processed': False
        }
        
        result = supabase.table('broadcast_timeline').insert(timeline_data).execute()
        return result.data[0]['id'] if result.data else None
        
    except Exception as e:
        logger.error(f"Failed to create/get timeline: {str(e)}")
        return None

async def save_segments_to_db(timeline_id: int, segments: List[Dict[str, Any]], audio_file_path: str, gcs_prefix: str):
    """Save segmentation results to timeline_labels table (optimized)"""
    try:
        segment_records = []
        
        for segment in segments:
            record = {
                'timeline_id': timeline_id,
                'label': segment['label'],
                'start_time': segment['start_time'],
                'end_time': segment['end_time'],
                'segmentation_confidence': segment['confidence'],
                'media_url': f"gs://{os.getenv('GCS_BUCKET')}/{audio_file_path}",
                'fingerprint_processed': False,
                'transcription_processed': False,
                'fingerprint_matched': False,
                'tags': [segment['label']]
            }
            segment_records.append(record)
        
        # Batch insert with smaller batches
        batch_size = 50  # Smaller batches for low memory
        for i in range(0, len(segment_records), batch_size):
            batch = segment_records[i:i + batch_size]
            supabase.table('timeline_labels').insert(batch).execute()
            gc.collect()  # Force garbage collection after each batch
        
        logger.info(f"Saved {len(segment_records)} segments to database")
        return True
        
    except Exception as e:
        logger.error(f"Failed to save segments to DB: {str(e)}")
        return False

async def update_timeline_status(timeline_id: int, status: str, segments_count: int = 0):
    """Update timeline processing status (optimized)"""
    try:
        update_data = {
            'status': status,
            'updated_at': datetime.utcnow().isoformat(),
            'segmentation_processed': status == 'completed'
        }
        
        if status == 'completed':
            update_data['processed_at'] = datetime.utcnow().isoformat()
        
        supabase.table('broadcast_timeline').update(update_data).eq('id', timeline_id).execute()
        logger.info(f"Updated timeline {timeline_id} status to {status}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update timeline status: {str(e)}")
        return False

# Optimized processing function
async def process_audio_files(request: SegmentationRequest, job_id: str):
    """Background task to process audio files (optimized for low resources)"""
    try:
        logger.info(f"Starting optimized processing for prefix: {request.gcs_prefix}")
        
        # Check memory before starting
        monitor.force_gc_if_needed()
        
        # List audio files from GCS (limited batch)
        bucket_name = os.getenv("GCS_BUCKET")
        bucket = storage_client.bucket(bucket_name)
        
        audio_files = []
        prefix = request.gcs_prefix.rstrip('/') + '/'
        blobs = bucket.list_blobs(prefix=prefix, max_results=100)  # Limit results
        
        for blob in blobs:
            if blob.name.endswith(('.wav', '.mp3', '.m4a', '.flac')):
                audio_files.append(blob.name)
        
        audio_files.sort()  # Ensure chronological order
        logger.info(f"Found {len(audio_files)} audio files to process")
        
        if not audio_files:
            if job_id:
                complete_job(job_id, success=False)
            return
        
        # Update job with file count
        update_job_progress(job_id, files_found=len(audio_files), status="processing")
        
        total_segments = 0
        processed_files = 0
        
        # Process files one at a time (sequential for low resources)
        for i, audio_file_path in enumerate(audio_files):
            start_time = time.time()
            
            # Check processing timeout
            if start_time - monitor.start_time > PROCESSING_TIMEOUT_SECONDS:
                logger.warning("Processing timeout reached")
                break
            
            # Update job progress
            progress_percent = (i / len(audio_files)) * 100
            update_job_progress(job_id,
                              current_file=audio_file_path,
                              files_processed=i,
                              progress_percent=round(progress_percent, 1))
            
            logger.info(f"Processing {i+1}/{len(audio_files)}: {os.path.basename(audio_file_path)}")
            
            temp_path = None
            timeline_id = None
            
            try:
                # Extract broadcast datetime
                broadcast_datetime = extract_broadcast_datetime(audio_file_path, prefix)
                
                # Create timeline
                timeline_id = await get_or_create_timeline(broadcast_datetime, request.source_id, audio_file_path)
                if not timeline_id:
                    logger.error(f"Failed to create timeline for {audio_file_path}")
                    continue
                
                # Create temporary file with smaller size limit
                temp_fd, temp_path = tempfile.mkstemp(suffix='.wav', prefix=f'seg_{os.getpid()}_')
                os.close(temp_fd)
                
                try:
                    # Download audio file
                    if not download_audio_chunk(bucket_name, audio_file_path, temp_path):
                        logger.warning(f"Skipping failed download: {audio_file_path}")
                        continue
                    
                    # Check file size (skip if too large)
                    file_size_mb = os.path.getsize(temp_path) / (1024 * 1024)
                    if file_size_mb > 100:  # Skip files larger than 100MB
                        logger.warning(f"Skipping large file: {audio_file_path} ({file_size_mb:.1f}MB)")
                        continue
                    
                    # Verify downloaded file
                    if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                        logger.warning(f"Downloaded file is empty: {audio_file_path}")
                        continue
                    
                    # Segment the audio file
                    segments = segment_audio_file(temp_path)
                    
                    if segments:
                        # Save segments to database
                        await save_segments_to_db(timeline_id, segments, audio_file_path, prefix)
                        await update_timeline_status(timeline_id, 'completed', len(segments))
                        total_segments += len(segments)
                        processed_files += 1
                        
                        # Update job progress
                        update_job_progress(job_id,
                                          segments_created=total_segments,
                                          files_processed=processed_files)
                        
                        logger.info(f"Successfully processed {os.path.basename(audio_file_path)}: {len(segments)} segments")
                    else:
                        await update_timeline_status(timeline_id, 'failed')
                        logger.warning(f"No segments found in {audio_file_path}")
                        
                finally:
                    # Cleanup temporary file
                    if temp_path:
                        safe_cleanup_file(temp_path, f"temp file for {audio_file_path}")
                
                # Force garbage collection after each file
                gc.collect()
                
            except Exception as e:
                logger.error(f"Failed to process {audio_file_path}: {str(e)}")
                if timeline_id:
                    await update_timeline_status(timeline_id, 'failed')
        
        # Final job completion
        if job_id:
            update_job_progress(job_id,
                              files_processed=len(audio_files),
                              segments_created=total_segments,
                              progress_percent=100.0,
                              status="completed")
            complete_job(job_id, success=True)
        
        logger.info(f"Optimized processing completed: {processed_files} files, {total_segments} segments total")
        
        # Force final garbage collection
        gc.collect()
        
    except Exception as e:
        logger.error(f"Audio processing failed: {str(e)}")
        if job_id:
            complete_job(job_id, success=False)

# Optimized API Endpoints

@app.get("/")
async def root():
    return {
        "message": "Optimized Audio Segmentation Service",
        "status": "running",
        "description": "Optimized for 2 vCPU, 4GB RAM systems",
        "optimizations": ["Sequential processing", "Memory management", "CPU-only libraries"]
    }

@app.get("/health")
async def health_check():
    """Optimized health check"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "segmentation-service-optimized",
        "version": "1.0.0-optimized"
    }

@app.post("/segment", response_model=SegmentResponse)
async def start_segmentation(request: SegmentationRequest, background_tasks: BackgroundTasks):
    """Start audio segmentation (optimized)"""
    try:
        logger.info(f"Received segmentation request for: {request.gcs_prefix}")
        
        # Check current job count (limit for low resources)
        active_jobs = len([job for job in PROCESSING_JOBS.values() if job.get("status") in ["started", "processing"]])
        if active_jobs >= MAX_CONCURRENT_JOBS:
            raise HTTPException(status_code=429, detail="Too many active jobs. Please wait for current processing to complete.")
        
        # Create job tracking
        job_id = track_job(request.source_id, request.gcs_prefix)
        
        # Update metrics
        monitor.request_count += 1
        
        # Add background task
        background_tasks.add_task(process_audio_files, request, job_id)
        
        return SegmentResponse(
            timeline_id=0,
            segments_processed=0,
            message=f"Optimized segmentation started for {request.gcs_prefix}",
            job_id=job_id
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f": {str(eSegmentation request failed)}")
        monitor.error_count += 1
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics/system")
async def get_system_metrics():
    """Get optimized system metrics"""
    try:
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "system": monitor.get_optimized_metrics()
        }
    except Exception as e:
        logger.error(f"Failed to get system metrics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/jobs")
async def list_processing_jobs():
    """List current processing jobs (optimized)"""
    try:
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "jobs": PROCESSING_JOBS,
            "total": len(PROCESSING_JOBS),
            "active_jobs": len([job for job in PROCESSING_JOBS.values() if job.get("status") in ["started", "processing"]])
        }
    except Exception as e:
        logger.error(f"Failed to list jobs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/temp-files")
async def list_temp_files():
    """List current temporary files (optimized)"""
    try:
        temp_dir = tempfile.gettempdir()
        temp_files = []
        
        for item in os.listdir(temp_dir):
            if item.startswith("segmentation_") or item.startswith("seg_"):
                item_path = os.path.join(temp_dir, item)
                try:
                    stat = os.stat(item_path)
                    temp_files.append({
                        "name": item,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "type": "directory" if os.path.isdir(item_path) else "file"
                    })
                except Exception as e:
                    temp_files.append({
                        "name": item,
                        "error": str(e)
                    })
        
        return {
            "temp_directory": temp_dir,
            "temp_files": temp_files,
            "count": len(temp_files)
        }
        
    except Exception as e:
        logger.error(f"Failed to list temp files: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cleanup")
async def manual_cleanup():
    """Manual cleanup endpoint (optimized)"""
    try:
        logger.info("Manual cleanup requested")
        monitor.force_gc_if_needed()
        cleanup_temp_files()
        return {
            "status": "completed",
            "message": "Optimized cleanup completed",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Manual cleanup failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    # Optimized uvicorn configuration for low resources
    uvicorn.run(
        "main_optimized:app",
        host="0.0.0.0",
        port=8000,
        workers=1,  # Single worker for 2 vCPU system
        log_level="warning",  # Reduced logging for performance
        access_log=False,  # Disable access logs for performance
        loop="asyncio"
    )