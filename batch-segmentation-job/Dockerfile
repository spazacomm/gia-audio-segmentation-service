# Dockerfile
FROM python:3.10-slim

# Install system dependencies required by inaSpeechSegmenter
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    wget \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements first for better caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY job.py .

# Create temp directory for processing
RUN mkdir -p /tmp/audio_processing

# Run as non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app /tmp/audio_processing
USER appuser

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the job
CMD ["python", "job.py"]