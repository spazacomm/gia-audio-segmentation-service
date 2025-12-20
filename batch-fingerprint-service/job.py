import os
import sys
import json
import uuid
import tempfile
import logging
from typing import Dict, List, Optional, Tuple

from pydub import AudioSegment
import acoustid
import chromaprint
from google.cloud import storage
from supabase import create_client, Client

# ============================================================
# CONFIGURATION
# ============================================================

# Cloud Run Job metadata
TASK_INDEX = int(os.getenv("CLOUD_RUN_TASK_INDEX", "0"))
TASK_ATTEMPT = int(os.getenv("CLOUD_RUN_TASK_ATTEMPT", "0"))
TASK_COUNT = int(os.getenv("CLOUD_RUN_TASK_COUNT", "2"))

# Processing configuration
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))
LABELS_TO_FINGERPRINT = os.getenv("LABELS_TO_FINGERPRINT", "music").split(",")
SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.85"))
SNIPPET_PADDING_SECONDS = float(os.getenv("SNIPPET_PADDING_SECONDS", "2.0"))

# Storage configuration
LIBRARY_BUCKET = os.getenv("LIBRARY_BUCKET", "spaza-audio-library")
SOURCE_BUCKET = os.getenv("SOURCE_BUCKET", "spaza-recordings")

# Matching configuration
MAX_LIBRARY_SEARCH = int(os.getenv("MAX_LIBRARY_SEARCH", "500"))
HIGH_CONFIDENCE_THRESHOLD = 0.95

# Supabase
SUPABASE_URL = os.environ["SUPABASE_URL"]
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | fingerprint-job | %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger(__name__)


# ============================================================
# AUDIO FINGERPRINTING JOB
# ============================================================

class AudioFingerprintJob:
    def __init__(self):
        self.supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)
        self.storage_client = storage.Client()
        self.library_bucket = self.storage_client.bucket(LIBRARY_BUCKET)
        
        self.processed_count = 0
        self.matched_count = 0
        self.new_audio_count = 0
        self.failed_count = 0
        self.task_id = f"task-{TASK_INDEX}-{TASK_ATTEMPT}-{uuid.uuid4().hex[:8]}"
        
        logger.info(
            json.dumps({
                "message": "Job initialized",
                "task_index": TASK_INDEX,
                "task_count": TASK_COUNT,
                "task_attempt": TASK_ATTEMPT,
                "task_id": self.task_id,
                "batch_size": BATCH_SIZE,
                "labels_to_fingerprint": LABELS_TO_FINGERPRINT,
                "severity": "INFO"
            })
        )

    # --------------------------------------------------------
    # MAIN EXECUTION
    # --------------------------------------------------------
    
    def run(self):
        """Main job execution"""
        logger.info(
            json.dumps({
                "message": "Starting fingerprinting job",
                "task_id": self.task_id,
                "severity": "INFO"
            })
        )
        
        labels = self.fetch_pending_labels()
        
        if not labels:
            logger.info(
                json.dumps({
                    "message": "No pending labels found",
                    "task_id": self.task_id,
                    "severity": "INFO"
                })
            )
            return
        
        logger.info(
            json.dumps({
                "message": f"Processing {len(labels)} labels",
                "count": len(labels),
                "task_id": self.task_id,
                "severity": "INFO"
            })
        )
        
        for idx, label in enumerate(labels, 1):
            try:
                self.process_label(label)
                self.processed_count += 1
                
                # Progress logging
                if idx % 5 == 0 or idx == len(labels):
                    logger.info(
                        json.dumps({
                            "message": "Progress update",
                            "processed": idx,
                            "total": len(labels),
                            "percentage": round((idx / len(labels)) * 100, 1),
                            "severity": "INFO"
                        })
                    )
                    
            except ValueError as e:
                # Validation errors - don't retry
                self.failed_count += 1
                logger.error(
                    json.dumps({
                        "message": "Validation error",
                        "label_id": label.get("id"),
                        "error": str(e),
                        "severity": "ERROR"
                    })
                )
                self.mark_label_failed(label["id"], f"Validation: {str(e)}")
                
            except (IOError, OSError) as e:
                # IO errors - might be transient, let Cloud Run retry
                self.failed_count += 1
                logger.error(
                    json.dumps({
                        "message": "IO error - will retry",
                        "label_id": label.get("id"),
                        "error": str(e),
                        "severity": "ERROR"
                    })
                )
                raise  # Let Cloud Run retry the entire task
                
            except Exception as e:
                # Unexpected errors - mark as failed
                self.failed_count += 1
                logger.error(
                    json.dumps({
                        "message": "Unexpected error",
                        "label_id": label.get("id"),
                        "error": str(e),
                        "severity": "ERROR"
                    }),
                    exc_info=True
                )
                self.mark_label_failed(label["id"], f"Error: {str(e)}")
        
        self.print_summary()

    # --------------------------------------------------------
    # FETCH WORK
    # --------------------------------------------------------
    
    def fetch_pending_labels(self) -> List[Dict]:
        """
        Fetch pending labels for this task using task-based distribution.
        Each parallel task gets a different subset based on TASK_INDEX.
        """
        try:
            # Calculate offset for this task
            offset = TASK_INDEX * BATCH_SIZE
            limit = BATCH_SIZE
            
            # Fetch labels to process
            response = self.supabase.table("timeline_labels") \
                .select("""
                    id,
                    label,
                    start_offset_seconds,
                    end_offset_seconds,
                    timeline_id,
                    broadcast_timeline!inner (
                        recording_url
                    )
                """) \
                .in_("label", LABELS_TO_FINGERPRINT) \
                .eq("fingerprint_processed", False) \
                .is_("processing_task_id", None) \
                .order("id") \
                .range(offset, offset + limit - 1) \
                .execute()
            
            labels = response.data or []
            
            if not labels:
                return []
            
            # Claim these labels by updating processing_task_id
            label_ids = [label["id"] for label in labels]
            
            self.supabase.table("timeline_labels").update({
                "processing_task_id": self.task_id,
                "processing_started_at": "now()"
            }).in_("id", label_ids).execute()
            
            logger.info(
                json.dumps({
                    "message": "Claimed labels for processing",
                    "count": len(labels),
                    "label_ids": label_ids[:5],  # Log first 5
                    "severity": "INFO"
                })
            )
            
            return labels
            
        except Exception as e:
            logger.error(
                json.dumps({
                    "message": "Failed to fetch pending labels",
                    "error": str(e),
                    "severity": "ERROR"
                })
            )
            raise

    # --------------------------------------------------------
    # PROCESS SINGLE LABEL
    # --------------------------------------------------------
    
    def process_label(self, label: Dict):
        """Process a single timeline label"""
        label_id = label["id"]
        
        # Validate nested data
        if not label.get("broadcast_timeline"):
            raise ValueError("Missing broadcast_timeline relation")
        
        recording_url = label["broadcast_timeline"].get("recording_url")
        if not recording_url:
            raise ValueError("Missing recording_url")
        
        # Extract time offsets
        start = float(label["start_offset_seconds"])
        end = float(label["end_offset_seconds"])
        
        # Validate time range
        if start < 0:
            raise ValueError(f"Invalid start time: {start} < 0")
        
        if end <= start:
            raise ValueError(f"Invalid time range: start={start} >= end={end}")
        
        # Check for unreasonably long segments (> 10 minutes)
        if (end - start) > 600:
            raise ValueError(f"Segment too long: {end - start}s")
        
        logger.info(
            json.dumps({
                "message": "Processing label",
                "label_id": label_id,
                "label_type": label["label"],
                "start": start,
                "end": end,
                "duration": round(end - start, 2),
                "severity": "INFO"
            })
        )
        
        with tempfile.TemporaryDirectory() as tmp_dir:
            source_path = os.path.join(tmp_dir, "source")
            snippet_path = os.path.join(tmp_dir, "snippet.mp3")
            
            try:
                # Download original recording
                self.download_recording(recording_url, source_path)
                
                # Validate downloaded file
                if not os.path.exists(source_path) or os.path.getsize(source_path) == 0:
                    raise ValueError("Downloaded file is empty or missing")
                
                # Extract audio segment with padding
                self.extract_segment(source_path, snippet_path, start, end)
                
                # Validate snippet
                if not os.path.exists(snippet_path) or os.path.getsize(snippet_path) == 0:
                    raise ValueError("Audio snippet is empty or missing")
                
                # Generate fingerprint
                duration, fp_encoded = acoustid.fingerprint_file(snippet_path)
                
                # Ensure proper encoding
                if isinstance(fp_encoded, bytes):
                    fp_encoded = fp_encoded.decode('utf-8')
                
                fp_ints, _ = chromaprint.decode_fingerprint(fp_encoded)
                
                # Try to find existing match
                match = self.find_match(fp_encoded, fp_ints)
                
                if match:
                    audio_id, score, media_url = match
                    self.matched_count += 1
                    
                    logger.info(
                        json.dumps({
                            "message": "Found matching audio",
                            "label_id": label_id,
                            "audio_id": audio_id,
                            "similarity": round(score, 4),
                            "severity": "INFO"
                        })
                    )
                else:
                    # Create new audio library entry
                    audio_id, media_url = self.insert_audio(
                        fp_encoded, 
                        duration, 
                        snippet_path
                    )
                    score = None
                    self.new_audio_count += 1
                    
                    logger.info(
                        json.dumps({
                            "message": "Created new audio library entry",
                            "label_id": label_id,
                            "audio_id": audio_id,
                            "severity": "INFO"
                        })
                    )
                
                # Update the label
                self.update_label(label_id, audio_id, media_url, score)
                
            except Exception as e:
                # Clean up is handled by TemporaryDirectory context manager
                raise

    # --------------------------------------------------------
    # AUDIO OPERATIONS
    # --------------------------------------------------------
    
    def download_recording(self, gcs_url: str, target_path: str):
        """Download recording from GCS"""
        bucket_name, blob_path = self.parse_gcs_url(gcs_url)
        
        logger.debug(
            json.dumps({
                "message": "Downloading recording",
                "bucket": bucket_name,
                "path": blob_path,
                "severity": "DEBUG"
            })
        )
        
        bucket = self.storage_client.bucket(bucket_name)
        blob = bucket.blob(blob_path)
        
        if not blob.exists():
            raise ValueError(f"Blob does not exist: {gcs_url}")
        
        blob.download_to_filename(target_path)
    
    def extract_segment(
        self, 
        source_path: str, 
        target_path: str, 
        start: float, 
        end: float
    ):
        """
        Extract audio segment with padding for better fingerprinting.
        Adds SNIPPET_PADDING_SECONDS before and after the segment.
        """
        try:
            audio = AudioSegment.from_file(source_path)
            audio_duration_sec = len(audio) / 1000.0
            
            # Apply padding
            padded_start = max(0, start - SNIPPET_PADDING_SECONDS)
            padded_end = min(audio_duration_sec, end + SNIPPET_PADDING_SECONDS)
            
            # Clamp end time if it exceeds audio duration
            if padded_end > audio_duration_sec:
                logger.warning(
                    json.dumps({
                        "message": "End time exceeds audio duration",
                        "requested_end": padded_end,
                        "audio_duration": audio_duration_sec,
                        "clamped_end": audio_duration_sec,
                        "severity": "WARNING"
                    })
                )
                padded_end = audio_duration_sec
            
            # Convert to milliseconds
            start_ms = int(padded_start * 1000)
            end_ms = int(padded_end * 1000)
            
            # Extract segment
            snippet = audio[start_ms:end_ms]
            
            if len(snippet) == 0:
                raise ValueError("Extracted audio snippet is empty")
            
            # Export as MP3
            snippet.export(target_path, format="mp3", bitrate="128k")
            
            logger.debug(
                json.dumps({
                    "message": "Extracted audio segment",
                    "original_range": f"{start}-{end}",
                    "padded_range": f"{padded_start}-{padded_end}",
                    "duration": round(padded_end - padded_start, 2),
                    "severity": "DEBUG"
                })
            )
            
        except Exception as e:
            raise ValueError(f"Failed to extract audio segment: {str(e)}")

    # --------------------------------------------------------
    # FINGERPRINT MATCHING
    # --------------------------------------------------------
    
    def find_match(
        self, 
        fp_encoded: str, 
        fp_ints: List[int]
    ) -> Optional[Tuple[int, float, str]]:
        """
        Find matching fingerprint in audio library.
        First tries exact match, then similarity search on recent entries.
        """
        # Step 1: Try exact match (instant lookup)
        try:
            exact_match = self.supabase.table("audio_library") \
                .select("id, media_url") \
                .eq("fingerprint_hash", fp_encoded) \
                .limit(1) \
                .execute()
            
            if exact_match.data:
                logger.debug("Found exact fingerprint match")
                return (
                    exact_match.data[0]["id"], 
                    1.0, 
                    exact_match.data[0]["media_url"]
                )
        except Exception as e:
            logger.warning(f"Exact match query failed: {e}")
        
        # Step 2: Similarity search on recent entries
        # Most duplicates are recent (same songs/jingles recurring)
        try:
            rows = self.supabase.table("audio_library") \
                .select("id, fingerprint_hash, media_url") \
                .order("created_at", desc=True) \
                .limit(MAX_LIBRARY_SEARCH) \
                .execute() \
                .data
            
            if not rows:
                return None
            
            best = (None, 0.0, None)
            
            for row in rows:
                try:
                    stored_fp_encoded = row["fingerprint_hash"]
                    
                    # Ensure proper encoding
                    if isinstance(stored_fp_encoded, str):
                        stored_fp_encoded = stored_fp_encoded.encode('utf-8')
                    
                    stored_fp, _ = chromaprint.decode_fingerprint(stored_fp_encoded)
                    score = self.calculate_similarity(fp_ints, stored_fp)
                    
                    if score > best[1]:
                        best = (row["id"], score, row["media_url"])
                    
                    # Early exit on high confidence match
                    if score >= HIGH_CONFIDENCE_THRESHOLD:
                        logger.debug(
                            json.dumps({
                                "message": "High confidence match, stopping search",
                                "audio_id": row["id"],
                                "score": round(score, 4),
                                "severity": "DEBUG"
                            })
                        )
                        break
                        
                except Exception as e:
                    logger.debug(f"Failed to decode fingerprint for audio_id={row.get('id')}: {e}")
                    continue
            
            # Return best match if above threshold
            if best[1] >= SIMILARITY_THRESHOLD:
                return best
            
        except Exception as e:
            logger.warning(f"Similarity search failed: {e}")
        
        return None
    
    def calculate_similarity(self, fp1: List[int], fp2: List[int]) -> float:
        """
        Calculate similarity between two fingerprints using bit error rate.
        Returns value between 0.0 (no match) and 1.0 (perfect match).
        """
        length = min(len(fp1), len(fp2))
        if length == 0:
            return 0.0
        
        # Count bit differences
        bit_errors = 0
        for i in range(length):
            bit_errors += bin(fp1[i] ^ fp2[i]).count("1")
        
        # Calculate similarity (1.0 - error_rate)
        return 1.0 - (bit_errors / (length * 32))

    # --------------------------------------------------------
    # DATABASE OPERATIONS
    # --------------------------------------------------------
    
    def insert_audio(
        self, 
        fp_encoded: str, 
        duration: float, 
        snippet_path: str
    ) -> Tuple[int, str]:
        """Insert new audio snippet into library"""
        try:
            # Upload snippet to GCS
            blob_name = f"{uuid.uuid4().hex}.mp3"
            blob = self.library_bucket.blob(blob_name)
            blob.upload_from_filename(snippet_path)
            
            media_url = f"gs://{LIBRARY_BUCKET}/{blob_name}"
            
            logger.debug(
                json.dumps({
                    "message": "Uploaded snippet to library",
                    "blob_name": blob_name,
                    "severity": "DEBUG"
                })
            )
            
            # Insert into audio_library
            result = self.supabase.table("audio_library").insert({
                "fingerprint_hash": fp_encoded,
                "audio_type": "unknown",
                "duration_seconds": duration,
                "media_url": media_url,
            }).execute()
            
            audio_id = result.data[0]["id"]
            
            return audio_id, media_url
            
        except Exception as e:
            raise IOError(f"Failed to insert audio: {str(e)}")
    
    def update_label(
        self,
        label_id: int,
        audio_id: int,
        media_url: str,
        score: Optional[float]
    ):
        """Update timeline label with fingerprint results"""
        try:
            self.supabase.table("timeline_labels").update({
                "fingerprint_id": audio_id,
                "media_url": media_url,
                "fingerprint_matched": score is not None,
                "fingerprint_confidence": score,
                "fingerprint_processed": True,
                "processing_task_id": None,
                "processing_completed_at": "now()"
            }).eq("id", label_id).execute()
            
        except Exception as e:
            raise IOError(f"Failed to update label {label_id}: {str(e)}")
    
    def mark_label_failed(self, label_id: int, error: str):
        """Mark a label as failed"""
        try:
            self.supabase.table("timeline_labels").update({
                "fingerprint_processed": True,
                "processing_task_id": None,
                "processing_error": error[:500],  # Truncate long errors
                "processing_completed_at": "now()"
            }).eq("id", label_id).execute()
            
        except Exception as e:
            logger.error(
                json.dumps({
                    "message": "Failed to mark label as failed",
                    "label_id": label_id,
                    "error": str(e),
                    "severity": "ERROR"
                })
            )

    # --------------------------------------------------------
    # UTILITIES
    # --------------------------------------------------------
    
    def parse_gcs_url(self, url: str) -> Tuple[str, str]:
        """Parse GCS URL and return (bucket_name, blob_path)"""
        if url.startswith("gs://"):
            rest = url.replace("gs://", "")
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
    
    def print_summary(self):
        """Print job execution summary"""
        logger.info(
            json.dumps({
                "message": "Job completed",
                "task_id": self.task_id,
                "processed": self.processed_count,
                "matched": self.matched_count,
                "new_audio": self.new_audio_count,
                "failed": self.failed_count,
                "severity": "INFO"
            })
        )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    try:
        job = AudioFingerprintJob()
        job.run()
        
        # Exit with error if any labels failed
        if job.failed_count > 0:
            logger.warning(f"Job completed with {job.failed_count} failures")
            sys.exit(1)
        
    except Exception as err:
        logger.error(
            json.dumps({
                "message": "Job failed with exception",
                "error": str(err),
                "task_index": TASK_INDEX,
                "task_attempt": TASK_ATTEMPT,
                "severity": "ERROR"
            }),
            exc_info=True
        )
        sys.exit(1)