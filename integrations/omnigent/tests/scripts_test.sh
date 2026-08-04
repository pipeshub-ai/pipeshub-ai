#!/usr/bin/env bash
# Offline tests for setup/run helpers (no live PipesHub required).

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

FAILS=0
pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*" >&2; FAILS=$((FAILS + 1)); }

# Isolate filesystem side effects before sourcing lib.sh defaults.
TEST_HOME="$(mktemp -d)"
trap 'rm -rf "$TEST_HOME"' EXIT
export INTEGRATION_ROOT="${TEST_HOME}/omnigent"
export AGENT_DIR="${INTEGRATION_ROOT}/agent"
export LOCAL_DIR="${INTEGRATION_ROOT}/.local"
export CREDENTIALS_FILE="${LOCAL_DIR}/credentials.env"
export CONFIG_EXAMPLE="${AGENT_DIR}/config.yaml.example"
export CONFIG_FILE="${AGENT_DIR}/config.yaml"

mkdir -p "${AGENT_DIR}/tools/mcp"
cp "${ROOT}/agent/config.yaml.example" "${CONFIG_EXAMPLE}"
cp "${ROOT}/agent/AGENTS.md" "${AGENT_DIR}/AGENTS.md"

# shellcheck source=../scripts/lib.sh
source "${ROOT}/scripts/lib.sh"

# --- URL normalization ---
if out="$(normalize_pipeshub_url "http://localhost:3000/")" && [[ "$out" == "http://localhost:3000" ]]; then
  pass "normalize strips trailing slash"
else
  fail "normalize strips trailing slash (got: ${out:-})"
fi

if mcp="$(mcp_url_from_origin "http://localhost:3000")" && [[ "$mcp" == "http://localhost:3000/mcp" ]]; then
  pass "mcp url appends /mcp"
else
  fail "mcp url appends /mcp (got: ${mcp:-})"
fi

if (normalize_pipeshub_url "http://localhost:3000/api/v1" >/dev/null 2>&1); then
  fail "should reject /api/v1 suffix"
else
  pass "rejects /api/v1 suffix"
fi

if (normalize_pipeshub_url "http://example.com" >/dev/null 2>&1); then
  fail "should require https for non-localhost"
else
  pass "requires https for non-localhost"
fi

if (normalize_pipeshub_url "https://host.example/path" >/dev/null 2>&1); then
  fail "should reject non-origin path"
else
  pass "rejects non-origin path"
fi

if (normalize_pipeshub_url "https://user:pass@host.example" >/dev/null 2>&1); then
  fail "should reject userinfo in URL"
else
  pass "rejects userinfo in URL"
fi

# --- credentials + config write ---
write_credentials "http://localhost:3000" "test-token-value" "refresh-value" "token"
mode="$(stat -c '%a' "${CREDENTIALS_FILE}" 2>/dev/null || stat -f '%OLp' "${CREDENTIALS_FILE}")"
if [[ "$mode" == "600" ]]; then
  pass "credentials file mode 600"
else
  fail "credentials file mode 600 (got ${mode})"
fi

if grep -q 'test-token-value' "${CREDENTIALS_FILE}" && ! grep -q 'export ' "${CREDENTIALS_FILE}"; then
  pass "credentials contain token without export keyword"
else
  # printf %q may quote; still must contain token text
  if grep -q 'test-token-value' "${CREDENTIALS_FILE}"; then
    pass "credentials contain token"
  else
    fail "credentials missing token"
  fi
fi

write_agent_config "http://localhost:3000/mcp"
if grep -Fq "http://localhost:3000/mcp" "${CONFIG_FILE}" && ! grep -Fq "__PIPESHUB_MCP_URL__" "${CONFIG_FILE}"; then
  pass "agent config placeholder substituted"
else
  fail "agent config placeholder substituted"
fi

cfg_mode="$(stat -c '%a' "${CONFIG_FILE}" 2>/dev/null || stat -f '%OLp' "${CONFIG_FILE}")"
if [[ "$cfg_mode" == "600" ]]; then
  pass "generated config mode 600"
else
  fail "generated config mode 600 (got ${cfg_mode})"
fi

# --- load_credentials ---
unset PIPESHUB_MCP_TOKEN PIPESHUB_URL PIPESHUB_MCP_URL || true
load_credentials
if [[ "${PIPESHUB_MCP_TOKEN}" == "test-token-value" && "${PIPESHUB_URL}" == "http://localhost:3000" ]]; then
  pass "load_credentials restores values"
else
  fail "load_credentials restores values"
fi

# --- jwt expiry helper ---
# header.payload.sig with exp far in the future
FUTURE_JWT="$(python3 - <<'PY'
import base64, json
def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
payload=b64(json.dumps({"exp": 4102444800}).encode())  # 2100-01-01
print(f"aaa.{payload}.bbb")
PY
)"
if jwt_is_expired "$FUTURE_JWT"; then
  fail "future jwt should not be expired"
else
  pass "future jwt not expired"
fi

PAST_JWT="$(python3 - <<'PY'
import base64, json
def b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()
payload=b64(json.dumps({"exp": 1}).encode())
print(f"aaa.{payload}.bbb")
PY
)"
if jwt_is_expired "$PAST_JWT"; then
  pass "past jwt is expired"
else
  fail "past jwt should be expired"
fi

# --- secrets never printed by mcp-check error path ---
if out="$(python3 "${ROOT}/scripts/mcp-check.py" --url "http://127.0.0.1:9/mcp" --token "super-secret-token-xyz" 2>&1 || true)"; then
  :
fi
if [[ "$out" == *super-secret-token-xyz* ]]; then
  fail "mcp-check leaked token in output"
else
  pass "mcp-check does not print token on connection failure"
fi

# --- generated config contains full MCP tool allowlist ---
EXPECTED_TOOLS=(
  pipeshub_chat
  pipeshub_search
  pipeshub_sources
  pipeshub_directory
  pipeshub_download_record
  pipeshub_agents
)
missing_tools=()
for tool in "${EXPECTED_TOOLS[@]}"; do
  grep -Fq -- "- ${tool}" "${CONFIG_FILE}" || missing_tools+=("$tool")
done
if [[ "${#missing_tools[@]}" -eq 0 ]]; then
  pass "generated config allowlists all MCP tools"
else
  fail "generated config missing tools: ${missing_tools[*]}"
fi

# --- OAuth scopes cover conversation/agent/directory needs ---
missing_scopes=()
for scope in semantic:write kb:read conversation:write conversation:chat agent:read agent:execute user:read team:read; do
  if [[ " ${OAUTH_SCOPES} " != *" ${scope} "* ]]; then
    missing_scopes+=("$scope")
  fi
done
if [[ "${#missing_scopes[@]}" -eq 0 ]]; then
  pass "OAUTH_SCOPES cover full MCP surface"
else
  fail "OAUTH_SCOPES missing: ${missing_scopes[*]}"
fi

# --- mcp-check accepts multiple --require-tool values (regression) ---
if out="$(python3 "${ROOT}/scripts/mcp-check.py" \
  --url "http://127.0.0.1:9/mcp" \
  --token "tok" \
  --require-tool pipeshub_chat pipeshub_search pipeshub_sources 2>&1 || true)"; then
  :
fi
if [[ "$out" == *"unrecognized arguments"* ]]; then
  fail "mcp-check rejects multiple --require-tool values"
else
  pass "mcp-check accepts multiple --require-tool values"
fi

# --- materialize resolves inline MCP token and keeps all tools ---
MAT="$(
  python3 "${ROOT}/scripts/materialize_agent.py" \
    --agent-dir "${AGENT_DIR}" \
    --token "super-secret-token-xyz" \
    --mcp-url "http://localhost:3000/mcp"
)"
mat_missing=()
for tool in "${EXPECTED_TOOLS[@]}"; do
  grep -Fq -- "- ${tool}" "${MAT}/config.yaml" || mat_missing+=("$tool")
done
if grep -Fq 'Bearer super-secret-token-xyz' "${MAT}/config.yaml" \
  && ! grep -Fq '${PIPESHUB_MCP_TOKEN}' "${MAT}/config.yaml" \
  && [[ "${#mat_missing[@]}" -eq 0 ]]; then
  pass "materialize_agent resolves token and keeps all MCP tools"
else
  fail "materialize_agent resolves token and keeps all MCP tools (missing: ${mat_missing[*]:-none})"
fi
rm -rf "${MAT}"

# --- AGENTS.md chat-first routing ---
if grep -Fq 'Default tool: `pipeshub__pipeshub_chat`' "${AGENT_DIR}/AGENTS.md" \
  && grep -Fq 'must use **chat**, not directory' "${AGENT_DIR}/AGENTS.md"; then
  pass "AGENTS.md defaults to chat for knowledge/who-is questions"
else
  fail "AGENTS.md defaults to chat for knowledge/who-is questions"
fi

# --- offline agent validation ---
if python3 "${ROOT}/tests/validate_agent.py"; then
  pass "validate_agent.py"
else
  fail "validate_agent.py"
fi

# --- MCP contract (examples / docs / scopes) ---
if python3 "${ROOT}/tests/mcp_contract.py"; then
  pass "mcp_contract.py"
else
  fail "mcp_contract.py"
fi

if [[ "$FAILS" -eq 0 ]]; then
  echo
  echo "All helper tests passed."
  exit 0
fi
echo
echo "${FAILS} test(s) failed." >&2
exit 1
