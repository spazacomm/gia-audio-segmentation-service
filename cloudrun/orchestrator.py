import os
import logging
from datetime import datetime
from typing import List, Optional
from pathlib import Path

from google.cloud import storage
from supabase import create_client, Client
import requests

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Environment Variables ---
PROJECT_ID = os.getenv("PROJECT_ID", "spaza-media-monitor")
BUCKET = os.getenv("BUCKET", "spaza-recordings")
COUNTRY = os.getenv("COUNTRY", "kenya")
CHANNEL = os.getenv("CHANNEL", "radio")
STATION = os.getenv("STATION", "capital-fm")
DATE = os.getenv("DATE", datetime.now().strftime("%Y-%m-%d"))

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")
INASPEECH_SERVICE_URL = os.getenv("INASPEECH_SERVICE_URL")  # e.g., http://34.56.78.90:8000

# --- Initialize Clients ---
storage_client = storage.Client(project=PROJECT_ID)
supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

def get_source_id(station: str) -> str:
    """Get source_id from database based on station name"""
    try:
        response = supabase.table('sources').select('id').eq('name', station).single().execute()
        if response.data:
            return response.data['id']
        else:
            logger.error(f"Source not found for station: {station}")
            raise ValueError(f"Source not found: {station}")
    except Exception as e:
        logger.error(f"Error fetching source_id: {e}")
        raise

def get_audio_files(bucket_name: str, folder_path: str) -> List[str]:
    """List all audio files in the specified GCS folder"""
    bucket = storage_client.bucket(bucket_name)
    blobs = bucket.list_blobs(prefix=folder_path)
    
    audio_files = []
    for blob in blobs:
        if blob.name.endswith(('.mp3', '.wav', '.m4a', '.flac')):
            audio_files.append(f"gs://{bucket_name}/{blob.name}")
    
    logger.info(f"Found {len(audio_files)} audio files in {folder_path}")
    return audio_files

def check_if_processed(recording_url: str) -> bool:
    """Check if this file has already been processed successfully"""
    try:
        response = supabase.table('broadcast_timeline')\
            .select('id, status, segmentation_processed')\
            .eq('recording_url', recording_url)\
            .execute()
        
        if response.data and len(response.data) > 0:
            record = response.data[0]
            # Already processed successfully
            if record['status'] == 'completed' and record['segmentation_processed']:
                logger.info(f"File already processed: {recording_url}")
                return True
        
        return False
    except Exception as e:
        logger.error(f"Error checking if processed: {e}")
        return False

def create_broadcast_timeline(recording_url: str, source_id: str, broadcast_datetime: datetime) -> int:
    """Create or get broadcast_timeline entry"""
    try:
        # Try to get existing entry
        response = supabase.table('broadcast_timeline')\
            .select('id')\
            .eq('recording_url', recording_url)\
            .execute()
        
        if response.data and len(response.data) > 0:
            timeline_id = response.data[0]['id']
            logger.info(f"Using existing timeline_id: {timeline_id}")
            return timeline_id
        
        # Create new entry
        data = {
            'recording_url': recording_url,
            'source_id': source_id,
            'broadcast_datetime': broadcast_datetime.isoformat(),
            'status': 'pending',
            'segmentation_processed': False
        }
        
        response = supabase.table('broadcast_timeline').insert(data).execute()
        timeline_id = response.data[0]['id']
        logger.info(f"Created new timeline_id: {timeline_id}")
        return timeline_id
        
    except Exception as e:
        logger.error(f"Error creating broadcast_timeline: {e}")
        raise

def update_timeline_status(timeline_id: int, status: str, error: str = None):
    """Update broadcast_timeline status"""
    try:
        data = {
            'status': status,
            'updated_at': datetime.utcnow().isoformat()
        }
        
        if status == 'completed':
            data['segmentation_processed'] = True
            data['processed_at'] = datetime.utcnow().isoformat()
        
        if error:
            data['metadata'] = {'error': error}
        
        supabase.table('broadcast_timeline').update(data).eq('id', timeline_id).execute()
        logger.info(f"Updated timeline {timeline_id} to status: {status}")
        
    except Exception as e:
        logger.error(f"Error updating timeline status: {e}")

def insert_timeline_labels(timeline_id: int, segments: List[dict]):
    """Insert segmentation results into timeline_labels"""
    try:
        labels_data = []
        for seg in segments:
            labels_data.append({
                'timeline_id': timeline_id,
                'label': seg['label'],
                'start_time': seg['start_time'],
                'end_time': seg['end_time']
            })
        
        if labels_data:
            response = supabase.table('timeline_labels').insert(labels_data).execute()
            logger.info(f"Inserted {len(labels_data)} timeline_labels for timeline {timeline_id}")
            return len(labels_data)
        
        return 0
        
    except Exception as e:
        logger.error(f"Error inserting timeline_labels: {e}")
        raise

def call_segmentation_service(recording_url: str) -> List[dict]:
    """Call InaSpeech service to segment audio and get results"""
    try:
        payload = {'processing_url': recording_url}
        logger.info(f"Calling InaSpeech service: {INASPEECH_SERVICE_URL}/process")
        
        response = requests.post(
            f"{INASPEECH_SERVICE_URL}/process",
            json=payload,
            timeout=3600  # 1 hour timeout for long files
        )
        
        response.raise_for_status()
        result = response.json()
        
        logger.info(f"Received {result['total_segments']} segments from service")
        return result['segments']
        
    except requests.exceptions.Timeout:
        logger.error("Segmentation service timeout")
        raise Exception("Segmentation service timeout after 1 hour")
    except requests.exceptions.RequestException as e:
        logger.error(f"Error calling segmentation service: {e}")
        raise Exception(f"Segmentation service error: {str(e)}")

def extract_datetime_from_filename(filename: str, date: str) -> datetime:
    """Extract datetime from filename or use date"""
    # Example: capital-fm_20251218_140530.mp3
    try:
        # Try to parse datetime from filename
        # Adjust this based on your actual filename format
        parts = Path(filename).stem.split('_')
        if len(parts) >= 3:
            time_str = parts[-1]  # e.g., 140530
            if len(time_str) == 6:
                hour = time_str[:2]
                minute = time_str[2:4]
                second = time_str[4:6]
                return datetime.fromisoformat(f"{date}T{hour}:{minute}:{second}")
    except Exception as e:
        logger.debug(f"Could not parse datetime from filename: {e}")
    
    # Fallback to date only
    return datetime.fromisoformat(f"{date}T00:00:00")

def process_audio_file(recording_url: str, source_id: str, broadcast_datetime: datetime):
    """Process a single audio file: create timeline, call service, store results"""
    logger.info(f"Processing: {recording_url}")
    
    # Check if already processed
    if check_if_processed(recording_url):
        logger.info(f"Skipping already processed file: {recording_url}")
        return True
    
    timeline_id = None
    
    try:
        # Create/get timeline entry
        timeline_id = create_broadcast_timeline(recording_url, source_id, broadcast_datetime)
        
        # Update status to processing
        update_timeline_status(timeline_id, 'processing')
        
        # Call segmentation service and get results
        segments = call_segmentation_service(recording_url)
        
        # Insert segments into database
        inserted_count = insert_timeline_labels(timeline_id, segments)
        
        # Update status to completed
        update_timeline_status(timeline_id, 'completed')
        
        logger.info(f"Successfully processed {recording_url}: {inserted_count} segments")
        return True
        
    except Exception as e:
        error_msg = str(e)
        logger.error(f"Failed to process {recording_url}: {error_msg}")
        
        # Update status to failed if we have a timeline_id
        if timeline_id:
            update_timeline_status(timeline_id, 'failed', error_msg)
        
        return False

def main():
    """Main orchestration logic"""
    logger.info("="*60)
    logger.info("Starting Audio Processing Orchestrator")
    logger.info(f"Project: {PROJECT_ID}")
    logger.info(f"Bucket: {BUCKET}")
    logger.info(f"Folder: {COUNTRY}/{CHANNEL}/{STATION}/{DATE}")
    logger.info(f"InaSpeech Service: {INASPEECH_SERVICE_URL}")
    logger.info("="*60)
    
    # Get source_id
    source_id = get_source_id(STATION)
    logger.info(f"Source ID: {source_id}")
    
    # Build folder path
    folder_path = f"{COUNTRY}/{CHANNEL}/{STATION}/{DATE}/"
    
    # Get all audio files
    audio_files = get_audio_files(BUCKET, folder_path)
    
    if not audio_files:
        logger.warning("No audio files found!")
        return
    
    # Process each file
    success_count = 0
    error_count = 0
    skipped_count = 0
    
    for audio_url in audio_files:
        try:
            # Extract datetime from filename
            filename = audio_url.split('/')[-1]
            broadcast_datetime = extract_datetime_from_filename(filename, DATE)
            
            # Process the file
            success = process_audio_file(audio_url, source_id, broadcast_datetime)
            
            if success:
                # Check if it was skipped or actually processed
                response = supabase.table('broadcast_timeline')\
                    .select('status')\
                    .eq('recording_url', audio_url)\
                    .single()\
                    .execute()
                
                if response.data['status'] == 'completed':
                    success_count += 1
                else:
                    skipped_count += 1
            else:
                error_count += 1
            
        except Exception as e:
            logger.error(f"Unexpected error processing {audio_url}: {e}")
            error_count += 1
    
    # Summary
    logger.info("="*60)
    logger.info("Processing Summary:")
    logger.info(f"Total files: {len(audio_files)}")
    logger.info(f"Processed: {success_count}")
    logger.info(f"Skipped (already done): {skipped_count}")
    logger.info(f"Errors: {error_count}")
    logger.info("="*60)

if __name__ == "__main__":
    main()