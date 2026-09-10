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
  "cd backend/python && . venv/bin/activate && (i=0; while [ \$i -lt 30 ]; do curl -fsS --max-time 1 http://127.0.0.1:8088/health >/dev/null && exit 0; sleep 1; i=\$((i+1)); done; echo \"Error: Connector service failed to start on port 8088\"; exit 1) && (i=0; while [ \$i -lt 300 ]; do if curl -fsS --max-time 1 http://127.0.0.1:8002/health 2>/dev/null | grep -q '\"healthy\"'; then exit 0; fi; sleep 1; i=\$((i+1)); done; echo \"Error: Embedding service failed to become healthy on port 8002\"; exit 1) && python -m app.indexing_main" \
  "cd backend/python && . venv/bin/activate && (i=0; while [ \$i -lt 30 ]; do curl -fsS --max-time 1 http://127.0.0.1:8088/health >/dev/null && exit 0; sleep 1; i=\$((i+1)); done; echo \"Error: Connector service failed to start on port 8088\"; exit 1) && (i=0; while [ \$i -lt 300 ]; do if curl -fsS --max-time 1 http://127.0.0.1:8002/health 2>/dev/null | grep -q '\"healthy\"'; then exit 0; fi; sleep 1; i=\$((i+1)); done; echo \"Error: Embedding service failed to become healthy on port 8002\"; exit 1) && python -m app.query_main"
