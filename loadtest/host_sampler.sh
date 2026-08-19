#!/usr/bin/env bash
# host_sampler.sh <outfile> <seconds> [interval]
#
# Total host CPU and the load generator's own share of it. resource_sampler.sh
# covers containers and query workers but not the curl driver, which runs on the
# host and shares the same 8 cores. Without this, a run where the generator
# starves the service is indistinguishable from one where the workers saturate --
# and at 256+ users that is the difference between "we found the ceiling" and
# "we measured our own test harness".
set -uo pipefail
OUT=${1:?usage: host_sampler.sh <outfile> <seconds> [interval]}
SECS=${2:?}
INT=${3:-5}

echo "ts,host_cpu_pct,gen_cpu_pct,gen_procs,runq" > "$OUT"

read_cpu() {  # busy and total jiffies from /proc/stat
    awk '/^cpu /{idle=$5+$6; total=0; for(i=2;i<=NF;i++) total+=$i; print total-idle, total}' /proc/stat
}
# aggregate utime+stime of every curl the harness has running
read_gen() {
    local tot=0 n=0 pid
    for pid in $(pgrep -x curl 2>/dev/null); do
        if read -r _ _ _ _ _ _ _ _ _ _ _ _ _ ut st _ < /proc/"$pid"/stat 2>/dev/null; then
            tot=$(( tot + ut + st )); n=$(( n + 1 ))
        fi
    done
    echo "$tot $n"
}

HZ=$(getconf CLK_TCK)
read -r pb pt < <(read_cpu)
read -r gb gn < <(read_gen)
end=$(( $(date +%s) + SECS ))
while [ "$(date +%s)" -lt "$end" ]; do
    sleep "$INT"
    read -r cb ct < <(read_cpu)
    read -r gc gn < <(read_gen)
    dt=$(( ct - pt ))
    [ "$dt" -le 0 ] && { pb=$cb; pt=$ct; gb=$gc; continue; }
    host=$(awk -v a=$(( cb - pb )) -v b="$dt" 'BEGIN{printf "%.1f", a*100/b}')
    # curl pids churn between samples, so a negative delta just means the
    # previous set exited; clamp rather than report nonsense.
    gd=$(( gc - gb )); [ "$gd" -lt 0 ] && gd=0
    gen=$(awk -v j="$gd" -v hz="$HZ" -v i="$INT" 'BEGIN{printf "%.1f", j*100/(hz*i)}')
    runq=$(awk '{print $1}' /proc/loadavg)
    echo "$(date +%s),$host,$gen,$gn,$runq" >> "$OUT"
    pb=$cb; pt=$ct; gb=$gc
done
