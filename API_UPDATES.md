# API Design Updates - Simplified Approach

## Changes Made

I've updated the FastAPI service to use your preferred simplified API design:

### ✅ **Updated API Request Format**

**Before:**
```json
{
  "broadcast_date": "2025-12-16",
  "source_id": "uuid",
  "gcs_prefix": "kenya/radio/capital-fm/2025-12-16/",
  "force_reprocess": false
}
```

**After:**
```json
{
  "source_id": "uuid",
  "gcs_prefix": "kenya/radio/capital-fm/2025-12-16/"
}
```

### ✅ **Automatic Datetime Detection**

The service now automatically extracts broadcast datetime from audio file paths using pattern matching:

- `2025-12-16T08:00:00` → `2025-12-16 08:00:00`
- `20251216_080000` → `2025-12-16 08:00:00`
- `2025-12-16_08:00` → `2025-12-16 08:00:00`

### ✅ **Individual File Processing**

Instead of processing chunks as one timeline, each audio file becomes its own timeline:

```
GCS Directory: kenya/radio/capital-fm/2025-12-16/
├── audio_2025-12-16T08-00-00.wav  → Timeline #1
├── audio_2025-12-16T08-05-00.wav  → Timeline #2
└── audio_2025-12-16T08-10-00.wav  → Timeline #3
```

### ✅ **Enhanced API Endpoints**

Added new endpoints for better functionality:

1. **`POST /segment`** - Simplified request (no broadcast_date needed)
2. **`GET /broadcasts/{source_id}`** - Get broadcasts for specific source
3. **`GET /segments/{timeline_id}`** - Get all segments for a timeline
4. **`GET /broadcasts`** - Enhanced with pagination (limit, offset)

### ✅ **Updated Response Format**

**Segmentation Response:**
```json
{
  "timeline_id": 0,
  "segments_processed": 0,
  "message": "Segmentation started for audio files in kenya/radio/capital-fm/2025-12-16/"
}
```

**Note:** `timeline_id` is 0 because multiple timelines are created (one per audio file)

### ✅ **Updated Database Integration**

Each audio file creates:
- **1 broadcast_timeline record** - with extracted datetime and source_id
- **N timeline_labels records** - one for each audio segment found

### ✅ **Files Updated**

1. **`main.py`** - Core service logic updated
2. **`test_service.py`** - Test cases updated for new API
3. **`README.md`** - Documentation updated
4. **`IMPLEMENTATION_SUMMARY.md`** - Summary updated
5. **`example_usage.py`** - New example script added

## How It Works Now

1. **Send Request**: `POST /segment` with source_id and gcs_prefix
2. **Discover Files**: Service finds all audio files in GCS prefix
3. **Extract Datetime**: Parse broadcast time from each file path
4. **Process Files**: Each audio file → separate timeline → segment labels
5. **Store Results**: Save to broadcast_timeline + timeline_labels tables
6. **Monitor**: Use new endpoints to track progress by source or timeline

## Usage Example

```bash
# Start processing
curl -X POST "http://localhost:8000/segment" \
  -H "Content-Type: application/json" \
  -d '{"source_id": "capital-fm-uuid", "gcs_prefix": "kenya/radio/capital-fm/2025-12-16/"}'

# Check all broadcasts
curl "http://localhost:8000/broadcasts"

# Get broadcasts for specific radio station
curl "http://localhost:8000/broadcasts/capital-fm-uuid"

# Get segments for a specific timeline
curl "http://localhost:8000/segments/123"
```

## Benefits of New Design

- ✅ **Simpler API** - No need to specify broadcast date
- ✅ **Automatic Detection** - Datetime extracted from file paths
- ✅ **Flexible Processing** - Each audio file becomes its own timeline
- ✅ **Better Organization** - Clear separation between different broadcasts
- ✅ **Enhanced Monitoring** - New endpoints for better progress tracking
- ✅ **Scalable** - Can handle any number of audio files in a directory

The service is now ready to process your audio files exactly as you requested!