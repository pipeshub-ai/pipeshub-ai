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
#   host       -> /proc/stat (busy, iowait, steal) and /proc/meminfo
# All are monotonic counters, so CPU% over an interval is exact rather than
# sampled, and a burst between samples still shows up in the total.
#
# Container memory is memory.current minus inactive_file, the figure `docker
# stats` shows. Raw memory.current counts reclaimable page cache, so a datastore
# that streams files looks like it is leaking.
#
# Processes are attributed to a service by walking up the parent chain to the
# service's root process, and the process list is re-read every sample. Uvicorn
# workers and the indexing pool are multiprocessing.spawn children whose argv is
# "spawn_main(...)", so matching argv alone misses them, and the indexing pool
# starts and replaces workers during a run.
#
# CSV rows (ts,scope,name,cpu_pct,rss_mb); cpu is % of one core:
#   process,<service>:<pid>    one row per process of a service
#   container,<name>
#   host,cpu|iowait|steal      cpu_pct only
#   host,mem                   rss_mb = MemTotal - MemAvailable
#   host,loadavg               1-minute load in the cpu_pct column
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
    proj=$($DOCKER inspect -f '{{index .Config.Labels "com.docker.compose.project"}}' "$CONTAINER" 2>/dev/null | tr -d '\r')
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
    cid=$($DOCKER inspect "$c" --format '{{.Id}}' 2>/dev/null | tr -d '\r') || continue
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

# One exec per sample: list the app container's processes, attribute each to a
# service root, and print "<service> <pid> <cpu ticks> <rss pages>".
# /proc/<pid>/stat is split after the closing paren because comm may hold spaces.
read_procs() {
    $DOCKER exec "$CONTAINER" sh -c '
ps -eo pid=,ppid=,args= 2>/dev/null | awk '"'"'
{ pid = $1; par[pid] = $2; $1 = ""; $2 = ""; a = $0
  s = ""
  if      (a ~ /app[.]query_main/)       s = "query"
  else if (a ~ /app[.]indexing_main/)    s = "indexing"
  else if (a ~ /app[.]connectors_main/)  s = "connectors"
  else if (a ~ /app[.]docling_main/)     s = "docling"
  else if (a ~ /app[.]embedding_main/)   s = "embedding"
  else if (a ~ /app[.]parsing_main/)     s = "parsing"
  else if (a ~ /app[.]extraction_main/)  s = "extraction"
  else if (a ~ /node .*slack-bot/)       s = "slack_bot"
  else if (a ~ /node dist\/index[.]js/)  s = "node"
  if (s != "") root[pid] = s
  pids[pid] = 1 }
END {
  for (p in pids) {
    q = p; s = ""; n = 0
    while (q != "" && q > 1 && n < 32) { if (q in root) { s = root[q]; break }; q = par[q]; n++ }
    if (s == "") continue
    line = ""; f = "/proc/" p "/stat"
    if ((getline line < f) <= 0) { close(f); continue }
    close(f)
    sub(/^.*\) /, "", line); split(line, x, " ")
    rss = 0; g = "/proc/" p "/statm"
    if ((getline m < g) > 0) { split(m, y, " "); rss = y[2] }
    close(g)
    print s, p, x[12] + x[13], rss
  }
}'"'"'' 2>/dev/null | tr -d '\r'
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

# "<busy> <iowait> <steal> <total>" jiffies summed over all cores, then MB used.
read_host() {
    awk '/^cpu /{busy = $2 + $3 + $4 + $7 + $8 + $9; print busy, $6, $9, busy + $5 + $6; exit}' /proc/stat
}
host_mem_mb() {
    awk '/^MemTotal:/{t=$2} /^MemAvailable:/{a=$2} END{printf "%.1f", (t - a) / 1024}' /proc/meminfo
}
NCPU=$(nproc 2>/dev/null || echo 1)

echo "ts,scope,name,cpu_pct,rss_mb" > "$CSV"

declare -A P0 C0
while read -r _ pid ticks _; do P0[$pid]=$ticks; done < <(read_procs)
while read -r c usec _; do C0[$c]=$usec; done < <(read_cg)
read -r H_BUSY H_IO H_ST H_TOT < <(read_host)
T0=$(date +%s.%N)

END=$(( $(date +%s) + SECS ))
while [ "$(date +%s)" -lt "$END" ]; do
    sleep "$INTERVAL"
    NOW=$(date +%s); T1=$(date +%s.%N)
    DT=$(awk -v a="$T0" -v b="$T1" 'BEGIN{print b - a}')
    awk -v d="$DT" 'BEGIN{exit !(d <= 0)}' && continue

    # A pid first seen this sample has no baseline; it reports from the next one.
    declare -A P1=()
    while read -r svc pid ticks rss; do
        a=${P0[$pid]:-}
        if [ -n "$a" ] && [ "$ticks" -ge "$a" ]; then
            awk -v t="$NOW" -v n="$svc:$pid" -v d="$ticks" -v a="$a" -v clk="$CLK" -v dt="$DT" -v r="$rss" \
                'BEGIN{printf "%s,process,%s,%.2f,%.1f\n", t, n, (d - a) / (clk * dt) * 100, r * 4096 / 1048576}' >> "$CSV"
        fi
        P1[$pid]=$ticks
    done < <(read_procs)
    P0=()
    for k in "${!P1[@]}"; do P0[$k]=${P1[$k]}; done
    unset P1

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

    read -r busy io st tot < <(read_host)
    awk -v t="$NOW" -v b="$((busy - H_BUSY))" -v i="$((io - H_IO))" -v s="$((st - H_ST))" \
        -v d="$((tot - H_TOT))" -v n="$NCPU" -v m="$(host_mem_mb)" \
        -v l="$(cut -d' ' -f1 /proc/loadavg 2>/dev/null || echo 0)" \
        'BEGIN{ if (d <= 0) d = 1
                printf "%s,host,cpu,%.2f,0\n", t, b / d * n * 100
                printf "%s,host,iowait,%.2f,0\n", t, i / d * n * 100
                printf "%s,host,steal,%.2f,0\n", t, s / d * n * 100
                printf "%s,host,mem,0,%s\n", t, m
                printf "%s,host,loadavg,%s,0\n", t, l }' >> "$CSV"
    H_BUSY=$busy; H_IO=$io; H_ST=$st; H_TOT=$tot
    T0=$T1
done
