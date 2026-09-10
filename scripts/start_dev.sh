#!/bin/bash

# Make sure we are in the root directory
cd "$(dirname "$0")/.." || exit 1

echo "🚀 Starting PipesHub Local Development Environment..."

npx -y concurrently \
  -n "FRONT,NODE,EMBED,DOCS,CONN,INDEX,QUERY" \
  -c "cyan,blue,magenta,yellow,green,red,white" \
  "cd frontend && PORT=3001 npm run dev" \
  "cd backend/nodejs/apps && npm run dev" \
  "cd backend/python && . venv/bin/activate && python -m app.embedding_main" \
  "cd backend/python && . venv/bin/activate && python -m app.docling_main" \
  "cd backend/python && . venv/bin/activate && python -m app.connectors_main" \
  "cd backend/python && . venv/bin/activate && (for i in {1..30}; do curl -s http://127.0.0.1:8002/health >/dev/null && break || sleep 1; done); python -m app.indexing_main" \
  "cd backend/python && . venv/bin/activate && (for i in {1..30}; do curl -s http://127.0.0.1:8002/health >/dev/null && break || sleep 1; done); python -m app.query_main"
