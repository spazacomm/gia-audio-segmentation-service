import os
import sys
import logging
import tempfile
import asyncio
from pathlib import Path
from typing import List, Optional, Dict, Any
from datetime import datetime, timezone

from pydantic import BaseModel, field_validator
from pydantic_settings import BaseSettings
from google.cloud import storage
from supabase import create_client, Client
from pydub import AudioSegment
import whisper
import torch

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
    # Optional filters
    SOURCE_ID: Optional[str] = None
    BATCH_SIZE: int = 50
    
    # Supabase
    SUPABASE_URL: str
    SUPABASE_KEY: str
    
    # GCS
    BUCKET_NAME: str = "spaza-recordings"
    
    # Processing
    MAX_CONCURRENT: int = 2  # Lower for CPU processing
    SUPPORTED_LABELS: List[str] = ["female", "male"]
    
    # Whisper Settings
    WHISPER_MODEL: str = "base"  # tiny, base, small, medium, large
    WHISPER_LANGUAGE: Optional[str] = "en"  # None for auto-detect, or 'en', 'sw', etc.
    WHISPER_DEVICE: str = "cpu"
    WHISPER_COMPUTE_TYPE: str = "int8"  # int8 for CPU optimization
    
    class Config:
        env_file = ".env"
    
    @field_validator('SUPABASE_URL', 'SUPABASE_KEY')
    @classmethod
    def validate_required(cls, v: str, info) -> str:
        if not v or not v.strip():
            raise ValueError(f"{info.field_name} is required")
        return v.strip()

settings = Settings()

# ============================================================
# DATA MODELS
# ============================================================

class TranscriptionLabel(BaseModel):
    id: int
    timeline_id: int
    label: str
    start_offset_seconds: float
    end_offset_seconds: float
    recording_url: str
    transcription_processed: bool

class ProcessingStats(BaseModel):
    total_labels: int = 0
    completed: int = 0
    failed: int = 0
    skipped: int = 0
    failed_labels: List[int] = []
    start_time: datetime
    end_time: Optional[datetime] = None

# ============================================================
# SUPABASE CLIENT
# ============================================================

class SupabaseClient:
    def __init__(self):
        self.client: Client = create_client(settings.SUPABASE_URL, settings.SUPABASE_KEY)
        logger.info("Supabase client initialized")
    
    def fetch_unprocessed_labels(self, source_id: Optional[str] = None, 
                                 limit: int = 50) -> List[TranscriptionLabel]:
        """Fetch unprocessed speech labels from database"""
        try:
            query = self.client.table('timeline_labels')\
                .select('''
                    id,
                    timeline_id,
                    label,
                    start_offset_seconds,
                    end_offset_seconds,
                    transcription_processed,
                    broadcast_timeline!inner(recording_url, source_id)
                ''')\
                .eq('transcription_processed', False)\
                .in_('label', settings.SUPPORTED_LABELS)\
                .order('id')\
                .limit(limit)
            
            if source_id:
                query = query.eq('broadcast_timeline.source_id', source_id)
            
            response = query.execute()
            
            labels = []
            for item in response.data:
                labels.append(TranscriptionLabel(
                    id=item['id'],
                    timeline_id=item['timeline_id'],
                    label=item['label'],
                    start_offset_seconds=item['start_offset_seconds'],
                    end_offset_seconds=item['end_offset_seconds'],
                    recording_url=item['broadcast_timeline']['recording_url'],
                    transcription_processed=item['transcription_processed']
                ))
            
            return labels
            
        except Exception as e:
            logger.error(f"Failed to fetch unprocessed labels: {e}")
            return []
    
    def update_transcription(self, label_id: int, transcription: str, 
                            confidence: float, language: str) -> bool:
        """Update label with transcription results"""
        try:
            update_data = {
                'transcript': transcription,
                'transcript_confidence': confidence,
                'transcript_language': language,
                'transcription_processed': True,
                'processing_completed_at': datetime.now(timezone.utc).isoformat()
            }
            
            self.client.table('timeline_labels')\
                .update(update_data)\
                .eq('id', label_id)\
                .execute()
            
            return True
        except Exception as e:
            logger.error(f"Failed to update transcription for label {label_id}: {e}")
            return False
    
    def mark_transcription_failed(self, label_id: int, error_message: str) -> bool:
        """Mark label transcription as failed"""
        try:
            update_data = {
                'transcription_processed': True,
                'transcription_error': error_message,
                'processing_completed_at': datetime.now(timezone.utc).isoformat()
            }
            
            self.client.table('timeline_labels')\
                .update(update_data)\
                .eq('id', label_id)\
                .execute()
            
            return True
        except Exception as e:
            logger.error(f"Failed to mark transcription failed for label {label_id}: {e}")
            return False

# ============================================================
# GCS CLIENT
# ============================================================

class GCSClient:
    def __init__(self):
        self.storage_client = storage.Client()
        self._download_cache: Dict[str, str] = {}  # Cache downloaded files
        logger.info(f"GCS client initialized")
    
    def download_to_temp(self, gcs_url: str) -> Optional[str]:
        """Download file from GCS URL to temporary location (with caching)"""
        try:
            # Check cache first
            if gcs_url in self._download_cache:
                cached_path = self._download_cache[gcs_url]
                if os.path.exists(cached_path):
                    logger.debug(f"Using cached file for {gcs_url}")
                    return cached_path
            
            # Parse gs://bucket/path format
            if not gcs_url.startswith('gs://'):
                raise ValueError(f"Invalid GCS URL: {gcs_url}")
            
            url_parts = gcs_url[5:].split('/', 1)
            bucket_name = url_parts[0]
            blob_path = url_parts[1]
            
            bucket = self.storage_client.bucket(bucket_name)
            blob = bucket.blob(blob_path)
            
            suffix = Path(blob_path).suffix
            with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp_file:
                tmp_path = tmp_file.name
            
            blob.download_to_filename(tmp_path)
            logger.debug(f"Downloaded {gcs_url} to {tmp_path}")
            
            # Cache the path
            self._download_cache[gcs_url] = tmp_path
            return tmp_path
            
        except Exception as e:
            logger.error(f"Failed to download {gcs_url}: {e}")
            return None
    
    def clear_cache(self):
        """Clear download cache and delete temp files"""
        for path in self._download_cache.values():
            if os.path.exists(path):
                try:
                    os.unlink(path)
                except Exception as e:
                    logger.warning(f"Failed to delete cached file {path}: {e}")
        self._download_cache.clear()

# ============================================================
# WHISPER TRANSCRIPTION
# ============================================================

class WhisperTranscriber:
    def __init__(self):
        logger.info(f"Loading Whisper model: {settings.WHISPER_MODEL}")
        logger.info(f"Device: {settings.WHISPER_DEVICE}")
        
        # Load model with CPU optimizations
        self.model = whisper.load_model(
            settings.WHISPER_MODEL,
            device=settings.WHISPER_DEVICE
        )
        
        # Set to evaluation mode for inference
        self.model.eval()
        
        # Disable gradient computation for inference
        torch.set_grad_enabled(False)
        
        logger.info("Whisper model loaded successfully")
    
    def transcribe(self, audio_path: str) -> Optional[Dict[str, Any]]:
        """Transcribe audio using Whisper"""
        try:
            # Transcribe with optimized settings for CPU
            result = self.model.transcribe(
                audio_path,
                language=settings.WHISPER_LANGUAGE,
                task="transcribe",
                fp16=False,  # Disable FP16 for CPU
                verbose=False,
                beam_size=1,  # Faster, less memory
                best_of=1,  # Faster, less memory
                temperature=0.0,  # Deterministic
                compression_ratio_threshold=2.4,
                logprob_threshold=-1.0,
                no_speech_threshold=0.6
            )
            
            if not result or not result.get('text'):
                return None
            
            # Calculate average confidence from segments
            avg_confidence = 0.0
            if 'segments' in result and result['segments']:
                confidences = [
                    seg.get('avg_logprob', 0.0) 
                    for seg in result['segments']
                ]
                # Convert log probabilities to approximate confidence (0-1)
                avg_confidence = sum([min(1.0, max(0.0, (c + 1))) for c in confidences]) / len(confidences)
            
            return {
                'transcription': result['text'].strip(),
                'confidence': avg_confidence,
                'language': result.get('language', settings.WHISPER_LANGUAGE or 'unknown')
            }
            
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")
            return None

# ============================================================
# AUDIO PROCESSOR
# ============================================================

class AudioProcessor:
    def __init__(self):
        self.gcs_client = GCSClient()
        self.db_client = SupabaseClient()
        self.transcriber = WhisperTranscriber()
        logger.info("Audio processor initialized")
    
    async def extract_audio_segment(self, audio_path: str, 
                                    start_sec: float, end_sec: float) -> Optional[str]:
        """Extract audio segment and save to temp file"""
        try:
            def _extract():
                # Load audio file
                audio = AudioSegment.from_file(audio_path)
                
                # Extract segment (pydub uses milliseconds)
                start_ms = int(start_sec * 1000)
                end_ms = int(end_sec * 1000)
                segment = audio[start_ms:end_ms]
                
                # Convert to mono and 16kHz (Whisper's expected format)
                segment = segment.set_channels(1)
                segment = segment.set_frame_rate(16000)
                
                # Save to temp file
                with tempfile.NamedTemporaryFile(suffix='.wav', delete=False) as tmp_file:
                    tmp_path = tmp_file.name
                
                segment.export(tmp_path, format='wav')
                return tmp_path
            
            return await asyncio.to_thread(_extract)
            
        except Exception as e:
            logger.error(f"Failed to extract audio segment: {e}")
            return None
    
    async def process_label(self, label: TranscriptionLabel) -> bool:
        """Process a single label: download audio, extract segment, transcribe"""
        segment_path = None
        
        try:
            logger.debug(f"Processing label {label.id} ({label.label})")
            
            # Download full audio file (cached)
            audio_path = await asyncio.to_thread(
                self.gcs_client.download_to_temp, 
                label.recording_url
            )
            
            if not audio_path:
                await asyncio.to_thread(
                    self.db_client.mark_transcription_failed,
                    label.id,
                    "Failed to download audio file"
                )
                return False
            
            # Extract the specific segment
            segment_path = await self.extract_audio_segment(
                audio_path,
                label.start_offset_seconds,
                label.end_offset_seconds
            )
            
            if not segment_path:
                await asyncio.to_thread(
                    self.db_client.mark_transcription_failed,
                    label.id,
                    "Failed to extract audio segment"
                )
                return False
            
            # Transcribe segment with Whisper
            result = await asyncio.to_thread(
                self.transcriber.transcribe,
                segment_path
            )
            
            if not result or not result['transcription']:
                await asyncio.to_thread(
                    self.db_client.mark_transcription_failed,
                    label.id,
                    "No transcription returned"
                )
                return False
            
            # Update database with transcription
            success = await asyncio.to_thread(
                self.db_client.update_transcription,
                label.id,
                result['transcription'],
                result['confidence'],
                result['language']
            )
            
            if success:
                logger.info(f"✓ Label {label.id}: '{result['transcription'][:50]}...' "
                          f"(confidence: {result['confidence']:.2f}, lang: {result['language']})")
            
            return success
            
        except Exception as e:
            logger.error(f"Error processing label {label.id}: {e}", exc_info=True)
            await asyncio.to_thread(
                self.db_client.mark_transcription_failed,
                label.id,
                str(e)
            )
            return False
            
        finally:
            # Cleanup segment file only (keep cached full audio)
            if segment_path and os.path.exists(segment_path):
                try:
                    os.unlink(segment_path)
                except Exception as e:
                    logger.warning(f"Failed to cleanup segment file {segment_path}: {e}")

# ============================================================
# MAIN JOB
# ============================================================

async def run_job():
    """Main job execution"""
    stats = ProcessingStats(start_time=datetime.now(timezone.utc))
    
    logger.info("=" * 60)
    logger.info("Starting Whisper Audio Transcription Service (CPU Optimized)")
    logger.info("=" * 60)
    logger.info(f"Whisper Model: {settings.WHISPER_MODEL}")
    logger.info(f"Device: {settings.WHISPER_DEVICE}")
    logger.info(f"Language: {settings.WHISPER_LANGUAGE or 'Auto-detect'}")
    logger.info(f"Source ID Filter: {settings.SOURCE_ID or 'None (all sources)'}")
    logger.info(f"Batch Size: {settings.BATCH_SIZE}")
    logger.info(f"Max Concurrent: {settings.MAX_CONCURRENT}")
    logger.info(f"Supported Labels: {', '.join(settings.SUPPORTED_LABELS)}")
    logger.info("")
    
    # Initialize processor
    processor = AudioProcessor()
    
    # Fetch unprocessed labels
    logger.info("Fetching unprocessed labels from database...")
    labels = await asyncio.to_thread(
        processor.db_client.fetch_unprocessed_labels,
        settings.SOURCE_ID,
        settings.BATCH_SIZE
    )
    
    if not labels:
        logger.info("No unprocessed labels found")
        return
    
    stats.total_labels = len(labels)
    logger.info(f"Found {stats.total_labels} unprocessed labels")
    logger.info(f"Processing with {settings.MAX_CONCURRENT} concurrent workers...")
    logger.info("")
    
    # Group labels by recording_url for efficient processing
    labels_by_recording: Dict[str, List[TranscriptionLabel]] = {}
    for label in labels:
        if label.recording_url not in labels_by_recording:
            labels_by_recording[label.recording_url] = []
        labels_by_recording[label.recording_url].append(label)
    
    logger.info(f"Grouped into {len(labels_by_recording)} unique recordings")
    logger.info("")
    
    # Process labels with concurrency limit
    semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT)
    
    async def process_with_limit(label: TranscriptionLabel) -> tuple[TranscriptionLabel, bool]:
        async with semaphore:
            success = await processor.process_label(label)
            return label, success
    
    # Create all tasks
    tasks = [process_with_limit(label) for label in labels]
    
    # Process with progress tracking
    completed_count = 0
    for coro in asyncio.as_completed(tasks):
        label, success = await coro
        completed_count += 1
        
        if success:
            stats.completed += 1
        else:
            stats.failed += 1
            stats.failed_labels.append(label.id)
        
        # Progress indicator every 10% or every 5 labels
        if completed_count % max(1, stats.total_labels // 10) == 0 or completed_count % 5 == 0:
            progress_pct = (completed_count / stats.total_labels) * 100
            logger.info(f"Progress: {completed_count}/{stats.total_labels} ({progress_pct:.1f}%) - "
                       f"Completed: {stats.completed}, Failed: {stats.failed}")
    
    # Clear download cache
    processor.gcs_client.clear_cache()
    
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
    logger.info(f"Total Labels Processed: {stats.total_labels}")
    logger.info(f"Successfully Transcribed: {stats.completed}")
    logger.info(f"Failed: {stats.failed}")
    logger.info(f"Duration: {duration_str}")
    
    if stats.completed > 0:
        avg_time = duration / stats.completed
        logger.info(f"Average Time per Label: {avg_time:.2f}s")
    
    logger.info("")
    
    if stats.failed_labels:
        logger.info("Failed Label IDs:")
        for label_id in stats.failed_labels[:20]:  # Show first 20
            logger.info(f"  - {label_id}")
        if len(stats.failed_labels) > 20:
            logger.info(f"  ... and {len(stats.failed_labels) - 20} more")
        logger.info("")
    
    logger.info("Check Supabase for detailed results:")
    logger.info(f"  SELECT * FROM timeline_labels WHERE transcription_processed = true")
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