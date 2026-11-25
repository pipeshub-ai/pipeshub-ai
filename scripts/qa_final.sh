#!/usr/bin/env bash
# QA-FINAL: the final QA pass. Every local lane of docs/test-plan/QA-FINAL.md in order (the reported cases'
# regressions first, the stacks of their own last), then final.md and final.json beside qa_full's report.
#
#   scripts/qa_final.sh --out <dir>                              the whole pass (some eight hours)
#   scripts/qa_final.sh --out <dir> --sample                     each lane on a small selection: proves the wiring
#   scripts/qa_final.sh --list                                   the lanes, in the order they run
#
# Any qa_full.py option passes through (--lanes, --skip, --keep-stack, ...). Rows that need a live model, CI or the
# reference host are marked so in final.md; nothing here makes a paid or live model call.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec bash "$here/qa_full.sh" --final "$@"
