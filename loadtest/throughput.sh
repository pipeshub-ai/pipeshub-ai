#!/usr/bin/env bash
# throughput.sh <mark> [end_mark] — completed chat turns, server-side.
#
# Counts `step=finalize` marks in the query service log, one per completed
# turn. Client-side counting undercounts: a turn still streaming when the load
# generator stops is never counted, which penalises exactly the slow configs
# you are trying to compare.
#
# <mark> comes from `log_mark` (see _common.sh) — an epoch in docker mode, a
# byte offset in native mode. `perftest.sh` passes it through automatically.
set -uo pipefail
START=${1:?usage: throughput.sh <mark> [end_mark]}
END=${2:-}
# shellcheck source=_common.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/_common.sh"
require_target || exit 1

if ! log_available; then
    echo "  (no log source — set PIPESHUB_QUERY_LOG in .env; see .env.example)"
    exit 0
fi

# `step=finalize` is emitted by the instrumentation patch (./instrument.sh on).
# Without it, fall back to a line the application already logs once per
# completed turn — `respond.py`'s AnswerFinalizer. Fall back rather than count
# both: an instrumented run emits BOTH, and summing them doubles every turn.
LOGTEXT=$(log_read "$START" "$END")
marks=$(printf '%s' "$LOGTEXT" | grep -ac 'step=finalize' || true)
if [ "$marks" -eq 0 ]; then
    # One line per finished turn (TURN_DONE_RE). Counting every AnswerFinalizer
    # line doubles a turn that warns "answer empty after normalization" and then
    # still logs "finalized response".
    marks=$(printf '%s' "$LOGTEXT" | grep -aEc "${PIPESHUB_TURN_MARKER:-$TURN_DONE_RE}" || true)
fi

# A turn whose agent run raised still finalizes and still returns HTTP 200: the
# user gets an error message as the answer. 14 of 22 turns once ended that way
# unnoticed, and a failed turn is shorter than a real one, so they inflate the
# rate. They are subtracted here and reported, and above 5% the run is INVALID.
failed=$(printf '%s' "$LOGTEXT" | grep -ac 'AnswerFinalizer: agent run failed' || true)
marks=$(( marks > failed ? marks - failed : 0 ))
citations=$(printf '%s' "$LOGTEXT" | grep -ao 'finalized response ([0-9]* chars, [0-9]* citations)' \
    | sed -E 's/.*, ([0-9]+) citations\)/\1/' | tr '\n' ' ')
reasons=$(printf '%s' "$LOGTEXT" | grep -a 'AnswerFinalizer: agent run failed' \
    | sed -E 's/.*\): //; s/\x1b\[[0-9;]*m//g' | cut -c1-120 | sort | uniq -c | sort -rn | head -3)

if [ "$PIPESHUB_MODE" = "docker" ]; then
    window=$(( ${END:-$(date -u +%s)} - START ))
else
    window=${PIPESHUB_WINDOW_SECONDS:-0}
fi
[ "$window" -gt 0 ] || window=1

"$PYTHON" - "$marks" "$window" "$failed" "$citations" "$reasons" <<'PY'
import sys
marks, window, failed = int(sys.argv[1]), int(sys.argv[2]), int(sys.argv[3])
cites = [int(c) for c in sys.argv[4].split()]
per_min = marks * 60.0 / window
print(f"  completed turns : {marks}")
print(f"  window          : {window}s")
print(f"  throughput      : {per_min:.1f} req/min  ({marks/window:.3f}/s)")
total = marks + failed
print(f"  failed answers  : {failed}" + (f"  ({failed / total * 100:.1f}% of turns answered with an error message, HTTP 200)" if total else ""))
for line in sys.argv[5].splitlines():
    print(f"      {line.strip()}")
if cites:
    zero = sum(c == 0 for c in cites)
    s = sorted(cites)
    print(f"  citations/answer: p50 {s[len(s) // 2]}  min {s[0]}  max {s[-1]}  ({zero} of {len(cites)} answers had none)")
if total and failed / total > 0.05:
    print(f"  ** INVALID: {failed / total * 100:.1f}% of turns failed inside the agent (>5%)")
if not marks:
    print("  (zero - no completed turns found in the log for this window)")
PY
