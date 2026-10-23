# ═══════════════════════════════════════════════════════════════════
# Enterprise IT Support Copilot — Hardened Production Dockerfile
# ═══════════════════════════════════════════════════════════════════
# Security Standards:
#   1. Multi-stage build (minimizes attack surface & image size)
#   2. Non-root user execution (UID/GID 10001)
#   3. Minimal runtime dependencies (no compiler or build tools in final image)
#   4. Strict file permissions on application directories
#   5. Native container HEALTHCHECK
#   6. Proper UNIX SIGTERM signal handling (no shell wrapping)
# ═══════════════════════════════════════════════════════════════════

# ── Stage 1: Build Environment ────────────────────────────────────
FROM python:3.11-slim-bookworm AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Install essential build tools only for compilation
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .

# Install dependencies into a separate virtual environment
RUN python -m venv /opt/venv && \
    /opt/venv/bin/pip install --upgrade pip setuptools wheel && \
    /opt/venv/bin/pip install -r requirements.txt


# ── Stage 2: Hardened Production Runtime ──────────────────────────
FROM python:3.11-slim-bookworm AS runtime

WORKDIR /app

# Security & runtime environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PORT=8080 \
    APP_ENV=production

# Install only minimal runtime system packages & curl for healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    libpq5 \
    ca-certificates \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Create dedicated non-privileged user and group
RUN groupadd --system --gid 10001 appgroup && \
    useradd --system --uid 10001 --gid appgroup --no-create-home --shell /usr/sbin/nologin appuser

# Copy virtual environment from builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application files
COPY . .

# Create persistent runtime directories and set strict non-root ownership
RUN mkdir -p /app/uploads /app/data && \
    chown -R appuser:appgroup /app && \
    chmod -R 750 /app && \
    chmod 770 /app/uploads /app/data

# Switch to non-privileged user
USER appuser

# Expose internal HTTP port
EXPOSE 8080

# Container healthcheck probe
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD curl -f http://localhost:8080/api/health || exit 1

# Direct execution form for clean UNIX signal handling
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080", "--workers", "2", "--proxy-headers"]