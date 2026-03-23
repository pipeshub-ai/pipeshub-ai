#!/bin/bash

# Process monitor script for DEVELOPMENT (Hot Reload)
# Manages: Node.js backend (nodemon), Vite frontend, and Python services (watchmedo)
set -e

# So Python finds the app package (code is at /app/python/app/)
export PYTHONPATH="${PYTHONPATH:+$PYTHONPATH:}/app/python"

LOG_FILE="/app/dev_process_monitor.log"
CHECK_INTERVAL=${CHECK_INTERVAL:-20}
NODEJS_PORT=${NODEJS_PORT:-3000}

# PIDs of child processes
NODEJS_PID=""
FRONTEND_PID=""
DOCLING_PID=""
INDEXING_PID=""
CONNECTOR_PID=""
QUERY_PID=""

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

start_nodejs() {
    log "Starting Node.js backend DEV service (nodemon)..."
    cd /app/backend
    npm run dev &
    NODEJS_PID=$!
    log "Node.js backend (dev) started with PID: $NODEJS_PID"
}

start_frontend() {
    log "Starting Frontend DEV service (Vite HMR)..."
    cd /app/frontend
    npm run dev &
    FRONTEND_PID=$!
    log "Frontend (dev) started with PID: $FRONTEND_PID"
}

start_docling() {
    log "Starting Docling DEV service (watchmedo)..."
    cd /app/python
    watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.docling_main &
    DOCLING_PID=$!
    log "Docling (dev) started with PID: $DOCLING_PID"
}

start_indexing() {
    log "Starting Indexing DEV service (watchmedo)..."
    cd /app/python
    watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.indexing_main &
    INDEXING_PID=$!
    log "Indexing (dev) started with PID: $INDEXING_PID"
}

start_connector() {
    log "Starting Connector DEV service (watchmedo)..."
    cd /app/python
    watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.connectors_main &
    CONNECTOR_PID=$!
    log "Connector (dev) started with PID: $CONNECTOR_PID"
}

start_query() {
    log "Starting Query DEV service (watchmedo)..."
    cd /app/python
    watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.query_main &
    QUERY_PID=$!
    log "Query (dev) started with PID: $QUERY_PID"
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
    log "Shutting down all DEV services..."
    [ -n "$NODEJS_PID" ]    && kill -- -$(ps -o pgid= $NODEJS_PID    | tr -d '[:space:]') 2>/dev/null || true
    [ -n "$FRONTEND_PID" ]  && kill -- -$(ps -o pgid= $FRONTEND_PID  | tr -d '[:space:]') 2>/dev/null || true
    [ -n "$DOCLING_PID" ]   && kill -- -$(ps -o pgid= $DOCLING_PID   | tr -d '[:space:]') 2>/dev/null || true
    [ -n "$INDEXING_PID" ]  && kill -- -$(ps -o pgid= $INDEXING_PID  | tr -d '[:space:]') 2>/dev/null || true
    [ -n "$CONNECTOR_PID" ] && kill -- -$(ps -o pgid= $CONNECTOR_PID | tr -d '[:space:]') 2>/dev/null || true
    [ -n "$QUERY_PID" ]     && kill -- -$(ps -o pgid= $QUERY_PID     | tr -d '[:space:]') 2>/dev/null || true
    wait
    log "All services stopped."
    exit 0
}

trap cleanup SIGTERM SIGINT SIGQUIT

log "=== DEV Process Monitor Starting (Hot Reload) ==="
log "  Node.js  → nodemon   watches /app/backend/src/**/*.ts"
log "  Frontend → Vite HMR  watches /app/frontend/src/**"
log "  Python   → watchmedo watches /app/python/app/**/*.py"

# Start Connector and Query first — Node backend health-checks depend on them
start_connector
start_query
sleep 15  # Give Python services time to boot before Node.js health-checks them
start_nodejs
start_frontend
start_indexing
start_docling

log "All services started. Backend: :3000 | Frontend: :3001. Wait ~30s for full startup."

# Keep-alive loop: restart any crashed watcher
while true; do
    sleep "$CHECK_INTERVAL"
    if ! check_process "$NODEJS_PID"    "Node.js Watcher";   then start_nodejs;    fi
    if ! check_process "$FRONTEND_PID"  "Frontend Watcher";  then start_frontend;  fi
    if ! check_process "$DOCLING_PID"   "Docling Watcher";   then start_docling;   fi
    if ! check_process "$INDEXING_PID"  "Indexing Watcher";  then start_indexing;  fi
    if ! check_process "$CONNECTOR_PID" "Connector Watcher"; then start_connector; fi
    if ! check_process "$QUERY_PID"     "Query Watcher";     then start_query;     fi
done
