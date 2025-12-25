# =============================================================================
# Whisper STT Service Dockerfile
# =============================================================================
# GPU-enabled container for OpenAI Whisper speech-to-text
# =============================================================================

FROM nvidia/cuda:12.1-runtime-ubuntu22.04

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3.11 \
    python3.11-venv \
    python3-pip \
    git \
    curl \
    ffmpeg \
    libsndfile1 \
    && rm -rf /var/lib/apt/lists/*

# Set Python 3.11 as default
RUN update-alternatives --install /usr/bin/python python /usr/bin/python3.11 1 \
    && update-alternatives --install /usr/bin/python3 python3 /usr/bin/python3.11 1

# Create app directory
WORKDIR /app

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV=/opt/venv

# Upgrade pip
RUN pip install --upgrade pip wheel setuptools

# Install PyTorch with CUDA support
RUN pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu121

# Install OpenAI Whisper
RUN pip install openai-whisper

# Install FastAPI and dependencies for the wrapper
COPY requirements.txt /app/
RUN pip install -r requirements.txt

# Copy the FastAPI wrapper application
COPY app.py /app/

# Create directories for models and cache
RUN mkdir -p /models /root/.cache

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV CUDA_VISIBLE_DEVICES=0
ENV WHISPER_MODEL=base
ENV XDG_CACHE_HOME=/models

# Expose port
EXPOSE 8002

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
    CMD curl -f http://localhost:8002/v1/health || exit 1

# Start the service
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8002"]
