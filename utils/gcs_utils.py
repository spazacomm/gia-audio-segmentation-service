import os
import re
from datetime import datetime
import apache_beam as beam
from typing import Iterable, Tuple
import logging

class ParseGCSFileMetadata(beam.DoFn):
    """
    Parses metadata from GCS file path.
    Expected structure: country/platform/station/date/station_YYYY-MM-DD_HH-mm-ss.mp3
    Example: kenya/radio/capital-fm/2025-12-16/capital-fm_2025-12-16_10-20-11.mp3
    """
    @staticmethod
    def parse_path(file_path: str) -> dict:
        try:
            # Remove protocol and bucket if present
            path = file_path.replace('gs://', '')
            parts = path.split('/')
            
            if len(parts) < 5:
                return {}

            filename = parts[-1]
            date_str = parts[-2]
            station = parts[-3]
            platform = parts[-4]
            country = parts[-5]

            match = re.search(r'(\d{4}-\d{2}-\d{2})_(\d{2}-\d{2}-\d{2})', filename)
            if match:
                dt_str = f"{match.group(1)} {match.group(2).replace('-', ':')}"
                file_timestamp = datetime.strptime(dt_str, '%Y-%m-%d %H:%M:%S')
            else:
                file_timestamp = datetime.strptime(date_str, '%Y-%m-%d')

            return {
                'file_uri': file_path,
                'country_code': country,
                'platform': platform,
                'station': station,
                'file_timestamp': file_timestamp,
            }
        except Exception as e:
            logging.error(f"Error parsing metadata for {file_path}: {e}")
            return {}

    def process(self, file_path: str) -> Iterable[dict]:
        logging.info(f"Parsing metadata for file: {file_path}")
        metadata = self.parse_path(file_path)
        if metadata:
            yield metadata

def get_file_list(path: str):
    # This would typically be beam.io.fileio.MatchFiles
    pass
