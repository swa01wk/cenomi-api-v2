# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — builder
# Installs all dependencies (including dev tools used by setuptools) into a
# clean venv so the final image only copies the venv, not the build tools.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS builder

WORKDIR /build

# System deps needed to compile asyncpg, cryptography, bcrypt
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libffi-dev \
    libssl-dev \
    && rm -rf /var/lib/apt/lists/*

# Create isolated venv
RUN python -m venv /venv
ENV PATH="/venv/bin:$PATH"

# Install app in production mode (no dev extras)
COPY pyproject.toml .
COPY app/ app/
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir .


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — runtime
# Lean image — no build tools, no pip, no source outside the venv.
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.11-slim AS runtime

# Non-root user for security
RUN addgroup --system appgroup && adduser --system --ingroup appgroup appuser

WORKDIR /app

# Runtime-only system libs (asyncpg needs libssl at runtime)
RUN apt-get update && apt-get install -y --no-install-recommends \
    libssl3 \
    && rm -rf /var/lib/apt/lists/*

# Copy venv from builder (already has all packages)
COPY --from=builder /venv /venv
ENV PATH="/venv/bin:$PATH"

# Copy application source and Alembic migrations
COPY --chown=appuser:appgroup app/ app/
COPY --chown=appuser:appgroup alembic/ alembic/
COPY --chown=appuser:appgroup alembic.ini alembic.ini
COPY --chown=appuser:appgroup scripts/ scripts/

USER appuser

# ── Runtime configuration ────────────────────────────────────────────────────
# All secrets are injected via environment variables at runtime.
# Never bake .env into the image.

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

EXPOSE 8000

# ── Health check ─────────────────────────────────────────────────────────────
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/v1/health')"

# ── Entrypoint ───────────────────────────────────────────────────────────────
# Uses uvicorn directly. For Azure Container Apps / AKS use 1 worker per pod
# and scale horizontally — gunicorn is not needed.
CMD ["uvicorn", "app.main:app", \
     "--host", "0.0.0.0", \
     "--port", "8000", \
     "--workers", "1", \
     "--log-level", "info", \
     "--no-access-log"]
