#!/bin/bash
# Start the Parsing Service (port 8092) and Extraction Service (port 8093).
# Run from the Python backend directory:
#   cd backend/python && bash ../../scripts/start-parsing-extraction-services.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_DIR="${1:-$(dirname "$SCRIPT_DIR")/backend/python}"

echo "🚀 Starting PipesHub AI — Parsing & Extraction Services"
echo "📁 Python directory: $PYTHON_DIR"

cd "$PYTHON_DIR"

PARSING_PORT="${PARSING_SERVICE_PORT:-8092}"
EXTRACTION_PORT="${EXTRACTION_SERVICE_PORT:-8093}"

echo "📄 Starting Parsing Service on port $PARSING_PORT …"
uvicorn app.parsing_main:app \
  --host 0.0.0.0 \
  --port "$PARSING_PORT" \
  --log-level "${LOG_LEVEL:-info}" &
PARSING_PID=$!

echo "🧠 Starting Extraction Service on port $EXTRACTION_PORT …"
uvicorn app.extraction_main:app \
  --host 0.0.0.0 \
  --port "$EXTRACTION_PORT" \
  --log-level "${LOG_LEVEL:-info}" &
EXTRACTION_PID=$!

echo "✅ Services started"
echo "   Parsing Service  PID=$PARSING_PID  → http://localhost:$PARSING_PORT/health"
echo "   Extraction Service PID=$EXTRACTION_PID → http://localhost:$EXTRACTION_PORT/health"
echo ""
echo "To enable routing through these services in the Indexing service, set:"
echo "   USE_PARSING_SERVICE=true"
echo "   PARSING_SERVICE_URL=http://localhost:$PARSING_PORT"
echo "   EXTRACTION_SERVICE_URL=http://localhost:$EXTRACTION_PORT"

# Wait for both processes
wait $PARSING_PID $EXTRACTION_PID
