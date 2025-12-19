# Use Python 3.11 slim image for smaller size
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# Install system dependencies required for audio processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libchromaprint-tools \
    libchromaprint1 \
    libavcodec-extra \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements file
COPY requirements.txt .

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY job.py .

# Set environment variables for Cloud Run
ENV PYTHONUNBUFFERED=1
ENV BATCH_SIZE=5
ENV SIMILARITY_THRESHOLD=0.85
ENV MAX_LIBRARY_FETCH=1000

# Run the job
CMD ["python", "job.py"]