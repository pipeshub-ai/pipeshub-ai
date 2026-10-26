#!/usr/bin/env bash
# qprof.sh — profile the query service across a matrix of load levels, in one
# command. Docker deployments only.
#
#   ./qprof.sh [--users 8,16] [--sets 3] [--duration 300] [--workers 1,4]
#              [--flame cpu|wall|off] [--label NAME] [--think 3] [--cooldown 30]
#              [--queries FILE] [--turn-timeout 900] [--no-instrument]
#              [--idle-cpu 80] [--idle-timeout 14400] [--no-warmup]
#              [--order random|unique]
#
# Every flag is optional. For each workers x users combination it runs
# `perftest.sh` --sets times, then rolls the trials into one table with
# aggregate.py and draws scaling.svg (throughput, latency and CPU against
# users). Results land in results/<label>/. See docs/query-profiling.md.
#
# Before every trial it waits until the whole stack has been under --idle-cpu
# (% of one core, summed over its containers) for a minute, so it can be started
# while indexing is still running; --idle-cpu 0 turns the gate off. The first
# trial after each start or restart of the query service is preceded by one
# unmeasured pass over the query file (--no-warmup skips it).
# --order unique asks every question once before any repeats; build the file
# from your own records with make_queries.py, and pair it with --no-warmup.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

USERS_LIST=8
SETS=3
DURATION=300
WORKERS_LIST=""
FLAME=cpu
LABEL="qprof-$(date +%Y%m%d-%H%M%S)"
THINK=""
COOLDOWN=30
# Agentic turns with several tool rounds routinely pass 300s; a turn cut off by
# curl is recorded as a failure and invalidates the trial.
TURN_TIMEOUT=900
QUERIES=""
INSTRUMENT=1
IDLE_CPU=80
IDLE_TIMEOUT=14400
WARMUP=1
ORDER=

usage() { sed -n '2,22p' "$0" | sed 's/^# \{0,1\}//'; exit "${1:-0}"; }
need() { [ -n "${2:-}" ] || { echo "$1 needs a value" >&2; exit 1; }; }

while [ $# -gt 0 ]; do
    case "$1" in
        --users)         need "$1" "${2:-}"; USERS_LIST=$2; shift 2 ;;
        --sets)          need "$1" "${2:-}"; SETS=$2; shift 2 ;;
        --duration)      need "$1" "${2:-}"; DURATION=$2; shift 2 ;;
        --workers)       need "$1" "${2:-}"; WORKERS_LIST=$2; shift 2 ;;
        --flame)         need "$1" "${2:-}"; FLAME=$2; shift 2 ;;
        --label)         need "$1" "${2:-}"; LABEL=$2; shift 2 ;;
        --think)         need "$1" "${2:-}"; THINK=$2; shift 2 ;;
        --cooldown)      need "$1" "${2:-}"; COOLDOWN=$2; shift 2 ;;
        --queries)       need "$1" "${2:-}"; QUERIES=$2; shift 2 ;;
        --turn-timeout)  need "$1" "${2:-}"; TURN_TIMEOUT=$2; shift 2 ;;
        --no-instrument) INSTRUMENT=0; shift ;;
        --idle-cpu)      need "$1" "${2:-}"; IDLE_CPU=$2; shift 2 ;;
        --idle-timeout)  need "$1" "${2:-}"; IDLE_TIMEOUT=$2; shift 2 ;;
        --no-warmup)     WARMUP=0; shift ;;
        --order)         need "$1" "${2:-}"; ORDER=$2; shift 2 ;;
        -h|--help)       usage 0 ;;
        *) echo "unknown option: $1" >&2; usage 1 ;;
    esac
done

positive_list() {
    local name=$1 v
    for v in ${2//,/ }; do
        case "$v" in ''|*[!0-9]*|0) echo "--$name takes positive integers, got '$2'" >&2; exit 1 ;; esac
    done
}
positive_list users "$USERS_LIST"
positive_list sets "$SETS"
positive_list duration "$DURATION"
positive_list turn-timeout "$TURN_TIMEOUT"
[ -z "$WORKERS_LIST" ] || positive_list workers "$WORKERS_LIST"
case "$COOLDOWN" in ''|*[!0-9]*) echo "--cooldown takes seconds, got '$COOLDOWN'" >&2; exit 1 ;; esac
case "$IDLE_CPU" in ''|*[!0-9]*) echo "--idle-cpu takes a percent of one core, got '$IDLE_CPU'" >&2; exit 1 ;; esac
positive_list idle-timeout "$IDLE_TIMEOUT"
case "$FLAME" in cpu|wall|off) ;; *) echo "--flame must be cpu, wall or off, got '$FLAME'" >&2; exit 1 ;; esac
# The label becomes a directory name and part of every run label.
case "$LABEL" in ''|*[!A-Za-z0-9._-]*) echo "--label may use letters, digits, '.', '_' and '-' only" >&2; exit 1 ;; esac
# Unset leaves PIPESHUB_QUERY_ORDER to .env or the environment.
case "$ORDER" in "") ;; random|unique) export PIPESHUB_QUERY_ORDER=$ORDER ;; *) echo "--order is random or unique" >&2; exit 1 ;; esac

if [ -n "$QUERIES" ]; then
    [ -f "$QUERIES" ] || { echo "no query file at $QUERIES" >&2; exit 1; }
    export PIPESHUB_QUERY_FILE
    PIPESHUB_QUERY_FILE=$(cd "$(dirname "$QUERIES")" && pwd)/$(basename "$QUERIES")
fi
[ -z "$THINK" ] || export PIPESHUB_THINK_TIME=$THINK
export PIPESHUB_FLAME=$FLAME
export PIPESHUB_TURN_MAX_SECONDS=$TURN_TIMEOUT

# shellcheck source=_common.sh
. "$HERE/_common.sh"
require_target || exit 1
[ "$PIPESHUB_MODE" = docker ] || {
    echo "ABORT: qprof.sh supports docker deployments only (found $PIPESHUB_MODE)." >&2; exit 1; }

ROOT="$HERE/results/$LABEL"
[ -e "$ROOT" ] && { echo "ABORT: $ROOT already exists — pick another --label." >&2; exit 1; }
mkdir -p "$ROOT"
LOG="$ROOT/qprof.log"
say() { echo "$@" | tee -a "$LOG"; }

current_workers() {
    local v
    v=$($DOCKER exec "$CONTAINER" sh -c 'echo "${QUERY_UVICORN_WORKERS:-${WEB_CONCURRENCY:-}}"' 2>/dev/null | tr -d '\r ')
    case "$v" in ''|*[!0-9]*) echo 1 ;; *) echo "$v" ;; esac
}

instrument() {
    [ "$INSTRUMENT" = 1 ] || return 0
    local st
    st=$(bash "$HERE/instrument.sh" status 2>/dev/null)
    if echo "$st" | grep -q 'phase marks : applied' && echo "$st" | grep -q 'backend probe: wired'; then
        say "== probes already in place"
        return 0
    fi
    say "== installing probes (restarts the query service)"
    bash "$HERE/instrument.sh" on 2>&1 | tee -a "$LOG"
    [ "${PIPESTATUS[0]}" = 0 ] || { say "ABORT: instrument.sh on failed (see above). --no-instrument skips it."; exit 1; }
}

# py-spy has to attach as root: query_main marks itself non-dumpable, which
# refuses a same-user ptrace. Without this check a missing sudo shows up only
# as "no profile captured" at the end of every trial.
check_pyspy() {
    [ "$FLAME" != off ] || return 0
    local pyspy qpid
    pyspy=$(command -v py-spy || { [ -x "$HOME/.local/bin/py-spy" ] && echo "$HOME/.local/bin/py-spy"; })
    [ -n "$pyspy" ] || { say "ABORT: py-spy not found. pip3 install --user py-spy, or pass --flame off."; exit 1; }
    qpid=$(query_pid) || { say "ABORT: cannot find the query service pid from the host for py-spy."; exit 1; }
    detect_sudo
    $SUDO "$pyspy" dump --pid "$qpid" >/dev/null 2>&1 || {
        say "ABORT: py-spy cannot attach to pid $qpid. It needs root (the service is"
        say "       non-dumpable): run as root or allow passwordless sudo, or pass --flame off."
        exit 1; }
}

ORIG_WORKERS=$(current_workers)
COMPOSE_ENV=${ENV_FILE:-${COMPOSE_DIR:-$HERE/../deployment/docker-compose}/.env}
CHANGED_WORKERS=0
restore_workers() {
    [ "$CHANGED_WORKERS" = 1 ] || return 0
    CHANGED_WORKERS=0
    say ""
    say "== restoring query workers to $ORIG_WORKERS"
    bash "$HERE/set_workers.sh" "$ORIG_WORKERS" >>"$LOG" 2>&1 || say "!! restore failed — check $COMPOSE_ENV"
    [ -f "$ROOT/compose.env.orig" ] && cp "$ROOT/compose.env.orig" "$COMPOSE_ENV"
    instrument
}
trap restore_workers EXIT
trap 'exit 130' INT TERM

read -r -a USERS_ARR <<< "${USERS_LIST//,/ }"
if [ -n "$WORKERS_LIST" ]; then
    read -r -a WORKERS_ARR <<< "${WORKERS_LIST//,/ }"
    [ -f "$COMPOSE_ENV" ] && cp "$COMPOSE_ENV" "$ROOT/compose.env.orig"
else
    WORKERS_ARR=("$ORIG_WORKERS")
fi
COMBOS=$(( ${#WORKERS_ARR[@]} * ${#USERS_ARR[@]} ))
TRIALS=$(( COMBOS * SETS ))
# +120s per trial for the drain after load stops and the report.
EST=$(( (TRIALS * (DURATION + 120) + (TRIALS - 1) * COOLDOWN) / 60 ))

say "== qprof $LABEL"
say "   workers   : ${WORKERS_ARR[*]}$([ -z "$WORKERS_LIST" ] && echo ' (current, unchanged)')"
say "   users     : ${USERS_ARR[*]}"
say "   sets      : $SETS trials per combination, ${DURATION}s each"
say "   profiling : $FLAME"
say "   total     : $TRIALS trials, about $EST min"
say "   results   : $ROOT"
say ""

instrument
check_pyspy

wait_quiet() {
    [ "$IDLE_CPU" -gt 0 ] || return 0
    say "== waiting for the stack to go quiet (under ${IDLE_CPU}% of one core for 60s)"
    bash "$HERE/wait_idle.sh" "$IDLE_CPU" "$IDLE_TIMEOUT" 60 2>&1 | tee -a "$LOG"
    [ "${PIPESTATUS[0]}" = 0 ] || { say "ABORT: the stack did not go quiet within ${IDLE_TIMEOUT}s."; exit 1; }
}

# 1 whenever the query service has (re)started since the last warm-up.
NEED_WARMUP=$WARMUP
n=0
for w in "${WORKERS_ARR[@]}"; do
    if [ -n "$WORKERS_LIST" ] && [ "$w" != "$(current_workers)" ]; then
        say "== setting query workers to $w (recreates the container)"
        CHANGED_WORKERS=1
        bash "$HERE/set_workers.sh" "$w" >>"$LOG" 2>&1 || { say "ABORT: set_workers.sh $w failed (see $LOG)."; exit 1; }
        instrument
        check_pyspy
        NEED_WARMUP=$WARMUP
    fi
    for u in "${USERS_ARR[@]}"; do
        for t in $(seq 1 "$SETS"); do
            n=$((n + 1))
            [ "$n" -gt 1 ] && [ "$COOLDOWN" -gt 0 ] && { say "== cooling down ${COOLDOWN}s"; sleep "$COOLDOWN"; }
            wait_quiet
            say ""
            say "######## trial $n/$TRIALS: workers=$w users=$u set=$t ########"
            # Seed excludes the workers and the label, so every arm replays the same queries.
            PIPESHUB_SEED="u${u}_t${t}" PIPESHUB_ARM="$LABEL" PIPESHUB_TRIAL="$t" PIPESHUB_WORKERS="$w" \
            PIPESHUB_WARMUP="$NEED_WARMUP" \
                bash "$HERE/perftest.sh" "$LABEL/w${w}_u${u}_t${t}" "$u" "$DURATION" 2>&1 | tee -a "$LOG"
            [ "${PIPESTATUS[0]}" = 0 ] || say "!! trial $n failed — continuing; it will be missing from the summary"
            NEED_WARMUP=0
        done
    done
done

restore_workers

say ""
say "==================== SUMMARY: $LABEL ===================="
detect_python
"$PYTHON" "$HERE/aggregate.py" "$ROOT" 2>&1 | tee "$ROOT/summary.txt" | tee -a "$LOG"
"$PYTHON" "$HERE/instr/plot_scaling.py" "$ROOT" "$ROOT/scaling.svg" "$LABEL" "$(nproc)" 2>&1 | tee -a "$LOG"
[ -s "$ROOT/scaling.svg" ] && say "   scaling graph (throughput, latency, CPU against users): $ROOT/scaling.svg"
say ""
say "   per-trial reports, flame graphs and graphs: $ROOT/w*_u*_t*/"
say "   this summary: $ROOT/summary.txt"
