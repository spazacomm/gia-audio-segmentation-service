import os
import uuid
import tempfile
import logging
from typing import Dict, List, Optional

from pydub import AudioSegment
import acoustid
import chromaprint
from google.cloud import storage
from supabase import create_client, Client

SIMILARITY_THRESHOLD = float(os.getenv("SIMILARITY_THRESHOLD", "0.85"))
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "25"))

LIBRARY_BUCKET = os.getenv("LIBRARY_BUCKET", "spaza-audio-library")

logger = logging.getLogger("audio-fingerprint-worker")


class AudioFingerprintWorker:
    def __init__(self):
        self.supabase: Client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )

        self.storage = storage.Client()
        self.library_bucket = self.storage.bucket(LIBRARY_BUCKET)

    # ------------------------------------------------------------
    # Batch entrypoint
    # ------------------------------------------------------------
    def run_batch(self):
        logger.info("🔍 Fetching pending timeline labels")

        labels = self._fetch_pending_labels()

        if not labels:
            logger.info("✅ No pending labels found")
            return

        logger.info(f"🚀 Processing batch size={len(labels)}")

        for label in labels:
            try:
                self._process_label(label)
            except Exception:
                logger.exception(
                    f"❌ Fatal error processing label_id={label.get('id')}"
                )

    # ------------------------------------------------------------
    # Supabase queries
    # ------------------------------------------------------------
    def _fetch_pending_labels(self) -> List[Dict]:
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

    # ------------------------------------------------------------
    # Single label pipeline
    # ------------------------------------------------------------
    def _process_label(self, label: Dict):
        label_id = label["id"]
        start = float(label["start_time"])
        end = float(label["end_time"])
        recording_url = label["broadcast_timeline"]["recording_url"]

        logger.info(
            f"▶ label_id={label_id} "
            f"segment={start:.2f}-{end:.2f}s"
        )

        with tempfile.TemporaryDirectory() as tmp:
            source_path = f"{tmp}/source"
            snippet_path = f"{tmp}/snippet.mp3"

            self._download_recording(recording_url, source_path)
            self._trim_audio(source_path, snippet_path, start, end)

            duration, fp_encoded = acoustid.fingerprint_file(snippet_path)
            fp_ints, _ = chromaprint.decode_fingerprint(fp_encoded)

            match = self._find_fingerprint_match(fp_ints)

            if match:
                audio_id, score, media_url = match
                logger.info(
                    f"✅ Match audio_id={audio_id} score={score:.3f}"
                )
            else:
                audio_id, media_url = self._insert_audio(
                    fp_encoded, duration, snippet_path
                )
                score = None
                logger.info(f"➕ Inserted new audio_id={audio_id}")

            self._update_label(label_id, audio_id, media_url, score)

    # ------------------------------------------------------------
    # Audio handling
    # ------------------------------------------------------------
    def _download_recording(self, gcs_url: str, target_path: str):
        bucket_name, blob_path = self._parse_gcs_url(gcs_url)
        bucket = self.storage.bucket(bucket_name)

        logger.info(f"⬇ Downloading gs://{bucket_name}/{blob_path}")
        bucket.blob(blob_path).download_to_filename(target_path)

    def _trim_audio(self, source_path: str, target_path: str, start: float, end: float):
        audio = AudioSegment.from_file(source_path)
        snippet = audio[int(start * 1000): int(end * 1000)]
        snippet.export(target_path, format="mp3")

    # ------------------------------------------------------------
    # Fingerprint matching
    # ------------------------------------------------------------
    def _find_fingerprint_match(self, fp_ints):
        rows = (
            self.supabase
            .table("audio_library")
            .select("id,fingerprint_hash,media_url")
            .execute()
            .data
        )

        best_id = None
        best_score = 0.0
        best_url = None

        for row in rows:
            stored_fp, _ = chromaprint.decode_fingerprint(
                row["fingerprint_hash"].encode()
            )
            score = self._similarity(fp_ints, stored_fp)

            if score > best_score:
                best_id = row["id"]
                best_score = score
                best_url = row["media_url"]

        if best_score >= SIMILARITY_THRESHOLD:
            self._increment_play_count(best_id)
            return best_id, best_score, best_url

        return None

    # ------------------------------------------------------------
    # DB updates
    # ------------------------------------------------------------
    def _insert_audio(self, fp_encoded, duration, snippet_path):
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

    def _increment_play_count(self, audio_id: int):
        self.supabase.rpc(
            "increment_audio_play_count",
            {"audio_id": audio_id}
        ).execute()

    def _update_label(
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

        logger.info(f"✅ Updated timeline_labels.id={label_id}")

    # ------------------------------------------------------------
    # Utils
    # ------------------------------------------------------------
    def _similarity(self, fp1, fp2):
        length = min(len(fp1), len(fp2))
        if length == 0:
            return 0.0

        errors = 0
        for i in range(length):
            errors += bin(fp1[i] ^ fp2[i]).count("1")

        return 1.0 - (errors / (length * 32))

    def _parse_gcs_url(self, url: str):
        if url.startswith("gs://"):
            _, rest = url.split("gs://", 1)
            bucket, path = rest.split("/", 1)
            return bucket, path

        if url.startswith("https://storage.googleapis.com/"):
            rest = url.replace("https://storage.googleapis.com/", "")
            bucket, path = rest.split("/", 1)
            return bucket, path

        raise ValueError(f"Unsupported GCS URL: {url}")
