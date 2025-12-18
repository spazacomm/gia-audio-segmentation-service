# 1. Build and push to Artifact Registry
gcloud builds submit --tag gcr.io/spaza-media-monitor/audio-orchestrator

# 2. Create Cloud Run Job
gcloud run jobs create audio-orchestrator \
  --image gcr.io/spaza-media-monitor/audio-orchestrator \
  --region us-central1 \
  --project spaza-media-monitor \
  --set-env-vars PROJECT_ID=spaza-media-monitor,\
BUCKET=spaza-recordings,\
SUPABASE_URL=https://your-project.supabase.co,\
SUPABASE_KEY=your-supabase-anon-key,\
INASPEECH_SERVICE_URL=http://VM_EXTERNAL_IP:8000 \
  --task-timeout 7200s \
  --max-retries 1 \
  --memory 512Mi