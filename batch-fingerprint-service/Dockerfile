# Use Python 3.11 slim image for smaller size
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies
# - ffmpeg: Required by pydub for audio processing
# - libchromaprint-tools: Chromaprint library for audio fingerprinting
# - libffi-dev, libssl-dev: Required for cryptography packages
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libchromaprint-tools \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for better layer caching
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY job.py .

# Set environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Run as non-root user for security
RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

# Run the job
CMD ["python", "job.py"]