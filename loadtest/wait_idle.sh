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

# docker.exe on Git Bash writes CRLF and command substitution drops only the \n,
# so every value from docker is stripped of \r before it is used as a filter, a
# path or an exec argument.
proj=$($DOCKER inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER" 2>/dev/null | tr -d '\r')

# Rediscovered every window, so a container replaced mid-wait is followed.
list_ids() {
    if [ -n "$proj" ]; then
        $DOCKER ps -q --no-trunc --filter "label=com.docker.compose.project=$proj" 2>/dev/null | tr -d '\r'
    else
        $DOCKER inspect "$CONTAINER" --format '{{.Id}}' 2>/dev/null | tr -d '\r'
    fi
}

# usage_usec of one container, empty when unreadable. Same lookup as
# resource_sampler.sh: the host cgroup dir, else cpu.stat from inside the
# container (Docker Desktop, rootless). `cat` rather than awk inside, since
# slim images such as qdrant ship no awk.
usage_usec() {
    local id=$1 d
    for d in "/sys/fs/cgroup/system.slice/docker-$id.scope" "/sys/fs/cgroup/docker/$id"; do
        [ -r "$d/cpu.stat" ] && { awk '/^usage_usec/{print $2}' "$d/cpu.stat" | tr -d '\r'; return; }
    done
    $DOCKER exec "$id" cat /sys/fs/cgroup/cpu.stat 2>/dev/null | tr -d '\r' | awk '/^usage_usec/{print $2}'
}

# Reads every listed container's counter into S[id]; N is how many were listed.
sample() {
    local id v
    S=(); N=0
    for id in $(list_ids); do
        N=$((N + 1)); v=$(usage_usec "$id")
        [ -n "$v" ] && S[$id]=$v
    done
}

declare -A A B S
N=0; seen=0; blind=0
start=$(date +%s)
while :; do
    sample
    A=(); for id in "${!S[@]}"; do A[$id]=${S[$id]}; done
    listed=$N
    if [ "${#A[@]}" -eq 0 ]; then
        waited=$(( $(date +%s) - start ))
        # Nothing readable before any counter has ever been read (cgroup v1, or
        # docker not answering yet) cannot be waited out; after one has been
        # read, an empty sample is a hiccup and the stack is sampled again.
        if [ "$seen" -eq 0 ] && [ "$blind" -ge 2 ]; then
            echo "   WARN: no container CPU counter is readable (needs cgroup v2), not waiting for idle"
            exit 0
        fi
        blind=$((blind + 1))
        echo "   no container CPU counter readable, sampling again (waited ${waited}s)"
        [ "$waited" -ge "$TIMEOUT" ] && { echo "   gave up after ${TIMEOUT}s"; exit 1; }
        sleep 2
        continue
    fi
    seen=1
    t0=$(date +%s.%N)
    sleep "$WINDOW"
    sample
    B=(); for id in "${!S[@]}"; do B[$id]=${S[$id]}; done
    t1=$(date +%s.%N)
    waited=$(( $(date +%s) - start ))
    unread=$(( listed - ${#A[@]} ))
    note=""; [ "$unread" -gt 0 ] && note=", $unread container(s) unreadable and skipped"

    # A container that vanished, appeared, became readable or unreadable, or
    # whose counter went backwards (restart) says nothing about load: sample
    # again, as resource_sampler.sh does. One unreadable at both ends stays out.
    reset=0; used=0
    for id in "${!A[@]}"; do
        if [ -z "${B[$id]:-}" ] || [ "${B[$id]}" -lt "${A[$id]}" ]; then reset=1; break; fi
        used=$(( used + B[$id] - A[$id] ))
    done
    for id in "${!B[@]}"; do [ -z "${A[$id]:-}" ] && reset=1; done
    if [ "$reset" -eq 1 ]; then
        echo "   container restarted, stopped or changed readability during the window, sampling again (waited ${waited}s)"
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
