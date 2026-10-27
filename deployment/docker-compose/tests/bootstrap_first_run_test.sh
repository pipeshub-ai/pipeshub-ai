#!/usr/bin/env bash
# ==============================================================================
# Tests for bootstrap-first-run.sh — no Docker, no live instance.
# Run: bash deployment/docker-compose/tests/bootstrap_first_run_test.sh
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BOOTSTRAP="$COMPOSE_DIR/bootstrap-first-run.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

PASS_FILE="$TMP_ROOT/.pass"; FAIL_FILE="$TMP_ROOT/.fail"
: >"$PASS_FILE"; : >"$FAIL_FILE"
pass() { printf "  ok   - %s\n" "$1"; echo x >>"$PASS_FILE"; }
fail() { printf "  FAIL - %s\n" "$1"; echo "$1" >>"$FAIL_FILE"; }

# Fixture PAT — must never appear on the script's stdout/stderr.
FIXTURE_PAT='phpat_eyJtestfixtureTokenNotForHumans'
export FIXTURE_PAT

make_env() {
  local path="$1"
  cat >"$path" <<EOF
PIPESHUB_ORIGIN=http://localhost:3000
PIPESHUB_ACCOUNT_EMAIL=demo@example.com
PIPESHUB_ACCOUNT_PASSWORD='ChangeMe1!'
PIPESHUB_ACCOUNT_FULL_NAME='Demo User'
PIPESHUB_ACCOUNT_TYPE=individual
  PIPESHUB_LLM_PROVIDER=ollama
PIPESHUB_LLM_MODEL=qwen3.5:2b
PIPESHUB_LLM_API_KEY='sk_test_llm_not_for_argv'
PIPESHUB_LLM_ENDPOINT=http://host.docker.internal:11434
EOF
}

make_fake_curl() {
  local bindir="$1"
  mkdir -p "$bindir"
  cat >"$bindir/curl" <<'EOF'
#!/usr/bin/env bash
method="GET"
url=""
hdr_out=""
body_out=""
data_file=""
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  case "${args[$i]}" in
    -X) method="${args[$((i+1))]}"; i=$((i+1)) ;;
    -D) hdr_out="${args[$((i+1))]}"; i=$((i+1)) ;;
    -o) body_out="${args[$((i+1))]}"; i=$((i+1)) ;;
    --data-binary)
      raw="${args[$((i+1))]}"; i=$((i+1))
      data_file="${raw#@}"
      ;;
    http://*|https://*) url="${args[$i]}" ;;
  esac
done
path="${url#*://}"
path="/${path#*/}"
path="${path#/}"
# url is origin + path; strip scheme+host
if [[ "$url" == *://* ]]; then
  path="${url#*://*/}"
  path="/${path}"
fi
# More reliable: everything after the origin port
if [[ "$url" =~ ^https?://[^/]+(/.*)$ ]]; then
  path="${BASH_REMATCH[1]}"
fi

mkdir -p "$(dirname "$CURL_LOG")"
{
  echo "METHOD=$method PATH=$path"
  echo "ARGS=${args[*]}"
  if [[ -n "$data_file" && -f "$data_file" ]]; then
    echo "BODY=$(tr -d '\n' <"$data_file")"
  fi
} >>"$CURL_LOG"

write_hdr() {
  printf 'HTTP/1.1 %s\n' "$1" >"$hdr_out"
  shift
  printf '%s\n' "$@" >>"$hdr_out"
  printf '\n' >>"$hdr_out"
}

case "$method $path" in
  "GET /api/v1/org/exists")
    write_hdr 200
    if [[ "${FAKE_ORG_EXISTS:-false}" == "true" ]]; then
      printf '{"exists":true}' >"$body_out"
    else
      printf '{"exists":false}' >"$body_out"
    fi
    printf '200'
    ;;
  "POST /api/v1/org")
    write_hdr 200
    printf '{"_id":"org1","accountType":"individual"}' >"$body_out"
    printf '200'
    ;;
  "POST /api/v1/userAccount/initAuth")
    write_hdr 200 "x-session-token: sess_test_token"
    printf '{"message":"Authentication initialized"}' >"$body_out"
    printf '200'
    ;;
  "POST /api/v1/userAccount/authenticate")
    write_hdr 200
    printf '{"message":"Fully authenticated","accessToken":"jwt_test_access"}' >"$body_out"
    printf '200'
    ;;
  "POST /api/v1/configurationManager/ai-models/providers")
    write_hdr 200
    printf '{"ok":true}' >"$body_out"
    printf '200'
    ;;
  "POST /api/v1/personal-access-tokens")
    write_hdr 201
    printf '{"message":"ok","token":{"id":"t1","name":"agent-bootstrap","accessToken":"%s"}}' "${FIXTURE_PAT}" >"$body_out"
    printf '201'
    ;;
  "PUT /api/v1/org/onboarding-status")
    write_hdr 200
    printf '{"status":"configured"}' >"$body_out"
    printf '200'
    ;;
  "POST /api/v1/connectors/")
    write_hdr 200
    printf '{"success":true,"connector":{"connectorId":"demo-conn-1","connectorType":"Demo"}}' >"$body_out"
    printf '200'
    ;;
  "PUT /api/v1/connectors/demo-conn-1/config")
    write_hdr 200
    printf '{"success":true}' >"$body_out"
    printf '200'
    ;;
  "POST /api/v1/connectors/demo-conn-1/toggle")
    write_hdr 200
    printf '{"success":true}' >"$body_out"
    printf '200'
    ;;
  "POST /api/v1/users/")
    write_hdr 201
    printf '{"_id":"u1","email":"persona@example.com"}' >"$body_out"
    printf '201'
    ;;
  *)
    write_hdr 500
    printf '{"error":"unexpected %s %s"}' "$method" "$path" >"$body_out"
    printf '500'
    ;;
esac
EOF
  chmod +x "$bindir/curl"
}

echo "== syntax =="
if bash -n "$BOOTSTRAP"; then
  pass "bash -n bootstrap-first-run.sh"
else
  fail "bash -n bootstrap-first-run.sh"
fi

echo "== refuses public origin =="
envf="$TMP_ROOT/public.env"
make_env "$envf"
# override origin
printf '\nPIPESHUB_ORIGIN=https://example.com\n' >>"$envf"
out="$TMP_ROOT/public.out"
if "$BOOTSTRAP" --env-file "$envf" --token-file "$TMP_ROOT/should-not-exist" >"$out" 2>&1; then
  fail "public origin should fail"
else
  if grep -q "not loopback/private" "$out"; then
    pass "public origin refused"
  else
    fail "public origin error message"
    cat "$out"
  fi
fi
if [[ -e "$TMP_ROOT/should-not-exist" ]]; then
  fail "token file must not be created on origin reject"
else
  pass "no token file on origin reject"
fi
if grep -F "$FIXTURE_PAT" "$out" >/dev/null; then
  fail "fixture PAT leaked on origin reject"
else
  pass "no PAT on origin-reject stderr/stdout"
fi

echo "== refuses non-local HTTP even with ALLOW_NONLOCAL =="
envf="$TMP_ROOT/nonlocal-http.env"
make_env "$envf"
printf '\nPIPESHUB_ORIGIN=http://example.com\nPIPESHUB_ALLOW_NONLOCAL=1\n' >>"$envf"
out="$TMP_ROOT/nonlocal-http.out"
if "$BOOTSTRAP" --env-file "$envf" --token-file "$TMP_ROOT/should-not-exist-http" >"$out" 2>&1; then
  fail "non-local HTTP should fail"
else
  if grep -q "must use https" "$out"; then
    pass "non-local HTTP refused"
  else
    fail "non-local HTTP error message"
    cat "$out"
  fi
fi
if [[ -e "$TMP_ROOT/should-not-exist-http" ]]; then
  fail "token file must not be created on non-local HTTP reject"
else
  pass "no token file on non-local HTTP reject"
fi

echo "== refuses existing org =="
bindir="$TMP_ROOT/bin-exists"
CURL_LOG="$TMP_ROOT/exists.log"; export CURL_LOG
make_fake_curl "$bindir"
envf="$TMP_ROOT/exists.env"; make_env "$envf"
out="$TMP_ROOT/exists.out"
if FAKE_ORG_EXISTS=true PATH="$bindir:$PATH" \
  "$BOOTSTRAP" --env-file "$envf" --token-file "$TMP_ROOT/nope-exists" >"$out" 2>&1; then
  fail "existing org should fail"
else
  if grep -q "already has an org" "$out"; then
    pass "existing org refused"
  else
    fail "existing org error message"
    cat "$out"
  fi
fi

echo "== happy path writes PAT to a file =="
bindir="$TMP_ROOT/bin-ok"
CURL_LOG="$TMP_ROOT/ok.log"; export CURL_LOG
: >"$CURL_LOG"
make_fake_curl "$bindir"
envf="$TMP_ROOT/ok.env"; make_env "$envf"
token="$TMP_ROOT/token"
out="$TMP_ROOT/ok.out"
if PATH="$bindir:$PATH" \
  "$BOOTSTRAP" --env-file "$envf" --token-file "$token" >"$out" 2>&1; then
  pass "happy path exit 0"
else
  fail "happy path exit 0"
  cat "$out"
fi

if grep -F "$FIXTURE_PAT" "$out" >/dev/null; then
  fail "PAT must not appear on stdout/stderr"
  cat "$out"
else
  pass "PAT absent from stdout/stderr"
fi

if [[ -f "$token" ]]; then
  mode="$(stat -c '%a' "$token" 2>/dev/null || stat -f '%OLp' "$token")"
  if [[ "$mode" == "600" ]]; then
    pass "token file mode 600"
  else
    fail "token file mode (got $mode)"
  fi
  if [[ "$(cat "$token")" == "$FIXTURE_PAT" ]]; then
    pass "token file contains PAT only"
  else
    fail "token file contents"
  fi
else
  fail "token file created"
fi

if grep -q 'Do not cat' "$out"; then
  pass "success copy tells the operator not to cat the file"
else
  fail "success copy missing cat warning"
fi

log="$(cat "$CURL_LOG")"
for needle in \
  "METHOD=GET PATH=/api/v1/org/exists" \
  "METHOD=POST PATH=/api/v1/org" \
  "METHOD=POST PATH=/api/v1/userAccount/initAuth" \
  "METHOD=POST PATH=/api/v1/userAccount/authenticate" \
  "METHOD=POST PATH=/api/v1/configurationManager/ai-models/providers" \
  "METHOD=POST PATH=/api/v1/personal-access-tokens" \
  "METHOD=PUT PATH=/api/v1/org/onboarding-status"
 do
  if grep -q "$needle" "$CURL_LOG"; then
    pass "called $needle"
  else
    fail "called $needle"
  fi
done

if grep -q '"accountType":"individual"' "$CURL_LOG"; then
  pass "org body includes accountType"
else
  fail "org body includes accountType"
fi
if grep -q '"status":"configured"' "$CURL_LOG"; then
  pass "onboarding status configured"
else
  fail "onboarding status configured"
fi
if grep -q '"semantic:write"' "$CURL_LOG"; then
  pass "PAT create sends agent scopes"
else
  fail "PAT create sends agent scopes"
fi
if grep -q '"provider":"ollama"' "$CURL_LOG"; then
  pass "LLM provider ollama"
else
  fail "LLM provider ollama"
fi

echo "== demo data is opt-in =="
if grep -q "PATH=/api/v1/connectors" "$CURL_LOG"; then
  fail "no connector calls without PIPESHUB_DEMO_DATA"
else
  pass "no connector calls without PIPESHUB_DEMO_DATA"
fi

echo "== PIPESHUB_DEMO_DATA=1 creates and syncs the Demo connector =="
bindir="$TMP_ROOT/bin-demo"
CURL_LOG="$TMP_ROOT/demo.log"; export CURL_LOG
: >"$CURL_LOG"
make_fake_curl "$bindir"
envf="$TMP_ROOT/demo.env"; make_env "$envf"
printf '\nPIPESHUB_DEMO_DATA=1\n' >>"$envf"
out="$TMP_ROOT/demo.out"
if PATH="$bindir:$PATH" \
  "$BOOTSTRAP" --env-file "$envf" --token-file "$TMP_ROOT/token-demo" >"$out" 2>&1; then
  pass "demo path exit 0"
else
  fail "demo path exit 0"
  cat "$out"
fi
for needle in \
  "METHOD=POST PATH=/api/v1/connectors/" \
  "METHOD=PUT PATH=/api/v1/connectors/demo-conn-1/config" \
  "METHOD=POST PATH=/api/v1/connectors/demo-conn-1/toggle"
 do
  if grep -q "$needle" "$CURL_LOG"; then
    pass "called $needle"
  else
    fail "called $needle"
  fi
done
if grep -q '"connectorType":"Demo"' "$CURL_LOG" && grep -q '"scope":"team"' "$CURL_LOG"; then
  pass "Demo connector created as a team connector"
else
  fail "Demo connector created as a team connector"
fi
if grep -q '"type":"sync"' "$CURL_LOG"; then
  pass "sync toggled on"
else
  fail "sync toggled on"
fi
if grep -q 'Demo data' "$out"; then
  pass "success copy mentions the demo data"
else
  fail "success copy mentions the demo data"
fi

echo "== personas need a business account =="
bindir="$TMP_ROOT/bin-persona-indiv"
CURL_LOG="$TMP_ROOT/persona-indiv.log"; export CURL_LOG
make_fake_curl "$bindir"
envf="$TMP_ROOT/persona-indiv.env"; make_env "$envf"
printf '\nPIPESHUB_DEMO_DATA=1\nPIPESHUB_DEMO_PERSONAS=1\nPIPESHUB_DEMO_PASSWORD=Persona1!\n' >>"$envf"
out="$TMP_ROOT/persona-indiv.out"
if PATH="$bindir:$PATH" \
  "$BOOTSTRAP" --env-file "$envf" --token-file "$TMP_ROOT/token-persona-indiv" >"$out" 2>&1; then
  fail "personas on an individual account should be refused"
else
  if grep -q "needs PIPESHUB_ACCOUNT_TYPE=business" "$out" && ! grep -q "METHOD=" "$CURL_LOG" 2>/dev/null; then
    pass "personas refused on individual account before any request"
  else
    fail "personas refused on individual account before any request"
    cat "$out"
  fi
fi

echo "== a persona password the server would refuse is refused before any request =="
long_pw="Aa1!$(printf 'x%.0s' $(seq 1 70))"   # meets every rule except the 72-byte cap
# Strong-looking, but the server's JavaScript "." stops at a line separator (U+2028).
sep_pw="Persona1!$(printf '\342\200\250')tail"
for case in "weak:weakpass" "long:$long_pw" "separator:$sep_pw"; do
  label="${case%%:*}"; pw="${case#*:}"
  bindir="$TMP_ROOT/bin-persona-pw-$label"
  CURL_LOG="$TMP_ROOT/persona-pw-$label.log"; export CURL_LOG
  : >"$CURL_LOG"
  make_fake_curl "$bindir"
  envf="$TMP_ROOT/persona-pw-$label.env"; make_env "$envf"
  printf '\nPIPESHUB_ACCOUNT_TYPE=business\nPIPESHUB_REGISTERED_NAME=Acme\nPIPESHUB_DEMO_DATA=1\nPIPESHUB_DEMO_PERSONAS=1\nPIPESHUB_DEMO_PASSWORD=%s\n' "$pw" >>"$envf"
  out="$TMP_ROOT/persona-pw-$label.out"
  if PATH="$bindir:$PATH" \
    "$BOOTSTRAP" --env-file "$envf" --token-file "$TMP_ROOT/token-persona-pw-$label" >"$out" 2>&1; then
    fail "$label persona password should be refused"
  elif grep -q "PIPESHUB_DEMO_PASSWORD needs" "$out" && ! grep -q "METHOD=" "$CURL_LOG" 2>/dev/null \
       && ! grep -qF -- "$pw" "$out"; then
    pass "$label persona password refused before any request, without echoing it"
  else
    fail "$label persona password refused before any request, without echoing it"
    cat "$out"
  fi
done

echo "== PIPESHUB_DEMO_PERSONAS=1 creates Alice and Bob before the connector =="
bindir="$TMP_ROOT/bin-persona"
CURL_LOG="$TMP_ROOT/persona.log"; export CURL_LOG
: >"$CURL_LOG"
make_fake_curl "$bindir"
envf="$TMP_ROOT/persona.env"; make_env "$envf"
printf '\nPIPESHUB_ACCOUNT_TYPE=business\nPIPESHUB_REGISTERED_NAME=Acme\nPIPESHUB_DEMO_DATA=1\nPIPESHUB_DEMO_PERSONAS=1\nPIPESHUB_DEMO_PASSWORD=Persona1!\n' >>"$envf"
out="$TMP_ROOT/persona.out"
if PATH="$bindir:$PATH" \
  "$BOOTSTRAP" --env-file "$envf" --token-file "$TMP_ROOT/token-persona" >"$out" 2>&1; then
  pass "persona path exit 0"
else
  fail "persona path exit 0"
  cat "$out"
fi
if [[ "$(grep -c 'METHOD=POST PATH=/api/v1/users/' "$CURL_LOG")" == "2" ]]; then
  pass "two persona users created"
else
  fail "two persona users created"
fi
if grep -q '"email":"alice@acme-demo.example"' "$CURL_LOG" && grep -q '"email":"bob@acme-demo.example"' "$CURL_LOG" \
   && grep -q '"fullName":"Alice Chen"' "$CURL_LOG" && grep -q '"password":"Persona1!"' "$CURL_LOG"; then
  pass "personas carry fixture emails, full names and the starting password"
else
  fail "personas carry fixture emails, full names and the starting password"
fi
users_line="$(grep -n 'METHOD=POST PATH=/api/v1/users/' "$CURL_LOG" | head -1 | cut -d: -f1)"
conn_line="$(grep -n 'METHOD=POST PATH=/api/v1/connectors/' "$CURL_LOG" | head -1 | cut -d: -f1)"
if [[ -n "$users_line" && -n "$conn_line" && "$users_line" -lt "$conn_line" ]]; then
  pass "personas created before the connector sync"
else
  fail "personas created before the connector sync"
fi
if grep -q 'Persona1!' "$out"; then
  fail "persona password must not appear on stdout/stderr"
else
  pass "persona password absent from stdout/stderr"
fi
if grep -q 'ARGS=.*Persona1!' "$CURL_LOG"; then
  fail "persona password must stay off curl argv"
else
  pass "persona password off curl argv"
fi

echo "== secrets stay off curl argv =="
args_only="$(grep '^ARGS=' "$CURL_LOG" || true)"
for secret in 'ChangeMe1!' 'jwt_test_access' 'sess_test_token' 'sk_test_llm_not_for_argv'; do
  if printf '%s\n' "$args_only" | grep -F -- "$secret" >/dev/null; then
    fail "secret on curl argv ($secret)"
  else
    pass "secret off curl argv ($secret)"
  fi
done

echo
pass_n=$(wc -c <"$PASS_FILE" | tr -d ' ')
fail_n=$(wc -l <"$FAIL_FILE" | tr -d ' ')
echo "passed=$pass_n failed=$fail_n"
if [[ "$fail_n" != "0" ]]; then
  echo "failures:"
  cat "$FAIL_FILE"
  exit 1
fi
exit 0
