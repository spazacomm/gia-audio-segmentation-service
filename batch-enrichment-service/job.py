import os
import json
import logging
import httpx
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, Field, ValidationError

import vertexai
from vertexai.generative_models import GenerativeModel, GenerationConfig
from tenacity import retry, stop_after_attempt, wait_random_exponential

# --- Configuration ---
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY") # Service role bypasses RLS for backend
PROJECT_ID = os.getenv("GCP_PROJECT_ID")
LOCATION = os.getenv("GCP_LOCATION", "us-central1")
MODEL_ID = os.getenv("MODEL_ID", "gemini-1.5-flash-002")
BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))

logging.basicConfig(level=logging.INFO, format='%(levelname)s: %(message)s')
logger = logging.getLogger(__name__)

# --- Structured Output Schema ---
class EnrichmentData(BaseModel):
    content_type: str = Field(description="One of: advertisement, talk, news, music, intro, sponsorship")
    brands: List[str] = Field(default_factory=list)
    products: List[str] = Field(default_factory=list)
    topics: List[str] = Field(default_factory=list)
    confidence_score: float

class LLMResult(BaseModel):
    segment_id: int
    enrichment: EnrichmentData

# --- The Service Class ---
class RobustEnricher:
    def __init__(self):
        vertexai.init(project=PROJECT_ID, location=LOCATION)
        self.model = GenerativeModel(MODEL_ID)
        self.api_client = httpx.Client(
            base_url=f"{SUPABASE_URL}/rest/v1",
            headers={
                "apikey": SUPABASE_KEY,
                "Authorization": f"Bearer {SUPABASE_KEY}",
                "Prefer": "return=representation" 
            },
            timeout=60.0
        )

    def fetch_and_lock_batch(self) -> List[Dict]:
        """
        Uses a 'handshake' pattern to fetch and lock rows. 
        Note: PostgREST doesn't support 'FOR UPDATE SKIP LOCKED', 
        so we use a task_id to claim rows atomically.
        """
        task_id = f"job_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}"
        
        # 1. Select candidates
        search_params = {
            "label": "in.(male,female)",
            "enrichment_data": "is.null",
            "processing_task_id": "is.null",
            "select": "id,transcript,duration_seconds,label",
            "limit": BATCH_SIZE
        }
        resp = self.api_client.get("/timeline_labels", params=search_params)
        resp.raise_for_status()
        rows = resp.json()
        
        if not rows:
            return []

        # 2. Mark them as processing immediately
        ids = [r['id'] for r in rows]
        lock_resp = self.api_client.patch(
            "/timeline_labels",
            params={"id": f"in.({','.join(map(str, ids))})"},
            json={
                "processing_task_id": task_id,
                "processing_started_at": datetime.now(timezone.utc).isoformat()
            }
        )
        lock_resp.raise_for_status()
        return rows

    @retry(wait=wait_random_exponential(min=1, max=10), stop=stop_after_attempt(3))
    def call_gemini_with_patterns(self, segments: List[Dict]) -> List[Dict]:
        """Analyzes segments using duration-based heuristics."""
        
        prompt_parts = []
        for s in segments:
            # We explicitly pass duration to help Gemini identify Ads/Tags
            prompt_parts.append(
                f"ID: {s['id']} | Dur: {s['duration_seconds']}s | Transcript: {s['transcript']}"
            )

        instructions = f"""
        Analyze the following radio/TV segments. Use the Duration (Dur) to aid classification:
        - 15s, 30s, 60s are likely 'advertisement'.
        - <10s mentioning a brand is likely 'sponsorship'.
        - >60s is likely 'talk' or 'news'.
        
        Return a JSON array where each object has 'segment_id' and 'enrichment' (content_type, brands, products, topics, confidence_score).
        
        Segments:
        {chr(10).join(prompt_parts)}
        """

        response = self.model.generate_content(
            instructions,
            generation_config=GenerationConfig(
                response_mime_type="application/json",
                temperature=0.1
            )
        )
        return json.loads(response.text)

    def finalize_batch(self, results: List[Dict]):
        """Updates Supabase with enriched data and clears the task lock."""
        for res in results:
            try:
                # Validation check
                valid_res = LLMResult(**res)
                
                self.api_client.patch(
                    "/timeline_labels",
                    params={"id": f"eq.{valid_res.segment_id}"},
                    json={
                        "enrichment_data": valid_res.enrichment.model_dump(),
                        "processing_completed_at": datetime.now(timezone.utc).isoformat(),
                        "processing_task_id": None
                    }
                ).raise_for_status()
            except Exception as e:
                logger.error(f"Failed to save result for ID {res.get('segment_id')}: {e}")

    def run(self):
        try:
            segments = self.fetch_and_lock_batch()
            if not segments:
                logger.info("No work found.")
                return

            logger.info(f"Processing {len(segments)} segments...")
            results = self.call_gemini_with_patterns(segments)
            self.finalize_batch(results)
            logger.info("Batch enrichment successful.")

        except Exception as e:
            logger.error(f"Job failed: {e}")
            # Optional: Clear task_ids on fatal failure to allow retry
            raise

if __name__ == "__main__":
    RobustEnricher().run()