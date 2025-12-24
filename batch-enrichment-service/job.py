import os
import json
import logging
import hashlib
from datetime import datetime
from typing import List, Dict, Set

import httpx
from tenacity import retry, stop_after_attempt, wait_random_exponential

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig

# ============================================================
# 1. Configuration
# ============================================================

class Config:
    def __init__(self):
        self.supabase_url = os.getenv("SUPABASE_URL")
        self.supabase_key = os.getenv("SUPABASE_KEY")
        self.project_id = os.getenv("GCP_PROJECT_ID")

        # Heuristics
        self.pause_threshold = float(os.getenv("PAUSE_THRESHOLD", "2.5"))
        self.jingle_threshold = float(os.getenv("JINGLE_THRESHOLD", "8.0"))
        self.song_threshold = float(os.getenv("SONG_THRESHOLD", "60.0"))
        ad_list = os.getenv("AD_DURATIONS", "15,30,45,60")
        self.ad_durations: Set[int] = {int(d.strip()) for d in ad_list.split(",")}

        # Label categories
        self.label_categories = {
            "speech": {"male", "female"},
            "music": {"music"},
            "noise": {"noise", "noEnergy"}
        }
        self.known_labels = set().union(*self.label_categories.values())


# ============================================================
# 2. Batch Assembler
# ============================================================

class BatchAssembler:
    """Stitches timeline labels into logical broadcast events."""

    def __init__(self, config: Config):
        self.config = config

    def label_category(self, label: str) -> str:
        for category, labels in self.config.label_categories.items():
            if label in labels:
                return category
        return "unknown"

    def assemble_events(self, labels: List[Dict]) -> List[Dict]:
        events = []
        buffer = []

        for i, curr in enumerate(labels):
            # Skip if timestamps missing
            if not curr.get("start_time") or not curr.get("end_time"):
                logging.warning(f"Skipping label with missing timestamps: {curr}")
                continue

            # Default source_id if missing
            curr.setdefault("source_id", "unknown_source")

            if not buffer:
                buffer.append(curr)
                continue

            prev = buffer[-1]

            t_curr = self._parse(curr["start_time"])
            t_prev = self._parse(prev["end_time"])
            gap = (t_curr - t_prev).total_seconds()

            t_start = self._parse(buffer[0]["start_time"])
            current_duration = (t_prev - t_start).total_seconds()

            merge = False

            cat_prev = self.label_category(prev["label"])
            cat_curr = self.label_category(curr["label"])

            # -------------------------
            # Rule 1: Speech continuity
            # -------------------------
            if cat_prev == "speech" and cat_curr == "speech":
                if gap < self.config.pause_threshold:
                    merge = True

            # -------------------------
            # Rule 2: Short music jingle
            # -------------------------
            elif cat_curr == "music" and float(curr.get("duration_seconds", 0)) < self.config.jingle_threshold:
                if i + 1 < len(labels):
                    next_cat = self.label_category(labels[i + 1]["label"])
                    if next_cat == "speech":
                        merge = True

            # -------------------------
            # Rule 3: Noise / noEnergy
            # -------------------------
            elif cat_curr in ("noise", "unknown"):
                if gap < self.config.pause_threshold:
                    merge = True
                else:
                    merge = False

            # -------------------------
            # Rule 4: Enforce ad boundaries
            # -------------------------
            if any(abs(current_duration - d) < 1.0 for d in self.config.ad_durations):
                merge = False

            # -------------------------
            # Rule 5: Long music = song
            # -------------------------
            if cat_curr == "music" and float(curr.get("duration_seconds", 0)) > self.config.song_threshold:
                merge = False

            if merge:
                buffer.append(curr)
            else:
                events.append(self._package_event(buffer))
                buffer = [curr]

        if buffer:
            events.append(self._package_event(buffer))

        return events

    def _package_event(self, segments: List[Dict]) -> Dict:
        t_start = self._parse(segments[0]["start_time"])
        t_end = self._parse(segments[-1]["end_time"])

        source_id = segments[0]["source_id"]
        id_source = f"{source_id}|{segments[0]['start_time']}|{segments[0]['id']}"
        event_id = hashlib.sha1(id_source.encode()).hexdigest()

        # Merge acoustic profile including unknown labels
        acoustic_profile = sorted({s["label"] for s in segments if s.get("label")})

        return {
            "id": event_id,
            "source_id": source_id,
            "start_time": segments[0]["start_time"],
            "end_time": segments[-1]["end_time"],
            "duration": (t_end - t_start).total_seconds(),
            "transcript": " ".join(
                s.get("transcript", "") for s in segments if s.get("transcript")
            ).strip() or None,
            "acoustic_profile": acoustic_profile,
            "timeline_label_ids": [str(s["id"]) for s in segments],
            "boundary_reason": "heuristic_cut"
        }

    @staticmethod
    def _parse(ts: str) -> datetime:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))


# ============================================================
# 3. Broadcast Batch Job
# ============================================================

class BroadcastBatchJob:
    def __init__(self):
        self.config = Config()

        vertexai.init(
            project=self.config.project_id,
            location="us-central1"
        )

        self.model = GenerativeModel("gemini-1.5-flash-002")

        self.client = httpx.Client(
            base_url=f"{self.config.supabase_url}/rest/v1",
            headers={
                "apikey": self.config.supabase_key,
                "Authorization": f"Bearer {self.config.supabase_key}",
                "Prefer": "resolution=merge-duplicates"
            },
            timeout=120
        )

    @retry(wait=wait_random_exponential(min=1, max=10), stop=stop_after_attempt(3))
    def enrich_with_ai(self, event: Dict) -> Dict:
        """Use AI to enrich the event with topics, entities, and classification."""
        if not event["transcript"]:
            return {
                "event_type": "music" if "music" in event["acoustic_profile"] else "noise",
                "event_name": None,
                "is_commercial": False,
                "summary": None,
                "topics": [],
                "entities": []
            }

        prompt = f"""
Analyze this radio broadcast segment:

"{event['transcript'][:4000]}"

Return STRICT JSON:
{{
  "event_type": "ad|show|news|music",
  "event_name": string|null,
  "is_commercial": boolean,
  "summary": string|null,
  "topics": [string],
  "entities": [string]
}}
"""

        response = self.model.generate_content(
            prompt,
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                temperature=0.2
            )
        )

        return json.loads(response.text)

    def run(self, source_id: str, date_str: str):
        logging.info(f"Processing source={source_id} date={date_str}")

        chunks_resp = self.client.get(
            "/broadcast_timeline",
            params={
                "source_id": f"eq.{source_id}",
                "broadcast_datetime": f"gte.{date_str}T00:00:00",
                "broadcast_datetime": f"lte.{date_str}T23:59:59",
                "order": "broadcast_datetime.asc"
            }
        )

        chunks = chunks_resp.json()
        if not chunks:
            logging.warning("No broadcast chunks found")
            return

        labels = []
        for chunk in chunks:
            resp = self.client.get(
                "/timeline_labels",
                params={
                    "timeline_id": f"eq.{chunk['id']}",
                    "order": "start_time.asc"
                }
            )
            labels_chunk = resp.json()
            for lbl in labels_chunk:
                lbl["source_id"] = chunk.get("source_id", "unknown_source")
            labels.extend(labels_chunk)

        if not labels:
            logging.warning("No labels found")
            return

        assembler = BatchAssembler(self.config)
        events = assembler.assemble_events(labels)

        for event in events:
            try:
                enrichment = self.enrich_with_ai(event)

                payload = {
                    "id": event["id"],
                    "source_id": event["source_id"],
                    "start_time": event["start_time"],
                    "end_time": event["end_time"],
                    "duration": event["duration"],
                    "transcript": event["transcript"],
                    "event_type": enrichment.get("event_type"),
                    "event_name": enrichment.get("event_name"),
                    "is_commercial": enrichment.get("is_commercial", False),
                    "acoustic_profile": event["acoustic_profile"],
                    "boundary_reason": event["boundary_reason"],
                    "timeline_label_ids": event["timeline_label_ids"],
                    "metadata": {
                        "summary": enrichment.get("summary"),
                        "topics": enrichment.get("topics", []),
                        "entities": enrichment.get("entities", []),
                        "model": "gemini-1.5-flash-002",
                        "enriched_at": datetime.utcnow().isoformat()
                    }
                }

                self.client.post("/broadcast_events", json=payload).raise_for_status()

            except Exception as e:
                logging.error(f"Failed to persist event {event['id']}: {e}")

        logging.info(f"Completed: {len(events)} broadcast events processed")


# ============================================================
# 4. Entry Point
# ============================================================

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)

    source_id = os.getenv("SOURCE_ID")
    process_date = os.getenv("PROCESS_DATE")  # YYYY-MM-DD

    if not source_id or not process_date:
        raise RuntimeError("SOURCE_ID and PROCESS_DATE must be set")

    job = BroadcastBatchJob()
    job.run(source_id, process_date)
