#!/usr/bin/env bash
# Shared helpers for the PipesHub + Omnigent integration scripts.

set -euo pipefail

_LIB_DEFAULT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
INTEGRATION_ROOT="${INTEGRATION_ROOT:-${_LIB_DEFAULT_ROOT}}"
AGENT_DIR="${AGENT_DIR:-${INTEGRATION_ROOT}/agent}"
LOCAL_DIR="${LOCAL_DIR:-${INTEGRATION_ROOT}/.local}"
CREDENTIALS_FILE="${CREDENTIALS_FILE:-${LOCAL_DIR}/credentials.env}"
CONFIG_EXAMPLE="${CONFIG_EXAMPLE:-${AGENT_DIR}/config.yaml.example}"
CONFIG_FILE="${CONFIG_FILE:-${AGENT_DIR}/config.yaml}"
REQUIRED_TOOL="${REQUIRED_TOOL:-pipeshub_chat}"
# Space-separated MCP tools that setup/run preflight must see.
REQUIRED_TOOLS="${REQUIRED_TOOLS:-pipeshub_chat pipeshub_search pipeshub_sources pipeshub_directory pipeshub_download_record pipeshub_agents}"
# OAuth client_credentials scopes for the full PipesHub MCP tool surface.
OAUTH_SCOPES="${OAUTH_SCOPES:-semantic:write kb:read conversation:write conversation:chat agent:read agent:execute user:read team:read}"
MIN_OMNIGENT_VERSION="${MIN_OMNIGENT_VERSION:-0.7.0}"

die() {
  echo "error: $*" >&2
  exit 1
}

info() {
  echo "$*"
}

require_cmd() {
  local cmd="$1"
  local hint="${2:-}"
  command -v "$cmd" >/dev/null 2>&1 || die "missing required command: ${cmd}${hint:+ (${hint})}"
}

normalize_pipeshub_url() {
  local url="${1:-}"
  [[ -n "$url" ]] || die "PipesHub URL is required"

  url="${url%"${url##*[![:space:]]}"}"
  url="${url#"${url%%[![:space:]]*}"}"
  url="${url%/}"

  if [[ "$url" == */api/v1 ]]; then
    die "PipesHub URL must be the site origin (for example http://localhost:3000), not .../api/v1"
  fi
  if [[ "$url" == */mcp ]]; then
    die "PipesHub URL must be the site origin (for example http://localhost:3000), not .../mcp"
  fi

  local host
  host="$(python3 - "$url" <<'PY'
import sys
from urllib.parse import urlparse
u = urlparse(sys.argv[1])
if u.scheme not in ("http", "https") or not u.netloc:
    raise SystemExit("invalid URL; expected http(s)://host[:port]")
if u.username or u.password:
    raise SystemExit("URL must not contain user information")
if u.path not in ("", "/") or u.query or u.fragment:
    raise SystemExit("URL must be a site origin without a path, query, or fragment")
print(u.hostname or "")
PY
)" || die "invalid PipesHub URL: ${url}"

  if [[ "$host" != "localhost" && "$host" != "127.0.0.1" && "$url" != https://* ]]; then
    die "non-localhost PipesHub URLs must use https:// (got: ${url})"
  fi

  printf '%s\n' "$url"
}

mcp_url_from_origin() {
  printf '%s/mcp\n' "$(normalize_pipeshub_url "$1")"
}

write_credentials() {
  local origin="$1"
  local token="$2"
  local refresh_token="${3:-}"
  local auth_method="${4:-token}"

  mkdir -p "${LOCAL_DIR}"
  umask 077
  local tmp
  tmp="$(mktemp "${LOCAL_DIR}/credentials.env.XXXXXX")"
  {
    printf 'PIPESHUB_URL=%q\n' "$origin"
    printf 'PIPESHUB_MCP_URL=%q\n' "$(mcp_url_from_origin "$origin")"
    printf 'PIPESHUB_MCP_TOKEN=%q\n' "$token"
    printf 'PIPESHUB_AUTH_METHOD=%q\n' "$auth_method"
    if [[ -n "$refresh_token" ]]; then
      printf 'PIPESHUB_REFRESH_TOKEN=%q\n' "$refresh_token"
    fi
  } >"$tmp"
  mv "$tmp" "${CREDENTIALS_FILE}"
  chmod 600 "${CREDENTIALS_FILE}"
}

load_credentials() {
  [[ -f "${CREDENTIALS_FILE}" ]] || die "missing ${CREDENTIALS_FILE}; run ./scripts/setup.sh first"
  # shellcheck disable=SC1090
  set -a
  # credentials.env uses shell-quoted assignments from printf %q
  source "${CREDENTIALS_FILE}"
  set +a
  [[ -n "${PIPESHUB_MCP_TOKEN:-}" ]] || die "PIPESHUB_MCP_TOKEN missing in credentials file"
  [[ -n "${PIPESHUB_MCP_URL:-}" || -n "${PIPESHUB_URL:-}" ]] || die "PIPESHUB_URL missing in credentials file"
  if [[ -z "${PIPESHUB_MCP_URL:-}" ]]; then
    PIPESHUB_MCP_URL="$(mcp_url_from_origin "${PIPESHUB_URL}")"
  fi
}

write_agent_config() {
  local mcp_url="$1"
  [[ -f "${CONFIG_EXAMPLE}" ]] || die "missing ${CONFIG_EXAMPLE}"
  mkdir -p "${LOCAL_DIR}"
  umask 077
  local tmp
  tmp="$(mktemp "${LOCAL_DIR}/config.yaml.XXXXXX")"
  python3 - "$CONFIG_EXAMPLE" "$tmp" "$mcp_url" <<'PY'
from pathlib import Path
import re
import sys
src, dst, mcp_url = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
text = src.read_text(encoding="utf-8")
pattern = re.compile(r'(?m)^(\s*url:\s*)["\']?__PIPESHUB_MCP_URL__["\']?\s*$')
if not pattern.search(text):
    raise SystemExit("config example is missing url: __PIPESHUB_MCP_URL__ placeholder")
updated = pattern.sub(rf'\1{mcp_url}', text, count=1)
dst.write_text(updated, encoding="utf-8")
PY
  mv "$tmp" "${CONFIG_FILE}"
  chmod 600 "${CONFIG_FILE}"
}

jwt_is_expired() {
  # Exit 0 when expired (or unknown/unparseable — force re-auth path only when
  # clearly expired). Exit 1 when the token is still valid.
  local token="$1"
  python3 - "$token" <<'PY'
import base64, json, sys, time
token = sys.argv[1]
parts = token.split(".")
if len(parts) < 2:
    raise SystemExit(1)  # unknown shape; try the request
pad = "=" * (-len(parts[1]) % 4)
try:
    payload = json.loads(base64.urlsafe_b64decode(parts[1] + pad))
except Exception:
    raise SystemExit(1)
if not isinstance(payload, dict):
    raise SystemExit(1)
try:
    exp = float(payload.get("exp"))
except (TypeError, ValueError):
    raise SystemExit(1)
# expire a minute early to avoid racing the request
raise SystemExit(0 if time.time() >= (exp - 60) else 1)
PY
}

version_ge() {
  # return 0 if $1 >= $2
  python3 - "$1" "$2" <<'PY'
import sys
def parts(v):
    out = []
    for p in v.split("."):
        try:
            out.append(int(p))
        except ValueError:
            out.append(0)
    return out
a, b = parts(sys.argv[1]), parts(sys.argv[2])
n = max(len(a), len(b))
a += [0] * (n - len(a))
b += [0] * (n - len(b))
raise SystemExit(0 if a >= b else 1)
PY
}
