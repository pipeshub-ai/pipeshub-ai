#!/usr/bin/env bash
# Restart ONLY the query service inside the container and wait for it to be
# healthy again. process_monitor.sh polls every CHECK_INTERVAL (20s) and
# restarts a dead query process, so killing it is the whole mechanism -- the
# container itself is never touched.
set -euo pipefail

. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
require_target || exit 1
[ "$PIPESHUB_MODE" = "docker" ] || { echo "$(basename "$0") needs PIPESHUB_MODE=docker (it restarts the container)."; exit 1; }
PORT=${PIPESHUB_QUERY_PORT:-8000}
DEADLINE=${DEADLINE:-180}

echo
echo "== Restart query service (container untouched)"
old=$($DOCKER exec "$CONTAINER" pgrep -f 'app\.query_main' | head -1 || true)
echo "   old pid: ${old:-none}"

$DOCKER exec "$CONTAINER" pkill -f 'app\.query_main' || true

start=$(date +%s)
while :; do
    now=$(( $(date +%s) - start ))
    if [ "$now" -ge "$DEADLINE" ]; then
        echo "   FAILED: query service not healthy after ${DEADLINE}s" >&2
        exit 1
    fi
    code=$($DOCKER exec "$CONTAINER" \
        curl -s -o /dev/null -w '%{http_code}' --connect-timeout 3 --max-time 10 \
        "http://localhost:${PORT}/health" 2>/dev/null || true)
    new=$($DOCKER exec "$CONTAINER" pgrep -f 'app\.query_main' | head -1 || true)
    # The pid MUST have changed. Polling health alone returns 200 from the
    # still-running old process before the kill lands, which reports success
    # while the old code is still loaded -- silently measuring stale code.
    if [ "$code" = "200" ] && [ -n "$new" ] && [ "$new" != "$old" ]; then
        echo "   healthy after ${now}s (new pid: ${new})"
        exit 0
    fi
    sleep 3
done
