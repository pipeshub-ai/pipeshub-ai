#!/bin/bash
echo "Starting all Python backend services..."

# Activate virtual environment
source venv/bin/activate

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

# Function to cleanly shut down all background processes when you press Ctrl+C
cleanup() {
    echo "Shutting down all python services..."
    kill -TERM $PID_EMBED $PID_CONN $PID_INDEX $PID_QUERY $PID_DOC
    wait $PID_EMBED $PID_CONN $PID_INDEX $PID_QUERY $PID_DOC 2>/dev/null
    echo "All services stopped."
    exit 0
}

# Trap Ctrl+C (SIGINT) to call cleanup
trap cleanup SIGINT SIGTERM

echo "All services are running! Press Ctrl+C to stop them all."
# Wait forever so the script doesn't exit until interrupted
wait
