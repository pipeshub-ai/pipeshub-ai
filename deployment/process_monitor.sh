#!/bin/bash

# Process monitor script with parent-child process management
set -e

# Root here means docker-entrypoint.sh was bypassed (a compose file from before
# it existed), so services may create root-owned files in the data volumes.
# Dropping its markers makes the entrypoint's next start re-run the full pass.
if [ "$(id -u)" = "0" ]; then
    rm -f /data/pipeshub/.pipeshub-ownership-v1 /root/.local/.pipeshub-ownership-v1 \
        /root/.cache/huggingface/.pipeshub-ownership-v1 || true
fi

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

# Startup limits. A service is waited on while it is alive and using CPU, because on a slow machine
# importing the Python services takes minutes and a fixed window kills them half way through. It is
# given up on when it died, when it used no CPU for STARTUP_STALL_SECONDS (hung), or when
# STARTUP_MAX_SECONDS passed (bounded even if something spins).
STARTUP_STALL_SECONDS=${STARTUP_STALL_SECONDS:-120}
STARTUP_MAX_SECONDS=${STARTUP_MAX_SECONDS:-900}
STARTUP_POLL_SECONDS=${STARTUP_POLL_SECONDS:-2}

# A mistyped limit must not disable the wait or turn it into a busy loop, so anything unusable falls back to the default.
check_startup_limits() {
    local name default floor value
    # Each limit is checked against the one before it, which has already been repaired.
    for spec in "STARTUP_POLL_SECONDS 2 1" "STARTUP_STALL_SECONDS 120 STARTUP_POLL_SECONDS" "STARTUP_MAX_SECONDS 900 STARTUP_STALL_SECONDS"; do
        read -r name default floor <<< "$spec"
        [ "$floor" = "1" ] || floor=${!floor}
        value=${!name}
        case $value in
            ''|*[!0-9]*) value=bad ;;
        esac
        if [ "$value" = "bad" ] || [ "$value" -lt "$floor" ]; then
            log "WARNING: ${name}=${!name} is not an integer of at least ${floor}; using ${default}"
            printf -v "$name" '%s' "$default"
        fi
    done
}
check_startup_limits

# CPU ticks (user + system, including reaped children) used by a process and all its live descendants; empty
# when it is gone. Descendants count because a service may sit in a wrapper while a child does the work.
cpu_ticks() {
    # The command name may contain spaces and parentheses, so cut up to the last ") " before splitting fields.
    cat /proc/[0-9]*/stat 2>/dev/null | sed 's/^\([0-9]*\) (.*) \(.*\)/\1 \2/' | awk -v root="$1" '
        { ppid[$1] = $3; t[$1] = $13 + $14 + $15 + $16 }
        END {
            if (!(root in t)) exit
            mark[root] = 1
            do {
                grew = 0
                for (p in ppid) if (!(p in mark) && (ppid[p] in mark)) { mark[p] = 1; grew = 1 }
            } while (grew)
            for (p in mark) sum += t[p]
            print sum
        }'
}

# wait_ready <name> <pid> <health url>: 0 once the URL answers, 1 if the process died, hung or took too long.
wait_ready() {
    local name=$1 pid=$2 url=$3
    local began=$SECONDS last_move=$SECONDS last_ticks="" ticks request_limit

    log "Waiting for $name health check..."
    while true; do
        # A service that takes the connection and never answers must not hold this past the limits below.
        request_limit=$((STARTUP_MAX_SECONDS - (SECONDS - began)))
        [ "$request_limit" -le "$STARTUP_STALL_SECONDS" ] || request_limit=$STARTUP_STALL_SECONDS
        [ "$request_limit" -ge 1 ] || request_limit=1
        if curl -s -f --max-time "$request_limit" "$url" > /dev/null 2>&1; then
            log "$name health check passed after $((SECONDS - began))s!"
            return 0
        fi
        if ! kill -0 "$pid" 2>/dev/null; then
            log "ERROR: $name (PID: $pid) exited before it became healthy"
            return 1
        fi
        ticks=$(cpu_ticks "$pid")
        # Unreadable CPU counters count as progress: only the overall bound applies then.
        if [ -z "$ticks" ] || [ "$ticks" != "$last_ticks" ]; then
            last_ticks=$ticks
            last_move=$SECONDS
        fi
        if [ $((SECONDS - last_move)) -ge "$STARTUP_STALL_SECONDS" ]; then
            log "ERROR: $name (PID: $pid) used no CPU for ${STARTUP_STALL_SECONDS}s and is not healthy"
            return 1
        fi
        if [ $((SECONDS - began)) -ge "$STARTUP_MAX_SECONDS" ]; then
            log "ERROR: $name (PID: $pid) not healthy after ${STARTUP_MAX_SECONDS}s"
            return 1
        fi
        sleep "$STARTUP_POLL_SECONDS"
    done
}

# await_services <name> <pid> <url> ...: waits for every service at once. Returns 1 as soon as one fails, without
# waiting for the others; only the waiters started here are stopped.
await_services() {
    local dir i=0 pids=() names=() done_count=0 total result
    dir=$(mktemp -d)
    while [ $# -ge 3 ]; do
        ( set +e; wait_ready "$1" "$2" "$3"; echo $? > "$dir/$i" ) &      # errexit would end the waiter at its failure, before the status is written
        pids+=($!)
        i=$((i + 1))
        shift 3
    done
    total=$i
    while [ "$done_count" -lt "$total" ]; do
        done_count=0
        for ((i = 0; i < total; i++)); do
            [ -s "$dir/$i" ] || continue
            done_count=$((done_count + 1))
            read -r result < "$dir/$i"
            if [ "$result" != "0" ]; then
                kill "${pids[@]}" 2>/dev/null || true
                wait "${pids[@]}" 2>/dev/null || true
                rm -rf "$dir"
                return 1
            fi
        done
        [ "$done_count" -lt "$total" ] && sleep 0.5
    done
    rm -rf "$dir"
    return 0
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

spawn_embedding() {
    log "Starting Embedding service..."
    cd /app/python
    python -m app.embedding_main &
    EMBEDDING_PID=$!
    log "Embedding service started with PID: $EMBEDDING_PID"
}

start_embedding() {
    spawn_embedding
    wait_ready "Embedding service" "$EMBEDDING_PID" "http://localhost:${EMBEDDING_PORT}/health"
}

start_docling() {
    log "Starting Docling service..."
    cd /app/python
    python -m app.docling_main &
    DOCLING_PID=$!
    log "Docling started with PID: $DOCLING_PID"
}

# The per-service logins stay in this shell but are not inherited: a child gets only its own, as PG_CORE_URL.
# All processes here share one user, so this limits what a bug in one service can reach; isolating a compromised
# process from another's login needs separate containers, where each one is given only its own URL.
export -n PG_CORE_QUERY_URL PG_CORE_INDEXING_URL PG_CORE_CONNECTORS_URL
# A process whose own login is set (PG_CORE_<SERVICE>_URL, e.g. PG_CORE_QUERY_URL) connects with it, as its own role,
# with PG_<SERVICE>_POOL_MAX as its pool size when set. Chosen by the process, not the role: a process without its own
# login keeps the shared PG_CORE_URL and role exactly as before. Call it in the subshell that execs the service.
use_own_pg_login() {
    local up url_var pool_var
    up=$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')
    url_var="PG_CORE_${up}_URL"
    pool_var="PG_${up}_POOL_MAX"
    [ -n "${!url_var:-}" ] || return 0
    # The pgvector store connects with the same login (it reads PGVECTOR_DSN, else a DSN in the configuration store, which every service would share).
    export PG_RUNTIME_SERVICE="$1" PG_CORE_URL="${!url_var}" PGVECTOR_DSN="${!url_var}"
    if [ -n "${!pool_var:-}" ]; then export PG_POOL_MAX="${!pool_var}"; fi
}

start_indexing() {
    log "Starting Indexing service..."
    cd /app/python
    (use_own_pg_login indexing; exec python -m app.indexing_main) &
    INDEXING_PID=$!
    log "Indexing started with PID: $INDEXING_PID"
}

spawn_connector() {
    log "Starting Connector service..."
    cd /app/python
    (use_own_pg_login connectors; exec python -m app.connectors_main) &
    CONNECTOR_PID=$!
    log "Connector started with PID: $CONNECTOR_PID"
}

start_connector() {
    spawn_connector
    wait_ready "Connector" "$CONNECTOR_PID" "http://localhost:8088/health"
}

start_query() {
    log "Starting Query service..."
    cd /app/python
    (use_own_pg_login query; exec python -m app.query_main) &
    QUERY_PID=$!
    log "Query started with PID: $QUERY_PID"
}

spawn_parsing() {
    log "Starting Parsing service (port ${PARSING_PORT})..."
    cd /app/python
    PARSING_SERVICE_PORT=${PARSING_PORT} python -m app.parsing_main &
    PARSING_PID=$!
    log "Parsing service started with PID: $PARSING_PID"
}

start_parsing() {
    spawn_parsing
    wait_ready "Parsing service" "$PARSING_PID" "http://localhost:${PARSING_PORT}/health"
}

spawn_extraction() {
    log "Starting Extraction service (port ${EXTRACTION_PORT})..."
    cd /app/python
    EXTRACTION_SERVICE_PORT=${EXTRACTION_PORT} python -m app.extraction_main &
    EXTRACTION_PID=$!
    log "Extraction service started with PID: $EXTRACTION_PID"
}

start_extraction() {
    spawn_extraction
    wait_ready "Extraction service" "$EXTRACTION_PID" "http://localhost:${EXTRACTION_PORT}/health"
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
# Python services import for minutes on a small machine and the embedding server also loads its model
# before it listens, so start them all first and wait for them together instead of one behind another.
spawn_embedding
spawn_connector
start_query
start_docling
# Conditionally start the standalone Parsing and Extraction services.
# Set USE_PARSING_SERVICE=true in the environment to enable them.
# Must be healthy *before* Indexing: on startup Indexing
# recovers in-progress records immediately and, when USE_PARSING_SERVICE=true,
# routes them through the Parsing service — if that service isn't up yet those
# recovery calls fail with connection errors / 503s.
if [ "${USE_PARSING_SERVICE:-false}" = "true" ]; then
    log "USE_PARSING_SERVICE=true — starting Parsing and Extraction services"
    spawn_parsing
    spawn_extraction
else
    log "USE_PARSING_SERVICE not set — skipping Parsing and Extraction services"
fi

# The first failed wait ends the monitor, as it did when the waits were serial.
if [ "${USE_PARSING_SERVICE:-false}" = "true" ]; then
    await_services \
        "Embedding service" "$EMBEDDING_PID" "http://localhost:${EMBEDDING_PORT}/health" \
        "Connector" "$CONNECTOR_PID" "http://localhost:8088/health" \
        "Parsing service" "$PARSING_PID" "http://localhost:${PARSING_PORT}/health" \
        "Extraction service" "$EXTRACTION_PID" "http://localhost:${EXTRACTION_PORT}/health" || exit 1
else
    await_services \
        "Embedding service" "$EMBEDDING_PID" "http://localhost:${EMBEDDING_PORT}/health" \
        "Connector" "$CONNECTOR_PID" "http://localhost:8088/health" || exit 1
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
