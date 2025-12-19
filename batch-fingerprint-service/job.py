import os
import sys
import json
import uuid
import time
import tempfile
import logging
from typing import Dict, List, Optional

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

    # ---------------------------------------------
    # Entry point
    # ---------------------------------------------
    def run(self):
        logger.info(
            f"🚀 Starting Cloud Run Job "
            f"task_index={TASK_INDEX} attempt={TASK_ATTEMPT}"
        )

        labels = self.fetch_pending_labels()

        if not labels:
            logger.info("✅ No pending labels found")
            return

        logger.info(f"🔧 Processing {len(labels)} labels")

        for label in labels:
            self.process_label(label)

        logger.info("🎉 Job completed successfully")

    # ---------------------------------------------
    # Fetch work
    # ---------------------------------------------
    def fetch_pending_labels(self) -> List[Dict]:
        response = (
            self.supabase
            .table("timeline_labels")
            .select(
                """
                id,
                start_time,
                end_time,
                timeline_id,
                broadcast_timeline (
                    recording_url
                )
                """
            )
            .is_("media_url", None)
            .eq("fingerprint_processed", False)
            .limit(BATCH_SIZE)
            .execute()
        )

        return response.data or []

    # ---------------------------------------------
    # Process one label
    # ---------------------------------------------
    def process_label(self, label: Dict):
        label_id = label["id"]
        start = float(label["start_offset_seconds"])
        end = float(label["end_offset_seconds"])
        recording_url = label["broadcast_timeline"]["recording_url"]

        logger.info(
            f"▶ label_id={label_id} segment={start:.2f}-{end:.2f}s"
        )

        with tempfile.TemporaryDirectory() as tmp:
            source_path = f"{tmp}/source"
            snippet_path = f"{tmp}/snippet.mp3"

            self.download_recording(recording_url, source_path)
            self.trim_audio(source_path, snippet_path, start, end)

            duration, fp_encoded = acoustid.fingerprint_file(snippet_path)
            fp_ints, _ = chromaprint.decode_fingerprint(fp_encoded)

            match = self.find_match(fp_ints)

            if match:
                audio_id, score, media_url = match
                logger.info(
                    f"✅ Match audio_id={audio_id} score={score:.3f}"
                )
            else:
                audio_id, media_url = self.insert_audio(
                    fp_encoded, duration, snippet_path
                )
                score = None
                logger.info(f"➕ Inserted new audio_id={audio_id}")

            self.update_label(label_id, audio_id, media_url, score)

    # ---------------------------------------------
    # Audio helpers
    # ---------------------------------------------
    def download_recording(self, gcs_url: str, target_path: str):
        bucket, path = self.parse_gcs_url(gcs_url)
        logger.info(f"⬇ Downloading gs://{bucket}/{path}")
        self.storage.bucket(bucket).blob(path).download_to_filename(target_path)

    def trim_audio(self, source: str, target: str, start: float, end: float):
        audio = AudioSegment.from_file(source)
        snippet = audio[int(start * 1000): int(end * 1000)]
        snippet.export(target, format="mp3")

    # ---------------------------------------------
    # Fingerprint matching
    # ---------------------------------------------
    def find_match(self, fp_ints):
        rows = (
            self.supabase
            .table("audio_library")
            .select("id,fingerprint_hash,media_url")
            .execute()
            .data
        )

        best = (None, 0.0, None)

        for row in rows:
            stored_fp, _ = chromaprint.decode_fingerprint(
                row["fingerprint_hash"].encode()
            )
            score = self.similarity(fp_ints, stored_fp)

            if score > best[1]:
                best = (row["id"], score, row["media_url"])

        if best[1] >= SIMILARITY_THRESHOLD:
            return best

        return None

    # ---------------------------------------------
    # DB writes
    # ---------------------------------------------
    def insert_audio(self, fp_encoded, duration, snippet_path):
        blob_name = f"{uuid.uuid4().hex}.mp3"
        blob = self.library_bucket.blob(blob_name)
        blob.upload_from_filename(snippet_path)

        media_url = blob.public_url
        fp_str = fp_encoded.decode() if isinstance(fp_encoded, bytes) else fp_encoded

        result = (
            self.supabase
            .table("audio_library")
            .insert({
                "fingerprint_hash": fp_str,
                "audio_type": "unknown",
                "duration_seconds": duration,
                "media_url": media_url,
            })
            .execute()
        )

        return result.data[0]["id"], media_url

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
        }).eq("id", label_id).execute()

        logger.info(f"✅ Updated label_id={label_id}")

    # ---------------------------------------------
    # Utils
    # ---------------------------------------------
    def similarity(self, fp1, fp2):
        length = min(len(fp1), len(fp2))
        if length == 0:
            return 0.0

        errors = 0
        for i in range(length):
            errors += bin(fp1[i] ^ fp2[i]).count("1")

        return 1.0 - (errors / (length * 32))

    def parse_gcs_url(self, url: str):
        if url.startswith("gs://"):
            _, rest = url.split("gs://", 1)
            return rest.split("/", 1)

        if url.startswith("https://storage.googleapis.com/"):
            rest = url.replace("https://storage.googleapis.com/", "")
            return rest.split("/", 1)

        raise ValueError(f"Unsupported GCS URL: {url}")


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
