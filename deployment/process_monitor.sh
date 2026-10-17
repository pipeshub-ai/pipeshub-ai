#!/bin/bash

# Process monitor script with parent-child process management
set -e

LOG_FILE="/app/process_monitor.log"
CHECK_INTERVAL=${CHECK_INTERVAL:-20}
NODEJS_PORT=${NODEJS_PORT:-3000}
EMBEDDING_PORT=${EMBEDDING_SERVER_PORT:-8002}

# PIDs of child processes
NODEJS_PID=""
SLACKBOT_PID=""
EMBEDDING_PID=""
DOCLING_PID=""
INDEXING_PID=""
CONNECTOR_PID=""
QUERY_PID=""
PARSING_PID=""
EXTRACTION_PID=""

PARSING_PORT=${PARSING_SERVICE_PORT:-8092}
EXTRACTION_PORT=${EXTRACTION_SERVICE_PORT:-8093}

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1" | tee -a "$LOG_FILE"
}

start_nodejs() {
    log "Starting Node.js service..."
    cd /app/backend
    node dist/index.js &
    NODEJS_PID=$!
    log "Node.js started with PID: $NODEJS_PID"
    
    log "Waiting for Node.js health check..."
    local MAX_RETRIES=30
    local RETRY_COUNT=0
    local HEALTH_CHECK_URL="http://localhost:${NODEJS_PORT}/api/v1/health"
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "$HEALTH_CHECK_URL" > /dev/null 2>&1; then
            log "Node.js health check passed!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "Health check attempt $RETRY_COUNT/$MAX_RETRIES failed, retrying in 2 seconds..."
        sleep 2
    done
    
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log "ERROR: Node.js health check failed after $MAX_RETRIES attempts"
        return 1
    fi
}

start_slackbot() {
    log "Starting Slack Bot service..."
    cd /app/backend
    node "${SLACKBOT_ENTRY:-dist/integrations/slack-bot/src/index.js}" &
    SLACKBOT_PID=$!
    log "Slack Bot started with PID: $SLACKBOT_PID"
}

start_embedding() {
    log "Starting Embedding service..."
    cd /app/python
    python -m app.embedding_main &
    EMBEDDING_PID=$!
    log "Embedding service started with PID: $EMBEDDING_PID"

    log "Waiting for Embedding service health check..."
    local MAX_RETRIES=120
    local RETRY_COUNT=0
    local HEALTH_CHECK_URL="http://localhost:${EMBEDDING_PORT}/health"

    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "$HEALTH_CHECK_URL" > /dev/null 2>&1; then
            log "Embedding service health check passed!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "Health check attempt $RETRY_COUNT/$MAX_RETRIES failed, retrying in 2 seconds..."
        sleep 2
    done

    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log "ERROR: Embedding service health check failed after $MAX_RETRIES attempts"
        return 1
    fi
}

start_docling() {
    log "Starting Docling service..."
    cd /app/python
    python -m app.docling_main &
    DOCLING_PID=$!
    log "Docling started with PID: $DOCLING_PID"
}

start_indexing() {
    log "Starting Indexing service..."
    cd /app/python
    python -m app.indexing_main &
    INDEXING_PID=$!
    log "Indexing started with PID: $INDEXING_PID"
}

start_connector() {
    log "Starting Connector service..."
    cd /app/python
    python -m app.connectors_main &
    CONNECTOR_PID=$!
    log "Connector started with PID: $CONNECTOR_PID"
    
    log "Waiting for Connector health check..."
    local MAX_RETRIES=60
    local RETRY_COUNT=0
    local HEALTH_CHECK_URL="http://localhost:8088/health"
    
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "$HEALTH_CHECK_URL" > /dev/null 2>&1; then
            log "Connector health check passed!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "Health check attempt $RETRY_COUNT/$MAX_RETRIES failed, retrying in 2 seconds..."
        sleep 2
    done
    
    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log "ERROR: Connector health check failed after $MAX_RETRIES attempts"
        return 1
    fi
}

start_query() {
    log "Starting Query service..."
    cd /app/python
    python -m app.query_main &
    QUERY_PID=$!
    log "Query started with PID: $QUERY_PID"
}

start_parsing() {
    log "Starting Parsing service (port ${PARSING_PORT})..."
    cd /app/python
    PARSING_SERVICE_PORT=${PARSING_PORT} python -m app.parsing_main &
    PARSING_PID=$!
    log "Parsing service started with PID: $PARSING_PID"

    log "Waiting for Parsing service health check..."
    local MAX_RETRIES=60
    local RETRY_COUNT=0
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "http://localhost:${PARSING_PORT}/health" > /dev/null 2>&1; then
            log "Parsing service health check passed!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "Health check attempt $RETRY_COUNT/$MAX_RETRIES failed, retrying in 2 seconds..."
        sleep 2
    done

    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log "ERROR: Parsing service health check failed after $MAX_RETRIES attempts"
        return 1
    fi
}

start_extraction() {
    log "Starting Extraction service (port ${EXTRACTION_PORT})..."
    cd /app/python
    EXTRACTION_SERVICE_PORT=${EXTRACTION_PORT} python -m app.extraction_main &
    EXTRACTION_PID=$!
    log "Extraction service started with PID: $EXTRACTION_PID"

    log "Waiting for Extraction service health check..."
    local MAX_RETRIES=60
    local RETRY_COUNT=0
    while [ $RETRY_COUNT -lt $MAX_RETRIES ]; do
        if curl -s -f "http://localhost:${EXTRACTION_PORT}/health" > /dev/null 2>&1; then
            log "Extraction service health check passed!"
            break
        fi
        RETRY_COUNT=$((RETRY_COUNT + 1))
        log "Health check attempt $RETRY_COUNT/$MAX_RETRIES failed, retrying in 2 seconds..."
        sleep 2
    done

    if [ $RETRY_COUNT -eq $MAX_RETRIES ]; then
        log "ERROR: Extraction service health check failed after $MAX_RETRIES attempts"
        return 1
    fi
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
    log "Shutting down all services..."
    
    [ -n "$NODEJS_PID" ] && kill "$NODEJS_PID" 2>/dev/null || true
    [ -n "$SLACKBOT_PID" ] && kill "$SLACKBOT_PID" 2>/dev/null || true
    [ -n "$EMBEDDING_PID" ] && kill "$EMBEDDING_PID" 2>/dev/null || true
    [ -n "$DOCLING_PID" ] && kill "$DOCLING_PID" 2>/dev/null || true
    [ -n "$INDEXING_PID" ] && kill "$INDEXING_PID" 2>/dev/null || true
    [ -n "$CONNECTOR_PID" ] && kill "$CONNECTOR_PID" 2>/dev/null || true
    [ -n "$QUERY_PID" ] && kill "$QUERY_PID" 2>/dev/null || true
    [ -n "$PARSING_PID" ] && kill "$PARSING_PID" 2>/dev/null || true
    [ -n "$EXTRACTION_PID" ] && kill "$EXTRACTION_PID" 2>/dev/null || true
    
    wait
    log "All services stopped."
    exit 0
}

trap cleanup SIGTERM SIGINT SIGQUIT

log "=== Process Monitor Starting ==="
start_nodejs
if [ "${PIPESHUB_SKIP_SLACKBOT:-false}" = "true" ]; then
    log "PIPESHUB_SKIP_SLACKBOT=true — skipping Slack Bot"
else
    start_slackbot
fi
start_embedding
start_connector
start_query
start_docling
# Conditionally start the standalone Parsing and Extraction services.
# Set USE_PARSING_SERVICE=true in the environment to enable them.
# Must start (and pass health checks) *before* Indexing: on startup Indexing
# recovers in-progress records immediately and, when USE_PARSING_SERVICE=true,
# routes them through the Parsing service — if that service isn't up yet those
# recovery calls fail with connection errors / 503s.
if [ "${USE_PARSING_SERVICE:-false}" = "true" ]; then
    log "USE_PARSING_SERVICE=true — starting Parsing and Extraction services"
    start_parsing
    start_extraction
else
    log "USE_PARSING_SERVICE not set — skipping Parsing and Extraction services"
fi

start_indexing


log "All services started. Beginning monitoring cycle (checking every ${CHECK_INTERVAL}s)..."

while true; do
    sleep "$CHECK_INTERVAL"
    
    if ! check_process "$NODEJS_PID" "Node.js"; then
        start_nodejs
    fi
    
    if [ -n "$SLACKBOT_PID" ] && ! check_process "$SLACKBOT_PID" "Slack Bot"; then
        start_slackbot
    fi

    if ! check_process "$EMBEDDING_PID" "Embedding"; then
        start_embedding
    fi
    
    if ! check_process "$DOCLING_PID" "Docling"; then
        start_docling
    fi
    
    if ! check_process "$INDEXING_PID" "Indexing"; then
        start_indexing
    fi
    
    if ! check_process "$CONNECTOR_PID" "Connector"; then
        start_connector
    fi
    
    if ! check_process "$QUERY_PID" "Query"; then
        start_query
    fi

    if [ "${USE_PARSING_SERVICE:-false}" = "true" ]; then
        if ! check_process "$PARSING_PID" "Parsing"; then
            start_parsing
        fi
        if ! check_process "$EXTRACTION_PID" "Extraction"; then
            start_extraction
        fi
    fi
done
