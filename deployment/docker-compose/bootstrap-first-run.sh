#!/usr/bin/env bash
# ==============================================================================
# PipesHub — scripted first-run (mint-to-file)
# ==============================================================================
# Supported bootstrap for an *empty* instance. This is the Path C contract:
# mint the PAT to a file, never to stdout. Do not curl these APIs from an
# agent transcript — run this script instead.
#
# Does:
#   1. POST /api/v1/org                         (first org; first-claimer-wins)
#   2. POST /api/v1/userAccount/initAuth        (x-session-token response header)
#   3. POST /api/v1/userAccount/authenticate    (password; session JWT)
#   4. POST /api/v1/configurationManager/ai-models/providers   (LLM)
#   5. POST /api/v1/personal-access-tokens      (secret → --token-file only)
#   6. PUT  /api/v1/org/onboarding-status       { status: configured }
#
# Does not:
#   - Connect Slack / Drive / Jira (browser OAuth)
#   - Print, log, or echo the PAT, password, or LLM key
#   - Accept a token as a CLI argument
#
# Requires: bash, curl, python3, an empty instance (GET /api/v1/org/exists
# is {exists:false}). Origin must be loopback / private unless you set
# PIPESHUB_ALLOW_NONLOCAL=1.
#
# Usage:
#   ./bootstrap-first-run.sh --env-file ./bootstrap-first-run.env \
#       --token-file "$HOME/.config/pipeshub/token"
# ==============================================================================
set -euo pipefail
# Never dump secrets if someone runs `bash -x`.
set +x
unset HISTFILE || true

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

ENV_FILE=""
TOKEN_FILE=""
ORIGIN=""

usage() {
  cat <<'EOF'
Usage: bootstrap-first-run.sh --env-file PATH --token-file PATH

  --env-file PATH     gitignored env file (see bootstrap-first-run.env.example)
  --token-file PATH   write the PAT here (mode 0600). The file is created
                      and must not already exist. Do not cat this file.
  --origin URL        override PIPESHUB_ORIGIN from the env file
  --help              this message

Environment (also accepted in --env-file):
  PIPESHUB_ORIGIN              default http://localhost:3000
  PIPESHUB_ACCOUNT_EMAIL
  PIPESHUB_ACCOUNT_PASSWORD    complexity: 8+ with upper, lower, digit, special
  PIPESHUB_ACCOUNT_FULL_NAME
  PIPESHUB_ACCOUNT_TYPE        individual | business  (default individual)
  PIPESHUB_REGISTERED_NAME     required when account type is business
  PIPESHUB_LLM_PROVIDER        e.g. ollama, openAI, anthropic, gemini
  PIPESHUB_LLM_MODEL
  PIPESHUB_LLM_API_KEY         required except ollama / openAICompatible-with-endpoint
  PIPESHUB_LLM_ENDPOINT        ollama default http://host.docker.internal:11434
  PIPESHUB_ALLOW_NONLOCAL      1 to skip the loopback/private-host check
  PIPESHUB_BOOTSTRAP_CURL      curl binary (tests inject a fake)
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --env-file) ENV_FILE="${2:-}"; shift 2 ;;
    --token-file) TOKEN_FILE="${2:-}"; shift 2 ;;
    --origin) ORIGIN="${2:-}"; shift 2 ;;
    --help|-h) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

die() { echo "bootstrap-first-run: $*" >&2; exit 1; }

need_bin() {
  command -v "$1" >/dev/null 2>&1 || die "missing required binary: $1"
}
need_bin python3
CURL_BIN="${PIPESHUB_BOOTSTRAP_CURL:-curl}"
need_bin "$CURL_BIN"

[[ -n "$ENV_FILE" ]] || die "--env-file is required"
[[ -f "$ENV_FILE" ]] || die "env file not found: $ENV_FILE"
[[ -n "$TOKEN_FILE" ]] || die "--token-file is required"
[[ "$TOKEN_FILE" != "-" && "$TOKEN_FILE" != "/dev/stdout" && "$TOKEN_FILE" != "/dev/stderr" ]] \
  || die "--token-file must be a real file path, not stdout"
[[ ! -e "$TOKEN_FILE" ]] || die "--token-file already exists: $TOKEN_FILE (refusing to overwrite)"

# Load KEY=VALUE lines for known keys only. No `source`, no `eval`.
load_env_file() {
  python3 - "$1" <<'PY'
import os, sys
allowed = {
    "PIPESHUB_ORIGIN",
    "PIPESHUB_ACCOUNT_EMAIL",
    "PIPESHUB_ACCOUNT_PASSWORD",
    "PIPESHUB_ACCOUNT_FULL_NAME",
    "PIPESHUB_ACCOUNT_TYPE",
    "PIPESHUB_REGISTERED_NAME",
    "PIPESHUB_LLM_PROVIDER",
    "PIPESHUB_LLM_MODEL",
    "PIPESHUB_LLM_API_KEY",
    "PIPESHUB_LLM_ENDPOINT",
    "PIPESHUB_ALLOW_NONLOCAL",
}
path = sys.argv[1]
out = {}
with open(path, encoding="utf-8") as f:
    for raw in f:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            raise SystemExit(f"invalid env line (expected KEY=VALUE): {line!r}")
        key, _, val = line.partition("=")
        key = key.strip()
        if key not in allowed:
            raise SystemExit(f"unknown env key {key!r} — will not load it")
        val = val.strip()
        if (val.startswith("'") and val.endswith("'")) or (val.startswith('"') and val.endswith('"')):
            val = val[1:-1]
        out[key] = val
for k, v in out.items():
    print(f"{k}={v}")
PY
}

# Apply file values only when the process env does not already have the key.
while IFS= read -r kv; do
  key="${kv%%=*}"
  val="${kv#*=}"
  if [[ -z "${!key:-}" ]]; then
    printf -v "$key" '%s' "$val"
  fi
done < <(load_env_file "$ENV_FILE")

ORIGIN="${ORIGIN:-${PIPESHUB_ORIGIN:-http://localhost:3000}}"
ORIGIN="${ORIGIN%/}"
ACCOUNT_EMAIL="${PIPESHUB_ACCOUNT_EMAIL:-}"
ACCOUNT_PASSWORD="${PIPESHUB_ACCOUNT_PASSWORD:-}"
ACCOUNT_NAME="${PIPESHUB_ACCOUNT_FULL_NAME:-}"
ACCOUNT_TYPE="${PIPESHUB_ACCOUNT_TYPE:-individual}"
REGISTERED_NAME="${PIPESHUB_REGISTERED_NAME:-}"
LLM_PROVIDER="${PIPESHUB_LLM_PROVIDER:-}"
LLM_MODEL="${PIPESHUB_LLM_MODEL:-}"
LLM_API_KEY="${PIPESHUB_LLM_API_KEY:-}"
LLM_ENDPOINT="${PIPESHUB_LLM_ENDPOINT:-}"
ALLOW_NONLOCAL="${PIPESHUB_ALLOW_NONLOCAL:-0}"

[[ -n "$ACCOUNT_EMAIL" ]] || die "PIPESHUB_ACCOUNT_EMAIL is required"
[[ -n "$ACCOUNT_PASSWORD" ]] || die "PIPESHUB_ACCOUNT_PASSWORD is required"
[[ -n "$ACCOUNT_NAME" ]] || die "PIPESHUB_ACCOUNT_FULL_NAME is required"
[[ "$ACCOUNT_TYPE" == "individual" || "$ACCOUNT_TYPE" == "business" ]] \
  || die "PIPESHUB_ACCOUNT_TYPE must be individual or business"
if [[ "$ACCOUNT_TYPE" == "business" && -z "$REGISTERED_NAME" ]]; then
  die "PIPESHUB_REGISTERED_NAME is required for business accounts"
fi
[[ -n "$LLM_PROVIDER" ]] || die "PIPESHUB_LLM_PROVIDER is required"
[[ -n "$LLM_MODEL" ]] || die "PIPESHUB_LLM_MODEL is required"
if [[ "$LLM_PROVIDER" == "ollama" && -z "$LLM_ENDPOINT" ]]; then
  LLM_ENDPOINT="http://host.docker.internal:11434"
fi
if [[ "$LLM_PROVIDER" != "ollama" && -z "$LLM_API_KEY" ]]; then
  die "PIPESHUB_LLM_API_KEY is required for provider $LLM_PROVIDER"
fi

python3 - "$ORIGIN" "$ALLOW_NONLOCAL" <<'PY' || die "origin is not loopback/private; set PIPESHUB_ALLOW_NONLOCAL=1 if you intend a remote empty instance (first-claimer-wins)"
import ipaddress, sys, urllib.parse
origin, allow = sys.argv[1], sys.argv[2]
if allow == "1":
    sys.exit(0)
u = urllib.parse.urlparse(origin)
if u.scheme not in ("http", "https") or not u.netloc:
    sys.exit(1)
host = (u.hostname or "").lower()
if host in ("localhost", "host.docker.internal"):
    sys.exit(0)
if host.endswith((".local", ".internal", ".svc")) or "." not in host:
    sys.exit(0)
try:
    ip = ipaddress.ip_address(host)
except ValueError:
    sys.exit(1)
if ip.is_loopback or ip.is_private or ip.is_link_local:
    sys.exit(0)
sys.exit(1)
PY

WORKDIR="$(mktemp -d "${TMPDIR:-/tmp}/pipeshub-bootstrap.XXXXXX")"
chmod 700 "$WORKDIR"
cleanup() {
  if [[ -d "$WORKDIR" ]]; then
    find "$WORKDIR" -type f -exec rm -f {} + 2>/dev/null || true
    rm -rf "$WORKDIR"
  fi
  unset ACCOUNT_PASSWORD LLM_API_KEY SESSION_TOKEN ACCESS_TOKEN || true
}
trap cleanup EXIT

# Five-scope agent preset (stock MCP_SCOPES). Do not omit `scopes` on create —
# the API then grants the full mcpScopes set.
AGENT_SCOPES='["conversation:chat","semantic:write","kb:read","user:read","connector:read"]'

json_escape() {
  python3 -c 'import json,sys; print(json.dumps(sys.argv[1]))' "$1"
}

header_value() {
  # $1 headers file, $2 header name
  python3 - "$1" "$2" <<'PY'
import sys
path, name = sys.argv[1], sys.argv[2].lower()
with open(path, encoding="utf-8", errors="replace") as f:
    for line in f:
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        if k.strip().lower() == name:
            print(v.strip())
            break
PY
}

json_str() {
  # $1 body file, $2 dotted path of a string field → stdout (caller must redirect to a 0600 file)
  python3 - "$1" "$2" <<'PY'
import json, sys
obj = json.load(open(sys.argv[1], encoding="utf-8"))
for part in sys.argv[2].split("."):
    if not isinstance(obj, dict) or part not in obj:
        raise SystemExit(f"missing JSON field {sys.argv[2]}")
    obj = obj[part]
if not isinstance(obj, str) or not obj:
    raise SystemExit(f"JSON field {sys.argv[2]} is not a non-empty string")
sys.stdout.write(obj)
PY
}

json_bool() {
  python3 - "$1" "$2" <<'PY'
import json, sys
obj = json.load(open(sys.argv[1], encoding="utf-8"))
for part in sys.argv[2].split("."):
    obj = obj[part]
print("true" if obj is True else "false" if obj is False else str(obj))
PY
}

ph_request() {
  local method="$1" path="$2" body_file="${3:-}" auth="${4:-}"
  local url="${ORIGIN}${path}"
  local hdr="$WORKDIR/last.hdr" body="$WORKDIR/last.body" code_file="$WORKDIR/last.code"
  : >"$hdr"; : >"$body"
  local args=(-sS -D "$hdr" -o "$body" -w "%{http_code}" -X "$method" "$url")
  args+=(-H "Accept: application/json")
  if [[ -n "$body_file" ]]; then
    args+=(-H "Content-Type: application/json" --data-binary @"$body_file")
  fi
  if [[ "$auth" == "session" ]]; then
    args+=(-H "x-session-token: ${SESSION_TOKEN}")
  elif [[ "$auth" == "bearer" ]]; then
    args+=(-H "Authorization: Bearer ${ACCESS_TOKEN}")
  fi
  local code
  code="$("$CURL_BIN" "${args[@]}")"
  printf '%s' "$code" >"$code_file"
  if [[ "$code" != "200" && "$code" != "201" ]]; then
    # Do not print response bodies — they can contain secrets or stack traces.
    die "HTTP $code on $method $path"
  fi
}

# --- 0. instance must be empty ---
ph_request GET "/api/v1/org/exists"
exists="$(json_bool "$WORKDIR/last.body" "exists")"
[[ "$exists" == "false" ]] || die "instance already has an org (GET /api/v1/org/exists is true). This script is first-run only. Use a throwaway stack, not a corpus you care about."

# --- 1. create org ---
{
  printf '{"accountType":%s,"contactEmail":%s,"adminFullName":%s,"password":%s' \
    "$(json_escape "$ACCOUNT_TYPE")" \
    "$(json_escape "$ACCOUNT_EMAIL")" \
    "$(json_escape "$ACCOUNT_NAME")" \
    "$(json_escape "$ACCOUNT_PASSWORD")"
  if [[ "$ACCOUNT_TYPE" == "business" ]]; then
    printf ',"registeredName":%s' "$(json_escape "$REGISTERED_NAME")"
  fi
  printf '}'
} >"$WORKDIR/org.json"
chmod 600 "$WORKDIR/org.json"
ph_request POST "/api/v1/org" "$WORKDIR/org.json"

# --- 2–3. login (session machine, not two independent posts) ---
printf '{"email":%s}' "$(json_escape "$ACCOUNT_EMAIL")" >"$WORKDIR/init.json"
chmod 600 "$WORKDIR/init.json"
ph_request POST "/api/v1/userAccount/initAuth" "$WORKDIR/init.json"
SESSION_TOKEN="$(header_value "$WORKDIR/last.hdr" "x-session-token")"
[[ -n "$SESSION_TOKEN" ]] || die "initAuth did not return x-session-token (CAPTCHA/Turnstile on this instance?)"

python3 - "$WORKDIR/auth.json" "$ACCOUNT_EMAIL" "$ACCOUNT_PASSWORD" <<'PY'
import json, sys
path, email, password = sys.argv[1], sys.argv[2], sys.argv[3]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"method": "password", "email": email, "credentials": {"password": password}}, f, separators=(",", ":"))
PY
chmod 600 "$WORKDIR/auth.json"
ph_request POST "/api/v1/userAccount/authenticate" "$WORKDIR/auth.json" session
json_str "$WORKDIR/last.body" "accessToken" >"$WORKDIR/access.jwt"
chmod 600 "$WORKDIR/access.jwt"
ACCESS_TOKEN="$(cat "$WORKDIR/access.jwt")"
[[ -n "$ACCESS_TOKEN" ]] || die "authenticate did not return accessToken"
rm -f "$WORKDIR/access.jwt"

# --- 4. LLM ---
python3 - "$WORKDIR/llm.json" "$LLM_PROVIDER" "$LLM_MODEL" "$LLM_API_KEY" "$LLM_ENDPOINT" <<'PY'
import json, sys
path, provider, model, api_key, endpoint = sys.argv[1:6]
configuration = {"model": model}
if api_key:
    configuration["apiKey"] = api_key
if endpoint:
    configuration["endpoint"] = endpoint
body = {
    "modelType": "llm",
    "provider": provider,
    "configuration": configuration,
    "isMultimodal": False,
    "isReasoning": False,
    "isDefault": True,
}
with open(path, "w", encoding="utf-8") as f:
    json.dump(body, f, separators=(",", ":"))
PY
chmod 600 "$WORKDIR/llm.json"
ph_request POST "/api/v1/configurationManager/ai-models/providers" "$WORKDIR/llm.json" bearer

# --- 5. PAT → file, never stdout ---
python3 - "$WORKDIR/pat.json" "$AGENT_SCOPES" <<'PY'
import json, sys
path, scopes_raw = sys.argv[1], sys.argv[2]
with open(path, "w", encoding="utf-8") as f:
    json.dump({"name": "agent-bootstrap", "scopes": json.loads(scopes_raw), "expiryDays": 30}, f, separators=(",", ":"))
PY
chmod 600 "$WORKDIR/pat.json"
ph_request POST "/api/v1/personal-access-tokens" "$WORKDIR/pat.json" bearer

token_dir="$(dirname "$TOKEN_FILE")"
mkdir -p "$token_dir"
umask 077
json_str "$WORKDIR/last.body" "token.accessToken" >"$TOKEN_FILE"
chmod 600 "$TOKEN_FILE"
# Confirm we wrote something without reading it back into the transcript later.
python3 - "$TOKEN_FILE" <<'PY'
import os, sys
path = sys.argv[1]
size = os.path.getsize(path)
if size < 20:
    raise SystemExit("token file is too small — PAT create response was not token.accessToken")
PY

# --- 6. flip onboarding so the dashboard is not the wizard ---
printf '{"status":"configured"}' >"$WORKDIR/onboard.json"
ph_request PUT "/api/v1/org/onboarding-status" "$WORKDIR/onboard.json" bearer

echo "bootstrap-first-run: first-run complete."
echo "PAT written to ${TOKEN_FILE} (mode 600). Do not cat, log, or paste that file into chat."
echo "MCP origin: ${ORIGIN}/mcp"
echo "Slack / Drive / Jira still need a browser. This script does not connect them."
echo "Next: Knowledge Base upload or Local FS, then pipeshub_search with backoff."
