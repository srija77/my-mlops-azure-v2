# syntax=docker/dockerfile:1
# ============================================================================
# Inference web app image.
#
#   docker build -f app/Dockerfile -t dam-mcp-app .        <- context is repo ROOT
#   docker run -p 8000:8000 --env-file .env dam-mcp-app
#
# Two stages so the runtime image carries no compiler toolchain and no pip cache.
# The wheels are built once in `builder` into a self-contained virtualenv, then
# copied across; nothing in the final image can rebuild or install packages.
# ============================================================================

# ---- Stage 1: build the virtualenv -----------------------------------------
FROM python:3.11-slim-bookworm AS builder

ENV PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    PYTHONDONTWRITEBYTECODE=1

WORKDIR /build

# Copied alone, before the source, so edits to app code reuse this cached layer
# instead of reinstalling every dependency on each build.
COPY app/requirements.txt ./requirements.txt

RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip setuptools wheel \
 && /opt/venv/bin/pip install -r requirements.txt

# ---- Stage 2: runtime -------------------------------------------------------
FROM python:3.11-slim-bookworm AS runtime

# APP_REVISION is surfaced in the UI, /health, and every log line, so a
# screenshot of the page is enough to identify the exact image that served it.
ARG APP_REVISION=dev
ENV APP_REVISION=${APP_REVISION} \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8000

# Non-root. Container Apps does not require it, but a scoring proxy holding an
# endpoint key is exactly the process that should not be able to write to its own
# filesystem or install anything at runtime.
RUN groupadd --system --gid 10001 appuser \
 && useradd --system --uid 10001 --gid appuser --no-create-home appuser

WORKDIR /srv

COPY --from=builder /opt/venv /opt/venv
COPY --chown=appuser:appuser app/ ./app/

USER appuser

EXPOSE 8000

# Liveness only — /health makes no outbound call, so an endpoint outage cannot
# make Docker consider this container unhealthy and restart it.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request,os,sys; sys.exit(0 if urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",8000)}/health', timeout=2).status==200 else 1)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --proxy-headers --forwarded-allow-ips '*'"]
