#!/usr/bin/env bash
# ==============================================================================
# Probe a running PipesHub deployment and report whether it is serving.
# ==============================================================================
# Used by the post-release probe workflow, and useful by hand after any deploy:
#
#   PROBE_URL=https://pipeshub.example.com bash health_probe.sh
#
# Exit status is the whole contract: 0 means the deployment is serving, non-zero
# means it is not. Output is a short report suitable for pasting into an alert.
#
# Deliberately shallow. This answers "is it up and are its services reporting
# healthy", which is what you want checked every thirty minutes. It is not a
# functional test — those belong in the release gate, where they can take
# minutes and use throwaway data.
#
# Optional env:
#   PROBE_TOKEN     bearer token, if the instance requires auth for health
#   PROBE_TIMEOUT   per-request timeout in seconds (default 20)
#   PROBE_RETRIES   attempts before declaring failure (default 3)
#   PROBE_SERVICES  comma-separated services that must be healthy
#                   (default: query,connector,indexing,docling)
# ==============================================================================
set -uo pipefail

URL="${PROBE_URL:-}"
TOKEN="${PROBE_TOKEN:-}"
TIMEOUT="${PROBE_TIMEOUT:-20}"
RETRIES="${PROBE_RETRIES:-3}"
REQUIRED="${PROBE_SERVICES:-query,connector,indexing,docling}"

[[ -n "$URL" ]] || { echo "PROBE_URL is not set"; exit 2; }
URL="${URL%/}"

# A bearer token on a plaintext URL is readable by anything on the path, and the
# probe runs unattended every 30 minutes. Refuse rather than leak it. Probing
# without a token over http is fine — there is nothing to disclose.
if [[ -n "$TOKEN" && "$URL" != https://* ]]; then
  echo "PROBE_TOKEN is set but PROBE_URL is not https:// — refusing to send the token in cleartext."
  echo "Use an https URL, or unset PROBE_TOKEN if the endpoint needs no auth."
  exit 2
fi

command -v curl >/dev/null 2>&1    || { echo "curl is required"; exit 2; }
command -v python3 >/dev/null 2>&1 || { echo "python3 is required"; exit 2; }

TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
FAILURES=0
note() { printf '%s\n' "$*"; }

request() { # request <path> <outfile> -> writes HTTP status to stdout
  local path="$1" out="$2" code
  local -a args=(-sS -o "$out" -w '%{http_code}' --max-time "$TIMEOUT")
  [[ -n "$TOKEN" ]] && args+=(-H "Authorization: Bearer $TOKEN")
  # On a connection failure curl still prints 000 from -w and exits non-zero.
  # Take its output as the single source of truth; adding a fallback echo here
  # concatenates the two into "000000".
  code="$(curl "${args[@]}" "${URL}${path}" 2>"$TMP/curl.err")"
  printf '%s' "${code:-000}"
}

attempt() { # attempt <n>
  local status body_file="$TMP/health.json"
  status="$(request /api/v1/health/services "$body_file")"

  if [[ "$status" == "000" ]]; then
    note "  attempt $1: unreachable — $(tr -d '\n' <"$TMP/curl.err" | cut -c1-120)"
    return 1
  fi
  if [[ "$status" != "200" ]]; then
    note "  attempt $1: HTTP $status"
    return 1
  fi

  REQUIRED="$REQUIRED" python3 - "$body_file" <<'PY'
import json, os, sys
try:
    data = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception as exc:
    print(f"  health endpoint did not return JSON: {exc}")
    sys.exit(1)

services = data.get("services") or {}
required = [s.strip() for s in os.environ["REQUIRED"].split(",") if s.strip()]
bad = {s: services.get(s) for s in required if services.get(s) != "healthy"}

if bad:
    print("  unhealthy: " + ", ".join(f"{k}={v!r}" for k, v in bad.items()))
    others = {k: v for k, v in services.items() if k not in bad}
    if others:
        print("  healthy:   " + ", ".join(sorted(others)))
    sys.exit(1)

print("  services healthy: " + ", ".join(required))
PY
}

note "probe ${URL}"
note ""

ok=false
for i in $(seq 1 "$RETRIES"); do
  if attempt "$i"; then ok=true; break; fi
  [[ "$i" -lt "$RETRIES" ]] && sleep 10
done

if [[ "$ok" != true ]]; then
  note ""
  note "FAIL  health check did not pass in ${RETRIES} attempts"
  FAILURES=$((FAILURES + 1))
fi

# A deployment can report healthy services and still not serve a page, so check
# that something actually answers on the front door too.
status="$(request / "$TMP/root.html")"
case "$status" in
  2*|3*) note "  front door: HTTP ${status}" ;;
  000)   note "  front door: unreachable"; FAILURES=$((FAILURES + 1)) ;;
  *)     note "  front door: HTTP ${status}"; FAILURES=$((FAILURES + 1)) ;;
esac

note ""
if [[ "$FAILURES" -eq 0 ]]; then
  note "PASS  deployment is serving"
  exit 0
fi
note "FAIL  ${FAILURES} check(s) failed against ${URL}"
exit 1
