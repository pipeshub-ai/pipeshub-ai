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
    # `AnswerFinalizer:` and not `...: finalized response` — a turn that ends
    # with an empty answer logs "empty response, using fallback" instead, and
    # the two are mutually exclusive. It still completed and still cost a turn.
    marks=$(printf '%s' "$LOGTEXT" | grep -ac "${PIPESHUB_TURN_MARKER:-AnswerFinalizer:}" || true)
fi

if [ "$PIPESHUB_MODE" = "docker" ]; then
    window=$(( ${END:-$(date -u +%s)} - START ))
else
    window=${PIPESHUB_WINDOW_SECONDS:-0}
fi
[ "$window" -gt 0 ] || window=1

"$PYTHON" - "$marks" "$window" <<'PY'
import sys
marks, window = int(sys.argv[1]), int(sys.argv[2])
per_min = marks * 60.0 / window
print(f"  completed turns : {marks}")
print(f"  window          : {window}s")
print(f"  throughput      : {per_min:.1f} req/min  ({marks/window:.3f}/s)")
if not marks:
    print("  (zero - no completed turns found in the log for this window)")
PY
