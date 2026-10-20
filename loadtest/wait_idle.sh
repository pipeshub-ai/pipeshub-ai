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

# Rediscovered every window, so a container replaced mid-wait is followed.
list_ids() {
    if [ -n "$proj" ]; then
        $DOCKER ps -q --no-trunc --filter "label=com.docker.compose.project=$proj" 2>/dev/null
    else
        $DOCKER inspect "$CONTAINER" --format '{{.Id}}' 2>/dev/null
    fi
}

# usage_usec of one container, empty when unreadable. Same lookup as
# resource_sampler.sh: the host cgroup dir, else cpu.stat from inside the
# container (Docker Desktop, rootless). `cat` rather than awk inside, since
# slim images such as qdrant ship no awk.
usage_usec() {
    local id=$1 d
    for d in "/sys/fs/cgroup/system.slice/docker-$id.scope" "/sys/fs/cgroup/docker/$id"; do
        [ -r "$d/cpu.stat" ] && { awk '/^usage_usec/{print $2}' "$d/cpu.stat"; return; }
    done
    $DOCKER exec "$id" cat /sys/fs/cgroup/cpu.stat 2>/dev/null | awk '/^usage_usec/{print $2}'
}

declare -A A B
start=$(date +%s)
while :; do
    A=(); B=()
    ids=$(list_ids)
    for id in $ids; do v=$(usage_usec "$id"); [ -n "$v" ] && A[$id]=$v; done
    if [ "${#A[@]}" -eq 0 ]; then
        echo "   WARN: no container CPU counter is readable (needs cgroup v2), not waiting for idle"
        exit 0
    fi
    t0=$(date +%s.%N)
    sleep "$WINDOW"
    for id in "${!A[@]}"; do v=$(usage_usec "$id"); [ -n "$v" ] && B[$id]=$v; done
    t1=$(date +%s.%N)
    waited=$(( $(date +%s) - start ))
    unread=$(( $(wc -w <<< "$ids") - ${#A[@]} ))
    note=""; [ "$unread" -gt 0 ] && note=", $unread container(s) unreadable and skipped"

    # A container that vanished or whose counter went backwards (restart) says
    # nothing about load: sample again, as resource_sampler.sh does.
    reset=0; used=0
    for id in "${!A[@]}"; do
        if [ -z "${B[$id]:-}" ] || [ "${B[$id]}" -lt "${A[$id]}" ]; then reset=1; break; fi
        used=$(( used + B[$id] - A[$id] ))
    done
    if [ "$reset" -eq 1 ]; then
        echo "   container restarted or stopped during the window, sampling again (waited ${waited}s)"
    else
        cpu=$(awk -v u="$used" -v t0="$t0" -v t1="$t1" 'BEGIN{printf "%.0f", u / ((t1 - t0) * 1e6) * 100}')
        if [ "$cpu" -le "$MAX" ]; then
            echo "   stack quiet: ${cpu}% of one core over ${WINDOW}s (limit ${MAX}%${note}), waited ${waited}s"
            exit 0
        fi
        echo "   stack busy: ${cpu}% of one core over ${WINDOW}s (limit ${MAX}%${note}), waited ${waited}s"
    fi
    [ "$waited" -ge "$TIMEOUT" ] && { echo "   gave up after ${TIMEOUT}s"; exit 1; }
done
