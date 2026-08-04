#!/usr/bin/env bash
# Configure the local Omnigent agent to talk to a PipesHub MCP endpoint.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=lib.sh
source "${SCRIPT_DIR}/lib.sh"

usage() {
  cat <<'EOF'
Usage: ./scripts/setup.sh [options]

Configure integrations/omnigent for a running PipesHub instance.

Options:
  --url URL                 PipesHub origin (e.g. http://localhost:3000)
  --auth token|password|oauth
                            Authentication method (default: prompt)
  --email EMAIL             Email for password login
  --non-interactive         Fail instead of prompting
  --skip-mcp-check          Write config without calling /mcp
  -h, --help                Show this help

Secrets (token, password, client secret) are never accepted as flags.
Provide them via hidden prompts or environment variables:
  PIPESHUB_MCP_TOKEN
  PIPESHUB_PASSWORD
  PIPESHUB_CLIENT_ID / PIPESHUB_CLIENT_SECRET
EOF
}

AUTH_METHOD=""
PIPESHUB_URL_ARG=""
EMAIL_ARG=""
NON_INTERACTIVE=0
SKIP_MCP_CHECK=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --url)
      PIPESHUB_URL_ARG="${2:-}"
      shift 2
      ;;
    --auth)
      AUTH_METHOD="${2:-}"
      shift 2
      ;;
    --email)
      EMAIL_ARG="${2:-}"
      shift 2
      ;;
    --non-interactive)
      NON_INTERACTIVE=1
      shift
      ;;
    --skip-mcp-check)
      SKIP_MCP_CHECK=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

require_cmd curl "install curl"
require_cmd python3 "install python3"

prompt() {
  local label="$1"
  local default="${2:-}"
  local value=""
  if [[ -n "$default" ]]; then
    read -r -p "${label} [${default}]: " value || true
    printf '%s\n' "${value:-$default}"
  else
    read -r -p "${label}: " value || true
    printf '%s\n' "$value"
  fi
}

prompt_secret() {
  local label="$1"
  local value=""
  read -r -s -p "${label}: " value || true
  echo >&2
  printf '%s\n' "$value"
}

if [[ -z "$PIPESHUB_URL_ARG" ]]; then
  if [[ "$NON_INTERACTIVE" -eq 1 ]]; then
    die "--url is required in non-interactive mode"
  fi
  PIPESHUB_URL_ARG="$(prompt "PipesHub URL" "http://localhost:3000")"
fi
ORIGIN="$(normalize_pipeshub_url "$PIPESHUB_URL_ARG")"
MCP_URL="$(mcp_url_from_origin "$ORIGIN")"

if [[ -z "$AUTH_METHOD" ]]; then
  if [[ -n "${PIPESHUB_MCP_TOKEN:-}" ]]; then
    AUTH_METHOD="token"
  elif [[ -n "${PIPESHUB_CLIENT_ID:-}" && -n "${PIPESHUB_CLIENT_SECRET:-}" ]]; then
    AUTH_METHOD="oauth"
  elif [[ "$NON_INTERACTIVE" -eq 1 ]]; then
    die "--auth is required in non-interactive mode (or set PIPESHUB_MCP_TOKEN / OAuth env vars)"
  else
    cat <<'EOF'
How should Omnigent connect to PipesHub?
  1) Paste an existing access token
  2) Log in to a local/password-enabled PipesHub
  3) Use OAuth app client credentials (CI/service)
EOF
    local_choice="$(prompt "Choice" "1")"
    case "$local_choice" in
      1) AUTH_METHOD="token" ;;
      2) AUTH_METHOD="password" ;;
      3) AUTH_METHOD="oauth" ;;
      *) die "invalid choice: ${local_choice}" ;;
    esac
  fi
fi

ACCESS_TOKEN=""
REFRESH_TOKEN=""

case "$AUTH_METHOD" in
  token)
    if [[ -n "${PIPESHUB_MCP_TOKEN:-}" ]]; then
      ACCESS_TOKEN="${PIPESHUB_MCP_TOKEN}"
    elif [[ "$NON_INTERACTIVE" -eq 1 ]]; then
      die "PIPESHUB_MCP_TOKEN must be set in non-interactive token mode"
    else
      ACCESS_TOKEN="$(prompt_secret "Access token")"
    fi
    [[ -n "$ACCESS_TOKEN" ]] || die "access token is required"
    ;;
  password)
    EMAIL="${EMAIL_ARG:-${PIPESHUB_EMAIL:-}}"
    if [[ -z "$EMAIL" ]]; then
      [[ "$NON_INTERACTIVE" -eq 0 ]] || die "--email or PIPESHUB_EMAIL required for password auth"
      EMAIL="$(prompt "Email")"
    fi
    if [[ -n "${PIPESHUB_PASSWORD:-}" ]]; then
      PASSWORD="${PIPESHUB_PASSWORD}"
    elif [[ "$NON_INTERACTIVE" -eq 1 ]]; then
      die "PIPESHUB_PASSWORD must be set in non-interactive password mode"
    else
      PASSWORD="$(prompt_secret "Password")"
    fi
    [[ -n "$EMAIL" && -n "$PASSWORD" ]] || die "email and password are required"

    info "Logging in to ${ORIGIN} ..."
    INIT_HEADERS="$(mktemp)"
    INIT_BODY="$(mktemp)"
    HTTP_CODE="$(curl -sS -D "$INIT_HEADERS" -o "$INIT_BODY" -w '%{http_code}' \
      -X POST "${ORIGIN}/api/v1/userAccount/initAuth" \
      -H 'Content-Type: application/json' \
      -d "$(python3 -c 'import json,sys; print(json.dumps({"email": sys.argv[1]}))' "$EMAIL")" || true)"
    if [[ "$HTTP_CODE" != "200" ]]; then
      die "initAuth failed with HTTP ${HTTP_CODE}"
    fi
    SESSION_TOKEN="$(awk -F': ' 'tolower($1)=="x-session-token"{gsub(/\r/,"",$2); print $2}' "$INIT_HEADERS")"
    ALLOWED="$(python3 - "$INIT_BODY" <<'PY'
import json,sys
try:
  data=json.load(open(sys.argv[1]))
except Exception:
  raise SystemExit("initAuth response was not valid JSON")
if not isinstance(data, dict):
  raise SystemExit("initAuth response was not a JSON object")
methods=data.get("allowedMethods") or []
if methods and (not isinstance(methods, list) or any(not isinstance(m, str) for m in methods)):
  raise SystemExit("initAuth allowedMethods must be a list of strings")
print(",".join(methods) if isinstance(methods, list) else "")
PY
)" || die "initAuth response was malformed"
    rm -f "$INIT_HEADERS" "$INIT_BODY"
    [[ -n "$SESSION_TOKEN" ]] || die "initAuth did not return x-session-token"
    if [[ -n "$ALLOWED" && ",${ALLOWED}," != *",password,"* ]]; then
      die "this PipesHub org does not allow password login (allowed: ${ALLOWED}). Use --auth token or --auth oauth."
    fi

    AUTH_BODY="$(mktemp)"
    HTTP_CODE="$(curl -sS -o "$AUTH_BODY" -w '%{http_code}' \
      -X POST "${ORIGIN}/api/v1/userAccount/authenticate" \
      -H "Content-Type: application/json" \
      -H "x-session-token: ${SESSION_TOKEN}" \
      -d "$(python3 -c 'import json,sys; print(json.dumps({"email":sys.argv[1],"method":"password","credentials":{"password":sys.argv[2]}}))' "$EMAIL" "$PASSWORD")" || true)"
    PASSWORD=""
    if [[ "$HTTP_CODE" != "200" ]]; then
      msg="$(python3 - "$AUTH_BODY" <<'PY'
import json,sys
try:
  data=json.load(open(sys.argv[1]))
except Exception:
  print("authentication failed")
  raise SystemExit
if not isinstance(data, dict):
  print("authentication failed")
  raise SystemExit
print(data.get("message") or data.get("error") or "authentication failed")
PY
)"
      rm -f "$AUTH_BODY"
      die "authenticate failed with HTTP ${HTTP_CODE}: ${msg}"
    fi
    eval "$(python3 - "$AUTH_BODY" <<'PY'
import json,sys,shlex
try:
  data=json.load(open(sys.argv[1]))
except Exception:
  print('die "authenticate response was not valid JSON"')
  raise SystemExit
if not isinstance(data, dict):
  print('die "authenticate response was not a JSON object"')
  raise SystemExit
if data.get("nextStep") and not data.get("accessToken"):
    print('die "org requires multi-step or SSO authentication; use --auth token or --auth oauth"')
    raise SystemExit
token=data.get("accessToken") or ""
refresh=data.get("refreshToken") or ""
if not isinstance(token, str) or not token:
    print('die "authenticate response did not include accessToken"')
    raise SystemExit
if refresh is not None and not isinstance(refresh, str):
    print('die "authenticate response refreshToken was malformed"')
    raise SystemExit
print(f"ACCESS_TOKEN={shlex.quote(token)}")
print(f"REFRESH_TOKEN={shlex.quote(refresh or '')}")
PY
)"
    rm -f "$AUTH_BODY"
    ;;
  oauth)
    CLIENT_ID="${PIPESHUB_CLIENT_ID:-}"
    CLIENT_SECRET="${PIPESHUB_CLIENT_SECRET:-}"
    if [[ -z "$CLIENT_ID" || -z "$CLIENT_SECRET" ]]; then
      [[ "$NON_INTERACTIVE" -eq 0 ]] || die "PIPESHUB_CLIENT_ID and PIPESHUB_CLIENT_SECRET are required"
      CLIENT_ID="$(prompt "OAuth client ID")"
      CLIENT_SECRET="$(prompt_secret "OAuth client secret")"
    fi
    [[ -n "$CLIENT_ID" && -n "$CLIENT_SECRET" ]] || die "client id and secret are required"
    info "Requesting client_credentials token from ${ORIGIN} ..."
    TOKEN_BODY="$(mktemp)"
    HTTP_CODE="$(curl -sS -o "$TOKEN_BODY" -w '%{http_code}' \
      -X POST "${ORIGIN}/api/v1/oauth2/token" \
      -H 'Content-Type: application/json' \
      -d "$(python3 -c 'import json,sys; print(json.dumps({"grant_type":"client_credentials","client_id":sys.argv[1],"client_secret":sys.argv[2],"scope":sys.argv[3]}))' "$CLIENT_ID" "$CLIENT_SECRET" "$OAUTH_SCOPES")" || true)"
    CLIENT_SECRET=""
    if [[ "$HTTP_CODE" != "200" ]]; then
      rm -f "$TOKEN_BODY"
      die "oauth token request failed with HTTP ${HTTP_CODE}"
    fi
    ACCESS_TOKEN="$(python3 - "$TOKEN_BODY" <<'PY'
import json,sys
try:
  data=json.load(open(sys.argv[1]))
except Exception:
  raise SystemExit("oauth response was not valid JSON")
if not isinstance(data, dict):
  raise SystemExit("oauth response was not a JSON object")
token=data.get("access_token") or ""
if not isinstance(token, str) or not token:
  raise SystemExit("oauth response did not include access_token")
print(token)
PY
)" || die "oauth response was malformed"
    rm -f "$TOKEN_BODY"
    [[ -n "$ACCESS_TOKEN" ]] || die "oauth response did not include access_token"
    info "Note: client_credentials results use the OAuth app owner's permissions, not each end user's."
    ;;
  *)
    die "unsupported --auth value: ${AUTH_METHOD} (use token|password|oauth)"
    ;;
esac

write_credentials "$ORIGIN" "$ACCESS_TOKEN" "${REFRESH_TOKEN:-}" "$AUTH_METHOD"
write_agent_config "$MCP_URL"

if [[ "$SKIP_MCP_CHECK" -eq 0 ]]; then
  info "Checking MCP endpoint ${MCP_URL} ..."
  # shellcheck disable=SC2086
  python3 "${SCRIPT_DIR}/mcp-check.py" --url "$MCP_URL" --token "$ACCESS_TOKEN" --require-tool ${REQUIRED_TOOLS}
else
  info "Skipped MCP check (--skip-mcp-check)."
fi

cat <<EOF

Setup complete.
  config:      ${CONFIG_FILE}
  credentials: ${CREDENTIALS_FILE} (mode 600)

Run the knowledge agent:
  ./scripts/run.sh -p "What is our refund policy?"

OAuth apps need scopes:
  ${OAUTH_SCOPES}
EOF
