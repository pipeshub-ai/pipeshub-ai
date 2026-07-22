#!/bin/bash
set -e

export PYTHONPATH="/app/python"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

cleanup() {
    log "Shutting down services..."
    pkill -P $$ || true
    wait
    exit 0
}

trap cleanup SIGTERM SIGINT SIGQUIT

log "Starting development environment..."

log "Starting Embedding service..."
cd /app/python
EMBEDDING_PORT="${EMBEDDING_SERVER_PORT:-8002}"
python -m app.embedding_main &
EMBEDDING_PID=$!

log "Waiting for Embedding service health check..."
EMBEDDING_RETRIES=0
EMBEDDING_MAX_RETRIES=120
while [ "$EMBEDDING_RETRIES" -lt "$EMBEDDING_MAX_RETRIES" ]; do
    if curl -s -f "http://localhost:${EMBEDDING_PORT}/health" > /dev/null 2>&1; then
        log "Embedding service health check passed!"
        break
    fi
    EMBEDDING_RETRIES=$((EMBEDDING_RETRIES + 1))
    sleep 2
done
if [ "$EMBEDDING_RETRIES" -eq "$EMBEDDING_MAX_RETRIES" ]; then
    log "WARNING: Embedding service health check timed out; continuing startup"
fi

log "Starting Python services..."
cd /app/python
watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.connectors_main &
watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.query_main &
watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.docling_main &

DOCLING_PORT="${DOCLING_SERVICE_PORT:-8081}"
DOCLING_RETRIES=0
while [ "$DOCLING_RETRIES" -lt 120 ]; do
    if curl -s -f "http://localhost:${DOCLING_PORT}/health" > /dev/null 2>&1; then
        log "Docling service health check passed!"
        break
    fi
    DOCLING_RETRIES=$((DOCLING_RETRIES + 1))
    sleep 2
done
if [ "$DOCLING_RETRIES" -eq 120 ]; then
    log "ERROR: Docling service did not become healthy"
    exit 1
fi

# Standalone Parsing/Extraction services are opt-in (USE_PARSING_SERVICE=true,
# set by default in the hot-reload compose files). Without this block,
# indexing would call a dead :8092/:8093 -- these ports were exposed in the
# compose files but nothing here ever bound to them.
if [ "${USE_PARSING_SERVICE:-false}" = "true" ]; then
    log "USE_PARSING_SERVICE=true — starting Parsing and Extraction services..."
    PARSING_SERVICE_PORT="${PARSING_SERVICE_PORT:-8092}"
    EXTRACTION_SERVICE_PORT="${EXTRACTION_SERVICE_PORT:-8093}"
    export PARSING_SERVICE_PORT EXTRACTION_SERVICE_PORT
    watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.parsing_main &
    watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.extraction_main &
else
    log "USE_PARSING_SERVICE not set to true — skipping Parsing and Extraction services"
fi

sleep 15

log "Starting Node.js backend..."
cd /app/backend
npm run dev &

sleep 5

log "Starting remaining Python services..."
cd /app/python
watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.indexing_main &

sleep 10

log "Starting frontend (Next.js dev server on port 3001)..."
cd /app/frontend

# Ensure NODE_OPTIONS is set for memory limit (fallback if not in env)
export NODE_OPTIONS="${NODE_OPTIONS:---max-old-space-size=2048}"
log "Frontend starting with NODE_OPTIONS: $NODE_OPTIONS"

npm run dev &
FRONTEND_PID=$!

log "All services started. Frontend PID: $FRONTEND_PID"

# Monitor frontend memory usage every 30 seconds
while true; do
    sleep 30
    if ! kill -0 "$FRONTEND_PID" 2>/dev/null; then
        log "Frontend process (PID $FRONTEND_PID) exited. Restarting in 3s..."
        sleep 3
        cd /app/frontend
        npm run dev &
        FRONTEND_PID=$!
        log "Frontend restarted with PID $FRONTEND_PID"
    else
        # Check memory usage of frontend process
        if command -v ps &> /dev/null; then
            MEM_USAGE=$(ps -p "$FRONTEND_PID" -o rss= 2>/dev/null || echo "0")
            if (( MEM_USAGE > 0 )); then
                MEM_MB=$((MEM_USAGE / 1024))
                if (( MEM_MB > 1800 )); then
                    log "WARNING: Frontend memory usage high: ${MEM_MB}MB"
                fi
            fi
        fi
    fi
done &

wait
