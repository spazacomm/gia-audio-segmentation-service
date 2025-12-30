# GIA V2 Radio Monitoring Pipeline

This pipeline processes radio broadcasts, segments audio, identifies content using AcoustID, and enriches metadata using Gemini AI. It uses a **completely headless deployment flow**, meaning all builds and job submissions happen in the cloud, requiring zero local dependencies (no `apache-beam` or `ffmpeg` needed locally).

## Prerequisites

- **Google Cloud Project**: An active GCP project with billing enabled.
- **Enabled APIs**:
  - Cloud Dataflow API
  - BigQuery API
  - Cloud Storage API
  - Cloud Build API
  - Artifact Registry API
- **Cloud Storage Bucket**: A bucket for temporary and staging files.
- **BigQuery Dataset**: A dataset to store the output tables.
- **API Keys**:
  - [AcoustID API Key](https://acoustid.org/applications)
  - [Gemini API Key](https://aistudio.google.com/app/apikey)

## Configuration

Update the variables in `deploy.sh` to match your environment:

| Variable | Description |
| :--- | :--- |
| `PROJECT_ID` | Your Google Cloud Project ID. |
| `REGION` | Compute region for Dataflow (e.g., `us-central1`). |
| `BUCKET` | GCS bucket for temp/staging files (e.g., `my-buffer-bucket`). |
| `DATASET` | BigQuery dataset name. |
| `ARTIFACT_REGISTRY_LOCATION` | Region for your Docker repository. |
| `REPOSITORY_NAME` | Name of the repository (default: `gia-v2-radio-monitoring`). |

## Headless Deployment

The deployment process uses **Google Cloud Build** to handle everything: building the container, pushing it to **Artifact Registry**, and launching the **Dataflow** job.

To deploy, simply run:

```bash
chmod +x deploy.sh
./deploy.sh
```

The script will automatically:
1.  Check (and create if necessary) the Artifact Registry repository.
2.  Submit the code to **Cloud Build** for remote building and job submission.

## Pipeline Architecture

1.  **Ingestion**: Monitors a GCS path for new audio files.
2.  **Segmentation**: Splits long audio files into smaller segments using `ffmpeg` and `inaSpeechSegmenter`.
3.  **Fingerprinting**: Uses AcoustID to identify known songs or segments.
4.  **LLM Enrichment**: Uses Gemini AI to extract metadata (transcript, tags, topics) for unknown segments.
5.  **Storage**: Saves raw segments, identified media items, and an audio library to BigQuery.

## Monitoring

You can monitor the job status and logs through the [Google Cloud Console](https://console.cloud.google.com/dataflow/jobs).
