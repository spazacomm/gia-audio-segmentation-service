# Broadcast Audio Segmentation Service

A FastAPI service for segmenting broadcast audio recordings using inaSpeechSegmenter and storing results in Supabase.

## Features

- **Audio Segmentation**: Uses inaSpeechSegmenter to identify speech, music, and noise segments
- **Google Cloud Storage Integration**: Automatically loads audio chunks from GCS
- **Supabase Database**: Stores timeline and segment data following your broadcast schema
- **Background Processing**: Handles long-running segmentation tasks asynchronously
- **REST API**: Simple HTTP endpoints for triggering and monitoring segmentation
- **Scalable**: Docker containerization for easy deployment

## Schema Integration

The service integrates with your existing broadcast database schema:

- **`broadcast_timeline`**: Creates and updates broadcast records
- **`timeline_labels`**: Stores segmented audio with classifications
- **`audio_library`**: Ready for future fingerprint matching integration

## Quick Start

### 1. Environment Setup

```bash
# Copy environment template
cp .env.example .env

# Edit .env with your credentials
# - GCS_BUCKET: Your Google Cloud Storage bucket
# - SUPABASE_URL: Your Supabase project URL
# - SUPABASE_KEY: Your Supabase anonymous key
```

### 2. Installation

```bash
# Install dependencies
pip install -r requirements.txt

# Or using Docker
docker-compose up -d
```

### 3. Run Service

```bash
# Development
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Production
uvicorn main:app --host 0.0.0.0 --port 8000 --workers 4
```

## API Endpoints

### Start Segmentation

```bash
POST /segment
Content-Type: application/json

{
  "source_id": "uuid-of-capital-fm-source",
  "gcs_prefix": "kenya/radio/capital-fm/2025-12-16/"
}
```

**Response:**
```json
{
  "timeline_id": 0,
  "segments_processed": 0,
  "message": "Segmentation started for audio files in kenya/radio/capital-fm/2025-12-16/"
}
```

**Note:** The service automatically detects broadcast datetime from audio file paths and creates separate timelines for each audio file.

### Get Timeline Status

```bash
GET /timeline/{timeline_id}
```

**Response:**
```json
{
  "id": 123,
  "broadcast_datetime": "2025-12-16T08:00:00",
  "status": "completed",
  "segments": [
    {
      "label": "male",
      "start_time": 0.0,
      "end_time": 45.2,
      "confidence": 1.0
    },
    {
      "label": "music",
      "start_time": 45.2,
      "end_time": 120.8,
      "confidence": 1.0
    }
  ]
}
```

### List Broadcasts

```bash
GET /broadcasts?source_id=uuid&limit=50&offset=0
```

### Get Source Broadcasts

```bash
GET /broadcasts/{source_id}?limit=50
```

### Get Timeline Segments

```bash
GET /segments/{timeline_id}
```

### Manual Cleanup

```bash
POST /cleanup
```

### List Temporary Files

```bash
GET /temp-files
```

### System Monitoring

```bash
# Get system metrics (CPU, memory, disk)
GET /metrics/system

# Get job processing metrics
GET /metrics/jobs

# Get comprehensive dashboard data
GET /metrics/dashboard

# Detailed health check with component status
GET /health/detailed
```

### Job Monitoring

```bash
# List all processing jobs
GET /jobs

# Get specific job status
GET /jobs/{job_id}

# Real-time monitoring dashboard
GET /dashboard
```

### Health Check

```bash
GET /health
```

## Usage Example

### Trigger Segmentation

```python
import requests

# Start segmentation for all audio files in the GCS prefix
response = requests.post('http://localhost:8000/segment', json={
    "source_id": "your-source-uuid",
    "gcs_prefix": "kenya/radio/capital-fm/2025-12-16/"
})

print(response.json())
# {'timeline_id': 0, 'segments_processed': 0, 'message': 'Segmentation started...'}
```

### Monitor Progress

```python
import time

# Check processing status for all broadcasts
while True:
    response = requests.get('http://localhost:8000/broadcasts')
    data = response.json()
    
    processing_count = sum(1 for b in data['broadcasts'] if b['status'] == 'processing')
    completed_count = sum(1 for b in data['broadcasts'] if b['status'] == 'completed')
    
    print(f"Processing: {processing_count}, Completed: {completed_count}")
    
    if processing_count == 0:  # All done
        break
    
    time.sleep(30)  # Check every 30 seconds
```

### Get Results for Specific Source

```python
# Get all broadcasts for a specific radio station
source_id = "your-source-uuid"
response = requests.get(f'http://localhost:8000/broadcasts/{source_id}')
broadcasts = response.json()['broadcasts']

# Get segments for a specific broadcast
timeline_id = broadcasts[0]['id']
response = requests.get(f'http://localhost:8000/segments/{timeline_id}')
segments = response.json()['segments']

print(f"Broadcast: {broadcasts[0]['broadcast_datetime']}")
print(f"Segments: {len(segments)}")
for segment in segments:
    print(f"  {segment['label']}: {segment['start_time']:.1f}s - {segment['end_time']:.1f}s")
```

### Cleanup Management

```python
# Manual cleanup of temporary files
response = requests.post('http://localhost:8000/cleanup')
print(f"Cleanup status: {response.json()['status']}")

# List temporary files for debugging
response = requests.get('http://localhost:8000/temp-files')
temp_files = response.json()
print(f"Found {temp_files['count']} temporary files")
for file_info in temp_files['temp_files']:
    print(f"  {file_info['name']}: {file_info['size']} bytes")
```

## Audio Processing Flow

1. **File Discovery**: Finds all audio files in GCS under the specified prefix
2. **Datetime Extraction**: Automatically extracts broadcast datetime from file paths
3. **Individual Processing**: Processes each audio file separately
4. **Segmentation**: Uses inaSpeechSegmenter to identify:
   - `male` - Male speech segments
   - `female` - Female speech segments  
   - `music` - Musical segments
   - `noise` - Noise/ambient segments
5. **Timeline Creation**: Creates separate timeline for each audio file
6. **Database Storage**: Saves segments to `timeline_labels` table
7. **Status Update**: Updates `broadcast_timeline` status for each file

## Supported Audio Formats

- **Input**: WAV, MP3, M4A, FLAC
- **Output**: Each audio file becomes a separate timeline with segment labels

## Temporary File Management

The service automatically manages temporary files to prevent disk space issues:

### Automatic Cleanup
- **During Processing**: Temporary files are deleted immediately after each audio file is processed
- **On Service Shutdown**: All remaining temporary files are cleaned up automatically
- **Orphaned File Detection**: Service can detect and clean up files from previous runs

### Manual Cleanup
- **Manual Endpoint**: `POST /cleanup` - Force cleanup of temporary files
- **Debug Endpoint**: `GET /temp-files` - List current temporary files for debugging

### Configuration
```bash
# Environment variables for cleanup
TEMP_DIR=/tmp/segmentation          # Custom temp directory
MAX_TEMP_FILES=10                   # Maximum temp files to keep
CLEANUP_ON_SHUTDOWN=true           # Enable automatic cleanup
AUTO_CLEANUP_INTERVAL=3600         # Cleanup interval in seconds
DEBUG_CLEANUP=false                # Enable debug cleanup logging
```

### Cleanup Safety
- **Safe Deletion**: Only files matching service patterns are deleted
- **Error Handling**: Cleanup failures are logged but don't crash the service
- **Permission Checks**: Service checks file permissions before deletion
- **Logging**: All cleanup operations are logged for audit trails

## Database Schema Mapping

The service uses these tables from your schema:

### `broadcast_timeline`
- Creates new records or updates existing ones
- Tracks processing status (`pending`, `processing`, `completed`, `failed`)
- Sets `segmentation_processed = true` on completion

### `timeline_labels`
- Stores each audio segment with:
  - `label`: Classification (male, female, music, noise)
  - `start_time`, `end_time`: Segment boundaries in seconds (relative to audio file)
  - `segmentation_confidence`: Confidence score
  - `media_url`: GCS path to the original audio file
  - `tags`: Array with segment classification
  - Processing flags for future pipeline stages

## Configuration

### Environment Variables

| Variable | Description | Required |
|----------|-------------|----------|
| `GCS_BUCKET` | Google Cloud Storage bucket name | Yes |
| `SUPABASE_URL` | Supabase project URL | Yes |
| `SUPABASE_KEY` | Supabase anonymous key | Yes |
| `FASTAPI_PORT` | Service port (default: 8000) | No |
| `LOG_LEVEL` | Logging level (default: INFO) | No |

### Google Cloud Setup

1. Create a service account with Storage permissions
2. Download the JSON key file
3. Set `GOOGLE_APPLICATION_CREDENTIALS` environment variable to the key file path

### Supabase Setup

1. Use your existing Supabase project
2. Ensure your database schema matches the provided schema
3. Get your project URL and anonymous key from Supabase dashboard

## Production Deployment

### Docker Deployment

```bash
# Build and run
docker-compose up -d

# Scale workers
docker-compose up -d --scale segmentation-service=3
```

### Cloud Deployment

The service can be deployed to:
- **Google Cloud Run**: Containerized deployment with automatic scaling
- **AWS ECS/Fargate**: Container orchestration
- **Azure Container Instances**: Simple container deployment
- **Kubernetes**: Full orchestration with scaling and monitoring

### Monitoring

- **Health checks**: `/health` endpoint for load balancer health checks
- **Logging**: Structured logging to stdout for container platforms
- **Metrics**: Ready for Prometheus integration

## Next Steps

The service is designed to be extended with:

1. **Fingerprint Processing**: Integrate with `audio_library` for content matching
2. **Transcription**: Add Whisper integration for speech-to-text
3. **Vector Embeddings**: Generate embeddings for similarity search
4. **Batch Processing**: Process multiple broadcasts simultaneously
5. **Real-time Processing**: Stream processing for live broadcasts

## Troubleshooting

### Common Issues

1. **GCS Permission Errors**: Check service account permissions
2. **Supabase Connection**: Verify URL and key are correct
3. **Audio Processing**: Ensure FFmpeg is installed for format conversion
4. **Memory Usage**: Large audio files may require increased container memory

### Debug Mode

```bash
# Enable debug logging
export LOG_LEVEL=DEBUG
uvicorn main:app --reload --log-level debug
```

## License

This service is designed for your broadcast audio analysis pipeline.