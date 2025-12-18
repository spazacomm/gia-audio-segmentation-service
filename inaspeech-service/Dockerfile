FROM python:3.9-slim

WORKDIR /app

# Install system dependencies for audio processing
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python packages
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application
COPY inaspeech_service.py .

EXPOSE 8000

CMD ["uvicorn", "inaspeech_service:app", "--host", "0.0.0.0", "--port", "8000"]