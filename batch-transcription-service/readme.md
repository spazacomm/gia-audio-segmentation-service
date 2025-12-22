# Whisper Audio Transcription Service

CPU-optimized transcription service using OpenAI Whisper for speech-to-text processing.

## Prerequisites

- Docker and Docker Compose
- Google Cloud Platform credentials (for GCS access)
- Supabase account and credentials

## Quick Start

### 1. Clone and Setup

```bash
# Create project directory
mkdir whisper-transcription && cd whisper-transcription

# Copy all files (Dockerfile, docker-compose.yml, requirements.txt, transcription_service.py)
```

### 2. Configure Environment

```bash
# Copy example environment file
cp .env.example .env

# Edit .env with your credentials
nano .env
```

Required variables:
- `SUPABASE_URL`: Your Supabase project URL
- `SUPABASE_KEY`: Your Supabase anon/service key

### 3. Add GCP Credentials

```bash
# Place your GCP service account key in the project directory
cp /path/to/your/gcp-key.json ./gcp-credentials.json
```

### 4. Build and Run

```bash
# Build the Docker image
docker-compose build

# Run the service
docker-compose up -d

# View logs
docker-compose logs -f
```

## Whisper Model Selection

Choose based on your CPU performance:

| Model  | Size | RAM   | Speed     | Accuracy |
|--------|------|-------|-----------|----------|
| tiny   | 39M  | ~1GB  | Fastest   | Good     |
| base   | 74M  | ~1.5GB| Fast      | Better   |
| small  | 244M | ~2.5GB| Medium    | Great    |
| medium | 769M | ~5GB  | Slow      | Excellent|
| large  | 1550M| ~10GB | Very Slow | Best     |

**Recommended for CPU: `base` model**

## Configuration Options

### Environment Variables

```bash
# Processing
BATCH_SIZE=50              # Number of labels to process per run
MAX_CONCURRENT=2           # Parallel workers (1-2 for CPU)

# Whisper
WHISPER_MODEL=base         # Model size
WHISPER_LANGUAGE=en        # Language code or empty for auto-detect
WHISPER_DEVICE=cpu         # Processing device

# Filtering
SOURCE_ID=                 # Optional: process specific source only
```

### Supported Languages

Common language codes:
- `en` - English
- `sw` - Swahili
- `fr` - French
- `es` - Spanish
- Leave empty for auto-detection

## Running Options

### One-time Run

```bash
docker-compose up
```

### Continuous Processing

```bash
# Run as daemon
docker-compose up -d

# Schedule with cron
# Add to crontab:
# */15 * * * * cd /path/to/project && docker-compose up
```

### Process Specific Source

```bash
docker-compose run -e SOURCE_ID=your-source-id transcription-service
```

## Resource Requirements

### Minimum (tiny model)
- CPU: 2 cores
- RAM: 2GB
- Storage: 5GB

### Recommended (base model)
- CPU: 4 cores
- RAM: 4GB
- Storage: 10GB

### Optimal (small model)
- CPU: 8 cores
- RAM: 8GB
- Storage: 15GB

## Monitoring

### View Logs

```bash
# Real-time logs
docker-compose logs -f

# Last 100 lines
docker-compose logs --tail=100
```

### Check Progress

The service logs show:
- Labels being processed
- Transcription results
- Success/failure counts
- Processing speed

### Database Verification

```sql
-- Check transcribed labels
SELECT 
    id,
    label,
    transcription,
    transcription_confidence,
    transcription_language
FROM timeline_labels
WHERE transcription_processed = true
ORDER BY transcription_processed_at DESC
LIMIT 10;

-- Count pending labels
SELECT COUNT(*) 
FROM timeline_labels 
WHERE transcription_processed = false 
AND label IN ('female', 'male');
```

## Troubleshooting

### Out of Memory

```bash
# Use smaller model
WHISPER_MODEL=tiny docker-compose up

# Reduce concurrency
MAX_CONCURRENT=1 docker-compose up

# Reduce batch size
BATCH_SIZE=25 docker-compose up
```

### Slow Performance

```bash
# Use faster model
WHISPER_MODEL=tiny

# Increase concurrency (if you have CPU cores)
MAX_CONCURRENT=4

# Process smaller batches more frequently
BATCH_SIZE=20
```

### Permission Errors

```bash
# Verify GCP credentials
docker-compose run transcription-service python -c "from google.cloud import storage; storage.Client()"

# Check file permissions
chmod 600 gcp-credentials.json
```

## Performance Tips

1. **Model Selection**: Start with `base`, upgrade if accuracy is insufficient
2. **Batch Processing**: Process during off-peak hours
3. **Caching**: The service caches downloaded audio files within a batch
4. **Concurrency**: Keep at 1-2 for CPU, higher only if you have 8+ cores
5. **Language**: Specify language if known (faster than auto-detect)

## Development

### Local Development (without Docker)

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # or `venv\Scripts\activate` on Windows

# Install dependencies
pip install -r requirements.txt

# Set environment variables
export SUPABASE_URL=your-url
export SUPABASE_KEY=your-key
export GOOGLE_APPLICATION_CREDENTIALS=./gcp-credentials.json

# Run
python transcription_service.py
```

### Testing

```bash
# Test with single label
SOURCE_ID=test-source BATCH_SIZE=1 python transcription_service.py

# Test with tiny model (fastest)
WHISPER_MODEL=tiny python transcription_service.py
```

## Production Deployment

### Cloud Run / Kubernetes

```dockerfile
# Use the same Dockerfile
# Set appropriate resource limits
resources:
  limits:
    cpu: "4"
    memory: "8Gi"
```

### Scheduled Jobs (Cron)

```yaml
# Kubernetes CronJob example
apiVersion: batch/v1
kind: CronJob
metadata:
  name: whisper-transcription
spec:
  schedule: "*/30 * * * *"  # Every 30 minutes
  jobTemplate:
    spec:
      template:
        spec:
          containers:
          - name: transcription
            image: your-registry/whisper-transcription:latest
            env:
            - name: BATCH_SIZE
              value: "50"
```

## Support

For issues or questions:
1. Check logs: `docker-compose logs -f`
2. Verify environment variables
3. Test with smaller batch/model
4. Review database schema compatibility