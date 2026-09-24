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
# Uvicorn workers are multiprocessing.spawn children, so their argv is
# "spawn_main(...)" and pgrep -f app.query_main only ever matches the idle
# master -- they are found by parent PID instead.
set -uo pipefail

OUTDIR=${1:?usage: resource_sampler.sh <outdir> <seconds> [interval]}
SECS=${2:-300}
INTERVAL=${3:-2}
CONTAINER=${PIPESHUB_CONTAINER:-pipeshub-ai}
CSV="$OUTDIR/resources.csv"
mkdir -p "$OUTDIR"

SUDO=""
[ "$(id -u)" -ne 0 ] && command -v sudo >/dev/null 2>&1 && SUDO="sudo"
DOCKER="$SUDO docker"

CONTAINERS="${PIPESHUB_STACK_CONTAINERS:-$CONTAINER mongodb neo4j qdrant redis}"
CLK=$(getconf CLK_TCK 2>/dev/null || echo 100)

# container id -> cgroup dir
declare -A CG
for c in $CONTAINERS; do
    cid=$($DOCKER inspect "$c" --format '{{.Id}}' 2>/dev/null) || continue
    [ -n "$cid" ] || continue
    d="/sys/fs/cgroup/system.slice/docker-${cid}.scope"
    [ -r "$d/cpu.stat" ] || d=$($SUDO find /sys/fs/cgroup -maxdepth 4 -type d -name "*${cid}*" 2>/dev/null | head -1)
    [ -n "$d" ] && CG[$c]="$d"
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

read_cg() {
    local c d
    for c in "${!CG[@]}"; do
        d=${CG[$c]}
        u=$($SUDO awk '/^usage_usec/{print $2}' "$d/cpu.stat" 2>/dev/null)
        m=$($SUDO cat "$d/memory.current" 2>/dev/null)
        [ -n "$u" ] && echo "$c $u ${m:-0}"
    done
}

echo "ts,scope,name,cpu_pct,rss_mb" > "$CSV"

declare -A P0 R0 C0 M0
while read -r pid ticks rss; do P0[$pid]=$ticks; R0[$pid]=$rss; done < <(read_procs)
while read -r c usec mem; do C0[$c]=$usec; M0[$c]=$mem; done < <(read_cg)
T0=$(date +%s.%N)

END=$(( $(date +%s) + SECS ))
while [ "$(date +%s)" -lt "$END" ]; do
    sleep "$INTERVAL"
    NOW=$(date +%s); T1=$(date +%s.%N)
    DT=$(echo "$T1 - $T0" | bc)
    [ "$(echo "$DT <= 0" | bc)" = "1" ] && continue

    while read -r pid ticks rss; do
        a=${P0[$pid]:-}
        if [ -n "$a" ]; then
            pct=$(echo "scale=2; ($ticks - $a) / ($CLK * $DT) * 100" | bc)
            mb=$(echo "scale=1; $rss * 4096 / 1048576" | bc)
            name=$(echo "$PROCSPEC" | awk -F: -v P="$pid" '{n=$1; for(i=2;i<=NF;i++){split($i,a," "); for(j in a) if(a[j]==P) print n}}' | head -1)
            echo "$NOW,process,${name:-pid$pid}:$pid,$pct,$mb" >> "$CSV"
        fi
        P0[$pid]=$ticks; R0[$pid]=$rss
    done < <(read_procs)

    while read -r c usec mem; do
        a=${C0[$c]:-}
        if [ -n "$a" ]; then
            pct=$(echo "scale=2; ($usec - $a) / (1000000 * $DT) * 100" | bc)
            mb=$(echo "scale=1; $mem / 1048576" | bc)
            echo "$NOW,container,$c,$pct,$mb" >> "$CSV"
        fi
        C0[$c]=$usec; M0[$c]=$mem
    done < <(read_cg)

    echo "$NOW,host,loadavg,$(cut -d' ' -f1 /proc/loadavg),0" >> "$CSV"
    T0=$T1
done
