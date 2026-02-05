# =============================================================================
# Wizelit Chainlit Hub - Dockerfile
# Single-stage build optimized for Python/Chainlit
# =============================================================================

FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user for security
RUN groupadd -r wizelit && useradd -r -g wizelit wizelit

# Copy dependency files first (for better caching)
COPY pyproject.toml ./

# Install Python dependencies (including wizelit-sdk from GitHub)
RUN pip install --upgrade pip && pip install .

# Copy application code (respects .dockerignore)
COPY . .

# Create necessary directories
RUN mkdir -p /app/data && \
    chown -R wizelit:wizelit /app

# Switch to non-root user
USER wizelit

# Expose Chainlit default port
EXPOSE 8000

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

# Run Chainlit
CMD ["chainlit", "run", "main.py", "--host", "0.0.0.0", "--port", "8000"]
