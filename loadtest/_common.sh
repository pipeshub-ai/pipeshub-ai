#!/usr/bin/env bash
# Shared config and environment detection. Sourced by the other scripts.
#
# Loads `.env` (see `.env.example`), then fills in the rest with Docker
# defaults; real environment variables win over the file.
#
# The checks here fail loudly on purpose: an unreachable target used to surface
# as "0 MB" / "0.0 req/min", which reads as a measurement of an idle service
# rather than as nothing having been measured.

_LT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Python defaults stdout to the ANSI codepage on Windows, which turns the
# report's non-ASCII punctuation into mojibake mid-table.
export PYTHONIOENCODING=${PYTHONIOENCODING:-utf-8}

if [ -f "$_LT_DIR/.env" ]; then
    # Snapshot what the caller already exported and restore it afterwards.
    # Sourcing alone lets the FILE win, which is backwards: it silently ignores
    # `PIPESHUB_QUERY= ./perftest.sh ...` and every other one-off override.
    # One `declare -p` per name. Passing the whole list at once dumps the ENTIRE
    # environment when the list is empty — the common case on a fresh shell —
    # and eval would then replay ~130 unrelated declarations. The class allows
    # digits so a name like PIPESHUB_WORKER_2 is not silently dropped.
    _lt_preset=""
    for _lt_name in $(env | sed -n 's/^\(PIPESHUB_[A-Z0-9_]*\|TOKEN\)=.*/\1/p'); do
        _lt_preset+="$(declare -p "$_lt_name" 2>/dev/null)"$'\n'
    done
    set -a
    # shellcheck disable=SC1091
    . "$_LT_DIR/.env"
    set +a
    [ -n "${_lt_preset//[[:space:]]/}" ] && eval "$_lt_preset"
    unset _lt_preset _lt_name
fi

PIPESHUB_MODE=${PIPESHUB_MODE:-auto}
PIPESHUB_HOST=${PIPESHUB_HOST:-http://localhost:3000}
PIPESHUB_CONTAINER=${PIPESHUB_CONTAINER:-pipeshub-ai}
PIPESHUB_QUERY_PORT=${PIPESHUB_QUERY_PORT:-8000}
PIPESHUB_QUERY_LOG=${PIPESHUB_QUERY_LOG:-}
PIPESHUB_QUERY_PID=${PIPESHUB_QUERY_PID:-}
PIPESHUB_THINK_TIME=${PIPESHUB_THINK_TIME:-3}
CONTAINER=$PIPESHUB_CONTAINER

case "$PIPESHUB_MODE" in
    auto|docker|native) ;;
    *) echo "ERROR: PIPESHUB_MODE must be 'auto', 'docker' or 'native', got '$PIPESHUB_MODE'." >&2; return 1 2>/dev/null || exit 1 ;;
esac

# `docker` needs sudo on a stock Linux daemon and rejects it under Docker
# Desktop / rootless / docker-group setups (on Windows, sudo is often disabled
# outright). Probe rather than assume.
detect_docker() {
    if [ -n "${DOCKER:-}" ]; then return 0; fi
    if docker info >/dev/null 2>&1; then
        DOCKER="docker"
    elif sudo -n docker info >/dev/null 2>&1; then
        DOCKER="sudo docker"
    else
        echo "ERROR: cannot talk to the docker daemon (tried with and without sudo)." >&2
        echo "       Is Docker running, and may this user reach it?" >&2
        return 1
    fi
}

# `python3` is not a usable name everywhere: on Windows it resolves to the
# Microsoft Store stub, which prints an ad to stdout and exits non-zero.
detect_python() {
    if [ -n "${PYTHON:-}" ]; then return 0; fi
    for c in python3 python; do
        if command -v "$c" >/dev/null 2>&1 && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 8) else 1)' >/dev/null 2>&1; then
            PYTHON="$c"
            return 0
        fi
    done
    echo "ERROR: no usable Python 3.8+ on PATH (tried python3, python)." >&2
    return 1
}

# py-spy needs root to attach to another process on a stock Linux host; as the
# same user on a Windows/macOS native run it does not, and `sudo` may not even
# be enabled. Empty is the safe default — a needless sudo turns a working
# profile into a permission prompt or a hard failure.
detect_sudo() {
    if [ -n "${SUDO+x}" ]; then return 0; fi
    if [ "$(id -u 2>/dev/null || echo 0)" = "0" ]; then
        SUDO=""
    elif sudo -n true 2>/dev/null; then
        SUDO="sudo"
    else
        SUDO=""
    fi
}

container_state() {
    detect_docker 2>/dev/null || return 1
    $DOCKER inspect -f '{{.State.Status}}' "$CONTAINER" 2>/dev/null
}

# A running container wins over a host process: if both exist, the container is
# the deployment under test and the host process is a stray dev server. A
# container that exists but is stopped does NOT win — that is exactly the
# machine where PipesHub was moved to host processes and the container is a
# leftover.
resolve_mode() {
    [ "$PIPESHUB_MODE" = "auto" ] || return 0
    if [ "$(container_state)" = "running" ]; then
        PIPESHUB_MODE=docker
    elif query_pid >/dev/null 2>&1; then
        PIPESHUB_MODE=native
    else
        echo "ERROR: found neither a running '$CONTAINER' container nor a query" >&2
        echo "       service process on port $PIPESHUB_QUERY_PORT." >&2
        echo "       Start one, or set PIPESHUB_MODE / PIPESHUB_CONTAINER /" >&2
        echo "       PIPESHUB_QUERY_PID in .env — see .env.example." >&2
        return 1
    fi
}

# Fails when the thing the caller is about to measure cannot be reached, so no
# script ever reports a number it could not have observed.
require_target() {
    detect_python || return 1
    resolve_mode || return 1
    if [ "$PIPESHUB_MODE" = "docker" ]; then
        detect_docker || return 1
        local state
        state=$(container_state) || state=""
        if [ -z "$state" ]; then
            echo "ERROR: no container named '$CONTAINER'." >&2
            echo "       Set PIPESHUB_CONTAINER, or PIPESHUB_MODE=native if PipesHub" >&2
            echo "       runs as host processes here. See .env.example." >&2
            return 1
        fi
        [ "$state" = "running" ] || {
            echo "ERROR: container '$CONTAINER' is '$state', not running." >&2
            echo "       Start it: docker start $CONTAINER" >&2
            return 1
        }
    else
        query_pid >/dev/null || {
            echo "ERROR: cannot find the query service process." >&2
            echo "       Set PIPESHUB_QUERY_PID in .env (the pid serving port $PIPESHUB_QUERY_PORT)." >&2
            return 1
        }
    fi
}

# Echoes the pid or fails silently — callers own the error message, so this is
# also usable as a probe during mode detection.
query_pid() {
    if [ -n "$PIPESHUB_QUERY_PID" ]; then echo "$PIPESHUB_QUERY_PID"; return 0; fi
    local pid=""
    if command -v pgrep >/dev/null 2>&1; then
        # Lowest match = the master. `query_rss_mb` sums its children and
        # py-spy runs with --subprocesses, so starting at the master covers the
        # workers; starting at a worker would miss its siblings.
        pid=$(pgrep -f 'app[._]query_main' 2>/dev/null | sort -n | head -1)
    fi
    if [ -z "$pid" ] && command -v powershell.exe >/dev/null 2>&1; then
        pid=$(powershell.exe -NoProfile -Command \
            "(Get-NetTCPConnection -LocalPort $PIPESHUB_QUERY_PORT -State Listen -EA SilentlyContinue | Select-Object -First 1).OwningProcess" \
            2>/dev/null | tr -d '\r\n ')
    fi
    [ -n "$pid" ] || return 1
    echo "$pid"
}

# A "mark" is an opaque token meaning "now", redeemed later by log_read.
# Docker filters by time; native records a byte offset, which is exact and
# needs no timestamp parsing.
log_mark() {
    if [ "$PIPESHUB_MODE" = "docker" ]; then
        date -u +%s
    elif [ -n "$PIPESHUB_QUERY_LOG" ] && [ -f "$PIPESHUB_QUERY_LOG" ]; then
        wc -c < "$PIPESHUB_QUERY_LOG" | tr -d ' '
    else
        echo 0
    fi
}

log_read() {
    local mark=$1
    if [ "$PIPESHUB_MODE" = "docker" ]; then
        $DOCKER logs "$CONTAINER" --since "$mark" ${2:+--until "$2"} 2>&1
    elif [ -n "$PIPESHUB_QUERY_LOG" ] && [ -f "$PIPESHUB_QUERY_LOG" ]; then
        tail -c "+$((mark + 1))" "$PIPESHUB_QUERY_LOG" 2>/dev/null
    fi
}

log_available() {
    [ "$PIPESHUB_MODE" = "docker" ] && return 0
    [ -n "$PIPESHUB_QUERY_LOG" ] && [ -f "$PIPESHUB_QUERY_LOG" ]
}

# Total RSS of the query service in MB (parent + workers), one number.
query_rss_mb() {
    if [ "$PIPESHUB_MODE" = "docker" ]; then
        $DOCKER exec "$CONTAINER" sh -c '
            par=$(pgrep -f "python -m uvicorn app.query_main" | head -1)
            [ -n "$par" ] || par=$(pgrep -f "python -m app.query_main" | head -1)
            [ -n "$par" ] || { echo 0; exit 0; }
            tot=0
            for p in $par $(pgrep -P $par 2>/dev/null); do
                r=$(awk "/^VmRSS:/{print \$2}" /proc/$p/status 2>/dev/null)
                tot=$((tot + ${r:-0}))
            done
            echo $((tot / 1024))' 2>/dev/null || echo 0
        return
    fi

    local pid
    pid=$(query_pid) || { echo 0; return; }
    if [ -r "/proc/$pid/status" ]; then
        local tot=0 r
        for p in $pid $(pgrep -P "$pid" 2>/dev/null); do
            r=$(awk '/^VmRSS:/{print $2}' "/proc/$p/status" 2>/dev/null)
            tot=$((tot + ${r:-0}))
        done
        echo $((tot / 1024))
    elif command -v powershell.exe >/dev/null 2>&1; then
        # PrivateMemorySize64, NOT WorkingSet64. Windows trims the working set
        # of an idle process, so WorkingSet reports ~15 MB for a query service
        # holding ~1 GB of commit — a memory graph built on it plots OS trimming
        # rather than the application. Private bytes is the stable analogue of
        # Linux VmRSS for "how much has this grown under load".
        powershell.exe -NoProfile -Command \
            "\$ids=@($pid); \$ids += (Get-CimInstance Win32_Process -Filter 'ParentProcessId=$pid' -EA SilentlyContinue).ProcessId
             [int](((Get-Process -Id \$ids -EA SilentlyContinue) | Measure-Object PrivateMemorySize64 -Sum).Sum / 1MB)" \
            2>/dev/null | tr -d '\r\n ' || echo 0
    else
        echo 0
    fi
}
