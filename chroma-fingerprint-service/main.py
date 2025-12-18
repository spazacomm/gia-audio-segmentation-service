import os
import json
import uuid
import logging
from typing import Optional

from fastapi import FastAPI, BackgroundTasks, HTTPException
from pydantic import BaseModel
import requests
from pydub import AudioSegment
import acoustid
import chromaprint
from google.cloud import storage

# --- CONFIGURATION ---
# Bucket containing the raw/long audio files
SOURCE_BUCKET_NAME = os.getenv("SOURCE_BUCKET", "spaza-recordings")

# Bucket to store snippets and the index
LIBRARY_BUCKET_NAME = os.getenv("LIBRARY_BUCKET", "audio-library")
INDEX_FILE = "fingerprints.json"

SIMILARITY_THRESHOLD = 0.85  # 85% match required
WEBHOOK_URL = os.getenv("WEBHOOK_URL", "https://webhook.site/5f3323cb-a7d2-4c57-ae80-a10419e0b03d")

# Setup Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# --- DATA MODELS ---
class AudioRequest(BaseModel):
    recording_filepath: str     # Path inside spaza-recordings (e.g., "raw/session1.mp3")
    timeline_label_id: str
    start_offset_seconds: float
    end_offset_seconds: float
    webhook_url: Optional[str] = None

# --- HELPER FUNCTIONS ---

def calculate_similarity(fp1_ints, fp2_ints):
    """Calculates Hamming distance similarity (0.0 to 1.0)."""
    if not fp1_ints or not fp2_ints: return 0.0
    length = min(len(fp1_ints), len(fp2_ints))
    if length == 0: return 0.0
    
    bit_error_count = 0
    total_bits = length * 32
    for i in range(length):
        xor_val = fp1_ints[i] ^ fp2_ints[i]
        bit_error_count += bin(xor_val).count('1')
    return 1.0 - (bit_error_count / total_bits)

def process_gcs_task(req: AudioRequest):
    """
    1. Download from Source Bucket.
    2. Trim.
    3. Fingerprint.
    4. Check Library Bucket for match.
    5. Upload/Update Library if needed.
    6. Webhook result.
    """
    temp_source_path = None
    temp_snippet_path = None
    final_webhook = req.webhook_url if req.webhook_url else WEBHOOK_URL
    
    try:
        storage_client = storage.Client()
        source_bucket = storage_client.bucket(SOURCE_BUCKET_NAME)
        library_bucket = storage_client.bucket(LIBRARY_BUCKET_NAME)

        # --- STEP 1: Download Source File ---
        logger.info(f"Downloading {req.recording_filepath} from {SOURCE_BUCKET_NAME}...")
        
        blob_source = source_bucket.blob(req.recording_filepath)
        if not blob_source.exists():
            logger.error(f"File not found: {req.recording_filepath}")
            # Notify webhook of failure (omitted for brevity)
            return

        # Create temp file for download
        temp_source_path = f"/tmp/{uuid.uuid4().hex}_{os.path.basename(req.recording_filepath)}"
        blob_source.download_to_filename(temp_source_path)

        # --- STEP 2: Trim Audio ---
        logger.info(f"Trimming audio ({req.start_offset_seconds}s to {req.end_offset_seconds}s)...")
        
        try:
            audio = AudioSegment.from_file(temp_source_path)
            # PyDub uses milliseconds
            start_ms = req.start_offset_seconds * 1000
            end_ms = req.end_offset_seconds * 1000
            
            # Handle out of bounds
            if start_ms > len(audio):
                raise ValueError("Start time is beyond audio duration")
                
            snippet = audio[start_ms:end_ms]
            
            temp_snippet_path = f"/tmp/snippet_{uuid.uuid4().hex}.mp3"
            snippet.export(temp_snippet_path, format="mp3")
        except Exception as e:
            logger.error(f"Error processing audio: {e}")
            return

        # --- STEP 3: Generate Fingerprint ---
        duration, fp_encoded = acoustid.fingerprint_file(temp_snippet_path)
        fp_ints, _ = chromaprint.decode_fingerprint(fp_encoded)

        # --- STEP 4: Check Index ---
        blob_index = library_bucket.blob(INDEX_FILE)
        index_data = {}
        
        if blob_index.exists():
            try:
                index_data = json.loads(blob_index.download_as_text())
            except:
                logger.warning("Index file corrupted or empty, starting fresh.")

        best_match_id = None
        best_score = 0.0

        for audio_id, data in index_data.items():
            stored_fp_encoded = data.get("fingerprint")
            # Decode stored string back to bytes if needed
            if isinstance(stored_fp_encoded, str):
                stored_fp_encoded = stored_fp_encoded.encode()
                
            stored_fp_ints, _ = chromaprint.decode_fingerprint(stored_fp_encoded)
            score = calculate_similarity(fp_ints, stored_fp_ints)
            
            if score > best_score:
                best_score = score
                best_match_id = audio_id

        # --- STEP 5: Prepare Result ---
        result_payload = {
            "timeline_label_id": req.timeline_label_id,
            "recording_filepath": req.recording_filepath,
            "start_offset_seconds": req.start_offset_seconds,
            "end_offset_seconds": req.end_offset_seconds,
            "status": "processed"
        }

        target_blob_name = ""
        
        if best_score >= SIMILARITY_THRESHOLD:
            # MATCH FOUND
            logger.info(f"✅ Match found: {best_match_id} (Score: {best_score:.2f})")
            target_blob_name = best_match_id
            
            result_payload["match_found"] = True
            result_payload["fingerprint_hash"] = best_match_id
            result_payload["similarity_score"] = best_score
        else:
            # NO MATCH - UPLOAD
            logger.info(f"❌ No match (Score: {best_score:.2f}). Adding to library.")
            
            # Use a unique hash/uuid for the library filename
            target_blob_name = f"{uuid.uuid4().hex}.mp3"
            
            blob_new_snippet = library_bucket.blob(target_blob_name)
            blob_new_snippet.upload_from_filename(temp_snippet_path)
            
            # Update Index
            fp_str = fp_encoded.decode('utf-8') if isinstance(fp_encoded, bytes) else fp_encoded
            index_data[target_blob_name] = {
                "fingerprint": fp_str,
                "duration": duration,
                "original_source": req.recording_filepath
            }
            blob_index.upload_from_string(json.dumps(index_data))

            result_payload["match_found"] = False
            result_payload["fingerprint_hash"] = target_blob_name

        # Generate Signed URL (valid for 1 hour)
        target_blob = library_bucket.blob(target_blob_name)
        snippet_url = target_blob.generate_signed_url(version="v4", expiration=3600, method="GET")
        result_payload["snippet_url"] = snippet_url

        # --- STEP 6: Send Webhook ---
        logger.info(f"Sending webhook to {final_webhook}")
        try:
            requests.post(final_webhook, json=result_payload, timeout=10)
        except Exception as e:
            logger.error(f"Webhook failed: {e}")

    except Exception as e:
        logger.error(f"Process failed: {e}")
    finally:
        # Cleanup temp files
        if temp_source_path and os.path.exists(temp_source_path):
            os.remove(temp_source_path)
        if temp_snippet_path and os.path.exists(temp_snippet_path):
            os.remove(temp_snippet_path)

# --- API ENDPOINT ---
@app.post("/fingerprint")
async def fingerprint_endpoint(req: AudioRequest, background_tasks: BackgroundTasks):
    """
    Input: Filepath in 'spaza-recordings', offsets, label ID.
    Output: JSON confirming task start. Actual processing happens in background.
    """
    background_tasks.add_task(process_gcs_task, req)
    return {
        "message": "Processing started", 
        "timeline_label_id": req.timeline_label_id,
        "source_bucket": SOURCE_BUCKET_NAME,
        "library_bucket": LIBRARY_BUCKET_NAME
    }

@app.get("/health")
def health():
    return {"status": "ok"}