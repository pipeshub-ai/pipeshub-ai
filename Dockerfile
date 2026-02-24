# -----------------------------------------------------------------------------
# Stage 1: Build Base - Contains all build tools (NOT in final image)
# -----------------------------------------------------------------------------
FROM python:3.10-slim AS build-base
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    build-essential \
    gnupg \
    wget \
    librocksdb-dev \
    libgflags-dev \
    libsnappy-dev \
    zlib1g-dev \
    libbz2-dev \
    liblz4-dev \
    libzstd-dev \
    libssl-dev \
    libspatialindex-dev \
    && rm -rf /var/lib/apt/lists/*

# Install Rust with minimal profile (only needed for building certain pip packages)
RUN curl https://sh.rustup.rs -sSf | sh -s -- -y --profile minimal --default-toolchain stable
ENV PATH="/root/.cargo/bin:${PATH}"

# Install uv for faster pip operations
RUN pip install --no-cache-dir uv

# -----------------------------------------------------------------------------
# Stage 2: Python Dependencies Build
# -----------------------------------------------------------------------------
FROM build-base AS python-deps
WORKDIR /app/python

COPY ./backend/python/pyproject.toml ./

# Install Python dependencies
RUN uv pip install --system -e . && \
    # Download ML models
    python -m spacy download en_core_web_sm && \
    python -c "import nltk; nltk.download('punkt', quiet=True)" && \
    python -c "from sentence_transformers import CrossEncoder; CrossEncoder(model_name='BAAI/bge-reranker-base')" && \
    # Clean up caches to save space
    rm -rf /root/.cache/pip /root/.cache/uv /tmp/*

# -----------------------------------------------------------------------------
# Stage 3: Node.js Backend Build
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
# Stage 4: Frontend Build
# -----------------------------------------------------------------------------
FROM node:20-slim AS frontend-build
WORKDIR /app/frontend

RUN mkdir -p packages
COPY frontend/package*.json ./
COPY frontend/packages ./packages/

RUN npm config set legacy-peer-deps true && \
    npm install && \
    npm cache clean --force

COPY frontend/ ./
RUN npm run build

# -----------------------------------------------------------------------------
# Stage 5: Runtime Base - Minimal runtime dependencies only
# -----------------------------------------------------------------------------
FROM python:3.10-slim AS runtime-base
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC

# Install ONLY runtime dependencies (no build tools!)
RUN apt-get update && apt-get install -y --no-install-recommends \
    # Basic utilities
    curl \
    ca-certificates \
    # Network debugging (optional - remove if not needed)
    iputils-ping \
    dnsutils \
    # Runtime libraries for RocksDB
    libsnappy1v5 \
    zlib1g \
    liblz4-1 \
    libzstd1 \
    # Other runtime deps
    libpq5 \
    # OpenGL library (required by Docling for PDF processing)
    libgl1 \
    libglib2.0-0 \
    # OCR tools
    ocrmypdf \
    tesseract-ocr \
    ghostscript \
    unpaper \
    qpdf \
    # LibreOffice - MINIMAL install (only writer and calc, headless)
    # Comment out if not needed - this is still ~300-400MB
    libreoffice-writer-nogui \
    libreoffice-calc-nogui \
    libreoffice-impress-nogui \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# Install Node.js runtime only (not full dev environment)
RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# Install Neo4j Cypher Shell
RUN apt-get update && \
    apt-get install -y --no-install-recommends gnupg && \
    curl -fsSL https://debian.neo4j.com/neotechnology.gpg.key | gpg --dearmor -o /usr/share/keyrings/neo4j.gpg && \
    echo "deb [signed-by=/usr/share/keyrings/neo4j.gpg] https://debian.neo4j.com stable latest" > /etc/apt/sources.list.d/neo4j.list && \
    apt-get update && \
    apt-get install -y --no-install-recommends cypher-shell && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/* /tmp/* /var/tmp/*

# -----------------------------------------------------------------------------
# Stage 6: Final Runtime Image
# -----------------------------------------------------------------------------
FROM runtime-base AS runtime
WORKDIR /app

# Copy Python site-packages from build stage
COPY --from=python-deps /usr/local/lib/python3.10/site-packages /usr/local/lib/python3.10/site-packages
COPY --from=python-deps /usr/local/bin /usr/local/bin

# Copy ML model data
COPY --from=python-deps /root/.cache/huggingface /root/.cache/huggingface
COPY --from=python-deps /root/nltk_data /root/nltk_data

# Copy Node.js backend (already pruned)
COPY --from=nodejs-backend /app/backend/dist ./backend/dist
COPY --from=nodejs-backend /app/backend/src/modules/mail ./backend/src/modules/mail
COPY --from=nodejs-backend /app/backend/src/modules/api-docs/pipeshub-openapi.yaml ./backend/src/modules/api-docs/pipeshub-openapi.yaml
COPY --from=nodejs-backend /app/backend/node_modules ./backend/dist/node_modules

# Copy frontend build
COPY --from=frontend-build /app/frontend/dist ./backend/dist/public

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
    if [ -z "$SLACK_SIGNING_SECRET" ] || [ -z "$BOT_TOKEN" ]; then
        log "Slack bot not configured (SLACK_SIGNING_SECRET or BOT_TOKEN not set), skipping..."
        return 0
    fi
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
