# syntax=docker/dockerfile:1
# Slow layers (APT, Rust, Python deps, ML models, runtime stack) live in Dockerfile.base.
# Override with local tags if registry images are missing:
#   docker build --build-arg PYTHON_DEPS_IMAGE=myreg/python-deps --build-arg RUNTIME_BASE_IMAGE=myreg/runtime .
# Slim app image (no pre-baked BGE; ~1.3 GB smaller — model downloads on first use):
#   docker build --build-arg PYTHON_DEPS_IMAGE=pipeshubai/pipeshub-ai-base:python-deps-slim -t pipeshubai/pipeshub-ai:slim .
ARG PYTHON_DEPS_IMAGE=pipeshubai/pipeshub-ai-base:python-deps
ARG RUNTIME_BASE_IMAGE=pipeshubai/pipeshub-ai-base:runtime

FROM ${PYTHON_DEPS_IMAGE} AS python-deps
# The base image bakes in dependencies as of its publish time. Reconcile with the
# current pyproject.toml so packages added since the base was published (e.g. new
# connector SDKs like opensearch-py) end up in the app image. uv skips
# already-satisfied packages, so this is a fast no-op when the base is current and
# installs only the delta otherwise. The base carries uv + the build toolchain
# (it is built FROM build-base in Dockerfile.base), so native wheels can still
# compile here when a new dependency needs it.
WORKDIR /app/python
COPY backend/python/pyproject.toml ./
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    uv pip install --system -e . && \
    crawl4ai-setup && \
    playwright install chromium


FROM ${RUNTIME_BASE_IMAGE} AS runtime-base

# -----------------------------------------------------------------------------
# Stage 1: Node.js Backend Build
# -----------------------------------------------------------------------------
FROM node:20-slim AS nodejs-backend
WORKDIR /app/backend

COPY backend/nodejs/apps/package*.json ./
COPY backend/nodejs/apps/tsconfig.json ./

# Install dependencies with architecture handling (npm ci: lockfile-speed + reproducible)
RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    set -e; \
    ARCH=$(uname -m); \
    echo "Building for architecture: $ARCH"; \
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then \
        echo "Detected ARM architecture"; \
        npm ci --ignore-scripts && \
        npm uninstall jpeg-recompress-bin mozjpeg imagemin-mozjpeg 2>/dev/null || true && \
        npm install sharp --save || true; \
    else \
        echo "Detected x86 architecture"; \
        npm ci; \
    fi

COPY backend/nodejs/apps/src ./src
RUN npm run build && \
    # Prune dev dependencies after build
    npm prune --production && \
    # Clean npm cache
    npm cache clean --force

# -----------------------------------------------------------------------------
# Stage 2: Frontend Build (Next.js in `frontend/`)
# -----------------------------------------------------------------------------
# Static export so the Node.js API can serve files from `backend/dist/public`.
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend

COPY frontend/package*.json ./

RUN --mount=type=cache,target=/root/.npm,sharing=locked \
    npm config set legacy-peer-deps true && \
    npm ci

COPY frontend/ ./

# Force static export for container builds by setting the env flag that
# `frontend-new/next.config.mjs` already uses to enable `output: 'export'`.
RUN ELECTRON_STATIC=1 npm run build && \
    mkdir -p /out && \
    cp -a out/. /out/

# -----------------------------------------------------------------------------
# Stage 3: Final Runtime Image
# -----------------------------------------------------------------------------
FROM runtime-base AS runtime
WORKDIR /app

# Point fastembed at the pre-populated cache we copy in below, matching the
# FASTEMBED_CACHE_PATH used at build time in the python-deps stage.
ENV FASTEMBED_CACHE_PATH=/root/.cache/fastembed

# Cap ML thread fan-out (PyTorch/OpenBLAS/MKL). runtime_threads.py propagates
# this to all libraries. Override via OMP_NUM_THREADS in .env if needed.
ENV OMP_NUM_THREADS=2

# Copy Python site-packages from build stage
COPY --from=python-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

# Copy ML model data (dense HF embeddings + reranker, sparse fastembed, NLTK)
COPY --from=python-deps /root/.cache/huggingface /root/.cache/huggingface
COPY --from=python-deps /root/.cache/fastembed /root/.cache/fastembed
COPY --from=python-deps /root/nltk_data /root/nltk_data

# Copy Playwright browser binaries
COPY --from=python-deps /root/.cache/ms-playwright /root/.cache/ms-playwright

# Copy Node.js backend (already pruned)
COPY --from=nodejs-backend /app/backend/dist ./backend/dist
COPY --from=nodejs-backend /app/backend/src/modules/mail ./backend/src/modules/mail
COPY --from=nodejs-backend /app/backend/src/modules/api-docs/pipeshub-openapi.yaml ./backend/src/modules/api-docs/pipeshub-openapi.yaml
COPY --from=nodejs-backend /app/backend/node_modules ./backend/dist/node_modules

# Copy frontend build (normalized to /out by the selected frontend stage)
COPY --from=frontend-build /out ./backend/dist/public

# Copy Python application code
COPY backend/python/app/ /app/python/app/

# Copy the process monitor script
COPY <<'EOF' /app/process_monitor.sh
#!/bin/bash

# Process monitor script with parent-child process management
set -e

LOG_FILE="/app/process_monitor.log"
CHECK_INTERVAL=${CHECK_INTERVAL:-20}
NODEJS_PORT=${NODEJS_PORT:-3000}
EMBEDDING_PORT=${EMBEDDING_SERVER_PORT:-8002}
DOCLING_PORT=${DOCLING_SERVICE_PORT:-8081}

# PIDs of child processes
NODEJS_PID=""
SLACKBOT_PID=""
EMBEDDING_PID=""
DOCLING_PID=""
INDEXING_PID=""
CONNECTOR_PID=""
QUERY_PID=""
PARSING_PID=""
EXTRACTION_PID=""

PARSING_PORT=${PARSING_SERVICE_PORT:-8092}
EXTRACTION_PORT=${EXTRACTION_SERVICE_PORT:-8093}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

start_nodejs() {
    log "Starting Node.js service..."
    cd /app/backend
    node dist/index.js &
    NODEJS_PID=$!
    log "Node.js started with PID: $NODEJS_PID"
    
    log "Waiting for Node.js health check..."
    local MAX_RETRIES=30
    local RETRY_COUNT=0
    local HEALTH_CHECK_URL="http://localhost:${NODEJS_PORT}/api/v1/health"
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "$HEALTH_CHECK_URL" > /dev/null 2>&1; then
            log "Node.js health check passed!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "Health check attempt $RETRY_COUNT/$MAX_RETRIES failed, retrying in 2 seconds..."
        sleep 2
    done
    
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log "ERROR: Node.js health check failed after $MAX_RETRIES attempts"
        return 1
    fi
}

start_slackbot() {
    log "Starting Slack Bot service..."
    cd /app/backend
    node dist/integrations/slack-bot/src/index.js &
    SLACKBOT_PID=$!
    log "Slack Bot started with PID: $SLACKBOT_PID"
}

start_embedding() {
    log "Starting Embedding service..."
    cd /app/python
    python -m app.embedding_main &
    EMBEDDING_PID=$!
    log "Embedding service started with PID: $EMBEDDING_PID"

    log "Waiting for Embedding service health check..."
    local MAX_RETRIES=120
    local RETRY_COUNT=0
    local HEALTH_CHECK_URL="http://localhost:${EMBEDDING_PORT}/health"

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "$HEALTH_CHECK_URL" > /dev/null 2>&1; then
            log "Embedding service health check passed!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "Health check attempt $RETRY_COUNT/$MAX_RETRIES failed, retrying in 2 seconds..."
        sleep 2
    done

    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log "ERROR: Embedding service health check failed after $MAX_RETRIES attempts"
        return 1
    fi
}

start_docling() {
    log "Starting Docling service (port ${DOCLING_PORT})..."
    cd /app/python
    python -m app.docling_main &
    DOCLING_PID=$!
    log "Docling started with PID: $DOCLING_PID"

    local RETRY_COUNT=0
    local MAX_RETRIES=120
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "http://localhost:${DOCLING_PORT}/health" > /dev/null 2>&1; then
            log "Docling service health check passed!"
            return 0
        fi
        if ! kill -0 "$DOCLING_PID" 2>/dev/null; then
            log "ERROR: Docling exited before becoming healthy"
            return 1
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        sleep 2
    done
    log "ERROR: Docling service health check failed after $MAX_RETRIES attempts"
    return 1
}

start_indexing() {
    log "Starting Indexing service..."
    cd /app/python
    python -m app.indexing_main &
    INDEXING_PID=$!
    log "Indexing started with PID: $INDEXING_PID"
}

start_connector() {
    log "Starting Connector service..."
    cd /app/python
    python -m app.connectors_main &
    CONNECTOR_PID=$!
    log "Connector started with PID: $CONNECTOR_PID"
    
    log "Waiting for Connector health check..."
    local MAX_RETRIES=60
    local RETRY_COUNT=0
    local HEALTH_CHECK_URL="http://localhost:8088/health"
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "$HEALTH_CHECK_URL" > /dev/null 2>&1; then
            log "Connector health check passed!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "Health check attempt $RETRY_COUNT/$MAX_RETRIES failed, retrying in 2 seconds..."
        sleep 2
    done
    
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log "ERROR: Connector health check failed after $MAX_RETRIES attempts"
        return 1
    fi
}

start_query() {
    log "Starting Query service..."
    cd /app/python
    python -m app.query_main &
    QUERY_PID=$!
    log "Query started with PID: $QUERY_PID"
}

start_parsing() {
    log "Starting Parsing service (port ${PARSING_PORT})..."
    cd /app/python
    if [ -n "$PARSING_PID" ]; then
        kill -TERM -- "-$PARSING_PID" 2>/dev/null || true
    fi
    PARSING_SERVICE_PORT="${PARSING_PORT}" python -c \
        'import os, sys; os.setsid(); os.execv(sys.executable, [sys.executable, "-m", "app.parsing_main"])' &
    PARSING_PID=$!
    log "Parsing service started with PID: $PARSING_PID"

    log "Waiting for Parsing service health check..."
    local MAX_RETRIES=60
    local RETRY_COUNT=0
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "http://localhost:${PARSING_PORT}/health" > /dev/null 2>&1; then
            log "Parsing service health check passed!"
            break
        fi
        if ! kill -0 "$PARSING_PID" 2>/dev/null; then
            log "ERROR: Parsing service exited before becoming healthy"
            return 1
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "Health check attempt $RETRY_COUNT/$MAX_RETRIES failed, retrying in 2 seconds..."
        sleep 2
    done

    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log "ERROR: Parsing service health check failed after $MAX_RETRIES attempts"
        return 1
    fi
}

start_extraction() {
    log "Starting Extraction service (port ${EXTRACTION_PORT})..."
    cd /app/python
    EXTRACTION_SERVICE_PORT=${EXTRACTION_PORT} python -m app.extraction_main &
    EXTRACTION_PID=$!
    log "Extraction service started with PID: $EXTRACTION_PID"

    log "Waiting for Extraction service health check..."
    local MAX_RETRIES=60
    local RETRY_COUNT=0
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "http://localhost:${EXTRACTION_PORT}/health" > /dev/null 2>&1; then
            log "Extraction service health check passed!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "Health check attempt $RETRY_COUNT/$MAX_RETRIES failed, retrying in 2 seconds..."
        sleep 2
    done

    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log "ERROR: Extraction service health check failed after $MAX_RETRIES attempts"
        return 1
    fi
}

check_process() {
    local pid=$1
    local name=$2
    
    if [ -z "$pid" ] || ! kill -0 "$pid" 2>/dev/null; then
        log "WARNING: $name (PID: $pid) is not running!"
        return 1
    fi
    return 0
}

cleanup() {
    log "Shutting down all services..."
    
    [ -n "$NODEJS_PID" ] && kill "$NODEJS_PID" 2>/dev/null || true
    [ -n "$SLACKBOT_PID" ] && kill "$SLACKBOT_PID" 2>/dev/null || true
    [ -n "$EMBEDDING_PID" ] && kill "$EMBEDDING_PID" 2>/dev/null || true
    [ -n "$DOCLING_PID" ] && kill "$DOCLING_PID" 2>/dev/null || true
    [ -n "$INDEXING_PID" ] && kill "$INDEXING_PID" 2>/dev/null || true
    [ -n "$CONNECTOR_PID" ] && kill "$CONNECTOR_PID" 2>/dev/null || true
    [ -n "$QUERY_PID" ] && kill "$QUERY_PID" 2>/dev/null || true
    [ -n "$PARSING_PID" ] && kill -TERM -- "-$PARSING_PID" 2>/dev/null || true
    [ -n "$EXTRACTION_PID" ] && kill "$EXTRACTION_PID" 2>/dev/null || true
    
    wait
    log "All services stopped."
    exit 0
}

trap cleanup SIGTERM SIGINT SIGQUIT

log "=== Process Monitor Starting ==="
start_nodejs
start_slackbot
start_embedding
start_connector
start_docling

# Conditionally start the standalone Parsing and Extraction services.
# Set USE_PARSING_SERVICE=true in the environment to enable them.
# Must start (and pass health checks) *before* Indexing: on startup Indexing
# recovers in-progress records immediately and, when USE_PARSING_SERVICE=true,
# routes them through the Parsing service — if that service isn't up yet those
# recovery calls fail with connection errors / 503s.

if [ "${USE_PARSING_SERVICE:-false}" = "true" ]; then
    log "USE_PARSING_SERVICE=true — starting Parsing and Extraction services"
    start_parsing
    start_extraction
else
    log "USE_PARSING_SERVICE not set — skipping Parsing and Extraction services"
fi

start_query
# Indexing consumes queued records immediately, so all of its downstream
# services must be healthy before it starts.
start_indexing

log "All services started. Beginning monitoring cycle (checking every ${CHECK_INTERVAL}s)..."

while true; do
    sleep "$CHECK_INTERVAL"
    
    if ! check_process "$NODEJS_PID" "Node.js"; then
        start_nodejs
    fi
    
    if [ -n "$SLACKBOT_PID" ] && ! check_process "$SLACKBOT_PID" "Slack Bot"; then
        start_slackbot
    fi

    if ! check_process "$EMBEDDING_PID" "Embedding"; then
        start_embedding
    fi
    
    if ! check_process "$DOCLING_PID" "Docling"; then
        start_docling
    fi
    
    if ! check_process "$INDEXING_PID" "Indexing"; then
        start_indexing
    fi
    
    if ! check_process "$CONNECTOR_PID" "Connector"; then
        start_connector
    fi
    
    if ! check_process "$QUERY_PID" "Query"; then
        start_query
    fi

    if [ "${USE_PARSING_SERVICE:-false}" = "true" ]; then
        if ! check_process "$PARSING_PID" "Parsing"; then
            start_parsing
        fi
        if ! check_process "$EXTRACTION_PID" "Extraction"; then
            start_extraction
        fi
    fi
done
EOF

RUN chmod +x /app/process_monitor.sh

EXPOSE 3000 8002 8092 8093

CMD ["/app/process_monitor.sh"]
