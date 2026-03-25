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

log "Starting Python services..."
cd /app/python
watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.connectors_main &
watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.query_main &

sleep 15

log "Starting Node.js backend..."
cd /app/backend
npm run dev &

log "Starting frontend..."
cd /app/frontend
npm run dev &

log "Starting remaining services..."
cd /app/python
watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.indexing_main &
watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.docling_main &

log "All services started"

wait
