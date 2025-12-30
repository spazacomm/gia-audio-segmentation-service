#!/bin/bash

# Configuration
PROJECT_ID="spaza-media-monitor"
REGION="us-central1"
BUCKET="spaza-recordings"
DATASET="media_monitoring"
DATE="2025-12-16"
# Job Configuration
RUN_MODE="batch" # batch or streaming
JOB_NAME="gia-v2-radio-monitoring-$RUN_MODE-$DATE"
GEMINI_API_KEY="AQ.Ab8RN6KLQNFSE7N2heHhaLkU1rdprkLQlp18xV14GLcV5Trfkw"
ACOUSTID_API_KEY="bKxcrQp3H2"

# Artifact Registry Config
ARTIFACT_REGISTRY_LOCATION="us-central1"
REPOSITORY_NAME="gia-v2-radio-monitoring"
IMAGE_NAME="gia-v2-radio-pipeline-sdk"
IMAGE_URI="$ARTIFACT_REGISTRY_LOCATION-docker.pkg.dev/$PROJECT_ID/$REPOSITORY_NAME/$IMAGE_NAME:latest"

# 1. Create Artifact Registry if it doesn't exist
gcloud artifacts repositories describe $REPOSITORY_NAME --location=$ARTIFACT_REGISTRY_LOCATION > /dev/null 2>&1
if [ $? -ne 0 ]; then
    echo "Creating Artifact Registry repository: $REPOSITORY_NAME"
    gcloud artifacts repositories create $REPOSITORY_NAME \
        --repository-format=docker \
        --location=$ARTIFACT_REGISTRY_LOCATION \
        --description="Docker repository for Radio Monitoring Pipeline"
fi

# 2. Trigger Headless Cloud Build (Build + Dataflow Job)
echo "Submitting headless build and deploy to Google Cloud (Mode: $RUN_MODE)..."
gcloud builds submit --config cloudbuild.yaml \
    --substitutions="_PROJECT_ID=$PROJECT_ID,_IMAGE_URI=$IMAGE_URI,_REGION=$REGION,_BUCKET=$BUCKET,_DATASET=$DATASET,_DATE=$DATE,_JOB_NAME=$JOB_NAME,_GEMINI_API_KEY=$GEMINI_API_KEY,_ACOUSTID_API_KEY=$ACOUSTID_API_KEY,_RUN_MODE=$RUN_MODE" \
    .

# Note: The machine_type and other scaling flags are now passed via cloudbuild.yaml 
# if you want them to be part of the job submission.
