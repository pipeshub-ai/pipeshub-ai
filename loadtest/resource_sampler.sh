#!/usr/bin/env bash
# resource_sampler.sh <outdir> <seconds> [interval] — CPU and RAM for every
# service, sampled continuously for the whole run.
#
# Why not `docker stats`: it returns one instantaneous, heavily smoothed number
# per container and nothing per-process, so a pegged uvicorn worker or a pegged
# gateway inside the shared app container is invisible. Two spot readings of the
# same run disagreed by 20x here, which is how a wrong "the datastores are idle"
# conclusion got made.
#
# Instead this reads the kernel's own accounting and differentiates it:
#   containers -> cgroup v2 cpu.stat usage_usec + memory.current
#   processes  -> /proc/<pid>/stat utime+stime, /proc/<pid>/statm RSS
# Both are monotonic counters, so CPU% over an interval is exact rather than
# sampled, and a burst between samples still shows up in the total.
#
# Container memory is memory.current minus inactive_file, the figure `docker
# stats` shows. Raw memory.current counts reclaimable page cache, so a datastore
# that streams files looks like it is leaking.
#
# Uvicorn workers are multiprocessing.spawn children, so their argv is
# "spawn_main(...)" and pgrep -f app.query_main only ever matches the idle
# master -- they are found by parent PID instead.
set -uo pipefail

OUTDIR=${1:?usage: resource_sampler.sh <outdir> <seconds> [interval]}
SECS=${2:-300}
INTERVAL=${3:-2}
CSV="$OUTDIR/resources.csv"
NOTES="$OUTDIR/resources.log"
mkdir -p "$OUTDIR"
# shellcheck source=_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
detect_docker || exit 1
: > "$NOTES"

# Default: every running container of the app container's compose project, so a
# stack on Kafka, etcd or OpenSearch is sampled without anyone listing it.
if [ -z "${PIPESHUB_STACK_CONTAINERS:-}" ]; then
    proj=$($DOCKER inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER" 2>/dev/null)
    if [ -n "$proj" ]; then
        PIPESHUB_STACK_CONTAINERS=$($DOCKER ps --filter "label=com.docker.compose.project=$proj" \
            --format '{{.Names}}' 2>/dev/null | tr -d '\r' | sort | tr '\n' ' ')
    fi
fi
CONTAINERS="${PIPESHUB_STACK_CONTAINERS:-$CONTAINER mongodb neo4j qdrant redis}"
CLK=$(getconf CLK_TCK 2>/dev/null || echo 100)

# container -> host cgroup dir, or "exec" when the host cannot read it (Docker
# Desktop keeps cgroups inside its VM). Under cgroup v2 a container sees its own
# cgroup at /sys/fs/cgroup, so reading from inside gives the same counters.
declare -A CG
for c in $CONTAINERS; do
    cid=$($DOCKER inspect "$c" --format '{{.Id}}' 2>/dev/null) || continue
    [ -n "$cid" ] || continue
    d=""
    for cand in "/sys/fs/cgroup/system.slice/docker-${cid}.scope" "/sys/fs/cgroup/docker/${cid}"; do
        [ -r "$cand/cpu.stat" ] && { d=$cand; break; }
    done
    if [ -n "$d" ]; then
        CG[$c]=$d
    elif $DOCKER exec "$c" cat /sys/fs/cgroup/cpu.stat >/dev/null 2>&1; then
        CG[$c]=exec
    else
        echo "skipped container $c: cgroup counters not readable from host or inside" >> "$NOTES"
        continue
    fi
    echo "container $c: ${CG[$c]}" >> "$NOTES"
done

# tracked processes inside the app container: name -> pid list
discover_pids() {
    local master workers
    master=$($DOCKER exec "$CONTAINER" pgrep -f '[a]pp.query_main' 2>/dev/null | tr -d '\r' | head -1)
    if [ -n "$master" ]; then
        workers=$($DOCKER exec "$CONTAINER" sh -c "pgrep -P $master -f multiprocessing-fork" 2>/dev/null | tr -d '\r')
        # one worker => uvicorn runs it in the master process, no children
        [ -z "$workers" ] && workers="$master"
        echo "query_worker:$(echo "$workers" | tr '\n' ' ')"
    fi
    for svc in node:'[n]ode dist/index.js' indexing:'[a]pp.indexing_main' \
               connectors:'[a]pp.connectors_main' docling:'[a]pp.docling_main' \
               embedding:'[a]pp.embedding_main'; do
        local name=${svc%%:*} pat=${svc#*:}
        local p
        p=$($DOCKER exec "$CONTAINER" pgrep -f "$pat" 2>/dev/null | tr -d '\r' | head -1)
        [ -n "$p" ] && echo "$name:$p"
    done
}

PROCSPEC=$(discover_pids)
PIDS=$(echo "$PROCSPEC" | cut -d: -f2- | tr '\n' ' ')

# one exec per sample: read every tracked pid's cpu ticks and RSS pages
read_procs() {
    $DOCKER exec "$CONTAINER" sh -c '
      for p in '"$PIDS"'; do
        if [ -r /proc/$p/stat ]; then
          set -- $(cat /proc/$p/stat)
          # fields 14,15 = utime,stime; statm field 2 = resident pages
          rss=$(awk "{print \$2}" /proc/$p/statm 2>/dev/null || echo 0)
          echo "$p $(( ${14} + ${15} )) $rss"
        fi
      done' 2>/dev/null | tr -d '\r'
}

# "<usage_usec> <memory.current> <inactive_file>" from a cpu.stat, memory.current
# and memory.stat concatenated in that order.
parse_cg() {
    awk '/^usage_usec/{u=$2} /^inactive_file/{i=$2} NF==1 && $1 ~ /^[0-9]+$/{m=$1}
         END{if (u != "") print u, (m == "" ? 0 : m), (i == "" ? 0 : i)}'
}

read_cg() {
    local c d line
    for c in "${!CG[@]}"; do
        d=${CG[$c]}
        if [ "$d" = exec ]; then
            line=$($DOCKER exec "$c" cat /sys/fs/cgroup/cpu.stat /sys/fs/cgroup/memory.current \
                /sys/fs/cgroup/memory.stat 2>/dev/null | tr -d '\r' | parse_cg)
        else
            line=$(cat "$d/cpu.stat" "$d/memory.current" "$d/memory.stat" 2>/dev/null | parse_cg)
        fi
        [ -n "$line" ] && echo "$c $line"
    done
}

echo "ts,scope,name,cpu_pct,rss_mb" > "$CSV"

declare -A P0 C0
while read -r pid ticks _; do P0[$pid]=$ticks; done < <(read_procs)
while read -r c usec _; do C0[$c]=$usec; done < <(read_cg)
T0=$(date +%s.%N)

END=$(( $(date +%s) + SECS ))
while [ "$(date +%s)" -lt "$END" ]; do
    sleep "$INTERVAL"
    NOW=$(date +%s); T1=$(date +%s.%N)
    DT=$(awk -v a="$T0" -v b="$T1" 'BEGIN{print b - a}')
    awk -v d="$DT" 'BEGIN{exit !(d <= 0)}' && continue

    while read -r pid ticks rss; do
        a=${P0[$pid]:-}
        if [ -n "$a" ] && [ "$ticks" -ge "$a" ]; then
            name=$(echo "$PROCSPEC" | awk -F: -v P="$pid" '{n=$1; for(i=2;i<=NF;i++){split($i,a," "); for(j in a) if(a[j]==P) print n}}' | head -1)
            awk -v t="$NOW" -v n="${name:-pid$pid}:$pid" -v d="$ticks" -v a="$a" -v clk="$CLK" -v dt="$DT" -v r="$rss" \
                'BEGIN{printf "%s,process,%s,%.2f,%.1f\n", t, n, (d - a) / (clk * dt) * 100, r * 4096 / 1048576}' >> "$CSV"
        fi
        P0[$pid]=$ticks
    done < <(read_procs)

    while read -r c usec mem inactive; do
        a=${C0[$c]:-}
        # A counter that went backwards means the container restarted (an OOM
        # kill, say); skip that interval rather than plot a negative CPU.
        if [ -n "$a" ] && [ "$usec" -ge "$a" ]; then
            awk -v t="$NOW" -v n="$c" -v u="$usec" -v a="$a" -v dt="$DT" -v m="$mem" -v i="$inactive" \
                'BEGIN{used = m - i; if (used < 0) used = m
                       printf "%s,container,%s,%.2f,%.1f\n", t, n, (u - a) / (1000000 * dt) * 100, used / 1048576}' >> "$CSV"
        fi
        C0[$c]=$usec
    done < <(read_cg)

    echo "$NOW,host,loadavg,$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0),0" >> "$CSV"
    T0=$T1
done
