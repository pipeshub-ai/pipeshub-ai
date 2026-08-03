#!/usr/bin/env bash
# perftest.sh — one command to measure the query service under load.
#
#   ./perftest.sh <label> [users] [seconds] [workers]
#
# Reports, for the measurement window:
#   * throughput  — completed chat turns per minute, counted server-side
#   * latency     — per-phase breakdown of a turn (retrieval, LLM, ...)
#   * CPU         — which Python functions burned CPU, plus a flame graph
#   * memory      — query-service RSS over time
#   * backends    — per-call latency and rate for Neo4j and the Node API
#
# Run it ON the machine hosting PipesHub. See README.md for setup.
set -uo pipefail

LABEL=${1:?usage: TOKEN=<jwt> ./perftest.sh <label> [users] [seconds] [workers]}
USERS=${2:-8}
SECS=${3:-300}
WORKERS=${4:-}

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTDIR="$HERE/results/$LABEL"
# shellcheck source=_common.sh
. "$HERE/_common.sh"
HOST=${PIPESHUB_HOST}
QUERY=${PIPESHUB_QUERY:-"What are the key features described in the documents?"}
# users=0 is the collect-only mode (someone else drives the load): no requests
# are sent from here, so no credential is needed.
if [ "$USERS" -gt 0 ]; then
  : "${TOKEN:?set TOKEN in loadtest/.env, or export it — see .env.example}"
fi
# Checked before the run, not after: every reporting section below reads the
# query service, so a 300s run against an unreachable one produces only zeroes.
require_target || exit 1

mkdir -p "$OUTDIR"
REPORT="$OUTDIR/report.txt"
: > "$REPORT"
say() { echo "$@" | tee -a "$REPORT"; }

# --- guard: an expired token turns every request into a 401, which looks
# --- exactly like a throughput collapse. Fail fast instead.
if [ "$USERS" -gt 0 ]; then
  probe=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$HOST/api/v1/conversations/stream" \
    -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
    -H "Accept: text/event-stream" -d "{\"query\":\"ping\",\"chatMode\":\"internal_search\"}" \
    --max-time 60 2>/dev/null || echo 000)
  if [ "$probe" != "200" ]; then
    say "ABORT: auth probe returned HTTP $probe (expected 200). Refresh TOKEN."
    exit 1
  fi
fi

if [ -n "$WORKERS" ]; then
  [ "$PIPESHUB_MODE" = "docker" ] || { say "ABORT: the workers argument needs PIPESHUB_MODE=docker."; exit 1; }
  say "== setting query workers to $WORKERS (restarts the container)"
  bash "$HERE/set_workers.sh" "$WORKERS" >>"$REPORT" 2>&1
fi

say "== $LABEL: $USERS users, ${SECS}s, host $HOST, mode $PIPESHUB_MODE"
say "== started $(date -u +%H:%M:%SZ)"
START=$(log_mark)
WALL_START=$(date -u +%s)

# --- CPU profile (py-spy attaches to the live process; --gil samples only
# --- while Python is actually executing, so idle threads never appear)
if command -v py-spy >/dev/null 2>&1 || [ -x "$HOME/.local/bin/py-spy" ]; then
  PYSPY=$(command -v py-spy || echo "$HOME/.local/bin/py-spy")
  QPID=$(query_pid 2>/dev/null || true)
  if [ -n "$QPID" ]; then
    detect_sudo
    $SUDO "$PYSPY" record --pid "$QPID" --subprocesses --gil --nonblocking \
      -d "$SECS" -r 100 -f flamegraph -o "$OUTDIR/cpu.svg" >/dev/null 2>&1 &
    $SUDO "$PYSPY" record --pid "$QPID" --subprocesses --gil --nonblocking \
      -d "$SECS" -r 100 -f raw -o "$OUTDIR/cpu.raw" >/dev/null 2>&1 &
  else
    say "   (py-spy: no query process found — skipping CPU profile)"
  fi
else
  say "   (py-spy not installed — skipping CPU profile; see README.md)"
fi

# --- memory sampler
( END_M=$(( $(date +%s) + SECS + 15 ))
  while [ "$(date +%s)" -lt "$END_M" ]; do
    echo "$(date +%s),$(query_rss_mb)" >> "$OUTDIR/memory.csv"
    sleep 2
  done ) &
MEM_PID=$!

# --- load: each user asks, reads the whole answer, thinks, repeats
THINK=${PIPESHUB_THINK_TIME:-3}
END=$(( $(date +%s) + SECS ))
LOAD_PIDS=()
for _ in $(seq 1 "$USERS"); do
  ( while [ "$(date +%s)" -lt "$END" ]; do
      curl -s -N -o /dev/null -X POST "$HOST/api/v1/conversations/stream" \
        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
        -H "Accept: text/event-stream" \
        -d "{\"query\":$(printf '%s' "$QUERY" | "$PYTHON" -c 'import json,sys; print(json.dumps(sys.stdin.read()))'),\"chatMode\":\"internal_search\"}" \
        --max-time 300 || true
      sleep "$THINK"
    done ) &
  LOAD_PIDS+=($!)
done
# Wait on the load generators BY PID. A bare `wait` (which is what an empty
# filtered list degrades to) also waits for the memory sampler, overrunning the
# window by its trailing samples and understating throughput.
if [ ${#LOAD_PIDS[@]} -gt 0 ]; then
  wait "${LOAD_PIDS[@]}" 2>/dev/null || true
else
  sleep "$SECS"
fi
FINISH=$(log_mark)
WALL_SECS=$(( $(date -u +%s) - WALL_START ))
kill "$MEM_PID" 2>/dev/null || true
sleep 3

say ""
say "==================== THROUGHPUT ===================="
PIPESHUB_WINDOW_SECONDS=$WALL_SECS bash "$HERE/throughput.sh" "$START" "$FINISH" | tee -a "$REPORT"

say ""
say "==================== TURN LATENCY (per phase, ms) ===================="
if log_available; then
  log_read "$START" "$FINISH" | "$PYTHON" "$HERE/instr/agg_phases.py" 2>/dev/null | tee -a "$REPORT" \
    || say "   (no phase data - run ./instrument.sh on to enable)"
else
  say "   (no log source - set PIPESHUB_QUERY_LOG in .env; see .env.example)"
fi

say ""
say "==================== CPU (top functions, real work only) ===================="
if [ -s "$OUTDIR/cpu.raw" ]; then
  "$PYTHON" "$HERE/instr/top_functions.py" "$OUTDIR/cpu.raw" | tee -a "$REPORT"
  say "   flame graph: $OUTDIR/cpu.svg"
else
  say "   (no profile captured)"
fi

say ""
say "==================== MEMORY (query service RSS, MB) ===================="
if [ -s "$OUTDIR/memory.csv" ]; then
  awk -F, 'NR==1{f=$2} {v[NR]=$2; if($2>m) m=$2; l=$2}
    END{ printf "  start=%d MB  peak=%d MB  end=%d MB  growth=+%d MB\n", f, m, l, m-f
         lo=(f<l?f:l); if(m<=lo) m=lo+1; split("▁ ▂ ▃ ▄ ▅ ▆ ▇ █",g," ")
         s=""; step=(NR>60?int(NR/60)+1:1)
         for(i=1;i<=NR;i+=step){ s=s g[int((v[i]-lo)*7/(m-lo))+1] }
         print "  " s }' "$OUTDIR/memory.csv" | tee -a "$REPORT"
else
  say "   (no samples)"
fi

say ""
say "==================== BACKEND CALLS (caller-side) ===================="
if log_available; then
  log_read "$START" "$FINISH" | grep -a BACKENDAGG \
    | "$PYTHON" "$HERE/instr/agg_backends.py" 2>/dev/null | tee -a "$REPORT" \
    || say "   (no backend data - run ./instrument.sh on to enable)"
else
  say "   (no log source - set PIPESHUB_QUERY_LOG in .env; see .env.example)"
fi

say ""
say "== done. full report: $REPORT"
