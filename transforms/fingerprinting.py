import os
import re
import uuid
import apache_beam as beam
from typing import Iterable, Any, Dict, Optional
from models import MediaItem, AudioLibraryItem
from apache_beam.io.filesystems import FileSystems
import tempfile
import acoustid
import musicbrainzngs
from pydub import AudioSegment as PydubAudio
from datetime import datetime

class FingerprintProcessor(beam.DoFn):
    """
    Generates Chromaprint fingerprint using fpcalc.
    Identifies songs by bridging AcoustID to MusicBrainz.
    """
    def __init__(self, acoustid_api_key: Optional[str] = None):
        self.acoustid_api_key = acoustid_api_key

    def setup(self):
        # Initialize MusicBrainz client
        app_name = os.environ.get('MB_APP_NAME', 'RadioMonitoringPipeline')
        app_version = os.environ.get('MB_APP_VERSION', '1.0.0')
        contact = os.environ.get('MB_CONTACT', 'admin@example.com')
        musicbrainzngs.set_useragent(app_name, app_version, contact)

    def process(self, item: MediaItem, library: Dict[str, Any]) -> Iterable[Any]:
        # Always process valid classifications (including SPEECH now for audio slicing)
        if item.classification not in ["MUSIC", "ADVERTISING", "BROADCAST", "SPEECH"]:
            item.asset_known = False
            yield item
            return

        temp_orig = None
        temp_clip = None
        try:
            # 1. Calculate Offsets
            base_ts = self._parse_timestamp_from_uri(item.source_id)
            if not base_ts or not item.start_time:
                item.asset_known = False
                yield item
                return

            offset_start_sec = (item.start_time - base_ts).total_seconds()
            duration_sec = (item.end_time - item.start_time).total_seconds()

            # 2. Prepare Temp Files
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tf_orig:
                temp_orig = tf_orig.name
            with tempfile.NamedTemporaryFile(suffix='.mp3', delete=False) as tf_clip:
                temp_clip = tf_clip.name

            # 3. Download and Clip
            with FileSystems.open(item.source_id) as f_source:
                with open(temp_orig, 'wb') as f_dest:
                    f_dest.write(f_source.read())

            audio = PydubAudio.from_file(temp_orig)
            start_ms = max(0, int(offset_start_sec * 1000))
            end_ms = int((offset_start_sec + duration_sec) * 1000)
            clip = audio[start_ms:end_ms]
            clip.export(temp_clip, format="mp3")

            # 4. Save Audio Slice to GCS (Required for LLM and Library)
            library_bucket = os.environ.get('AUDIO_LIBRARY_BUCKET', 'spaza-audio-library')
            # Use item_id for unique filename
            gcs_audio_path = f"gs://{library_bucket}/audio_assets/{item.item_id}.mp3" 
            
            with open(temp_clip, 'rb') as f_src:
                with FileSystems.create(gcs_audio_path) as f_dest:
                    f_dest.write(f_src.read())
            
            # Attach to item for LLM usage
            item.temp_audio_uri = gcs_audio_path
            # As requested, set source_url to the sliced audio
            item.source_url = gcs_audio_path

            # 5. Fingerprint Logic (Skip for SPEECH)
            if item.classification == "SPEECH":
                item.asset_known = False
                # Speech is stored as an unknown asset essentially, but we don't try to acoustid it.
                yield item
                return

            # Generate Fingerprint for MUSIC/AD/BROADCAST
            _, fingerprint = acoustid.fingerprint_file(temp_clip)
            item.metadata['fingerprint'] = fingerprint
            
            # 6. Check Library (Side Input)
            if fingerprint in library:
                cached_data = library[fingerprint]
                item.asset_known = True
                item.metadata['fingerprint_id'] = cached_data.get('fingerprint_id')
                item.content_text = cached_data.get('transcript')
                item.tags = cached_data.get('tags', [])
                item.topics = cached_data.get('topics', [])
                item.metadata.update(cached_data.get('metadata', {}))
                item.ad_detected_by = "FINGERPRINT"
                yield item
            else:
                item.asset_known = False
                fingerprint_id = str(uuid.uuid4())
                item.metadata['fingerprint_id'] = fingerprint_id
                
                # Identify via AcoustID -> MusicBrainz
                if item.classification == "MUSIC" and self.acoustid_api_key:
                    self._identify_song(item, fingerprint, duration_sec)
                    
                    # Create Library Item for the new song
                    song_lib_item = AudioLibraryItem(
                        fingerprint_id=fingerprint,
                        fingerprint_uri=f"gs://{library_bucket}/fingerprints/{fingerprint_id}.fp",
                        source_type="song",
                        title=item.metadata.get('title'),
                        artist=item.metadata.get('artist'),
                        metadata={
                            "mbid": item.metadata.get('mbid'),
                            "album": item.metadata.get('album'),
                            "release_date": item.metadata.get('release_date'),
                            "acoustid_id": item.metadata.get('acoustid_id'),
                            "audio_uri": gcs_audio_path # Store link to audio
                        }
                    )
                    yield song_lib_item
                
                # Save fingerprint to GCS
                gcs_fp_path = f"gs://{library_bucket}/fingerprints/{fingerprint_id}.fp"
                with FileSystems.create(gcs_fp_path) as f:
                    f.write(fingerprint.encode('utf-8'))
                
                item.metadata['fingerprint_uri'] = gcs_fp_path
                yield item

        except Exception as e:
            print(f"Error in FingerprintProcessor: {e}")
            item.asset_known = False
            yield item
        finally:
            if temp_orig and os.path.exists(temp_orig):
                os.remove(temp_orig)
            if temp_clip and os.path.exists(temp_clip):
                os.remove(temp_clip)

    def _identify_song(self, item: MediaItem, fingerprint: str, duration: float):
        """Attempts to identify the song using AcoustID to get MBID, then MusicBrainz for metadata."""
        try:
            results = acoustid.lookup(self.acoustid_api_key, fingerprint, duration)
            if results['status'] == 'ok' and results['results']:
                # Get the best match with a MusicBrainz recording ID
                best_match = results['results'][0]
                mbid = None
                if 'recordings' in best_match:
                    mbid = best_match['recordings'][0].get('id')
                
                if mbid:
                    item.metadata['mbid'] = mbid
                    # Query MusicBrainz for detailed metadata
                    mb_data = musicbrainzngs.get_recording_by_id(mbid, includes=["artists", "releases", "isrcs"])
                    recording = mb_data.get('recording', {})
                    
                    # Update top level fields
                    item.track_name = recording.get('title')
                    item.metadata['title'] = recording.get('title') # Keep for compatibility
                    
                    if 'artist-credit' in recording:
                        artist_name = recording['artist-credit'][0].get('artist', {}).get('name')
                        item.artist_name = artist_name
                        item.metadata['artist'] = artist_name
                        
                    if 'release-list' in recording:
                        release = recording['release-list'][0]
                        item.album_name = release.get('title')
                        item.metadata['album'] = release.get('title')
                        item.metadata['release_date'] = release.get('date')
                    
                    # Store structured Music Metadata
                    item.metadata['music_metadata'] = {
                        'recording_title': item.track_name,
                        'artist': {'artist_name': item.artist_name},
                        'isrc': recording.get('isrc-list', [{}])[0].get('id') if 'isrc-list' in recording else None,
                        'release': {
                            'release_title': item.album_name,
                            # Note: release_date parsing would be needed for DATE type, keeping as string in legacy or parsing if strictly needed.
                            # BQ expects DATE type for release_date. Let's try to pass YYYY-MM-DD if valid.
                        }
                    }
                    
                    # Try to parse release date for structured field if exists
                    if 'release-list' in recording and recording['release-list'][0].get('date'):
                         # MB dates can be YYYY, YYYY-MM, or YYYY-MM-DD. 
                         # Simple pass-through might fail BQ DATE if not full. Leaving as None if not full date for safety or need robust parsing.
                         pass
                else:
                    # Fallback to AcoustID's own metadata if no MBID
                    if 'recordings' in best_match:
                        rec = best_match['recordings'][0]
                        item.metadata['title'] = rec.get('title')
                        if 'artists' in rec:
                            item.metadata['artist'] = rec['artists'][0].get('name')
                            
        except Exception as e:
            print(f"Song identification failed: {e}")

    def _parse_timestamp_from_uri(self, uri: str) -> Optional[datetime]:
        filename = os.path.basename(uri)
        match = re.search(r'(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})', filename)
        if match:
            dt_str = f"{match.group(1)} {match.group(2).replace('-', ':')}"
            return datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
        return None
