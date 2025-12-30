from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime

@dataclass
class AudioSegment:
    segment_id: str
    parent_file_uri: str
    start_offset_seconds: float
    end_offset_seconds: float
    duration_seconds: float
    segment_label: str
    segmenter_confidence: Optional[float] = None
    transcription: Optional[str] = None
    stt_confidence: Optional[float] = None
    language_code: Optional[str] = None
    station_name: Optional[str] = None
    platform_type: Optional[str] = None
    processed_at: Optional[datetime] = None
    country_code: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None

@dataclass
class MediaItem:
    item_id: str
    external_id: Optional[str] = None
    classification: str = "UNKNOWN"
    classification_type: str = "UNKNOWN"
    content_format: str = "AUDIO"
    sub_classification: Optional[str] = None
    source_id: Optional[str] = None
    source_name: str = ""
    source_type: str = "RADIO_STATION"
    country_code: str = ""
    published_at: Optional[datetime] = None
    collected_at: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    content_text: Optional[str] = None
    tags: List[str] = field(default_factory=list)
    topic_keywords: List[str] = field(default_factory=list)
    sentiment_score: Optional[float] = None
    sentiment_label: Optional[str] = None
    
    # All Metadata fields aligned with BigQuery
    emotion_scores: Optional[Dict[str, float]] = None
    detected_entities: List[Dict[str, Any]] = field(default_factory=list)

    # Missing Top-Level Fields from Audit
    reach_estimate: Optional[int] = None
    impressions_count: Optional[int] = None
    engagement_count: Optional[int] = None
    engagement_rate: Optional[float] = None
    share_count: Optional[int] = None
    comment_count: Optional[int] = None
    like_count: Optional[int] = None
    sentiment_magnitude: Optional[float] = None
    sentiment_confidence: Optional[float] = None
    is_topic_match: Optional[bool] = None
    topic_id: Optional[str] = None
    topic_match_score: Optional[float] = None
    narrative_id: Optional[str] = None
    narrative_stage: Optional[str] = None
    rights_status: Optional[str] = None
    copyright_notice: Optional[str] = None
    processing_status: Optional[str] = "PENDING"
    processing_pipeline: Optional[str] = None
    quality_score: Optional[float] = None
    duplicate_of: Optional[str] = None
    requires_review: Optional[bool] = None
    review_status: Optional[str] = None
    reviewed_by: Optional[str] = None
    reviewed_at: Optional[datetime] = None
    version_number: Optional[int] = 1
    
    # BQ-Aligned Nested Metadata
    metadata: Dict[str, Any] = field(default_factory=dict)
    
    is_ad: bool = False
    ad_detected_by: Optional[str] = None
    asset_known: bool = False
    
    # Transient fields (these are often derived or temporary, not always stored directly)
    temp_audio_uri: Optional[str] = None
    brand_name: Optional[str] = None
    track_name: Optional[str] = None
    artist_name: Optional[str] = None
    genre: List[str] = field(default_factory=list)
    topic_name: Optional[str] = None

@dataclass
class AudioLibraryItem:
    fingerprint_id: str
    fingerprint_uri: str
    source_type: str
    title: Optional[str] = None
    artist: Optional[str] = None
    transcript: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
    brand: Optional[str] = None
    tags: List[str] = field(default_factory=list)

# BigQuery Table Schemas
AUDIO_SEGMENTS_SCHEMA = {
    'fields': [
        {'name': 'segment_id', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'parent_file_uri', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'start_offset_seconds', 'type': 'FLOAT', 'mode': 'REQUIRED'},
        {'name': 'end_offset_seconds', 'type': 'FLOAT', 'mode': 'REQUIRED'},
        {'name': 'duration_seconds', 'type': 'FLOAT', 'mode': 'REQUIRED'},
        {'name': 'segment_label', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'segmenter_confidence', 'type': 'FLOAT', 'mode': 'NULLABLE'},
        {'name': 'transcription', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'stt_confidence', 'type': 'FLOAT', 'mode': 'NULLABLE'},
        {'name': 'language_code', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'station_name', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'platform_type', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'processed_at', 'type': 'TIMESTAMP', 'mode': 'NULLABLE', 'defaultValueExpression': 'CURRENT_TIMESTAMP()'},
        {'name': 'start_time', 'type': 'TIMESTAMP', 'mode': 'NULLABLE'},
        {'name': 'end_time', 'type': 'TIMESTAMP', 'mode': 'NULLABLE'},
    ]
}

AUDIO_LIBRARY_SCHEMA = {
    'fields': [
        {'name': 'fingerprint_id', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'fingerprint_uri', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'source_type', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'title', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'artist', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'brand', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'transcript', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'tags', 'type': 'STRING', 'mode': 'REPEATED'},
        {'name': 'metadata', 'type': 'JSON', 'mode': 'NULLABLE'},
    ]
}

MEDIA_ITEMS_SCHEMA = {
    'fields': [
        {'name': 'item_id', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'external_id', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'classification', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'classification_type', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'content_format', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'sub_classification', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'source_id', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'source_name', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'source_type', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'country_code', 'type': 'STRING', 'mode': 'REQUIRED'},
        {'name': 'published_at', 'type': 'TIMESTAMP', 'mode': 'REQUIRED'},
        {'name': 'collected_at', 'type': 'TIMESTAMP', 'mode': 'REQUIRED'},
        {'name': 'processed_at', 'type': 'TIMESTAMP', 'mode': 'NULLABLE'},
        {'name': 'content_text', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'tags', 'type': 'STRING', 'mode': 'REPEATED'},
        
        # Engagement & Reach
        {'name': 'reach_estimate', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'impressions_count', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'engagement_count', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'engagement_rate', 'type': 'FLOAT', 'mode': 'NULLABLE'},
        {'name': 'share_count', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'comment_count', 'type': 'INTEGER', 'mode': 'NULLABLE'},
        {'name': 'like_count', 'type': 'INTEGER', 'mode': 'NULLABLE'},

        # Sentiment Analysis
        {'name': 'sentiment_score', 'type': 'FLOAT', 'mode': 'NULLABLE'},
        {'name': 'sentiment_magnitude', 'type': 'FLOAT', 'mode': 'NULLABLE'},
        {'name': 'sentiment_label', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'sentiment_confidence', 'type': 'FLOAT', 'mode': 'NULLABLE'},
        {'name': 'emotion_scores', 'type': 'RECORD', 'mode': 'NULLABLE', 'fields': [
             {'name': 'joy', 'type': 'FLOAT', 'mode': 'NULLABLE'},
             {'name': 'sadness', 'type': 'FLOAT', 'mode': 'NULLABLE'},
             {'name': 'anger', 'type': 'FLOAT', 'mode': 'NULLABLE'},
             {'name': 'fear', 'type': 'FLOAT', 'mode': 'NULLABLE'},
             {'name': 'surprise', 'type': 'FLOAT', 'mode': 'NULLABLE'},
             {'name': 'disgust', 'type': 'FLOAT', 'mode': 'NULLABLE'},
             {'name': 'trust', 'type': 'FLOAT', 'mode': 'NULLABLE'},
             {'name': 'anticipation', 'type': 'FLOAT', 'mode': 'NULLABLE'},
        ]},

        # Entities
        {'name': 'detected_entities', 'type': 'RECORD', 'mode': 'REPEATED', 'fields': [
            {'name': 'entity_id', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'entity_name', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'entity_type', 'type': 'STRING', 'mode': 'NULLABLE'},
            {'name': 'sentiment_score', 'type': 'FLOAT', 'mode': 'NULLABLE'},
            {'name': 'salience', 'type': 'FLOAT', 'mode': 'NULLABLE'},
            {'name': 'mentions_count', 'type': 'INTEGER', 'mode': 'NULLABLE'},
            {'name': 'context', 'type': 'STRING', 'mode': 'NULLABLE'},
        ]},

        # Ad Info
        {'name': 'is_ad', 'type': 'BOOLEAN', 'mode': 'NULLABLE'},
        {'name': 'ad_detected_by', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'advertiser_name', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'brand_name', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'campaign_name', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'source_url', 'type': 'STRING', 'mode': 'NULLABLE'},
        
        # Music Info (Top Level & Nested)
        {'name': 'track_name', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'artist_name', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'album_name', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'genre', 'type': 'STRING', 'mode': 'REPEATED'},
        
        # Topic Info
        {'name': 'is_topic_match', 'type': 'BOOLEAN', 'mode': 'NULLABLE'},
        {'name': 'topic_id', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'topic_name', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'topic_category', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'topic_match_score', 'type': 'FLOAT', 'mode': 'NULLABLE'},
        {'name': 'topic_keywords', 'type': 'STRING', 'mode': 'REPEATED'},
        
        # Content Governance
        {'name': 'rights_status', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'copyright_notice', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'processing_status', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'processing_pipeline', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'quality_score', 'type': 'FLOAT', 'mode': 'NULLABLE'},
        {'name': 'duplicate_of', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'requires_review', 'type': 'BOOLEAN', 'mode': 'NULLABLE'},
        {'name': 'review_status', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'reviewed_by', 'type': 'STRING', 'mode': 'NULLABLE'},
        {'name': 'reviewed_at', 'type': 'TIMESTAMP', 'mode': 'NULLABLE'},
        {'name': 'version_number', 'type': 'INTEGER', 'mode': 'NULLABLE'},

        # Detailed Metadata Record
        {'name': 'metadata', 'type': 'RECORD', 'mode': 'NULLABLE', 'fields': [
            {'name': 'ad_metadata', 'type': 'RECORD', 'mode': 'NULLABLE', 'fields': [
                {'name': 'ad_category', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'ad_subcategory', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'industry_code', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'creative_type', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'creative_format', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'ad_size', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'placement_name', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'placement_type', 'type': 'STRING', 'mode': 'NULLABLE'},
                 # Note: Omitting deeply nested 'dimensions', 'targeting_criteria' for now unless needed, 
                 # but keeping definition open to add if specific fields are requested.
            ]},
            # Music Metadata
            {'name': 'music_metadata', 'type': 'RECORD', 'mode': 'NULLABLE', 'fields': [
                {'name': 'recording_title', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'recording_version', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'duration_seconds', 'type': 'INTEGER', 'mode': 'NULLABLE'},
                {'name': 'isrc', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'artist', 'type': 'RECORD', 'mode': 'NULLABLE', 'fields': [
                     {'name': 'artist_name', 'type': 'STRING', 'mode': 'NULLABLE'},
                     {'name': 'artist_id', 'type': 'STRING', 'mode': 'NULLABLE'},
                     {'name': 'artist_type', 'type': 'STRING', 'mode': 'NULLABLE'},
                ]},
                {'name': 'release', 'type': 'RECORD', 'mode': 'NULLABLE', 'fields': [
                     {'name': 'release_title', 'type': 'STRING', 'mode': 'NULLABLE'},
                ]},
                {'name': 'genre', 'type': 'RECORD', 'mode': 'REPEATED', 'fields': [
                     {'name': 'genre_description', 'type': 'STRING', 'mode': 'NULLABLE'},
                ]},
                {'name': 'mood', 'type': 'STRING', 'mode': 'REPEATED'},
                {'name': 'tempo', 'type': 'STRING', 'mode': 'NULLABLE'},
            ]},
            # Topic Metadata
            {'name': 'topic_metadata', 'type': 'RECORD', 'mode': 'NULLABLE', 'fields': [
                {'name': 'topic_id', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'topic_name', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'topic_category', 'type': 'STRING', 'mode': 'NULLABLE'},
            ]},
            # Social Metadata (Placeholders for now)
            {'name': 'social_metadata', 'type': 'RECORD', 'mode': 'NULLABLE', 'fields': [
                 {'name': 'twitter', 'type': 'RECORD', 'mode': 'NULLABLE', 'fields': [{'name': 'tweet_id', 'type': 'STRING', 'mode': 'NULLABLE'}]},
            ]},
             # Other Metadata placeholders
            
            # Additional Fields
            {'name': 'additional_fields', 'type': 'RECORD', 'mode': 'REPEATED', 'fields': [
                {'name': 'field_name', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'field_value', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'field_type', 'type': 'STRING', 'mode': 'NULLABLE'},
                {'name': 'field_source', 'type': 'STRING', 'mode': 'NULLABLE'},
            ]},
            {'name': 'raw_source_data', 'type': 'STRING', 'mode': 'NULLABLE'},
        ]},
    ]
}
