import os
import logging
import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional

import httpx
from fastapi import FastAPI, HTTPException, status, Depends, Header
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

    WEBHOOK_URL: Optional[str] = None  # e.g., https://example.com/webhook
    WEBHOOK_TOKEN: Optional[str] = None  # e.g., https://example.com/webhook

    API_KEY: str = "supersecretapikey"  # API key for securing this service

    class Config:
        env_file = ".env"

settings = Settings()

# --- Initialize clients ---
storage_client = storage.Client(project=settings.GCP_PROJECT_ID)
segmenter = Segmenter()
logger.info("Initialized GCS client and segmenter")

# --- FastAPI App ---
app = FastAPI(title="InaSpeech Segmentation Service", version="1.1.0")

executor = ThreadPoolExecutor(max_workers=2)  # For CPU-bound processing

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
    segments: List[SegmentResult]
    total_segments: int

# --- Security Dependency ---
async def api_key_auth(x_api_key: str = Header(...)):
    if x_api_key != settings.API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

# --- Processing Logic ---
def process_audio_sync(processing_url: str) -> List[SegmentResult]:
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
        
        results = [
            SegmentResult(label=seg[0], start_time=float(seg[1]), end_time=float(seg[2]))
            for seg in segments
        ]
        logger.info(f"Segmentation complete. Found {len(results)} segments")
        return results
        
    except Exception as e:
        logger.error(f"Processing failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Processing failed: {str(e)}")
    
    finally:
        if local_path and os.path.exists(local_path):
            os.remove(local_path)
            logger.info(f"Cleaned up: {local_path}")

async def process_audio_async(processing_url: str) -> List[SegmentResult]:
    """Run the CPU-bound process in a thread to avoid blocking"""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, process_audio_sync, processing_url)

async def send_webhook(response_data: dict):
    """Send results to configured webhook with optional authentication"""
    if not settings.WEBHOOK_URL:
        logger.warning("No webhook URL configured, skipping webhook")
        return

    headers = {}
    if settings.WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {settings.WEBHOOK_TOKEN}"

    async with httpx.AsyncClient(timeout=30) as client:
        try:
            res = await client.post(settings.WEBHOOK_URL, json=response_data, headers=headers)
            res.raise_for_status()
            logger.info(f"Webhook sent successfully: {res.status_code}")
        except Exception as e:
            logger.error(f"Failed to send webhook: {e}")

# --- API Endpoint ---
@app.post("/process", response_model=ProcessResponse, dependencies=[Depends(api_key_auth)])
async def process_audio_endpoint(request: ProcessRequest):
    logger.info(f"Received request: {request.processing_url}")
    segments = await process_audio_async(request.processing_url)
    
    response_data = ProcessResponse(
        processing_url=request.processing_url,
        status="success",
        segments=segments,
        total_segments=len(segments)
    )

    # Send asynchronously to webhook (don't block response)
    asyncio.create_task(send_webhook(response_data.dict()))

    return response_data

# --- Health Check ---
@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "inaspeech-segmentation"}

# --- Startup ---
@app.on_event("startup")
async def startup():
    os.makedirs(settings.TEMP_DIR, exist_ok=True)
    logger.info(f"Temp directory ready: {settings.TEMP_DIR}")
    logger.info("InaSpeech Segmentation Service ready")
