# Service Request Chatbot API image.
# Build context MUST be service-request-chatbot/backend (this directory).
#
# Build args:
#   GIT_SHA — short revision, exposed via APP_GIT_SHA env
#   VERSION — exposed via APP_VERSION env
# Example:
#   docker build \
#     --build-arg GIT_SHA=$(git rev-parse --short HEAD) \
#     --build-arg VERSION=$(git describe --tags --always --dirty 2>/dev/null || echo dev) \
#     -t sr-chatbot-api .
#
FROM python:3.11-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY app ./app

RUN pip install --no-cache-dir --upgrade pip setuptools wheel && \
    pip install --no-cache-dir .

FROM python:3.11-slim

ARG GIT_SHA=unknown
ARG VERSION=dev
ENV APP_GIT_SHA=${GIT_SHA}
ENV APP_VERSION=${VERSION}
ENV PORT=8000

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY app ./app
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

RUN useradd -m -u 1000 appuser && chown -R appuser:appuser /app
USER appuser

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').read()"

CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
