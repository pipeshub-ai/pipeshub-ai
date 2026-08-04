#!/usr/bin/env bash
# Load local PipesHub credentials and start the Omnigent knowledge agent.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage: ./scripts/run.sh [omnigent args...]

Loads .local/credentials.env, materializes a temporary agent with the
PipesHub token resolved into config.yaml, and runs:
  omnigent run <temp-agent> [args...]

Examples:
  ./scripts/run.sh
  ./scripts/run.sh -p "What is our refund policy?"
  ./scripts/run.sh --harness antigravity --model gemini-2.5-flash
  GEMINI_API_KEY='...' ./scripts/run.sh --harness antigravity --model gemini-2.5-flash

Notes:
  - Put env vars BEFORE the command (GEMINI_API_KEY=... ./scripts/run.sh ...).
    Do not pass KEY=value as an argument after ./scripts/run.sh.
  - Starts a fresh Omnigent session by default (--no-session) so harness/model
    flags are not overridden by an auto-resumed chat. Pass --continue or
    --resume yourself if you want the old conversation.
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

for arg in "$@"; do
  if [[ "$arg" == [A-Za-z_][A-Za-z0-9_]*=* ]]; then
    die "refusing argument ${arg%%=*}=... — set environment variables before the command, e.g. ${arg%%=*}=... ./scripts/run.sh --harness antigravity"
  fi
done

require_cmd omnigent "install from https://omnigent.ai or: uv tool install omnigent"
require_cmd python3

wants_antigravity=0
prev=""
for arg in "$@"; do
  if [[ "$arg" == "--harness=antigravity" || ( "$prev" == "--harness" && "$arg" == "antigravity" ) ]]; then
    wants_antigravity=1
    break
  fi
  prev="$arg"
done
if [[ "$wants_antigravity" -eq 1 ]]; then
  # Omnigent 0.8.1 declares protobuf<7, but Antigravity gencode 7.35 needs runtime >=7.35.
  OMNI_PY="${HOME}/.local/share/uv/tools/omnigent/bin/python"
  if [[ -x "$OMNI_PY" ]]; then
    if ! "$OMNI_PY" -c 'import google.protobuf as p; from packaging.version import Version; import sys; sys.exit(0 if Version(p.__version__) >= Version("7.35.0") else 1)' 2>/dev/null; then
      die "Antigravity needs protobuf>=7.35.0 in the Omnigent tool env. Fix: uv pip install --python \"${OMNI_PY}\" 'protobuf==7.35.1'"
    fi
    if ! "$OMNI_PY" -c 'from google.antigravity.proto import localharness_pb2' 2>/dev/null; then
      die "Antigravity extra missing. Fix: uv tool install 'omnigent[antigravity]==0.8.1' && uv pip install --python \"${OMNI_PY}\" 'protobuf==7.35.1'"
    fi
  fi
fi

load_credentials

if jwt_is_expired "${PIPESHUB_MCP_TOKEN}"; then
  if [[ -n "${PIPESHUB_REFRESH_TOKEN:-}" ]]; then
    info "Access token expired; attempting refresh ..."
    ORIGIN="${PIPESHUB_URL}"
    REFRESH_BODY="$(mktemp)"
    HTTP_CODE="$(curl -sS -o "$REFRESH_BODY" -w '%{http_code}' \
      -X POST "${ORIGIN}/api/v1/userAccount/refresh/token" \
      -H "Authorization: Bearer ${PIPESHUB_REFRESH_TOKEN}" \
      -H 'Content-Type: application/json' || true)"
    if [[ "$HTTP_CODE" == "200" ]]; then
      NEW_TOKEN="$(python3 - "$REFRESH_BODY" <<'PY'
import json,sys
try:
  d=json.load(open(sys.argv[1]))
except Exception:
  raise SystemExit(1)
if not isinstance(d, dict):
  raise SystemExit(1)
token=d.get("accessToken") or d.get("access_token") or ""
print(token if isinstance(token, str) else "")
PY
)" || true
      NEW_REFRESH="$(python3 - "$REFRESH_BODY" <<'PY'
import json,sys
try:
  d=json.load(open(sys.argv[1]))
except Exception:
  raise SystemExit(1)
if not isinstance(d, dict):
  raise SystemExit(1)
token=d.get("refreshToken") or d.get("refresh_token") or ""
print(token if isinstance(token, str) else "")
PY
)" || true
      rm -f "$REFRESH_BODY"
      if [[ -n "$NEW_TOKEN" ]]; then
        write_credentials "$ORIGIN" "$NEW_TOKEN" "${NEW_REFRESH:-$PIPESHUB_REFRESH_TOKEN}" "${PIPESHUB_AUTH_METHOD:-token}"
        load_credentials
        info "Token refreshed."
      else
        die "refresh response missing access token; run ./scripts/setup.sh"
      fi
    else
      rm -f "$REFRESH_BODY"
      die "access token expired and refresh failed (HTTP ${HTTP_CODE}); run ./scripts/setup.sh"
    fi
  else
    die "access token expired; run ./scripts/setup.sh"
  fi
fi

[[ -f "${CONFIG_FILE}" ]] || die "missing ${CONFIG_FILE}; run ./scripts/setup.sh first"

# Refresh generated config from the example so tool allowlist stays current.
write_agent_config "${PIPESHUB_MCP_URL}"

export PIPESHUB_MCP_TOKEN
export PIPESHUB_URL
export PIPESHUB_MCP_URL

# Omnigent 0.7.0 does not expand ${PIPESHUB_MCP_TOKEN} in inline MCP headers
# inside config.yaml. Materialize a temp agent with the token substituted.
TMP_AGENT="$(
  python3 "${SCRIPT_DIR}/materialize_agent.py" \
    --agent-dir "${AGENT_DIR}" \
    --token "${PIPESHUB_MCP_TOKEN}" \
    --mcp-url "${PIPESHUB_MCP_URL}"
)"
cleanup() {
  rm -rf "${TMP_AGENT}"
}
trap cleanup EXIT

# Quick preflight so a dead/unauthorized MCP fails before the chat UI.
# shellcheck disable=SC2086
python3 "${SCRIPT_DIR}/mcp-check.py" \
  --url "${PIPESHUB_MCP_URL}" \
  --token "${PIPESHUB_MCP_TOKEN}" \
  --require-tool ${REQUIRED_TOOLS} >/dev/null

# Omnigent may auto-resume the last chat for this agent, which can ignore a new
# --harness/--model and keep a broken session. Prefer a fresh session unless the
# caller explicitly continues/resumes.
EXTRA_ARGS=()
want_resume=0
prev=""
for arg in "$@"; do
  case "$arg" in
    --continue|-c|--resume|-r|--no-session) want_resume=1 ;;
  esac
  if [[ "$prev" == "--resume" || "$prev" == "-r" ]]; then
    want_resume=1
  fi
  prev="$arg"
done
if [[ "$want_resume" -eq 0 ]]; then
  EXTRA_ARGS+=(--no-session)
fi

info "Starting Omnigent with PipesHub tools (default: pipeshub__pipeshub_chat) ..."
# Bash 3.2 (macOS) + set -u treats empty "${arr[@]}" as unbound.
omnigent run "${TMP_AGENT}" ${EXTRA_ARGS[@]+"${EXTRA_ARGS[@]}"} "$@"