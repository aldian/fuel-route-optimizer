# Multi-stage production Dockerfile for Fuel Route Optimizer API
# Stage 1: Build & Dependencies
FROM python:3.12-slim-bookworm AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir --prefix=/install -r requirements.txt

# Stage 2: Runtime
FROM python:3.12-slim-bookworm AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DJANGO_SETTINGS_MODULE=config.settings.production \
    PORT=8000

WORKDIR /app

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# Create non-root system user
RUN groupadd -g 10001 appuser && \
    useradd -u 10001 -g appuser -s /bin/bash -m appuser

# Copy application source code and pre-geocoded data fixtures
COPY --chown=appuser:appuser . .

# Make entrypoint and CLI wrappers executable
RUN chmod +x docker/entrypoint.sh bin/spotter-*

# Collect static assets (using build-time placeholder secret key; runtime key is injected via env)
USER appuser
RUN DJANGO_SECRET_KEY="build-time-collectstatic-placeholder-key-not-used-at-runtime" \
    ALLOWED_HOSTS="localhost,127.0.0.1" \
    python manage.py collectstatic --noinput

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health/', timeout=3)" || exit 1

ENTRYPOINT ["docker/entrypoint.sh"]
