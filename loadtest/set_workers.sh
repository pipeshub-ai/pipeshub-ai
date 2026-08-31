#!/usr/bin/env bash
# Set the query service to N uvicorn workers and wait until it is serving.
# Sets QUERY_UVICORN_WORKERS in the compose .env and recreates the container:
# that one value drives both the worker count and the per-process budget split.
#
#   ./set_workers.sh <N>
#   ./set_workers.sh restore     # back to the default of 1 worker
#
# Regenerated from ~/lt-backup/process_monitor.sh.orig every time -- never from
# a previously patched copy, so worker counts can't stack up. loadtest/pm.sh is
# deliberately NOT used: it hardcodes 4 workers and was never the file Play ran.
#
# process_monitor.sh is the container entrypoint, so changing it needs a
# `docker restart`. That is fine BETWEEN configs; never do it mid-trial.
# `docker restart` preserves docker cp'd files, so the phase-timing patch
# survives -- only `docker compose up -d` would wipe it.
set -euo pipefail

N=${1:?usage: set_workers.sh <N|restore>}
# Validated before anything is written: an unvalidated value reaches the sed as
# `--workers $N`, and the grep that follows would confirm its own bad string,
# so the container restarts into a uvicorn that cannot start.
case "$N" in
    restore) ;;
    ''|*[!0-9]*) echo "usage: set_workers.sh <N|restore> -- worker count must be a positive integer, got '$N'" >&2; exit 1 ;;
    0) echo "usage: set_workers.sh <N|restore> -- worker count must be > 0" >&2; exit 1 ;;
esac
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
# The port is interpolated into a launcher line that runs inside the container,
# so anything but a plain port number would be executed there.
case "$PIPESHUB_QUERY_PORT" in
    ''|*[!0-9]*) echo "PIPESHUB_QUERY_PORT must be 1-65535, got '$PIPESHUB_QUERY_PORT'" >&2; exit 1 ;;
esac
[ "$PIPESHUB_QUERY_PORT" -ge 1 ] && [ "$PIPESHUB_QUERY_PORT" -le 65535 ] || {
    echo "PIPESHUB_QUERY_PORT must be 1-65535, got '$PIPESHUB_QUERY_PORT'" >&2; exit 1; }

require_target || exit 1
[ "$PIPESHUB_MODE" = "docker" ] || { echo "$(basename "$0") needs PIPESHUB_MODE=docker (it restarts the container)."; exit 1; }
SETTLE=${SETTLE:-60}



# QUERY_UVICORN_WORKERS is the supported knob now. Setting it beats rewriting the
# launcher: run() reads it AND lifespan reads the same value to divide per-process
# budgets (concurrent LLM calls, storage connections). A hand-patched command line
# sets the worker count but not the budgets, so every worker claims the full caps
# and the run measures a configuration nobody ships.
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
COMPOSE_DIR=${COMPOSE_DIR:-$(cd "$HERE/../deployment/docker-compose" && pwd)}
ENV_FILE=${ENV_FILE:-$COMPOSE_DIR/.env}
[ -f "$ENV_FILE" ] || { echo "missing $ENV_FILE -- cannot set the worker count" >&2; exit 1; }

if [ "$N" = "restore" ]; then
    echo "== Restoring default query workers (1)"
    want=1
    set_to=1
else
    echo "== Setting query service to $N uvicorn worker(s) via QUERY_UVICORN_WORKERS"
    want=$N
    set_to=$N
fi

if grep -q '^#\? *QUERY_UVICORN_WORKERS=' "$ENV_FILE"; then
    sed -i "s|^#\? *QUERY_UVICORN_WORKERS=.*|QUERY_UVICORN_WORKERS=$set_to|" "$ENV_FILE"
else
    echo "QUERY_UVICORN_WORKERS=$set_to" >> "$ENV_FILE"
fi
grep -q "^QUERY_UVICORN_WORKERS=$set_to$" "$ENV_FILE" || {
    echo "failed to set QUERY_UVICORN_WORKERS in $ENV_FILE" >&2; exit 1; }

# An env change needs a recreate; `docker restart` keeps the old environment.
echo "== recreating $CONTAINER so the new value reaches the process"
(cd "$COMPOSE_DIR" && $DOCKER compose -p "${COMPOSE_PROJECT:-pipeshub-ai}" --env-file "$ENV_FILE"     up -d --no-deps --force-recreate "${COMPOSE_SERVICE:-pipeshub-ai}" >/dev/null) || {
    echo "compose recreate failed" >&2; exit 1; }


echo -n "== waiting for /health "
for _ in $(seq 1 120); do
    # Bounded: an unresponsive service would otherwise let a single curl hang
    # past the whole 120-iteration budget.
    code=$($DOCKER exec "$CONTAINER" curl -s -o /dev/null -w '%{http_code}' \
           --connect-timeout 3 --max-time 10 \
           "http://localhost:${PIPESHUB_QUERY_PORT}/health" 2>/dev/null || true)
    [ "$code" = "200" ] && break
    printf '.'; sleep 5
done
[ "${code:-}" = "200" ] || { echo " FAILED"; exit 1; }
echo " ok"

if [ "$N" = "restore" ]; then
    got=$($DOCKER exec "$CONTAINER" sh -c "ps -eo args | grep -c '[p]ython -m app.query_main'" || echo 0)
    echo "== launcher processes: $got (expect 1)"
    # Checked like the other branches: 0 means the restore did not take, and
    # anything above 1 means duplicate launchers are competing for the port.
    [ "$got" = "1" ] || { echo "LAUNCHER COUNT MISMATCH -- restore did not take" >&2; exit 1; }
elif [ "$N" = "1" ]; then
    # uvicorn only starts the multiprocess supervisor when workers > 1; at
    # --workers 1 the parent serves the app itself and spawns no children.
    got=$($DOCKER exec "$CONTAINER" sh -c "ps -eo args | grep -c '[p]ython -m uvicorn app.query_main'" || echo 0)
    echo "== serving processes: $got (expect 1, no spawned children by design)"
    [ "$got" = "1" ] || { echo "WORKER COUNT MISMATCH -- do not trial this config" >&2; exit 1; }
else
    # Count spawn_main children only. `grep -c "python -c from"` also matches
    # multiprocessing's resource_tracker, so it reports N+1 for N workers.
    got=$($DOCKER exec "$CONTAINER" sh -c '
        par=$(pgrep -f "python -m uvicorn app.query_main" | head -1)
        n=0
        for c in $(pgrep -P $par 2>/dev/null); do
            grep -qa spawn_main /proc/$c/cmdline 2>/dev/null && n=$((n+1))
        done
        echo $n' || echo 0)
    echo "== spawned workers: $got (expect $want)"
    [ "$got" = "$want" ] || { echo "WORKER COUNT MISMATCH -- do not trial this config" >&2; exit 1; }
fi

echo "== phase instrumentation still applied:"
$DOCKER exec "$CONTAINER" grep -c '_phase_mark("' \
    /app/python/app/api/routes/chatbot.py /app/python/app/agents/chat_modes/bridge.py || true

echo "== settling ${SETTLE}s (restart also bounces indexing/connector; F6 showed"
echo "   background indexing inflates latency and contaminated an early baseline)"
sleep "$SETTLE"
echo "== ready"
