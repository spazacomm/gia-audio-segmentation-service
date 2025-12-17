# Temporary File Cleanup Enhancements

## Overview

I've enhanced the FastAPI audio segmentation service with comprehensive temporary file management to ensure downloaded audio files are properly cleaned up from the local machine after processing is completed.

## Key Cleanup Features

### ✅ **Automatic Cleanup During Processing**
- **Per-File Cleanup**: Each audio file is deleted immediately after processing
- **Robust Error Handling**: Cleanup happens even if processing fails
- **Safe File Operations**: Uses proper file descriptors and error handling

### ✅ **Service Shutdown Cleanup**
- **atexit Handler**: Registers cleanup function to run on service shutdown
- **Orphaned File Detection**: Finds and cleans up files from previous runs
- **Pattern-Based Cleanup**: Only removes files matching service patterns

### ✅ **Manual Cleanup Endpoints**
- **`POST /cleanup`**: Force manual cleanup of temporary files
- **`GET /temp-files`**: List current temporary files for debugging

### ✅ **Enhanced Security**
- **Safe Deletion**: Only removes files with service-specific patterns
- **Permission Checks**: Verifies file permissions before deletion
- **Error Isolation**: Cleanup failures don't crash the service

## Technical Implementation

### New Cleanup Functions

```python
def safe_cleanup_file(file_path: str, description: str = "temporary file")
def safe_cleanup_directory(dir_path: str, description: str = "temporary directory")
def cleanup_temp_files()
```

### Enhanced Processing Loop

```python
# Create controlled temporary file with tracking
temp_fd, temp_path = tempfile.mkstemp(suffix='.wav', prefix=f'segmentation_{os.getpid()}_')

try:
    # Process audio file...
    segments = segment_audio_file(temp_path)
    
finally:
    # Comprehensive cleanup
    safe_cleanup_file(temp_path, f"temp audio file for {audio_file_path}")
    
    # Additional pattern-based cleanup
    cleanup_related_files(temp_path)
```

### New API Endpoints

1. **`POST /cleanup`**
   - Forces manual cleanup of all temporary files
   - Returns cleanup status and timestamp
   - Useful for scheduled cleanup or troubleshooting

2. **`GET /temp-files`**
   - Lists all temporary files matching service patterns
   - Shows file sizes, modification times, and types
   - Helpful for debugging and monitoring disk usage

## Configuration Options

### Environment Variables
```bash
# Temporary file settings
TEMP_DIR=/tmp/segmentation          # Custom temp directory
MAX_TEMP_FILES=10                   # Maximum temp files to keep
CLEANUP_ON_SHUTDOWN=true           # Enable automatic cleanup
AUTO_CLEANUP_INTERVAL=3600         # Cleanup interval (future feature)
DEBUG_CLEANUP=false                # Enable debug cleanup logging
```

### Service Integration
- **Process ID Tracking**: Uses PID in temp file names for identification
- **Pattern Matching**: Only cleans files with `segmentation_*` prefix
- **Safe Patterns**: Prevents accidental deletion of system files

## Cleanup Safety Measures

### ✅ **Selective Deletion**
- Only removes files matching service patterns
- Uses process-specific naming to avoid conflicts
- Includes error handling for permission issues

### ✅ **Comprehensive Coverage**
- Cleans up after successful processing
- Cleans up after failed processing
- Cleans up on service shutdown
- Detects and cleans orphaned files

### ✅ **Monitoring & Debugging**
- All cleanup operations are logged
- Manual endpoints for debugging
- File listing with detailed information
- Error reporting without service interruption

## Usage Examples

### Manual Cleanup
```bash
# Force cleanup of temporary files
curl -X POST "http://localhost:8000/cleanup"

# Response
{
  "status": "completed",
  "message": "Temporary files cleanup completed",
  "timestamp": "2025-12-17T18:10:26"
}
```

### Debug Temporary Files
```bash
# List current temporary files
curl "http://localhost:8000/temp-files"

# Response
{
  "temp_directory": "/tmp",
  "temp_files": [
    {
      "name": "segmentation_12345_audio_2025-12-16T08-00-00.wav",
      "path": "/tmp/segmentation_12345_audio_2025-12-16T08-00-00.wav",
      "size": 15728640,
      "modified": "2025-12-17T18:05:00",
      "type": "file"
    }
  ],
  "count": 1
}
```

### Python Integration
```python
import requests

# Check for temporary files
response = requests.get('http://localhost:8000/temp-files')
temp_data = response.json()

if temp_data['count'] > 0:
    print(f"Found {temp_data['count']} temporary files")
    
    # Run cleanup
    cleanup_response = requests.post('http://localhost:8000/cleanup')
    print(f"Cleanup status: {cleanup_response.json()['status']}")
```

## Benefits

### ✅ **Disk Space Management**
- Prevents accumulation of temporary files
- Automatic cleanup reduces manual intervention
- Configurable limits for disk usage

### ✅ **Security**
- Reduces risk of sensitive audio data lingering on disk
- Secure deletion patterns prevent accidental data loss
- Process isolation prevents file conflicts

### ✅ **Reliability**
- Cleanup works regardless of processing outcome
- Service shutdown cleanup ensures no orphaned files
- Error handling prevents cleanup failures from affecting service

### ✅ **Debugging & Monitoring**
- Visibility into temporary file status
- Manual cleanup for emergency situations
- Detailed logging for troubleshooting

## Testing

The cleanup functionality is tested through:

1. **Automatic Testing**: Test suite includes cleanup endpoint tests
2. **Manual Testing**: Interactive mode includes cleanup commands
3. **Example Scripts**: Usage examples demonstrate cleanup features

## Future Enhancements

- **Scheduled Cleanup**: Background task for periodic cleanup
- **Disk Space Monitoring**: Alerts when temp directory approaches limits
- **Cleanup Metrics**: Statistics on cleanup operations
- **Retention Policies**: Configurable file retention times

The enhanced cleanup system ensures your service maintains optimal disk usage while providing comprehensive visibility and control over temporary file management.