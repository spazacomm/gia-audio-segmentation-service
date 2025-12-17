"""
FastAPI service for broadcast audio segmentation using inaSpeechSegmenter
"""

import os
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

from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
import uvicorn

# Audio processing
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

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Broadcast Audio Segmentation Service",
    description="Service for segmenting broadcast audio and creating timeline labels",
    version="1.0.0"
)

# Initialize clients
storage_client = storage.Client()
supabase: Client = create_client(
    os.getenv("SUPABASE_URL"),
    os.getenv("SUPABASE_KEY")
)

# Pydantic models
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

# Utility functions
def download_audio_chunk(gcs_bucket: str, gcs_path: str, local_path: str) -> bool:
    """Download audio chunk from GCS to local file"""
    try:
        bucket = storage_client.bucket(gcs_bucket)
        blob = bucket.blob(gcs_path)
        blob.download_to_filename(local_path)
        logger.info(f"Downloaded {gcs_path} to {local_path}")
        return True
    except Exception as e:
        logger.error(f"Failed to download {gcs_path}: {str(e)}")
        return False

def segment_audio_file(file_path: str) -> List[Dict[str, Any]]:
    """Segment audio file using inaSpeechSegmenter"""
    try:
        logger.info(f"Starting segmentation of {file_path}")
        
        # Use inaSpeechSegmenter for audio segmentation
        segments = seg(file_path, fmt='json')
        
        processed_segments = []
        for segment in segments:
            # inaSpeechSegmenter returns: [label, start_time, end_time]
            label, start_time, end_time = segment
            
            processed_segments.append({
                'label': label,
                'start_time': float(start_time),
                'end_time': float(end_time),
                'confidence': 1.0, # inaSpeechSegmenter doesn't provide confidence
                'duration': float(end_time - start_time)
            })
        
        logger.info(f"Found {len(processed_segments)} segments")
        return processed_segments
        
    except Exception as e:
        logger.error(f"Segmentation failed for {file_path}: {str(e)}")
        raise

def upload_segment_to_gcs(local_file_path: str, gcs_path: str) -> str:
    """Upload processed audio segment to GCS"""
    try:
        bucket = storage_client.bucket(os.getenv("GCS_BUCKET"))
        blob = bucket.blob(gcs_path)
        blob.upload_from_filename(local_file_path)
        return f"gs://{os.getenv('GCS_BUCKET')}/{gcs_path}"
    except Exception as e:
        logger.error(f"Failed to upload segment to {gcs_path}: {str(e)}")
        return None

# Cleanup utilities
def safe_cleanup_file(file_path: str, description: str = "temporary file"):
    """Safely cleanup a single file with logging"""
    try:
        if file_path and os.path.exists(file_path):
            os.unlink(file_path)
            logger.debug(f"Cleaned up {description}: {file_path}")
            return True
    except Exception as e:
        logger.warning(f"Failed to cleanup {description} {file_path}: {str(e)}")
    return False

def safe_cleanup_directory(dir_path: str, description: str = "temporary directory"):
    """Safely cleanup a directory and all its contents"""
    try:
        if dir_path and os.path.exists(dir_path):
            shutil.rmtree(dir_path)
            logger.debug(f"Cleaned up {description}: {dir_path}")
            return True
    except Exception as e:
        logger.warning(f"Failed to cleanup {description} {dir_path}: {str(e)}")
    return False

def cleanup_temp_files():
    """Cleanup any remaining temporary files on service shutdown"""
    try:
        # Get the temp directory used by the service
        temp_base_dir = tempfile.gettempdir()
        service_temp_pattern = "segmentation_*"
        
        cleaned_count = 0
        for item in os.listdir(temp_base_dir):
            if item.startswith("segmentation_"):
                item_path = os.path.join(temp_base_dir, item)
                try:
                    if os.path.isfile(item_path):
                        safe_cleanup_file(item_path, "orphaned temp file")
                    elif os.path.isdir(item_path):
                        safe_cleanup_directory(item_path, "orphaned temp directory")
                    cleaned_count += 1
                except Exception as e:
                    logger.warning(f"Failed to cleanup {item_path}: {str(e)}")
        
        if cleaned_count > 0:
            logger.info(f"Cleaned up {cleaned_count} orphaned temporary files/directories")
            
    except Exception as e:
        logger.warning(f"Error during cleanup: {str(e)}")

# Monitoring and metrics
class SystemMonitor:
    """System monitoring and metrics collection"""
    
    def __init__(self):
        self.start_time = time.time()
        self.request_count = 0
        self.segmentation_jobs = {}
        self.error_count = 0
        
    def get_system_metrics(self) -> Dict[str, Any]:
        """Get current system metrics"""
        try:
            return {
                "cpu_percent": psutil.cpu_percent(interval=1),
                "memory_percent": psutil.virtual_memory().percent,
                "memory_used_gb": round(psutil.virtual_memory().used / (1024**3), 2),
                "memory_total_gb": round(psutil.virtual_memory().total / (1024**3), 2),
                "disk_usage_percent": psutil.disk_usage('/').percent,
                "disk_free_gb": round(psutil.disk_usage('/').free / (1024**3), 2),
                "disk_total_gb": round(psutil.disk_usage('/').total / (1024**3), 2),
                "process_count": len(psutil.pids()),
                "boot_time": datetime.fromtimestamp(psutil.boot_time()).isoformat()
            }
        except Exception as e:
            logger.error(f"Failed to get system metrics: {str(e)}")
            return {}
    
    def get_service_metrics(self) -> Dict[str, Any]:
        """Get service-specific metrics"""
        try:
            uptime = time.time() - self.start_time
            
            # Count processing jobs by status
            job_status_counts = {"pending": 0, "processing": 0, "completed": 0, "failed": 0}
            for job in self.segmentation_jobs.values():
                status = job.get("status", "pending")
                job_status_counts[status] = job_status_counts.get(status, 0) + 1
            
            return {
                "uptime_seconds": round(uptime, 2),
                "uptime_hours": round(uptime / 3600, 2),
                "total_requests": self.request_count,
                "error_count": self.error_count,
                "active_jobs": len(self.segmentation_jobs),
                "job_status_counts": job_status_counts,
                "average_processing_time": self._calculate_avg_processing_time()
            }
        except Exception as e:
            logger.error(f"Failed to get service metrics: {str(e)}")
            return {}
    
    def _calculate_avg_processing_time(self) -> float:
        """Calculate average processing time for completed jobs"""
        completed_times = []
        for job in self.segmentation_jobs.values():
            if job.get("status") == "completed" and "start_time" in job:
                if "end_time" in job:
                    processing_time = job["end_time"] - job["start_time"]
                    completed_times.append(processing_time)
        
        return round(sum(completed_times) / len(completed_times), 2) if completed_times else 0.0
    
    def add_job(self, job_id: str, source_id: str, gcs_prefix: str):
        """Add a new segmentation job"""
        self.segmentation_jobs[job_id] = {
            "source_id": source_id,
            "gcs_prefix": gcs_prefix,
            "status": "processing",
            "start_time": time.time(),
            "segments_processed": 0,
            "files_processed": 0
        }
    
    def update_job(self, job_id: str, **kwargs):
        """Update job status and metrics"""
        if job_id in self.segmentation_jobs:
            self.segmentation_jobs[job_id].update(kwargs)
    
    def complete_job(self, job_id: str, success: bool = True):
        """Mark job as completed or failed"""
        if job_id in self.segmentation_jobs:
            self.segmentation_jobs[job_id].update({
                "status": "completed" if success else "failed",
                "end_time": time.time()
            })

# Initialize system monitor
monitor = SystemMonitor()

# Global processing status tracking
PROCESSING_JOBS = {}
JOB_COUNTER = 0

def track_job(source_id: str, gcs_prefix: str) -> str:
    """Create a new job tracking entry"""
    global JOB_COUNTER
    JOB_COUNTER += 1
    job_id = f"job_{JOB_COUNTER}_{int(time.time())}"
    
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
    """Update job progress"""
    if job_id in PROCESSING_JOBS:
        PROCESSING_JOBS[job_id].update(kwargs)

def complete_job(job_id: str, success: bool = True):
    """Mark job as completed"""
    if job_id in PROCESSING_JOBS:
        PROCESSING_JOBS[job_id].update({
            "status": "completed" if success else "failed",
            "end_time": datetime.utcnow().isoformat()
        })

# Register cleanup function to run on service shutdown
atexit.register(cleanup_temp_files)

def extract_broadcast_datetime(gcs_path: str, gcs_prefix: str) -> datetime:
    """Extract broadcast datetime from GCS path"""
    try:
        # Remove prefix to get relative path
        relative_path = gcs_path.replace(gcs_prefix, '')
        
        # Common patterns for broadcast timestamps
        patterns = [
            r'(\d{4}-\d{2}-\d{2})[T_](\d{2}:\d{2}:\d{2})',  # 2025-12-16T08:00:00
            r'(\d{4}-\d{2}-\d{2})[T_](\d{2}:\d{2})',        # 2025-12-16T08:00
            r'(\d{4}\d{2}\d{2})[T_](\d{2}\d{2}\d{2})',      # 20251216_080000
            r'(\d{8})[T_](\d{6})',                           # 20251216_080000
        ]
        
        for pattern in patterns:
            match = re.search(pattern, relative_path)
            if match:
                date_str = match.group(1)
                time_str = match.group(2)
                
                # Handle different date formats
                if len(date_str) == 8:  # YYYYMMDD format
                    year = int(date_str[:4])
                    month = int(date_str[4:6])
                    day = int(date_str[6:8])
                else:  # YYYY-MM-DD format
                    year, month, day = map(int, date_str.split('-'))
                
                # Handle different time formats
                if len(time_str) == 6:  # HHMMSS format
                    hour = int(time_str[:2])
                    minute = int(time_str[2:4])
                    second = int(time_str[4:6])
                else:  # HH:MM format
                    hour, minute = map(int, time_str.split(':'))
                    second = 0
                
                return datetime(year, month, day, hour, minute, second)
        
        # Fallback: use current time if no pattern matches
        logger.warning(f"Could not extract datetime from {gcs_path}, using current time")
        return datetime.utcnow()
        
    except Exception as e:
        logger.error(f"Failed to extract datetime from {gcs_path}: {str(e)}")
        return datetime.utcnow()

def extract_audio_segment(input_file: str, output_file: str, start_time: float, end_time: float):
    """Extract specific segment from audio file"""
    try:
        # Load audio
        audio_data, sample_rate = librosa.load(input_file, sr=None, 
                                               offset=start_time, 
                                               duration=end_time - start_time)
        
        # Save extracted segment
        sf.write(output_file, audio_data, sample_rate)
        logger.info(f"Extracted segment: {start_time:.2f}s - {end_time:.2f}s")
        
    except Exception as e:
        logger.error(f"Failed to extract segment: {str(e)}")
        raise

async def get_or_create_timeline(broadcast_datetime: datetime, source_id: str, audio_file_path: str) -> Optional[int]:
    """Get existing timeline or create new one for specific audio file"""
    try:
        # Check if timeline exists for this broadcast
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
    """Save segmentation results to timeline_labels table"""
    try:
        segment_records = []
        
        for i, segment in enumerate(segments):
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
        
        # Insert segments in batch
        result = supabase.table('timeline_labels').insert(segment_records).execute()
        logger.info(f"Saved {len(segment_records)} segments to database for timeline {timeline_id}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to save segments to DB: {str(e)}")
        return False

async def update_timeline_status(timeline_id: int, status: str, segments_count: int = 0):
    """Update timeline processing status"""
    try:
        update_data = {
            'status': status,
            'updated_at': datetime.utcnow().isoformat(),
            'segmentation_processed': status == 'completed'
        }
        
        if status == 'completed':
            update_data['processed_at'] = datetime.utcnow().isoformat()
        
        result = supabase.table('broadcast_timeline').update(update_data).eq('id', timeline_id).execute()
        logger.info(f"Updated timeline {timeline_id} status to {status}")
        return True
        
    except Exception as e:
        logger.error(f"Failed to update timeline status: {str(e)}")
        return False

async def process_audio_files(request: SegmentationRequest, job_id: str = None):
    """Background task to process all audio files in the GCS prefix"""
    try:
        logger.info(f"Starting audio processing for prefix: {request.gcs_prefix}")
        
        # Update job status
        if job_id:
            update_job_progress(job_id, status="discovering_files")
        
        # List all audio files from GCS
        bucket_name = os.getenv("GCS_BUCKET")
        bucket = storage_client.bucket(bucket_name)
        
        # Find all audio files in the prefix
        audio_files = []
        prefix = request.gcs_prefix.rstrip('/') + '/'
        blobs = bucket.list_blobs(prefix=prefix)
        
        for blob in blobs:
            if blob.name.endswith(('.wav', '.mp3', '.m4a', '.flac')):
                audio_files.append(blob.name)
        
        audio_files.sort()  # Ensure chronological order
        logger.info(f"Found {len(audio_files)} audio files to process")
        
        # Update job with file count
        if job_id:
            update_job_progress(job_id, 
                              files_found=len(audio_files),
                              status="processing" if audio_files else "no_files")
        
        if not audio_files:
            logger.warning(f"No audio files found in prefix: {prefix}")
            if job_id:
                complete_job(job_id, success=False)
            return
        
        total_segments = 0
        processed_files = 0
        
        # Process each audio file
        for i, audio_file_path in enumerate(audio_files):
            # Update job progress
            progress_percent = (i / len(audio_files)) * 100
            if job_id:
                update_job_progress(job_id,
                                  current_file=audio_file_path,
                                  files_processed=i,
                                  progress_percent=round(progress_percent, 1))
            
            logger.info(f"Processing audio file {i+1}/{len(audio_files)}: {audio_file_path}")
            
            temp_path = None  # Initialize for cleanup tracking
            timeline_id = None
            
            try:
                # Extract broadcast datetime from file path
                broadcast_datetime = extract_broadcast_datetime(audio_file_path, prefix)
                
                # Get or create timeline for this broadcast
                timeline_id = await get_or_create_timeline(broadcast_datetime, request.source_id, audio_file_path)
                if not timeline_id:
                    logger.error(f"Failed to create timeline for {audio_file_path}")
                    continue
                
                # Create a more controlled temporary file with cleanup tracking
                temp_fd, temp_path = tempfile.mkstemp(suffix='.wav', prefix=f'segmentation_{os.getpid()}_')
                os.close(temp_fd)  # Close the file descriptor, we only need the path
                
                try:
                    if not download_audio_chunk(bucket_name, audio_file_path, temp_path):
                        logger.warning(f"Skipping failed download: {audio_file_path}")
                        continue
                    
                    # Verify downloaded file
                    if not os.path.exists(temp_path) or os.path.getsize(temp_path) == 0:
                        logger.warning(f"Downloaded file is empty or missing: {audio_file_path}")
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
                        if job_id:
                            update_job_progress(job_id,
                                              segments_created=total_segments,
                                              files_processed=processed_files)
                        
                        logger.info(f"Successfully processed {audio_file_path}: {len(segments)} segments")
                    else:
                        await update_timeline_status(timeline_id, 'failed')
                        logger.warning(f"No segments found in {audio_file_path}")
                        
                except Exception as e:
                    logger.error(f"Error processing audio file {audio_file_path}: {str(e)}")
                    if timeline_id:
                        await update_timeline_status(timeline_id, 'failed')
                    raise  # Re-raise to trigger finally block cleanup
                    
            except Exception as e:
                logger.error(f"Failed to process {audio_file_path}: {str(e)}")
                
            finally:
                # Comprehensive cleanup of temporary files
                if temp_path:
                    safe_cleanup_file(temp_path, f"temp audio file for {audio_file_path}")
                
                # Additional cleanup: remove any files with similar patterns
                try:
                    temp_dir = os.path.dirname(temp_path) if temp_path else tempfile.gettempdir()
                    temp_filename = os.path.basename(temp_path) if temp_path else ""
                    
                    # Clean up any files that might have been created during processing
                    if temp_filename:
                        # Look for files with similar patterns (same PID, etc.)
                        pattern_prefix = f"segmentation_{os.getpid()}_"
                        for file in os.listdir(temp_dir):
                            if file.startswith(pattern_prefix) and file.endswith('.wav'):
                                file_path = os.path.join(temp_dir, file)
                                safe_cleanup_file(file_path, "related temp file")
                except Exception as cleanup_error:
                    logger.warning(f"Error during additional cleanup: {cleanup_error}")
        
        # Final job completion
        if job_id:
            update_job_progress(job_id,
                              files_processed=len(audio_files),
                              segments_created=total_segments,
                              progress_percent=100.0,
                              status="completed")
            complete_job(job_id, success=True)
        
        logger.info(f"Processing completed: {processed_files} files, {total_segments} segments total")
        
    except Exception as e:
        logger.error(f"Audio processing failed: {str(e)}")
        if job_id:
            complete_job(job_id, success=False)

# API Endpoints

@app.get("/")
async def root():
    return {
        "message": "Broadcast Audio Segmentation Service",
        "status": "running",
        "description": "Process audio files from GCS and create timeline segments"
    }

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {
        "status": "healthy",
        "timestamp": datetime.utcnow().isoformat(),
        "service": "segmentation-service",
        "version": "1.0.0"
    }

@app.post("/segment", response_model=SegmentResponse)
async def start_segmentation(request: SegmentationRequest, background_tasks: BackgroundTasks):
    """Start audio segmentation for all files in GCS prefix"""
    try:
        logger.info(f"Received segmentation request for prefix: {request.gcs_prefix}")
        
        # Create job tracking entry
        job_id = track_job(request.source_id, request.gcs_prefix)
        
        # Update service metrics
        monitor.request_count += 1
        
        # Add background task with job tracking
        background_tasks.add_task(process_audio_files, request, job_id)
        
        return SegmentResponse(
            timeline_id=0,  # Multiple timelines will be created
            segments_processed=0,
            message=f"Segmentation started for audio files in {request.gcs_prefix}",
            job_id=job_id
        )
        
    except Exception as e:
        logger.error(f"Segmentation request failed: {str(e)}")
        monitor.error_count += 1
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/timeline/{timeline_id}", response_model=TimelineResponse)
async def get_timeline(timeline_id: int):
    """Get timeline with segments"""
    try:
        # Get timeline
        timeline_response = supabase.table('broadcast_timeline').select('*').eq('id', timeline_id).execute()
        
        if not timeline_response.data:
            raise HTTPException(status_code=404, detail="Timeline not found")
        
        timeline = timeline_response.data[0]
        
        # Get segments
        segments_response = supabase.table('timeline_labels').select('label, start_time, end_time, segmentation_confidence').eq('timeline_id', timeline_id).order('start_time').execute()
        
        segments = [
            {
                'label': seg['label'],
                'start_time': seg['start_time'],
                'end_time': seg['end_time'],
                'confidence': seg['segmentation_confidence']
            }
            for seg in segments_response.data
        ]
        
        return TimelineResponse(
            id=timeline['id'],
            broadcast_datetime=datetime.fromisoformat(timeline['broadcast_datetime']),
            status=timeline['status'],
            segments=segments
        )
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get timeline: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/broadcasts", response_model=BroadcastsResponse)
async def list_broadcasts(source_id: Optional[str] = None, limit: int = 50, offset: int = 0):
    """List broadcast timelines"""
    try:
        query = supabase.table('broadcast_timeline').select('*')
        
        if source_id:
            query = query.eq('source_id', source_id)
        
        response = query.order('broadcast_datetime', desc=True).range(offset, offset + limit - 1).execute()
        
        return BroadcastsResponse(
            broadcasts=response.data,
            total=len(response.data)
        )
        
    except Exception as e:
        logger.error(f"Failed to list broadcasts: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/broadcasts/{source_id}")
async def get_source_broadcasts(source_id: str, limit: int = 50):
    """Get all broadcasts for a specific source"""
    try:
        response = supabase.table('broadcast_timeline').select('*').eq('source_id', source_id).order('broadcast_datetime', desc=True).limit(limit).execute()
        
        return {"broadcasts": response.data, "total": len(response.data)}
        
    except Exception as e:
        logger.error(f"Failed to get source broadcasts: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/segments/{timeline_id}")
async def get_timeline_segments(timeline_id: int):
    """Get all segments for a timeline"""
    try:
        response = supabase.table('timeline_labels').select('*').eq('timeline_id', timeline_id).order('start_time').execute()
        
        return {"segments": response.data, "total": len(response.data)}
        
    except Exception as e:
        logger.error(f"Failed to get timeline segments: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/cleanup")
async def manual_cleanup():
    """Manual cleanup endpoint to remove temporary files"""
    try:
        logger.info("Manual cleanup requested")
        cleanup_temp_files()
        return {
            "status": "completed",
            "message": "Temporary files cleanup completed",
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Manual cleanup failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/temp-files")
async def list_temp_files():
    """List current temporary files for debugging"""
    try:
        temp_dir = tempfile.gettempdir()
        temp_files = []
        
        for item in os.listdir(temp_dir):
            if item.startswith("segmentation_"):
                item_path = os.path.join(temp_dir, item)
                try:
                    stat = os.stat(item_path)
                    temp_files.append({
                        "name": item,
                        "path": item_path,
                        "size": stat.st_size,
                        "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                        "type": "directory" if os.path.isdir(item_path) else "file"
                    })
                except Exception as e:
                    temp_files.append({
                        "name": item,
                        "path": item_path,
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

# ==================== MONITORING ENDPOINTS ====================

@app.get("/metrics/system")
async def get_system_metrics():
    """Get comprehensive system metrics"""
    try:
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "system": monitor.get_system_metrics(),
            "service": monitor.get_service_metrics()
        }
    except Exception as e:
        logger.error(f"Failed to get system metrics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics/jobs")
async def get_job_metrics():
    """Get current job processing metrics"""
    try:
        # Get job statistics
        job_stats = {
            "total_jobs": len(PROCESSING_JOBS),
            "jobs_by_status": {},
            "active_jobs": [],
            "completed_jobs": [],
            "failed_jobs": []
        }
        
        for job_id, job_data in PROCESSING_JOBS.items():
            status = job_data.get("status", "unknown")
            
            # Count by status
            job_stats["jobs_by_status"][status] = job_stats["jobs_by_status"].get(status, 0) + 1
            
            # Categorize jobs
            if status in ["processing", "started"]:
                job_stats["active_jobs"].append({**job_data, "job_id": job_id})
            elif status == "completed":
                job_stats["completed_jobs"].append({**job_data, "job_id": job_id})
            elif status == "failed":
                job_stats["failed_jobs"].append({**job_data, "job_id": job_id})
        
        # Calculate processing statistics
        completed_jobs = job_stats["completed_jobs"]
        if completed_jobs:
            processing_times = []
            for job in completed_jobs:
                if "start_time" in job and "end_time" in job:
                    start = datetime.fromisoformat(job["start_time"].replace("Z", "+00:00"))
                    end = datetime.fromisoformat(job["end_time"].replace("Z", "+00:00"))
                    processing_times.append((end - start).total_seconds())
            
            if processing_times:
                job_stats["avg_processing_time_seconds"] = round(sum(processing_times) / len(processing_times), 2)
                job_stats["min_processing_time_seconds"] = round(min(processing_times), 2)
                job_stats["max_processing_time_seconds"] = round(max(processing_times), 2)
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "job_statistics": job_stats,
            "database_status": await get_database_status()
        }
        
    except Exception as e:
        logger.error(f"Failed to get job metrics: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/metrics/dashboard")
async def get_monitoring_dashboard():
    """Get comprehensive monitoring dashboard data"""
    try:
        # Get database status
        db_status = await get_database_status()
        
        # Get recent broadcasts
        recent_broadcasts = supabase.table('broadcast_timeline').select('*').order('created_at', desc=True).limit(10).execute()
        
        # Calculate processing rates
        now = datetime.utcnow()
        last_hour = now - timedelta(hours=1)
        last_24_hours = now - timedelta(hours=24)
        
        # Count broadcasts by time period
        broadcasts_last_hour = supabase.table('broadcast_timeline').select('id').gte('created_at', last_hour.isoformat()).execute()
        broadcasts_last_24h = supabase.table('broadcast_timeline').select('id').gte('created_at', last_24_hours.isoformat()).execute()
        
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "overview": {
                "service_uptime_hours": monitor.get_service_metrics().get("uptime_hours", 0),
                "total_requests": monitor.get_service_metrics().get("total_requests", 0),
                "error_rate_percent": round((monitor.error_count / max(monitor.request_count, 1)) * 100, 2),
                "active_jobs": len([j for j in PROCESSING_JOBS.values() if j.get("status") in ["started", "processing"]])
            },
            "system": monitor.get_system_metrics(),
            "processing": {
                "jobs_last_hour": len(broadcasts_last_hour.data) if broadcasts_last_hour.data else 0,
                "jobs_last_24h": len(broadcasts_last_24h.data) if broadcasts_last_24h.data else 0,
                "current_jobs": PROCESSING_JOBS
            },
            "database": db_status,
            "recent_broadcasts": recent_broadcasts.data if recent_broadcasts.data else []
        }
        
    except Exception as e:
        logger.error(f"Failed to get dashboard data: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/health/detailed")
async def detailed_health_check():
    """Detailed health check with component status"""
    try:
        health_status = {
            "status": "healthy",
            "timestamp": datetime.utcnow().isoformat(),
            "components": {},
            "metrics": {}
        }
        
        # Check system health
        try:
            system_metrics = monitor.get_system_metrics()
            health_status["components"]["system"] = {
                "status": "healthy",
                "cpu_percent": system_metrics.get("cpu_percent", 0),
                "memory_percent": system_metrics.get("memory_percent", 0),
                "disk_usage_percent": system_metrics.get("disk_usage_percent", 0)
            }
            
            # Set warnings based on thresholds
            if system_metrics.get("cpu_percent", 0) > 80:
                health_status["components"]["system"]["status"] = "warning"
                health_status["status"] = "degraded"
            if system_metrics.get("memory_percent", 0) > 85:
                health_status["components"]["system"]["status"] = "warning"
                health_status["status"] = "degraded"
            if system_metrics.get("disk_usage_percent", 0) > 90:
                health_status["components"]["system"]["status"] = "critical"
                health_status["status"] = "unhealthy"
                
        except Exception as e:
            health_status["components"]["system"] = {
                "status": "error",
                "error": str(e)
            }
            health_status["status"] = "degraded"
        
        # Check database health
        try:
            db_status = await get_database_status()
            health_status["components"]["database"] = db_status
            
            if not db_status.get("connected", False):
                health_status["status"] = "unhealthy"
                
        except Exception as e:
            health_status["components"]["database"] = {
                "status": "error",
                "error": str(e)
            }
            health_status["status"] = "unhealthy"
        
        # Check GCS connectivity
        try:
            bucket_name = os.getenv("GCS_BUCKET")
            bucket = storage_client.bucket(bucket_name)
            bucket.reload()  # Test bucket access
            health_status["components"]["gcs"] = {
                "status": "healthy",
                "bucket": bucket_name
            }
        except Exception as e:
            health_status["components"]["gcs"] = {
                "status": "error",
                "error": str(e)
            }
            health_status["status"] = "degraded"
        
        # Add service metrics
        health_status["metrics"] = monitor.get_service_metrics()
        
        return health_status
        
    except Exception as e:
        logger.error(f"Detailed health check failed: {str(e)}")
        return {
            "status": "error",
            "timestamp": datetime.utcnow().isoformat(),
            "error": str(e)
        }

async def get_database_status() -> Dict[str, Any]:
    """Check database connectivity and status"""
    try:
        # Test basic connectivity
        response = supabase.table('broadcast_timeline').select('id').limit(1).execute()
        
        # Get table counts
        timeline_count = supabase.table('broadcast_timeline').select('id', count='exact').execute()
        labels_count = supabase.table('timeline_labels').select('id', count='exact').execute()
        
        return {
            "connected": True,
            "response_time_ms": "N/A",  # Could add timing here
            "tables": {
                "broadcast_timeline": timeline_count.count if timeline_count.count else 0,
                "timeline_labels": labels_count.count if labels_count.count else 0
            }
        }
    except Exception as e:
        return {
            "connected": False,
            "error": str(e)
        }

@app.get("/jobs")
async def list_processing_jobs():
    """List all current processing jobs"""
    try:
        return {
            "timestamp": datetime.utcnow().isoformat(),
            "jobs": PROCESSING_JOBS,
            "total": len(PROCESSING_JOBS)
        }
    except Exception as e:
        logger.error(f"Failed to list jobs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/jobs/{job_id}")
async def get_job_status(job_id: str):
    """Get status of specific processing job"""
    try:
        if job_id not in PROCESSING_JOBS:
            raise HTTPException(status_code=404, detail="Job not found")
        
        job_data = PROCESSING_JOBS[job_id].copy()
        job_data["job_id"] = job_id
        
        return job_data
        
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to get job status: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/dashboard")
async def get_dashboard():
    """Serve the monitoring dashboard"""
    try:
        dashboard_path = os.path.join(os.path.dirname(__file__), 'dashboard.html')
        if os.path.exists(dashboard_path):
            with open(dashboard_path, 'r', encoding='utf-8') as f:
                return HTMLResponse(content=f.read())
        else:
            return {"error": "Dashboard not found"}
    except Exception as e:
        logger.error(f"Failed to serve dashboard: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# Import for HTMLResponse
from fastapi.responses import HTMLResponse

if __name__ == "__main__":
    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=8000,
        reload=True,
        log_level="info"
    )