#!/usr/bin/env bash
# Run offline Omnigent integration tests; optionally run live MCP tool smokes.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REQUIRE_LIVE=0
SKIP_CHAT=0
SKIP_DOWNLOAD=0

usage() {
  cat <<'EOF'
Usage: ./tests/run_all.sh [--require-live] [--skip-chat] [--skip-download]

Runs:
  1) scripts_test.sh      (helpers / materialize / mcp-check)
  2) mcp_contract.py      (all MCP tools present in agent + docs)
  3) validate_agent.py    (agent structure)
  4) live_mcp_tools.py    (optional; needs ./scripts/setup.sh credentials)

Options:
  --require-live     Fail if live MCP checks are skipped or fail
  --skip-chat        Skip live pipeshub_chat call
  --skip-download    Skip live pipeshub_download_record call
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --require-live) REQUIRE_LIVE=1; shift ;;
    --skip-chat) SKIP_CHAT=1; shift ;;
    --skip-download) SKIP_DOWNLOAD=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown arg: $1" >&2; usage >&2; exit 1 ;;
  esac
done

echo "== Offline: scripts_test.sh =="
bash "${ROOT}/tests/scripts_test.sh"
echo
echo "== Offline: mcp_contract.py =="
python3 "${ROOT}/tests/mcp_contract.py"
echo
echo "== Offline: validate_agent.py =="
python3 "${ROOT}/tests/validate_agent.py"
echo

LIVE_ARGS=()
[[ "$SKIP_CHAT" -eq 1 ]] && LIVE_ARGS+=(--skip-chat)
[[ "$SKIP_DOWNLOAD" -eq 1 ]] && LIVE_ARGS+=(--skip-download)

echo "== Live: live_mcp_tools.py =="
set +e
python3 "${ROOT}/tests/live_mcp_tools.py" "${LIVE_ARGS[@]}"
LIVE_RC=$?
set -e

if [[ "$LIVE_RC" -eq 0 ]]; then
  echo
  echo "All offline + live tests passed."
  exit 0
fi

if [[ "$LIVE_RC" -eq 2 ]]; then
  if [[ "$REQUIRE_LIVE" -eq 1 ]]; then
    echo "Live MCP tests were skipped but --require-live was set." >&2
    exit 1
  fi
  echo
  echo "Offline tests passed. Live MCP skipped (no credentials or unreachable)."
  echo "To run live: ./scripts/setup.sh && ./tests/run_all.sh --require-live"
  exit 0
fi

echo "Live MCP tests failed." >&2
exit 1
