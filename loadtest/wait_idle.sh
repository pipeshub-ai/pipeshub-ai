#!/usr/bin/env bash
# wait_idle.sh <max_cpu_pct> <timeout_s> [window_s] — block until the stack is quiet.
#
# Quiet means the summed CPU of every container in the app's compose project
# stayed under <max_cpu_pct> (percent of one core) across a <window_s> window,
# measured from cgroup counters. Background indexing spoiled an earlier
# baseline, and "looks idle in docker stats" is not a measurement, so a trial
# should not start until this passes. Exits 0 when quiet, 1 on timeout.
set -uo pipefail
MAX=${1:?usage: wait_idle.sh <max_cpu_pct> <timeout_s> [window_s]}
TIMEOUT=${2:?usage: wait_idle.sh <max_cpu_pct> <timeout_s> [window_s]}
WINDOW=${3:-60}
# shellcheck source=_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
detect_docker || exit 1

proj=$($DOCKER inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER" 2>/dev/null)
ids=$($DOCKER ps -q --no-trunc ${proj:+--filter "label=com.docker.compose.project=$proj"} 2>/dev/null)
[ -n "$proj" ] || ids=$($DOCKER inspect "$CONTAINER" --format '{{.Id}}')

usage_usec() {
    local id tot=0 v
    for id in $ids; do
        v=$(awk '/^usage_usec/{print $2}' "/sys/fs/cgroup/system.slice/docker-$id.scope/cpu.stat" 2>/dev/null \
            || $DOCKER exec "$id" awk '/^usage_usec/{print $2}' /sys/fs/cgroup/cpu.stat 2>/dev/null)
        tot=$((tot + ${v:-0}))
    done
    echo "$tot"
}

start=$(date +%s)
while :; do
    a=$(usage_usec); t0=$(date +%s.%N)
    sleep "$WINDOW"
    b=$(usage_usec); t1=$(date +%s.%N)
    cpu=$(awk -v a="$a" -v b="$b" -v t0="$t0" -v t1="$t1" 'BEGIN{printf "%.0f", (b - a) / ((t1 - t0) * 1e6) * 100}')
    waited=$(( $(date +%s) - start ))
    if [ "$cpu" -le "$MAX" ]; then
        echo "   stack quiet: ${cpu}% of one core over ${WINDOW}s (limit ${MAX}%), waited ${waited}s"
        exit 0
    fi
    echo "   stack busy: ${cpu}% of one core over ${WINDOW}s (limit ${MAX}%), waited ${waited}s"
    [ "$waited" -ge "$TIMEOUT" ] && { echo "   gave up after ${TIMEOUT}s"; exit 1; }
done
