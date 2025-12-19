# -----------------------------
# Base image
# -----------------------------
FROM python:3.10-slim

# -----------------------------
# System dependencies
# -----------------------------
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    chromaprint \
    libchromaprint1 \
    build-essential \
    curl \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# -----------------------------
# Environment
# -----------------------------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

# -----------------------------
# Working directory
# -----------------------------
WORKDIR /app

# -----------------------------
# Python dependencies
# -----------------------------
COPY requirements.txt .

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# -----------------------------
# Application code
# -----------------------------
COPY . .

# -----------------------------
# Cloud Run: non-root user
# -----------------------------
RUN useradd --create-home appuser
USER appuser

# -----------------------------
# Expose port (Cloud Run uses $PORT)
# -----------------------------
EXPOSE 8080

# -----------------------------
# Start FastAPI
# -----------------------------
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
