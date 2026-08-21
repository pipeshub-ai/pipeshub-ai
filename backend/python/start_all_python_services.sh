#!/bin/bash
echo "Starting all Python backend services..."

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR" || exit 1

# Activate virtual environment
source venv/bin/activate || { echo "Failed to activate venv"; exit 1; }

# Function to cleanly shut down all background processes
cleanup() {
    local status=${1:-0}
    echo "Shutting down all python services..."
    # Suppress output if PIDs don't exist
    [ -n "$PID_EMBED" ] && kill -TERM $PID_EMBED 2>/dev/null
    [ -n "$PID_CONN" ] && kill -TERM $PID_CONN 2>/dev/null
    [ -n "$PID_INDEX" ] && kill -TERM $PID_INDEX 2>/dev/null
    [ -n "$PID_QUERY" ] && kill -TERM $PID_QUERY 2>/dev/null
    [ -n "$PID_DOC" ] && kill -TERM $PID_DOC 2>/dev/null
    
    wait $PID_EMBED $PID_CONN $PID_INDEX $PID_QUERY $PID_DOC 2>/dev/null || true
    echo "All services stopped."
    exit $status
}

# Trap Ctrl+C (SIGINT) and termination (SIGTERM) to call cleanup
trap cleanup SIGINT SIGTERM

# Start all services in the background
python -m app.embedding_main &
PID_EMBED=$!
echo "Started embedding service (PID: $PID_EMBED)"

python -m app.connectors_main &
PID_CONN=$!
echo "Started connectors service (PID: $PID_CONN)"

python -m app.indexing_main &
PID_INDEX=$!
echo "Started indexing service (PID: $PID_INDEX)"

python -m app.query_main &
PID_QUERY=$!
echo "Started query service (PID: $PID_QUERY)"

python -m app.docling_main &
PID_DOC=$!
echo "Started docling service (PID: $PID_DOC)"

echo "All services are running! Press Ctrl+C to stop them all."

# Wait for any child process to exit. If one fails, terminate everything.
while true; do
    if wait -n; then
        echo "A service exited successfully. Stopping remaining services..."
        cleanup 0
    else
        status=$?
        if [ $status -eq 127 ]; then
            # No more background jobs running
            exit 0
        fi
        echo "A service failed with status $status. Triggering cleanup..."
        cleanup $status
    fi
done
