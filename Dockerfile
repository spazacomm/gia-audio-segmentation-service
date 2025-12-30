FROM apache/beam_python3.11_sdk:latest

# Install system dependencies
RUN apt-get update && apt-get install -y \
    ffmpeg \
    libchromaprint-dev \
    && rm -rf /var/lib/apt/lists/*

# Set working directory
WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt


# Copy the rest of the application
COPY . .

# Set the entrypoint for the SDK container (Standard for Beam SDK images)
ENTRYPOINT ["/opt/apache/beam/boot"]
