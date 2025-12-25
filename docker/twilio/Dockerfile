# =============================================================================
# Twilio Integration Service Dockerfile
# =============================================================================
# Handles TwiML webhooks and Media Streams for voice calls
# =============================================================================

FROM python:3.11-slim-bookworm

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

# Create app directory
WORKDIR /app

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
ENV VIRTUAL_ENV=/opt/venv

# Upgrade pip
RUN pip install --upgrade pip wheel setuptools

# Copy and install dependencies
COPY requirements.txt /app/
RUN pip install -r requirements.txt

# Copy application code
COPY app.py /app/

# Create non-root user
RUN useradd --create-home --shell /bin/bash twilio
RUN chown -R twilio:twilio /app
USER twilio

# Environment variables
ENV PYTHONUNBUFFERED=1

# Expose port
EXPOSE 8003

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:8003/health || exit 1

# Start the service
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8003"]
