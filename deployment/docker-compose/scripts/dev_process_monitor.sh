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

sleep 5

log "Starting remaining Python services..."
cd /app/python
watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.indexing_main &
watchmedo auto-restart --recursive --pattern="*.py" --directory="." -- python -m app.docling_main &

sleep 10

log "Starting frontend (Vite dev server on port 3001)..."
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
