import argparse
import apache_beam as beam
from apache_beam.options.pipeline_options import PipelineOptions, GoogleCloudOptions, StandardOptions, SetupOptions
from apache_beam import window
from transforms.aggregation import AggregateSegments
from transforms.fingerprinting import FingerprintProcessor
from transforms.llm_enrichment import LLMEnrichmentProcessor
from models import AUDIO_SEGMENTS_SCHEMA, MEDIA_ITEMS_SCHEMA, AUDIO_LIBRARY_SCHEMA, AudioSegment, MediaItem, AudioLibraryItem
import os

def run(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--api_key_acoustid', required=False, help='AcoustID API Key')
    parser.add_argument('--api_key_gemini', required=False, help='Gemini API Key')
    parser.add_argument('--project', required=True)
    parser.add_argument('--dataset', default='media_monitoring')
    parser.add_argument('--date', required=True, help='Date to process (YYYY-MM-DD)')
    
    known_args, pipeline_args = parser.parse_known_args(argv)
    
    options = PipelineOptions(pipeline_args)
    options.view_as(GoogleCloudOptions).project = known_args.project
    options.view_as(SetupOptions).save_main_session = True
    
    import logging
    logging.info(f"Starting Radio Monitoring Pipeline for date: {known_args.date}")
    logging.info(f"Project: {known_args.project}, Dataset: {known_args.dataset}")

    with beam.Pipeline(options=options) as p:
        
        # 0. Load Library Side Input
        library = (
            p 
            | "ReadLibrary" >> beam.io.ReadFromBigQuery(
                query=f"SELECT fingerprint_id as fingerprint, transcript, tags, metadata FROM `{known_args.project}.{known_args.dataset}.audio_library`",
                use_standard_sql=True
            )
            | "MapLibrary" >> beam.Map(lambda x: (x.pop('fingerprint'), x))
        )
        library_side_input = beam.pvalue.AsDict(library)

        # 1. Ingestion from BigQuery (Pre-segmented audio)
        def map_to_audio_segment(record):
            # Extract country_code from parent_file_uri if missing
            if record.get('country_code') is None and record.get('parent_file_uri'):
                from utils.gcs_utils import ParseGCSFileMetadata
                metadata = ParseGCSFileMetadata.parse_path(record['parent_file_uri'])
                record['country_code'] = metadata.get('country_code')
            
            # Filter fields to match AudioSegment dataclass
            valid_fields = {k: v for k, v in record.items() if k in AudioSegment.__dataclass_fields__}
            return AudioSegment(**valid_fields)

        segments = (
            p 
            | "ReadSegmentsFromBQ" >> beam.io.ReadFromBigQuery(
                query=f"SELECT * FROM `{known_args.project}.{known_args.dataset}.audio_segments` WHERE DATE(start_time) = '{known_args.date}'",
                use_standard_sql=True
            )
            | "MapToAudioSegment" >> beam.Map(map_to_audio_segment)
            | "AddSegmentTimestamps" >> beam.Map(lambda x: beam.window.TimestampedValue(x, x.start_time.timestamp() if x.start_time else 0))
        )
        
        # 3. Aggregation
        event_blocks = (
            segments
            | "KeyByStation" >> beam.Map(lambda x: ((x.station_name, x.country_code, x.platform_type), x))
            | "GroupSegments" >> beam.GroupByKey()
            | "AggregateBlocks" >> beam.ParDo(AggregateSegments())
        )
        
        # 4. Enrichment (Branched & Deduplicated)
        # Step A: Fingerprint & Lookup
        fingerprinted_items = (
            event_blocks
            | "FingerprintAndLookup" >> beam.ParDo(FingerprintProcessor(acoustid_api_key=known_args.api_key_acoustid), 
                                                library=library_side_input)
        )
        
        # Step B: LLM Enrichment for unknown assets
        final_results = (
            fingerprinted_items
            | "LLMEnrichment" >> beam.ParDo(LLMEnrichmentProcessor(api_key=known_args.api_key_gemini))
        )
        
        # 5. Sinks
        # Media Items
        (
            final_results
            | "FilterMediaItems" >> beam.Filter(lambda x: isinstance(x, MediaItem))
            | "ToDictItems" >> beam.Map(lambda x: x.__dict__)
            | "WriteItemsToBQ" >> beam.io.WriteToBigQuery(
                table='media_items',
                dataset=known_args.dataset,
                project=known_args.project,
                schema=MEDIA_ITEMS_SCHEMA,
                create_disposition=beam.io.BigQueryDisposition.CREATE_IF_NEEDED,
                write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND
            )
        )

        # Audio Library (Archives new assets/songs)
        (
            final_results
            | "FilterLibItems" >> beam.Filter(lambda x: isinstance(x, AudioLibraryItem))
            | "ToDictLibItems" >> beam.Map(lambda x: x.__dict__)
            | "WriteLibToBQ" >> beam.io.WriteToBigQuery(
                table='audio_library',
                dataset=known_args.dataset,
                project=known_args.project,
                schema=AUDIO_LIBRARY_SCHEMA,
                create_disposition=beam.io.BigQueryDisposition.CREATE_IF_NEEDED,
                write_disposition=beam.io.BigQueryDisposition.WRITE_APPEND
            )
        )

if __name__ == '__main__':
    import logging
    logging.getLogger().setLevel(logging.INFO)
    run()
