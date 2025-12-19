import os
import sys
import json
import uuid
import time
import tempfile
import logging
from typing import Dict, List, Optional, Tuple
from contextlib import contextmanager

from pydub import AudioSegment
import acoustid
import chromaprint
from google.cloud import storage
from supabase import create_client, Client

# -------------------------------------------------
# Cloud Run Job metadata
# -------------------------------------------------
TASK_INDEX = int(os.getenv("CLOUD_RUN_TASK_INDEX", "0"))
TASK_ATTEMPT = int(os.getenv("CLOUD_RUN_TASK_ATTEMPT", "0"))

# -------------------------------------------------
# Config
# -------------------------------------------------
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "5"))
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.85"))
LIBRARY_BUCKET = os.getenv("LIBRARY_BUCKET", "spaza-audio-library")
MAX_LIBRARY_FETCH = int(os.getenv("MAX_LIBRARY_FETCH", "1000"))
MAX_RETRIES = 3
RETRY_DELAY = 2  # seconds

# -------------------------------------------------
# Logging (Cloud Run friendly)
# -------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | audio-job | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# -------------------------------------------------
# Retry decorator
# -------------------------------------------------
def retry_on_failure(max_attempts=MAX_RETRIES, delay=RETRY_DELAY):
    def decorator(func):
        def wrapper(*args, **kwargs):
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts - 1:
                        raise
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {str(e)}. "
                        f"Retrying in {delay}s..."
                    )
                    time.sleep(delay * (attempt + 1))
            return None
        return wrapper
    return decorator


# -------------------------------------------------
# Worker
# -------------------------------------------------
class AudioFingerprintJob:
    def __init__(self):
        self.supabase: Client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
        self.storage = storage.Client()
        self.library_bucket = self.storage.bucket(LIBRARY_BUCKET)
        self.processed_count = 0
        self.failed_count = 0

    # ---------------------------------------------
    # Entry point
    # ---------------------------------------------
    def run(self):
        logger.info(
            json.dumps({
                "message": "Starting Cloud Run Job",
                "task_index": TASK_INDEX,
                "attempt": TASK_ATTEMPT,
                "severity": "INFO"
            })
        )

        labels = self.fetch_pending_labels()

        if not labels:
            logger.info(
                json.dumps({
                    "message": "No pending labels found",
                    "severity": "INFO"
                })
            )
            return

        logger.info(
            json.dumps({
                "message": f"Processing {len(labels)} labels",
                "count": len(labels),
                "severity": "INFO"
            })
        )

        for label in labels:
            try:
                self.process_label(label)
                self.processed_count += 1
            except Exception as e:
                self.failed_count += 1
                logger.error(
                    json.dumps({
                        "message": "Failed to process label",
                        "label_id": label.get("id"),
                        "error": str(e),
                        "severity": "ERROR"
                    })
                )
                # Mark as failed but continue processing others
                try:
                    self.mark_label_failed(label["id"], str(e))
                except Exception as mark_error:
                    logger.error(
                        json.dumps({
                            "message": "Failed to mark label as failed",
                            "label_id": label.get("id"),
                            "error": str(mark_error),
                            "severity": "ERROR"
                        })
                    )

        logger.info(
            json.dumps({
                "message": "Job completed",
                "processed": self.processed_count,
                "failed": self.failed_count,
                "severity": "INFO"
            })
        )

    # ---------------------------------------------
    # Fetch work with locking
    # ---------------------------------------------
    @retry_on_failure()
    def fetch_pending_labels(self) -> List[Dict]:
        # First, atomically claim labels by updating their status
        claim_response = self.supabase.rpc(
            'claim_pending_labels',
            {
                'batch_size': BATCH_SIZE,
                'task_id': f"{TASK_INDEX}-{TASK_ATTEMPT}-{uuid.uuid4().hex[:8]}"
            }
        ).execute()
        
        # If the RPC function doesn't exist, fall back to direct query
        # with a processing flag
        if not claim_response.data:
            # Alternative approach: use update + select
            response = (
                self.supabase
                .table("timeline_labels")
                .select(
                    """
                    id,
                    start_offset_seconds,
                    end_offset_seconds,
                    timeline_id,
                    broadcast_timeline (
                        recording_url
                    )
                    """
                )
                .is_("media_url", None)
                .eq("fingerprint_processed", False)
                .is_("processing_task_id", None)
                .limit(BATCH_SIZE)
                .execute()
            )
            
            labels = response.data or []
            
            # Mark them as being processed
            if labels:
                label_ids = [label["id"] for label in labels]
                task_id = f"{TASK_INDEX}-{TASK_ATTEMPT}-{uuid.uuid4().hex[:8]}"
                self.supabase.table("timeline_labels").update({
                    "processing_task_id": task_id,
                    "processing_started_at": "now()"
                }).in_("id", label_ids).execute()
            
            return labels
        
        return claim_response.data or []

    # ---------------------------------------------
    # Process one label
    # ---------------------------------------------
    def process_label(self, label: Dict):
        label_id = label["id"]
        
        # Validate nested data exists
        if not label.get("broadcast_timeline"):
            raise ValueError("Missing broadcast_timeline relation")
        
        recording_url = label["broadcast_timeline"].get("recording_url")
        if not recording_url:
            raise ValueError("Missing recording_url")
        
        # Validate time range
        start = float(label["start_offset_seconds"])
        end = float(label["end_offset_seconds"])
        
        if start >= end:
            raise ValueError(f"Invalid time range: start={start} >= end={end}")
        
        if start < 0:
            raise ValueError(f"Invalid start time: {start} < 0")

        logger.info(
            json.dumps({
                "message": "Processing label",
                "label_id": label_id,
                "start": start,
                "end": end,
                "severity": "INFO"
            })
        )

        with tempfile.TemporaryDirectory() as tmp:
            try:
                source_path = f"{tmp}/source"
                snippet_path = f"{tmp}/snippet.mp3"

                self.download_recording(recording_url, source_path)
                
                # Validate file was downloaded
                if not os.path.exists(source_path) or os.path.getsize(source_path) == 0:
                    raise ValueError("Downloaded file is empty or missing")
                
                self.trim_audio(source_path, snippet_path, start, end)
                
                # Validate snippet was created
                if not os.path.exists(snippet_path) or os.path.getsize(snippet_path) == 0:
                    raise ValueError("Trimmed audio snippet is empty or missing")

                duration, fp_encoded = acoustid.fingerprint_file(snippet_path)
                
                # Ensure consistent encoding
                if isinstance(fp_encoded, bytes):
                    fp_encoded = fp_encoded.decode('utf-8')
                
                fp_ints, _ = chromaprint.decode_fingerprint(fp_encoded)

                match = self.find_match(fp_ints)

                if match:
                    audio_id, score, media_url = match
                    logger.info(
                        json.dumps({
                            "message": "Found matching audio",
                            "label_id": label_id,
                            "audio_id": audio_id,
                            "score": score,
                            "severity": "INFO"
                        })
                    )
                else:
                    audio_id, media_url = self.insert_audio(
                        fp_encoded, duration, snippet_path
                    )
                    score = None
                    logger.info(
                        json.dumps({
                            "message": "Inserted new audio",
                            "label_id": label_id,
                            "audio_id": audio_id,
                            "severity": "INFO"
                        })
                    )

                self.update_label(label_id, audio_id, media_url, score)
                
            except Exception as e:
                # Clean up is handled by TemporaryDirectory context manager
                raise

    # ---------------------------------------------
    # Audio helpers
    # ---------------------------------------------
    @retry_on_failure()
    def download_recording(self, gcs_url: str, target_path: str):
        # Check if GCS FUSE is available (volumes mounted)
        use_gcs_fuse = os.path.exists("/spaza-recordings")
        
        if use_gcs_fuse:
            # Use mounted volume (faster)
            bucket_name, path = self.parse_gcs_url(gcs_url)
            
            # Map bucket to mount point
            mount_map = {
                "spaza-recordings": "/spaza-recordings",
                "spaza-audio-library": "/spaza-audio-library"
            }
            
            if bucket_name not in mount_map:
                raise ValueError(f"Bucket {bucket_name} is not mounted")
            
            source_path = os.path.join(mount_map[bucket_name], path)
            
            logger.info(
                json.dumps({
                    "message": "Copying from mounted volume",
                    "source": source_path,
                    "severity": "INFO"
                })
            )
            
            if not os.path.exists(source_path):
                raise ValueError(f"File does not exist: {source_path}")
            
            # Copy file
            import shutil
            shutil.copy2(source_path, target_path)
        else:
            # Use Storage Client API
            bucket_name, path = self.parse_gcs_url(gcs_url)
            logger.info(
                json.dumps({
                    "message": "Downloading recording via Storage API",
                    "bucket": bucket_name,
                    "path": path,
                    "severity": "INFO"
                })
            )
            bucket = self.storage.bucket(bucket_name)
            blob = bucket.blob(path)
            
            if not blob.exists():
                raise ValueError(f"Blob does not exist: gs://{bucket_name}/{path}")
            
            blob.download_to_filename(target_path)

    def trim_audio(self, source: str, target: str, start: float, end: float):
        try:
            audio = AudioSegment.from_file(source)
            audio_duration_sec = len(audio) / 1000.0
            
            # Validate and clamp end time
            if end > audio_duration_sec:
                logger.warning(
                    json.dumps({
                        "message": "End time exceeds audio duration, clamping",
                        "requested_end": end,
                        "audio_duration": audio_duration_sec,
                        "severity": "WARNING"
                    })
                )
                end = audio_duration_sec
            
            start_ms = int(start * 1000)
            end_ms = int(end * 1000)
            
            snippet = audio[start_ms:end_ms]
            
            if len(snippet) == 0:
                raise ValueError("Trimmed audio snippet is empty")
            
            snippet.export(target, format="mp3")
            
        except Exception as e:
            raise ValueError(f"Failed to trim audio: {str(e)}")

    # ---------------------------------------------
    # Fingerprint matching (paginated)
    # ---------------------------------------------
    @retry_on_failure()
    def find_match(self, fp_ints: List[int]) -> Optional[Tuple[int, float, str]]:
        best = (None, 0.0, None)
        offset = 0
        page_size = 100
        total_checked = 0
        
        while total_checked < MAX_LIBRARY_FETCH:
            rows = (
                self.supabase
                .table("audio_library")
                .select("id,fingerprint_hash,media_url")
                .range(offset, offset + page_size - 1)
                .execute()
                .data
            )
            
            if not rows:
                break
            
            for row in rows:
                try:
                    stored_fp_encoded = row["fingerprint_hash"]
                    
                    # Ensure proper encoding
                    if isinstance(stored_fp_encoded, str):
                        stored_fp_encoded = stored_fp_encoded.encode('utf-8')
                    
                    stored_fp, _ = chromaprint.decode_fingerprint(stored_fp_encoded)
                    score = self.similarity(fp_ints, stored_fp)

                    if score > best[1]:
                        best = (row["id"], score, row["media_url"])
                        
                        # Early exit if we found a very high confidence match
                        if score >= 0.95:
                            logger.info(
                                json.dumps({
                                    "message": "High confidence match found, stopping search",
                                    "audio_id": row["id"],
                                    "score": score,
                                    "severity": "INFO"
                                })
                            )
                            return best
                            
                except Exception as e:
                    logger.warning(
                        json.dumps({
                            "message": "Failed to decode fingerprint",
                            "audio_id": row.get("id"),
                            "error": str(e),
                            "severity": "WARNING"
                        })
                    )
                    continue
            
            total_checked += len(rows)
            offset += page_size
            
            # If we got fewer rows than page_size, we've reached the end
            if len(rows) < page_size:
                break

        if best[1] >= SIMILARITY_THRESHOLD:
            return best

        return None

    # ---------------------------------------------
    # DB writes
    # ---------------------------------------------
    @retry_on_failure()
    def insert_audio(
        self, 
        fp_encoded: str, 
        duration: float, 
        snippet_path: str
    ) -> Tuple[int, str]:
        # Check if GCS FUSE is available
        use_gcs_fuse = os.path.exists("/spaza-audio-library")
        
        blob_name = f"{uuid.uuid4().hex}.mp3"
        
        if use_gcs_fuse:
            # Use mounted volume
            dest_path = f"/spaza-audio-library/{blob_name}"
            
            logger.info(
                json.dumps({
                    "message": "Copying to mounted volume",
                    "destination": dest_path,
                    "severity": "INFO"
                })
            )
            
            import shutil
            shutil.copy2(snippet_path, dest_path)
            
            # Construct public URL
            media_url = f"https://storage.googleapis.com/{LIBRARY_BUCKET}/{blob_name}"
        else:
            # Use Storage Client API
            blob = self.library_bucket.blob(blob_name)
            blob.upload_from_filename(snippet_path)
            media_url = blob.public_url

        result = (
            self.supabase
            .table("audio_library")
            .insert({
                "fingerprint_hash": fp_encoded,
                "audio_type": "unknown",
                "duration_seconds": duration,
                "media_url": media_url,
            })
            .execute()
        )

        return result.data[0]["id"], media_url

    @retry_on_failure()
    def update_label(
        self,
        label_id: int,
        audio_id: int,
        media_url: str,
        score: Optional[float],
    ):
        self.supabase.table("timeline_labels").update({
            "fingerprint_id": audio_id,
            "media_url": media_url,
            "fingerprint_matched": score is not None,
            "fingerprint_confidence": score,
            "fingerprint_processed": True,
            "processing_task_id": None,
            "processing_completed_at": "now()"
        }).eq("id", label_id).execute()

        logger.info(
            json.dumps({
                "message": "Updated label",
                "label_id": label_id,
                "audio_id": audio_id,
                "matched": score is not None,
                "severity": "INFO"
            })
        )

    @retry_on_failure()
    def mark_label_failed(self, label_id: int, error: str):
        """Mark a label as failed to prevent infinite retries"""
        self.supabase.table("timeline_labels").update({
            "fingerprint_processed": True,
            "processing_task_id": None,
            "processing_error": error,
            "processing_completed_at": "now()"
        }).eq("id", label_id).execute()

    # ---------------------------------------------
    # Utils
    # ---------------------------------------------
    def similarity(self, fp1: List[int], fp2: List[int]) -> float:
        """Calculate similarity between two fingerprints (0.0 to 1.0)"""
        length = min(len(fp1), len(fp2))
        if length == 0:
            return 0.0

        errors = 0
        for i in range(length):
            errors += bin(fp1[i] ^ fp2[i]).count("1")

        return 1.0 - (errors / (length * 32))

    def parse_gcs_url(self, url: str) -> Tuple[str, str]:
        """Parse GCS URL and return (bucket_name, blob_path)"""
        if url.startswith("gs://"):
            _, rest = url.split("gs://", 1)
            parts = rest.split("/", 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid GCS URL format: {url}")
            return parts[0], parts[1]

        if url.startswith("https://storage.googleapis.com/"):
            rest = url.replace("https://storage.googleapis.com/", "")
            parts = rest.split("/", 1)
            if len(parts) != 2:
                raise ValueError(f"Invalid GCS URL format: {url}")
            return parts[0], parts[1]

        raise ValueError(f"Unsupported GCS URL format: {url}")


# -------------------------------------------------
# Job entrypoint
# -------------------------------------------------
if __name__ == "__main__":
    try:
        job = AudioFingerprintJob()
        job.run()
    except Exception as err:
        message = (
            f"Task #{TASK_INDEX}, Attempt #{TASK_ATTEMPT} failed: {str(err)}"
        )
        print(json.dumps({"message": message, "severity": "ERROR"}))
        sys.exit(1)  # Causes Cloud Run Job retry