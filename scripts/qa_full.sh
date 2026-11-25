#!/usr/bin/env bash
# QA-AUTO: the full QA pass (every lane this repository holds), one HTML and JSON report.
#
#   scripts/qa_full.sh --out <dir>                                      every lane
#   scripts/qa_full.sh --list                                           the lanes
#
# The orchestrator is scripts/qa_full.py (its docstring says what each lane runs and how failures are labelled).
# This wrapper only picks the interpreter: PXH_PYTHON, else the checkout's venv, else python3.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
py="${PXH_PYTHON:-$here/../backend/python/venv/bin/python}"
[[ -x "$py" ]] || py="$(command -v python3)"
export PXH_PYTHON="$py"
export TMPDIR="${TMPDIR:-/tmp/pxhqa}"
mkdir -p "$TMPDIR"
exec "$py" "$here/qa_full.py" "$@"
