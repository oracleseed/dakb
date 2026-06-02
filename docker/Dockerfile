# DAKB Gateway Service Dockerfile
# ================================
#
# Multi-stage build for minimal production image
#
# Build:
#   docker build -t dakb-gateway -f docker/Dockerfile .
#
# Run:
#   docker run -p 3100:3100 --env-file docker/.env dakb-gateway

# ==============================================================================
# Stage 1: Builder
# ==============================================================================
FROM python:3.12-slim as builder

WORKDIR /build

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements first for caching
COPY requirements.txt .
RUN pip wheel --no-cache-dir --wheel-dir /wheels -r requirements.txt

# ==============================================================================
# Stage 2: Production
# ==============================================================================
FROM python:3.12-slim as production

# Security: Create non-root user
RUN groupadd -r dakb && useradd -r -g dakb dakb

WORKDIR /app

# Install runtime dependencies
#   curl     - health checks
#   libmagic1 - required by python-magic for vault MIME / executable detection
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

# Copy wheels from builder
COPY --from=builder /wheels /wheels
RUN pip install --no-cache-dir /wheels/* && rm -rf /wheels

# Copy application code.
# `COPY dakb/` brings in the full package tree, including the v2.0.0 subsystems:
#   - vault/         File Vault backends (local + s3) and validator
#   - bridge/        Session Bridge + Client SDK + WS handler
#   - chat_bridge/   Chat Bridge router + adapters (Telegram reference)
#   - integration/   alias_registration helpers
#   - agentic_core/  Agentic API envelope/registry/remediation
#   - admin/         Admin dashboard API + templates/*.html
#   - gateway/       Redis client, presence, notification bus, task router
COPY dakb/ /app/dakb/
COPY config/ /app/config/

# Create necessary directories (FAISS index, logs, and File Vault local store)
RUN mkdir -p /app/logs /app/data/dakb_faiss /app/data/vault /app/data/vault_holds \
    && chown -R dakb:dakb /app

# Switch to non-root user
USER dakb

# Environment defaults
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    DAKB_GATEWAY_HOST=0.0.0.0 \
    DAKB_GATEWAY_PORT=3100 \
    DAKB_CONTAINER=true

# Expose port
EXPOSE 3100

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -f http://localhost:3100/health || exit 1

# Run gateway
CMD ["python", "-m", "dakb.gateway"]
