# -----------------------------
# Base image
# -----------------------------
FROM python:3.10-slim

# -----------------------------
# System dependencies
# -----------------------------
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libchromaprint-tools \
    build-essential \
    curl \
    ca-certificates \
 && rm -rf /var/lib/apt/lists/*


# -----------------------------
# Environment
# -----------------------------
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# -----------------------------
# Working directory
# -----------------------------
WORKDIR /app

# -----------------------------
# Python dependencies
# -----------------------------
COPY requirements.txt .
COPY credentials.json .

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# --------------------------------------------------
# Application code
# --------------------------------------------------
COPY job.py .

# -----------------------------
# Cloud Run: non-root user
# -----------------------------
RUN useradd --create-home appuser
USER appuser

# -----------------------------
# Start FastAPI
# -----------------------------
CMD ["python", "job.py"]

