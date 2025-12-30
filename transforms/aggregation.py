import apache_beam as beam
from typing import List, Iterable
from models import AudioSegment, MediaItem
import logging
import uuid
from datetime import datetime, timedelta

class AggregateSegments(beam.DoFn):
    """
    Aggregates contiguous audio segments into logical MediaItems.
    Input: Keyed segments (station, country, platform)
    Output: MediaItem objects
    """
    def process(self, element: tuple) -> Iterable[MediaItem]:
        key, segments = element
        station, country, platform = key
        
        logging.info(f"Processing {len(segments)} segments for station: {station} ({country})")
        
        # Sort segments by start_offset_seconds
        sorted_segments = sorted(segments, key=lambda s: s.start_offset_seconds)
        
        if not sorted_segments:
            return

        current_block = [sorted_segments[0]]
        
        for i in range(1, len(sorted_segments)):
            prev = sorted_segments[i-1]
            curr = sorted_segments[i]
            
            # Distance threshold to merge segments (0.5s)
            is_contiguous = (curr.start_offset_seconds - prev.end_offset_seconds) < 0.5
            same_label = curr.segment_label == prev.segment_label
            
            # Heuristic: Merge if contiguous and same label
            if is_contiguous and same_label:
                current_block.append(curr)
            else:
                yield self._create_media_item(current_block, station, country, platform)
                current_block = [curr]
        
        if current_block:
            yield self._create_media_item(current_block, station, country, platform)

    def _create_media_item(self, segments: List[AudioSegment], station: str, country: str, platform: str) -> MediaItem:
        label = segments[0].segment_label
        duration = sum(s.duration_seconds for s in segments)
        start_time = segments[0].start_time
        end_time = segments[-1].end_time
        now = datetime.now()
        
        logging.debug(f"Creating MediaItem from {len(segments)} segments. Label: {label}, Duration: {duration:.2f}s")

        item = MediaItem(
            item_id=str(uuid.uuid4()),
            source_name=station,
            country_code=country,
            source_type=platform if platform else "RADIO_STATION",
            content_format="AUDIO",
            published_at=now,
            collected_at=start_time,
            processed_at=now
        )
        
        # Apply Classification Logic
        if label == 'music':
            if duration > 90:
                item.classification = "MUSIC"
                item.classification_type = "MUSIC"
            elif duration < 15:
                item.classification = "BROADCAST"
                item.classification_type = "JINGLE"
            else:
                item.classification = "ADVERTISING"
                item.classification_type = "AD"
        elif label in ['male', 'female']:
            if duration > 60:
                item.classification = "CONTENT"
                item.classification_type = "BROADCAST"
                item.sub_classification = "PROGRAM"
            else:
                item.classification = "ADVERTISING"
                item.classification_type = "AD"
                item.sub_classification = "RADIO_SPOT"
        else:
            item.classification = "UNKNOWN"
            
        logging.info(f"Generated {item.classification}:{item.classification_type} item for {station} (Duration: {duration:.1f}s)")
        return item
