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
case "$SECS" in
    ''|*[!0-9]*|0) echo "seconds must be a positive integer, got '$SECS'" >&2; exit 1 ;;
esac

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUTDIR="$HERE/results/$LABEL"
# shellcheck source=_common.sh
. "$HERE/_common.sh"
HOST=${PIPESHUB_HOST}
QUERY_FILE=${PIPESHUB_QUERY_FILE:-$HERE/queries.txt}
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
    --max-time 60 2>/dev/null) || true
  # curl already prints 000 when it never got a response; appending a fallback
  # to the same capture produced "HTTP 000000".
  [ -n "$probe" ] || probe=000
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

# One request body per query, JSON-encoded once here rather than per request:
# the previous inline `python -c json.dumps` forked a process for every single
# request, adding load to the host being measured.
if [ -n "${PIPESHUB_QUERY:-}" ]; then
  QUERIES=("$PIPESHUB_QUERY")
elif [ -f "$QUERY_FILE" ]; then
  mapfile -t QUERIES < <(grep -v '^[[:space:]]*#' "$QUERY_FILE" | grep -v '^[[:space:]]*$')
else
  say "ABORT: no query file at $QUERY_FILE and PIPESHUB_QUERY is unset."
  exit 1
fi
[ ${#QUERIES[@]} -gt 0 ] || { say "ABORT: $QUERY_FILE contains no queries."; exit 1; }
mapfile -t BODIES < <(printf '%s\n' "${QUERIES[@]}" | "$PYTHON" -c '
import json, sys
for line in sys.stdin.read().splitlines():
    print(json.dumps({"query": line, "chatMode": "internal_search"}))
')

say "== $LABEL: $USERS users, ${SECS}s, ${#QUERIES[@]} quer$([ ${#QUERIES[@]} -eq 1 ] && echo y || echo ies), host $HOST, mode $PIPESHUB_MODE"
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

# Query order per user, drawn from a PRNG seeded on (label, user index): random
# across users but reproducible, so two arms of an A/B see the SAME sequence
# rather than merely the same distribution.
mapfile -t USER_SEQ < <("$PYTHON" -c '
import random, sys
label, users, nq, turns = sys.argv[1], int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4])
for u in range(1, users + 1):
    rng = random.Random(f"{label}:{u}")
    print(" ".join(str(rng.randrange(nq)) for _ in range(turns)))
' "$LABEL" "$USERS" "${#BODIES[@]}" 500)

# --- load: each user asks, reads the whole answer, thinks, repeats
THINK=${PIPESHUB_THINK_TIME:-3}
END=$(( $(date +%s) + SECS ))
echo "user,turn,query_index,http_code,time_total,curl_exit" > "$OUTDIR/requests.csv"
LOAD_PIDS=()
for u in $(seq 1 "$USERS"); do
  ( turn=0
    read -r -a seq <<< "${USER_SEQ[$((u - 1))]}"
    while [ "$(date +%s)" -lt "$END" ]; do
      qi=${seq[$(( turn % ${#seq[@]} ))]}
      # Record the outcome of every request. Previously this was `-o /dev/null
      # || true`, so a run where every request 500'd was indistinguishable from
      # one that was merely slow.
      res=$(curl -s -N -o /dev/null -w '%{http_code} %{time_total}' \
        -X POST "$HOST/api/v1/conversations/stream" \
        -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
        -H "Accept: text/event-stream" \
        -d "${BODIES[$qi]}" \
        --max-time 300 2>/dev/null)
      # Immediately, and with no `|| true` in between: that would make this the
      # exit status of `true` and record every request as curl_exit=0, losing
      # exactly the timeouts (28) this column exists to surface. Safe without a
      # guard because the script does not run under `set -e`.
      rc=$?
      echo "$u,$turn,$qi,${res:-000 0} $rc" | tr ' ' ',' >> "$OUTDIR/requests.csv"
      turn=$((turn + 1))
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
LOAD_CUTOFF=$(( WALL_START + SECS ))
kill "$MEM_PID" 2>/dev/null || true
sleep 3

say ""
say "==================== THROUGHPUT ===================="
PIPESHUB_WINDOW_SECONDS=$WALL_SECS bash "$HERE/throughput.sh" "$START" "$FINISH" | tee -a "$REPORT"
# Turns finished while load was still being OFFERED. The figure above divides by
# elapsed time including the drain after load stops, so a config whose last
# turns run long reports a lower rate for the same work. When the two disagree,
# the drain is doing the talking.
if [ "$PIPESHUB_MODE" = "docker" ]; then
  SS_TURNS=$(log_read "$START" "$LOAD_CUTOFF" | grep -ac 'AnswerFinalizer:' || true)
  say "  steady-state    : $SS_TURNS turns in the ${SECS}s load window  ($("$PYTHON" -c "print(f'{$SS_TURNS*60/$SECS:.1f}')") req/min)"
fi

say ""
say "==================== REQUESTS (client-side) ===================="
"$PYTHON" "$HERE/instr/agg_requests.py" "$OUTDIR/requests.csv" | tee -a "$REPORT"

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
  "$PYTHON" "$HERE/instr/plot_memory.py" "$OUTDIR/memory.csv" "$OUTDIR/memory.svg" \
    "$LABEL — ${WORKERS:-?} workers, $USERS users" 2>/dev/null \
    && say "   memory graph: $OUTDIR/memory.svg"
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

# Machine-readable, so `aggregate.py` can roll many runs into one table without
# re-parsing the human report. PIPESHUB_ARM/TRIAL are set by the matrix driver.
"$PYTHON" "$HERE/instr/make_summary.py" \
  --outdir "$OUTDIR" --label "$LABEL" --users "$USERS" --secs "$SECS" \
  --workers "${WORKERS:-}" --arm "${PIPESHUB_ARM:-}" --trial "${PIPESHUB_TRIAL:-}" \
  --wall "$WALL_SECS" --queries "${#QUERIES[@]}" 2>/dev/null \
  && say "   summary: $OUTDIR/summary.json"

# ~19MB of the ~23MB a run writes. Kept (not deleted) so a profile can be
# re-analysed later, but not at full size 54 times over.
[ -s "$OUTDIR/cpu.raw" ] && gzip -f "$OUTDIR/cpu.raw" 2>/dev/null || true

say ""
say "== done. full report: $REPORT"
