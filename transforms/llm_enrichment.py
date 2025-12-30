import os
import json
import apache_beam as beam
import google.generativeai as genai
from typing import Iterable, Any, Dict, Optional
from models import MediaItem, AudioLibraryItem

class LLMEnrichmentProcessor(beam.DoFn):
    """
    Enriches unknown assets using Gemini AI.
    Processes Ads, Jingles, and Content that are not in the library.
    """
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.model = None

    def setup(self):
        genai.configure(api_key=self.api_key)
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    def process(self, item: Any) -> Iterable[Any]:
        # Handle AudioLibraryItem pass-through from previous step
        if isinstance(item, AudioLibraryItem):
            yield item
            return

        # Handle MediaItem
        if not isinstance(item, MediaItem):
            return

        # Filter: Process if SPEECH or (AD/BROADCAST and Unknown)
        should_process = (
            item.classification == "SPEECH" or 
            (item.classification in ["ADVERTISING", "BROADCAST"] and not item.asset_known)
        )
        
        if not should_process:
            yield item
            return
            
        # Ensure we have access to the audio
        if not item.temp_audio_uri:
            print(f"Skipping LLM enrichment for {item.item_id}: No audio URI available.")
            yield item
            return

        try:
            # 1. Prepare Prompt
            prompt_text = f"""
            Analyze this radio audio clip (Station: {item.source_name}, Country: {item.country_code}).
            The clip is classified as {item.classification} / {item.classification_type}.
            
            Provide a structured JSON response with exactly these fields:
            - transcript: verbatim transcription of speech/lyrics
            - brands: list of company names
            - topics: list of main subjects
            - topic_category: general category (e.g. Politics, Sports, Health)
            - sentiment: one word (POSITIVE, NEGATIVE, NEUTRAL)
            - sentiment_confidence: float 0.0-1.0
            - sentiment_magnitude: float 0.0-infinity (strength of emotion)
            - emotion_scores: object with keys [joy, sadness, anger, fear, surprise, disgust, trust, anticipation] and float values 0.0-1.0
            - keywords: list of terms
            - summary: 1-sentence abstractive summary
            - call_to_action: specific CTA or null
            - is_ad: boolean (verify if this sounds like an ad)
            - creative_type: "Spot", "Sponsorship", "Live Read", etc. if Ad
            """

            # 2. Call Gemini
            # We pass the GCS URI directly. Gemini 1.5 Flash supports audio.
            # Note: The File API or inline data might be needed depending on SDK version.
            # For vertex AI or GenAI: 'part' with mime_type and file_uri.
            
            audio_part = {
                "mime_type": "audio/mp3",
                "data": None, # If local
                "file_uri": item.temp_audio_uri # If supported by configured backend, otherwise need to download or use File API
            }
            
            # Using File API (Common pattern for large files or GCS in some SDKs)
            # Since we are in Dataflow, straightforward GCS usage depends on the specific genai library setup.
            # Assuming standard 'upload_file' pattern or direct URI if Vertex.
            # For google.generativeai (AI Studio), we usually need to upload.
            
            # Efficient cost-optimized approach:
            # If using Vertex AI SDK: direct GCS support.
            # If using AI Studio (api_key): Need `genai.upload_file`.
            
            # Let's verify setup. We configured `genai`.
            # We must upload the file from GCS to Gemini's File API first.
            # BUT: We are inside Dataflow worker. We have the GCS path.
            # We can't let Gemini read GCS directly with API Key. We need to download and upload, OR use Vertex AI.
            # Given `api_key` usage, this is AI Studio.
            
            # Download from GCS locally to upload to Gemini
            # (Optimisation: We could have uploaded to Gemini directly in previous step, but separating concerns is better)
            
            temp_local_path = f"/tmp/{item.item_id}.mp3"
            with beam.io.filesystems.FileSystems.open(item.temp_audio_uri) as f_remote:
                 with open(temp_local_path, 'wb') as f_local:
                     f_local.write(f_remote.read())
            
            uploaded_file = genai.upload_file(temp_local_path)
            
            response = self.model.generate_content([prompt_text, uploaded_file])
            
            # Clean up Gemini file (optional but good practice)
            # genai.delete_file(uploaded_file.name) 
            # Clean up local temp
            os.remove(temp_local_path)
            
            try:
                data = json.loads(response.text)
            except json.JSONDecodeError:
                # Fallback or raw text parsing
                data = {"transcript": response.text, "summary": "Raw response due to JSON error."}
            
            # 3. Update MediaItem (Mapping logic reused)

            # 3. Update MediaItem
            item.content_text = data.get('transcript')
            item.tags = data.get('keywords', [])
            item.topic_keywords = data.get('topics', [])
            item.topic_category = data.get('topic_category')
            item.sentiment_label = data.get('sentiment')
            item.sentiment_confidence = data.get('sentiment_confidence')
            item.sentiment_magnitude = data.get('sentiment_magnitude')
            item.emotion_scores = data.get('emotion_scores')
            
            # Verify Ad Status
            if data.get('is_ad') and not item.is_ad:
                item.is_ad = True
                item.ad_detected_by = "LLM_VERIFICATION"
            
            # Populate structured metadata
            item.metadata['additional_fields'] = [
                {'field_name': 'summary', 'field_value': data.get('summary')},
                {'field_name': 'call_to_action', 'field_value': data.get('call_to_action')}
            ]

            if item.is_ad:
                 brands_str = ", ".join(data.get('brands', []))
                 item.metadata['ad_metadata'] = {
                     'ad_category': brands_str, 
                     'creative_type': data.get('creative_type', 'AUDIO'),
                     'industry_code': None # Could ask LLM for this too
                 }
            
            # Ensure additional_fields includes brands if not in ad_metadata (or both).
            item.metadata['additional_fields'].append(
                {'field_name': 'brands', 'field_value': ", ".join(data.get('brands', []))}
            )
            
            # 4. Create Library Item for Archival
            library_bucket = os.environ.get('AUDIO_LIBRARY_BUCKET', 'spaza-audio-library')
            lib_item = AudioLibraryItem(
                fingerprint_id=item.metadata.get('fingerprint'), 
                fingerprint_uri=f"gs://{library_bucket}/fingerprints/{item.metadata.get('fingerprint_id')}.fp",
                source_type=item.classification_type.lower(),
                transcript=item.content_text,
                metadata={
                    "brands": data.get('brands'),
                    "topics": data.get('topics'),
                    "keywords": data.get('keywords'),
                    "cta": data.get('call_to_action'),
                    "summary": data.get('summary')
                }
            )
            
            yield item
            yield lib_item

        except Exception as e:
            print(f"Error in LLMEnrichmentProcessor: {e}")
            yield item
