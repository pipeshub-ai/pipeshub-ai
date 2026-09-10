#!/bin/bash

# Make sure we are in the root directory
cd "$(dirname "$0")/.."

echo "🚀 Starting PipesHub Local Development Environment..."

npx -y concurrently \
  -n "FRONT,NODE,EMBED,DOCS,CONN,INDEX,QUERY" \
  -c "cyan,blue,magenta,yellow,green,red,white" \
  "cd frontend && PORT=3001 npm run dev" \
  "cd backend/nodejs/apps && npm run dev" \
  "cd backend/python && source venv/bin/activate && python -m app.embedding_main" \
  "cd backend/python && source venv/bin/activate && python -m app.docling_main" \
  "cd backend/python && source venv/bin/activate && python -m app.connectors_main" \
  "cd backend/python && source venv/bin/activate && python -m app.indexing_main" \
  "cd backend/python && source venv/bin/activate && python -m app.query_main"
