# Slow layers (APT, Rust, Python deps, ML models, runtime stack) live in Dockerfile.base.
# Override with local tags if registry images are missing:
#   docker build --build-arg PYTHON_DEPS_IMAGE=myreg/python-deps --build-arg RUNTIME_BASE_IMAGE=myreg/runtime .
ARG PYTHON_DEPS_IMAGE=pipeshubai/pipeshub-ai-base:python-deps
ARG RUNTIME_BASE_IMAGE=pipeshubai/pipeshub-ai-base:runtime

FROM ${PYTHON_DEPS_IMAGE} AS python-deps
FROM ${RUNTIME_BASE_IMAGE} AS runtime-base

# -----------------------------------------------------------------------------
# Stage 1: Node.js Backend Build
# -----------------------------------------------------------------------------
FROM node:20-slim AS nodejs-backend
WORKDIR /app/backend

COPY backend/nodejs/apps/package*.json ./
COPY backend/nodejs/apps/tsconfig.json ./

# Install dependencies with architecture handling
RUN set -e; \
    ARCH=$(uname -m); \
    echo "Building for architecture: $ARCH"; \
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then \
        echo "Detected ARM architecture"; \
        npm install --ignore-scripts && \
        npm uninstall jpeg-recompress-bin mozjpeg imagemin-mozjpeg 2>/dev/null || true && \
        npm install sharp --save || true; \
    else \
        echo "Detected x86 architecture"; \
        npm install; \
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

RUN npm config set legacy-peer-deps true && \
    npm install && \
    npm cache clean --force

COPY frontend/ ./

# Force Next.js static export regardless of what next.config.mjs ships with,
# so the resulting assets can be served directly from disk.
RUN if grep -q "output: 'export'" next.config.mjs; then \
        sed -i "s|//[[:space:]]*output: 'export',|output: 'export',|" next.config.mjs; \
    else \
        sed -i "s|const nextConfig = {|const nextConfig = { output: 'export',|" next.config.mjs; \
    fi && \
    npm run build && \
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

# Copy Python site-packages from build stage
COPY --from=python-deps /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

# Copy ML model data (dense HF embeddings + reranker, sparse fastembed, NLTK)
COPY --from=python-deps /root/.cache/huggingface /root/.cache/huggingface
COPY --from=python-deps /root/.cache/fastembed /root/.cache/fastembed
COPY --from=python-deps /root/nltk_data /root/nltk_data

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

# PIDs of child processes
NODEJS_PID=""
SLACKBOT_PID=""
DOCLING_PID=""
INDEXING_PID=""
CONNECTOR_PID=""
QUERY_PID=""

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

start_docling() {
    log "Starting Docling service..."
    cd /app/python
    python -m app.docling_main &
    DOCLING_PID=$!
    log "Docling started with PID: $DOCLING_PID"
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
    local MAX_RETRIES=30
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
    [ -n "$DOCLING_PID" ] && kill "$DOCLING_PID" 2>/dev/null || true
    [ -n "$INDEXING_PID" ] && kill "$INDEXING_PID" 2>/dev/null || true
    [ -n "$CONNECTOR_PID" ] && kill "$CONNECTOR_PID" 2>/dev/null || true
    [ -n "$QUERY_PID" ] && kill "$QUERY_PID" 2>/dev/null || true
    
    wait
    log "All services stopped."
    exit 0
}

trap cleanup SIGTERM SIGINT SIGQUIT

log "=== Process Monitor Starting ==="
start_nodejs
start_slackbot
start_connector
start_indexing
start_query
start_docling

log "All services started. Beginning monitoring cycle (checking every ${CHECK_INTERVAL}s)..."

while true; do
    sleep "$CHECK_INTERVAL"
    
    if ! check_process "$NODEJS_PID" "Node.js"; then
        start_nodejs
    fi
    
    if [ -n "$SLACKBOT_PID" ] && ! check_process "$SLACKBOT_PID" "Slack Bot"; then
        start_slackbot
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
done
EOF

RUN chmod +x /app/process_monitor.sh

EXPOSE 3000

CMD ["/app/process_monitor.sh"]
