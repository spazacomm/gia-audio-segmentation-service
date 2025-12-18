import os
import logging

from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel
from pydantic_settings import BaseSettings

from inaSpeechSegmenter import Segmenter
from google.cloud import storage

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Configuration ---
class Settings(BaseSettings):
    GCP_PROJECT_ID: str = "spaza-media-monitor"
    TEMP_DIR: str = "/tmp/audio_processing"

    class Config:
        env_file = ".env"

settings = Settings()

# --- Initialize clients ---
storage_client = storage.Client(project=settings.GCP_PROJECT_ID)
segmenter = Segmenter()
logger.info("Initialized GCS client and segmenter")

# --- FastAPI App ---
app = FastAPI(title="InaSpeech Segmentation Service", version="1.0.0")

# --- Request/Response Models ---
class ProcessRequest(BaseModel):
    processing_url: str  # GCS URL (e.g., gs://bucket/file.mp3)

class SegmentResult(BaseModel):
    label: str
    start_time: float
    end_time: float

class ProcessResponse(BaseModel):
    processing_url: str
    status: str
    segments: list[SegmentResult]
    total_segments: int

# --- Processing Logic ---
def process_audio(processing_url: str) -> list[SegmentResult]:
    """Download audio from GCS, segment it, and return results"""
    logger.info(f"Processing: {processing_url}")
    
    local_path = None
    
    try:
        # Parse GCS URL
        bucket_name, blob_name = processing_url.replace("gs://", "").split("/", 1)
        bucket = storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_name)
        
        # Download file
        os.makedirs(settings.TEMP_DIR, exist_ok=True)
        local_path = os.path.join(settings.TEMP_DIR, os.path.basename(blob_name))
        logger.info(f"Downloading to {local_path}")
        blob.download_to_filename(local_path)
        
        # Run segmentation
        logger.info("Running segmentation...")
        segments = segmenter(local_path)
        
        # Format results
        results = [
            SegmentResult(
                label=seg[0],  # speech, music, noise, male, female
                start_time=float(seg[1]),
                end_time=float(seg[2])
            )
            for seg in segments
        ]
        
        logger.info(f"Segmentation complete. Found {len(results)} segments")
        return results
        
    except Exception as e:
        logger.error(f"Processing failed: {e}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Processing failed: {str(e)}"
        )
    
    finally:
        # Cleanup
        if local_path and os.path.exists(local_path):
            os.remove(local_path)
            logger.info(f"Cleaned up: {local_path}")

# --- API Endpoint ---
@app.post("/process", response_model=ProcessResponse)
async def process_audio_endpoint(request: ProcessRequest):
    """
    Segment audio file and return results synchronously.
    This is a blocking call that returns segments when complete.
    """
    logger.info(f"Received request: {request.processing_url}")
    
    # Process and return results
    segments = process_audio(request.processing_url)
    
    return ProcessResponse(
        processing_url=request.processing_url,
        status="success",
        segments=segments,
        total_segments=len(segments)
    )

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return {"status": "healthy", "service": "inaspeech-segmentation"}

# --- Startup ---
@app.on_event("startup")
async def startup():
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    logger.info(f"Temp directory ready: {settings.TEMP_DIR}")
    logger.info("InaSpeech Segmentation Service ready")