# =============================================================================
# Parlant API Dockerfile
# =============================================================================
# Production-ready container for Parlant agent framework
# =============================================================================

FROM python:3.11-slim-bookworm AS builder

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Install uv package manager
RUN curl -LsSf https://astral.sh/uv/install.sh | sh
ENV PATH="/root/.local/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy project files
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Create virtual environment and install dependencies
RUN uv venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Install Parlant with common optional dependencies
# Increase timeout for large packages like NVIDIA CUDA
ENV UV_HTTP_TIMEOUT=300
RUN uv pip install ".[anthropic,mongo,chroma]"

# =============================================================================
# Production Stage
# =============================================================================
FROM python:3.11-slim-bookworm

# Install runtime dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN useradd --create-home --shell /bin/bash parlant

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Set working directory
WORKDIR /app

# Copy application source
COPY --from=builder /app/src ./src

# Create data directory
RUN mkdir -p /data && chown parlant:parlant /data

# Switch to non-root user
USER parlant

# Environment variables
ENV PARLANT_HOME=/data
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Expose port
EXPOSE 8800

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8800/healthz || exit 1

# Start Parlant server (use --anthropic flag for Anthropic API)
# Override with docker-compose command if using different LLM provider
CMD ["parlant-server", "run", "--anthropic"]
