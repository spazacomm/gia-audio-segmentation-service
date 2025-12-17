# Quick Start Guide - Optimized for Low Resources

## 🚀 **Instant Setup for 2 vCPU, 4GB RAM**

Your service has been optimized for CPU-only processing on low-resource systems. Here's how to get started immediately:

## ⚡ **1-Minute Setup**

### **Option A: Automated (Recommended)**
```bash
# Download and run optimized setup
chmod +x deploy_optimized.sh
./deploy_optimized.sh --start
```

### **Option B: Manual**
```bash
# Install optimized dependencies
pip install -r requirements.txt

# Copy optimized environment
cp .env.optimized .env

# Edit .env with your credentials
nano .env

# Start optimized service
uvicorn main:app --workers 1 --log-level warning
```

### **Option C: Docker**
```bash
# Use optimized Docker configuration
docker-compose -f docker-compose.optimized.yml up -d
```

## 🔧 **Configuration**

Edit `.env` file with your credentials:
```bash
# Google Cloud Storage
GCS_BUCKET=your-bucket-name
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account-key.json

# Supabase
SUPABASE_URL=your-supabase-url
SUPABASE_KEY=your-supabase-anon-key

# Optimized settings (already configured)
MAX_CONCURRENT_JOBS=1
MAX_MEMORY_MB=3072
LOG_LEVEL=WARNING
```

## ✅ **Verify Installation**

```bash
# Test the optimized service
python3 test_optimized.py

# Check health
curl http://localhost:8000/health

# View system metrics
curl http://localhost:8000/metrics/system
```

## 🎯 **Start Processing**

```bash
# Start audio segmentation
curl -X POST "http://localhost:8000/segment" \
  -H "Content-Type: application/json" \
  -d '{"source_id": "your-source-uuid", "gcs_prefix": "kenya/radio/capital-fm/2025-12-16/"}'

# Monitor progress
curl http://localhost:8000/jobs
```

## 📊 **Monitor Performance**

### **Web Dashboard**
```
http://localhost:8000/dashboard
```

### **API Monitoring**
```bash
# System health
curl http://localhost:8000/health

# Resource usage
curl http://localhost:8000/metrics/system

# Active jobs
curl http://localhost:8000/jobs

# Clean up temp files
curl -X POST http://localhost:8000/cleanup
```

## 🎛️ **Key Optimizations Active**

- ✅ **Single Job Processing**: Only 1 concurrent job
- ✅ **Memory Limit**: Max 3GB usage (reserves 1GB for system)
- ✅ **CPU-Only Libraries**: No GPU dependencies
- ✅ **File Size Limits**: Skips files > 100MB
- ✅ **Sequential Processing**: No parallel processing
- ✅ **Lightweight Monitoring**: Reduced overhead
- ✅ **Automatic Cleanup**: Temp files managed automatically

## 📈 **Expected Performance**

- **Processing Speed**: 2-5 minutes per 5-minute audio file
- **Memory Usage**: 1.5-3GB during processing
- **CPU Usage**: 50-80% utilization
- **Concurrent Jobs**: 1 (recommended maximum)

## 🆘 **Troubleshooting**

### **High Memory Usage**
```bash
# Check temp files
curl http://localhost:8000/temp-files

# Force cleanup
curl -X POST http://localhost:8000/cleanup
```

### **Service Not Starting**
```bash
# Check dependencies
python3 -c "import inaSpeechSegmenter, librosa, soundfile; print('OK')"

# Check environment
cat .env

# Run validation
python3 validate_config.py
```

### **Slow Processing**
```bash
# Check active jobs
curl http://localhost:8000/jobs

# Verify resource limits
# Ensure files are under 100MB
# Check CPU: top -p $(pgrep -f segmentation)
```

## 📚 **Documentation**

- **Complete Guide**: `README.md`
- **Optimization Details**: `OPTIMIZATION_GUIDE.md`
- **Installation Help**: `INSTALLATION_GUIDE.md`
- **Monitoring Guide**: `MONITORING_GUIDE.md`

## 🎉 **You're Ready!**

Your optimized audio segmentation service is now running on your 2 vCPU, 4GB RAM system. The service will:

- ✅ Process audio files efficiently on CPU-only hardware
- ✅ Stay within memory limits (3GB max)
- ✅ Handle one job at a time for stability
- ✅ Automatically clean up temporary files
- ✅ Provide real-time monitoring and health checks

**Next Steps:**
1. Configure your `.env` file with actual credentials
2. Start processing your audio files
3. Monitor via the web dashboard or API endpoints
4. Use cleanup endpoint regularly for optimal performance

Enjoy your optimized, low-resource audio segmentation service! 🎵