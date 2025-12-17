# Installation Troubleshooting Guide

## Common Installation Issues and Solutions

### Issue: inaSpeechSegmenter version not found

**Error:**
```
ERROR: No matching distribution found for inaSpeechSegmenter==2.2.0
```

**Solution:**
The package has been updated. Use the corrected requirements.txt file:

```bash
pip install -r requirements.txt
```

Or install the specific available version:

```bash
pip install inaSpeechSegmenter==0.8.0
```

### Issue: Permission denied for system-wide installation

**Error:**
```
Defaulting to user installation because normal site-packages is not writeable
```

**Solution:**
This is normal and expected. The packages will be installed in your user directory. Continue with the installation.

### Issue: Missing system dependencies for audio processing

**Error:**
```
ImportError: libsndfile.so.1: cannot open shared object file
```

**Solution:**
Install system dependencies for audio processing:

**Ubuntu/Debian:**
```bash
sudo apt update
sudo apt install libsndfile1 ffmpeg
```

**macOS:**
```bash
brew install libsndfile ffmpeg
```

**CentOS/RHEL:**
```bash
sudo yum install libsndfile ffmpeg
```

### Issue: Google Cloud credentials not found

**Error:**
```
google.auth.exceptions.DefaultCredentialsError: Could not automatically determine credentials
```

**Solution:**
1. Download your service account key file from Google Cloud Console
2. Set the environment variable:
```bash
export GOOGLE_APPLICATION_CREDENTIALS="path/to/your/service-account-key.json"
```

### Issue: Supabase connection errors

**Error:**
```
Connection refused to Supabase
```

**Solution:**
1. Check your Supabase URL and key in the .env file
2. Verify the Supabase project is active
3. Ensure network connectivity to Supabase

## Installation Steps

### 1. Install Python Dependencies
```bash
# Install all dependencies
pip install -r requirements.txt

# Alternative: use pinned versions if you have conflicts
pip install -r requirements-pinned.txt
```

### 2. Install System Dependencies
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install libsndfile1 ffmpeg

# Or install everything at once
sudo apt update && sudo apt install -y libsndfile1 ffmpeg
```

### 3. Configure Environment
```bash
# Copy environment template
cp .env.example .env

# Edit with your credentials
nano .env
```

### 4. Validate Installation
```bash
# Run configuration validation
python3 validate_config.py
```

### 5. Test Installation
```bash
# Start the service
python3 main.py

# Test in another terminal
python3 test_service.py
```

## Virtual Environment Setup (Recommended)

For better dependency management, use a virtual environment:

```bash
# Create virtual environment
python3 -m venv segmentation_env

# Activate virtual environment
source segmentation_env/bin/activate  # Linux/macOS
# or
segmentation_env\Scripts\activate     # Windows

# Install dependencies
pip install -r requirements.txt

# Run validation
python3 validate_config.py

# Start service
python3 main.py
```

## Dependency Verification

To verify all dependencies are installed correctly:

```python
# Test script to verify all imports
import sys

required_modules = [
    'fastapi',
    'uvicorn',
    'pydantic',
    'inaSpeechSegmenter',
    'librosa',
    'soundfile',
    'numpy',
    'google.cloud.storage',
    'supabase',
    'psutil',
    'prometheus_client'
]

print("Checking required modules...")
for module in required_modules:
    try:
        __import__(module)
        print(f"✅ {module}")
    except ImportError as e:
        print(f"❌ {module}: {e}")

print("\nIf all modules show ✅, installation is successful!")
```

## GPU Support (Optional)

For better performance with inaSpeechSegmenter, you can install GPU support:

```bash
# Install CUDA-enabled TensorFlow if needed
pip install tensorflow-gpu

# Or install PyTorch with CUDA support
pip install torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu118
```

## Docker Installation (Alternative)

If you prefer Docker:

```bash
# Build Docker image
docker build -t segmentation-service .

# Run with Docker
docker run -p 8000:8000 -v $(pwd)/.env:/app/.env segmentation-service
```

## Getting Help

If you continue to have issues:

1. Check the service logs: `python3 main.py` will show detailed error messages
2. Run validation: `python3 validate_config.py`
3. Test individual components: Check each external service connection
4. Review the README.md for additional configuration options

## Environment Setup Checklist

- [ ] Python 3.8+ installed
- [ ] All Python dependencies installed (`pip install -r requirements.txt`)
- [ ] System audio dependencies installed (`libsndfile1`, `ffmpeg`)
- [ ] .env file configured with credentials
- [ ] Google Cloud service account key downloaded
- [ ] Supabase project URL and key configured
- [ ] Configuration validation passed (`python3 validate_config.py`)
- [ ] Service starts successfully (`python3 main.py`)