# Stage 1: Base dependencies
FROM python:3.10-slim AS base
ENV DEBIAN_FRONTEND=noninteractive TZ=Etc/UTC

WORKDIR /app

RUN pip install uv

# Install system dependencies and necessary runtime libraries
RUN apt-get update && apt-get install -y \
    curl build-essential gnupg iputils-ping telnet traceroute dnsutils net-tools wget \
    librocksdb-dev libgflags-dev libsnappy-dev zlib1g-dev \
    libbz2-dev liblz4-dev libzstd-dev libssl-dev ca-certificates libspatialindex-dev libpq5 && \
    curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y nodejs && \
    apt-get install -y libreoffice && \
    apt-get install -y ocrmypdf tesseract-ocr ghostscript unpaper qpdf && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN curl https://sh.rustup.rs -sSf | sh -s -- -y
ENV PATH="/root/.cargo/bin:${PATH}"

# Stage 2: Python dependencies
FROM base AS python-deps
COPY ./backend/python/pyproject.toml /app/python/
WORKDIR /app/python
RUN uv pip install --system -e .
# Download NLTK and spaCy models
RUN python -m spacy download en_core_web_sm && \
    python -m nltk.downloader punkt && \
    python -c "from sentence_transformers import CrossEncoder; model = CrossEncoder(model_name='BAAI/bge-reranker-base')"

# Stage 3: Node.js backend
FROM base AS nodejs-backend
WORKDIR /app/backend

COPY backend/nodejs/apps/package*.json ./
COPY backend/nodejs/apps/tsconfig.json ./

# Set up architecture detection and conditional handling
RUN set -e; \
    # Detect architecture
    ARCH=$(uname -m); \
    echo "Building for architecture: $ARCH"; \
    # Platform-specific handling
    if [ "$ARCH" = "arm64" ] || [ "$ARCH" = "aarch64" ]; then \
        echo "Detected ARM architecture (M1/Apple Silicon)"; \
        # ARM-specific handling: Skip problematic binary or use alternative
        npm install --prefix ./ --ignore-scripts && \
	npm uninstall jpeg-recompress-bin mozjpeg imagemin-mozjpeg 2>/dev/null || true; \
        # Install Sharp AS a better alternative for ARM64
        npm install sharp --save || echo "Sharp install failed, continuing without image optimization"; \
    else \
        echo "Detected x86 architecture"; \
        # Standard install for x86 platforms
        apt-get update && apt-get install -y libc6-dev-i386 && npm install --prefix ./; \
    fi

COPY backend/nodejs/apps/src ./src
RUN npm run build

# Stage 4: Frontend build
FROM base AS frontend-build
WORKDIR /app/frontend
RUN mkdir -p packages
COPY frontend/package*.json ./
COPY frontend/packages ./packages/
RUN npm config set legacy-peer-deps true && npm install
COPY frontend/ ./
RUN npm run build

# Stage 5: Final runtime
FROM python-deps AS runtime
WORKDIR /app

COPY --from=nodejs-backend /app/backend/dist ./backend/dist
COPY --from=nodejs-backend /app/backend/src/modules/mail ./backend/src/modules/mail
COPY --from=nodejs-backend /app/backend/src/modules/storage/docs/swagger.yaml ./backend/src/modules/storage/docs/swagger.yaml
COPY --from=nodejs-backend /app/backend/node_modules ./backend/dist/node_modules
COPY --from=frontend-build /app/frontend/dist ./backend/dist/public
COPY backend/python/app/ /app/python/app/

# Copy the process monitor script
COPY <<'EOF' /app/process_monitor.sh
#!/bin/bash

# Process monitor script with parent-child process management
set -e

LOG_FILE="/app/process_monitor.log"
CHECK_INTERVAL=10

# PIDs of child processes
NODEJS_PID=""
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
    [ -n "$DOCLING_PID" ] && kill "$DOCLING_PID" 2>/dev/null || true
    [ -n "$INDEXING_PID" ] && kill "$INDEXING_PID" 2>/dev/null || true
    [ -n "$CONNECTOR_PID" ] && kill "$CONNECTOR_PID" 2>/dev/null || true
    [ -n "$QUERY_PID" ] && kill "$QUERY_PID" 2>/dev/null || true
    
    wait
    log "All services stopped."
    exit 0
}

# Trap signals for graceful shutdown
trap cleanup SIGTERM SIGINT SIGQUIT

# Start all services
log "=== Process Monitor Starting ==="
start_nodejs
start_connector
start_indexing
start_query
start_docling

log "All services started. Beginning monitoring cycle (checking every ${CHECK_INTERVAL}s)..."

# Monitor loop
while true; do
    sleep "$CHECK_INTERVAL"
    
    # Check and restart Node.js
    if ! check_process "$NODEJS_PID" "Node.js"; then
        start_nodejs
    fi
    
    # Check and restart Docling
    if ! check_process "$DOCLING_PID" "Docling"; then
        start_docling
    fi
    
    # Check and restart Indexing
    if ! check_process "$INDEXING_PID" "Indexing"; then
        start_indexing
    fi
    
    # Check and restart Connector
    if ! check_process "$CONNECTOR_PID" "Connector"; then
        start_connector
    fi
    
    # Check and restart Query
    if ! check_process "$QUERY_PID" "Query"; then
        start_query
    fi
done
EOF

RUN chmod +x /app/process_monitor.sh

# Expose necessary ports
EXPOSE 3000 8000 8088 8091 8081

# Use the process monitor as the main process
CMD ["/app/process_monitor.sh"]
