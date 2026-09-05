#!/usr/bin/env bash
# ==============================================================================
# Tests for the release tooling — pure bash and python, no Docker, no network.
# ==============================================================================
# The upgrade smoke and the rollback script both take destructive actions
# against a real deployment, so the parts that decide *whether* to act are worth
# testing on their own. Everything here runs in a temporary directory against
# stub installers; nothing pulls an image or starts a container.
#
# Covers:
#   - Syntax of upgrade_smoke.sh and rollback.sh, and that upgrade_seed.py
#     imports cleanly.
#   - upgrade_smoke refusals: unknown deploy type, base == target, and the
#     installer-capability guards that stop it uninstalling someone else's stack.
#   - upgrade_smoke health parsing against the real /health/services shape
#     (services is a map of strings, not objects) — including the inverted case,
#     so the check cannot silently pass an unhealthy stack.
#   - rollback target resolution: explicit tag, previous-from-history, refusal
#     when there is no history, and refusal when target == current.
#   - rollback --dry-run leaves .env untouched.
#   - upgrade_seed response parsing across the shapes the KB list API may return.
#
# Run: bash deployment/docker-compose/tests/release_tooling_test.sh
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
UPGRADE="$SCRIPT_DIR/upgrade_smoke.sh"
SEED="$SCRIPT_DIR/upgrade_seed.py"
ROLLBACK="$COMPOSE_DIR/rollback.sh"
ROOT="$(cd "$COMPOSE_DIR/../.." && pwd)"
SCRIPTS="$ROOT/scripts"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

PASS_FILE="$TMP_ROOT/.pass"; FAIL_FILE="$TMP_ROOT/.fail"
: >"$PASS_FILE"; : >"$FAIL_FILE"
pass() { printf "  ok   - %s\n" "$1"; echo x >>"$PASS_FILE"; }
fail() { printf "  FAIL - %s\n" "$1"; echo "$1" >>"$FAIL_FILE"; }

check() { # check "desc" actual expected_substring
  if [[ "$2" == *"$3"* ]]; then pass "$1"; else
    fail "$1"; printf "         expected to contain: %s\n         got: %s\n" "$3" "$2"
  fi
}
check_not() { # check_not "desc" actual unexpected_substring
  if [[ "$2" != *"$3"* ]]; then pass "$1"; else
    fail "$1"; printf "         should not contain: %s\n         got: %s\n" "$3" "$2"
  fi
}

# A stub installer that satisfies the capability guards without doing anything.
make_stub_installer() { # make_stub_installer <path>
  cat >"$1" <<'STUB'
#!/usr/bin/env bash
# PIPESHUB_PROJECT
# HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-300}"
case " $* " in *" --upgrade "*) echo "stub: upgraded" ;; esac
exit 0
STUB
  chmod +x "$1"
}

echo "== Syntax =="
for f in "$UPGRADE" "$ROLLBACK"; do
  if bash -n "$f" 2>/dev/null; then pass "$(basename "$f") parses"; else fail "$(basename "$f") parses"; fi
done
if python3 -m py_compile "$SEED" 2>/dev/null; then pass "upgrade_seed.py compiles"; else fail "upgrade_seed.py compiles"; fi

echo
echo "== upgrade_smoke: refusals before anything is touched =="
out="$(PIPESHUB_DEPLOY_TYPE=medium bash "$UPGRADE" 2>&1)"
check "rejects an unknown deploy type" "$out" "must be slim or full"

out="$(PIPESHUB_BASE_VERSION=1.2.3 PIPESHUB_TARGET_VERSION=1.2.3 bash "$UPGRADE" 2>&1)"
check "refuses when base and target match" "$out" "nothing would be upgraded"

# Guard: an installer without PIPESHUB_PROJECT would uninstall the default stack.
GUARD_DIR="$TMP_ROOT/guard/tests"; mkdir -p "$GUARD_DIR"
cp "$UPGRADE" "$GUARD_DIR/"; cp "$SEED" "$GUARD_DIR/" 2>/dev/null || true
printf '#!/usr/bin/env bash\nexit 0\n' >"$TMP_ROOT/guard/install.sh"
: >"$TMP_ROOT/guard/docker-compose.yml"
out="$(PIPESHUB_BASE_VERSION=1 PIPESHUB_TARGET_VERSION=2 bash "$GUARD_DIR/upgrade_smoke.sh" 2>&1)"
check "refuses an installer without PIPESHUB_PROJECT" "$out" "does not honour PIPESHUB_PROJECT"

printf '#!/usr/bin/env bash\n# PIPESHUB_PROJECT\nexit 0\n' >"$TMP_ROOT/guard/install.sh"
out="$(PIPESHUB_BASE_VERSION=1 PIPESHUB_TARGET_VERSION=2 bash "$GUARD_DIR/upgrade_smoke.sh" 2>&1)"
check "refuses an installer with a fixed health timeout" "$out" "HEALTH_WAIT_SECS is not overridable"

make_stub_installer "$TMP_ROOT/guard/install.sh"
# Strip --upgrade so the last guard is the one that fires.
sed -i 's/ --upgrade / --nope /' "$TMP_ROOT/guard/install.sh"
out="$(PIPESHUB_BASE_VERSION=1 PIPESHUB_TARGET_VERSION=2 bash "$GUARD_DIR/upgrade_smoke.sh" 2>&1)"
check "refuses an installer with no --upgrade flag" "$out" "no --upgrade flag"

echo
echo "== upgrade_smoke: health parsing matches the real payload =="
# The API returns services as a map of strings. A parser written for nested
# objects passes nothing; one written too loosely passes everything.
# Extract the parser out of upgrade_smoke.sh and run that, rather than a copy of
# it. A copy only ever proves the copy is right: the real parser could change and
# every case here would still pass.
HEALTH_PARSER="$TMP_ROOT/health_parser.py"
python3 - "$UPGRADE" "$HEALTH_PARSER" <<'PY'
import re, sys
src = open(sys.argv[1], encoding="utf-8").read()
m = re.search(r'python3 - "\$WORK/health\.json" <<\'PY\'\n(.*?)\nPY\n', src, re.S)
if not m:
    sys.exit("could not find the health parser in upgrade_smoke.sh — update this test")
open(sys.argv[2], "w", encoding="utf-8").write(m.group(1))
PY
[[ -s "$HEALTH_PARSER" ]] && pass "extracted the health parser from upgrade_smoke.sh" \
                          || fail "extracted the health parser from upgrade_smoke.sh"

health_check() { # health_check <json> -> exit status
  printf '%s' "$1" >"$TMP_ROOT/h.json"
  python3 "$HEALTH_PARSER" "$TMP_ROOT/h.json" 2>/dev/null
}
ALL_OK='{"status":"healthy","services":{"query":"healthy","connector":"healthy","indexing":"healthy","docling":"healthy","embedding":"unhealthy"}}'
ONE_BAD='{"status":"unhealthy","services":{"query":"healthy","connector":"unhealthy","indexing":"healthy","docling":"healthy"}}'
MISSING='{"status":"healthy","services":{"query":"healthy"}}'

if health_check "$ALL_OK"; then pass "accepts all four core services healthy"; else fail "accepts all four core services healthy"; fi
if health_check "$ONE_BAD"; then fail "rejects an unhealthy core service"; else pass "rejects an unhealthy core service"; fi
if health_check "$MISSING"; then fail "rejects a truncated services map"; else pass "rejects a truncated services map"; fi
# embedding is intentionally not required: slim downloads its model on first use.
if health_check "$ALL_OK"; then pass "does not require embedding (slim downloads on first use)"; else fail "does not require embedding"; fi

echo
echo "== rollback: target resolution =="
RB="$TMP_ROOT/deploy"; mkdir -p "$RB"
cp "$ROLLBACK" "$RB/rollback.sh"
make_stub_installer "$RB/install.sh"
printf 'IMAGE_TAG=0.7.0\nAPP_PORT=3000\nDATA_STORE=neo4j\n' >"$RB/.env"

# rollback.sh asks the registry whether the target tag exists. Stub docker so
# this suite stays hermetic — a real `docker manifest inspect` makes the result
# depend on network reachability, Docker Hub rate limits, and which tags happen
# to be published today. MANIFEST_OK flips the answer.
STUB_BIN="$TMP_ROOT/stub-bin"; mkdir -p "$STUB_BIN"
cat >"$STUB_BIN/docker" <<'STUB'
#!/usr/bin/env bash
if [[ "${1:-}" == "manifest" && "${2:-}" == "inspect" ]]; then
  echo "${3:-}" >>"${MANIFEST_LOG:-/dev/null}"
  [[ "${MANIFEST_OK:-1}" == "1" ]] && exit 0
  exit 1
fi
exit 0
STUB
chmod +x "$STUB_BIN/docker"
export PATH="$STUB_BIN:$PATH"
export MANIFEST_LOG="$TMP_ROOT/manifest.log"; : >"$MANIFEST_LOG"

out="$(bash "$RB/rollback.sh" --list 2>&1)"
check "--list reports the running tag" "$out" "currently running: 0.7.0"

# No history beyond the current tag: refuse rather than guess.
out="$(bash "$RB/rollback.sh" 2>&1)"; rc=$?
check "refuses with no previous tag recorded" "$out" "no previous tag recorded"
if [[ $rc -ne 0 ]]; then pass "exits non-zero when it cannot resolve a target"; else fail "exits non-zero when it cannot resolve a target"; fi

# With history, the default target is the entry before the current one.
printf '2026-01-01T00:00:00Z 0.6.0\n2026-02-01T00:00:00Z 0.7.0\n' >"$RB/.pipeshub-image-history"
out="$(bash "$RB/rollback.sh" --dry-run 2>&1)"
check "defaults to the previously recorded tag" "$out" "target  : 0.6.0"

out="$(bash "$RB/rollback.sh" --dry-run 0.5.0 2>&1)"
check "an explicit tag overrides history" "$out" "target  : 0.5.0"

out="$(bash "$RB/rollback.sh" 0.7.0 2>&1)"
check "refuses to roll back to the running tag" "$out" "nothing to roll back to"

out="$(bash "$RB/rollback.sh" --dry-run 0.6.0 2>&1)"
check "warns that data is not rolled back" "$out" "Rolling back changes the code, not the data"
check "verifies the target image before acting" "$(cat "$MANIFEST_LOG")" "pipeshubai/pipeshub-ai:0.6.0"

# A tag that is not published must be refused before .env is touched.
env_before_missing="$(cat "$RB/.env")"
out="$(MANIFEST_OK=0 bash "$RB/rollback.sh" 0.6.0 2>&1)"; rc=$?
check "refuses a tag that is not in the registry" "$out" "not available in the registry"
if [[ $rc -ne 0 ]]; then pass "exits non-zero on a missing target image"; else fail "exits non-zero on a missing target image"; fi
if [[ "$(cat "$RB/.env")" == "$env_before_missing" ]]; then
  pass "leaves .env untouched when the target image is missing"
else
  fail "leaves .env untouched when the target image is missing"
fi

before="$(cat "$RB/.env")"
bash "$RB/rollback.sh" --dry-run 0.6.0 >/dev/null 2>&1
if [[ "$before" == "$(cat "$RB/.env")" ]]; then pass "--dry-run leaves .env untouched"; else fail "--dry-run leaves .env untouched"; fi

out="$(bash "$RB/rollback.sh" --bogus 2>&1)"
check "rejects an unknown option" "$out" "unknown option"

rm -f "$RB/.env"
out="$(bash "$RB/rollback.sh" 0.6.0 2>&1)"
check "refuses outside a deployment directory" "$out" "is this a PipesHub deployment directory?"

echo
echo "== upgrade_seed: KB list parsing =="
seed_parse() { # seed_parse <json> -> newline-separated names
  python3 - "$SEED" "$1" <<'PY'
import importlib.util, json, sys
spec = importlib.util.spec_from_file_location("seed", sys.argv[1])
mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
payload = json.loads(sys.argv[2])
# Stub only the transport, so the real list_kbs unwrapping is what runs.
mod.call = lambda *a, **k: (200, payload, {})
print("\n".join(sorted(mod.kb_name(e) for e in mod.list_kbs("http://stub", "token"))))
PY
}
out="$(seed_parse '[{"kbName":"alpha"},{"kbName":"beta"}]')"
check "parses a bare list" "$out" "alpha"
out="$(seed_parse '{"knowledgeBases":[{"kbName":"alpha"}]}')"
check "parses {knowledgeBases:[...]}" "$out" "alpha"
out="$(seed_parse '{"data":[{"name":"alpha"}]}')"
check "parses {data:[...]} with a name field" "$out" "alpha"
out="$(seed_parse '{"data":{"knowledgeBases":[{"title":"alpha"}]}}')"
check "parses a nested data.knowledgeBases" "$out" "alpha"
out="$(seed_parse '{"unexpected":true}')"
check_not "yields nothing for an unrecognised shape" "$out" "alpha"

echo
echo "== health_probe: reporting and exit status =="
PROBE="$COMPOSE_DIR/tests/health_probe.sh"

out="$(bash "$PROBE" 2>&1)"; rc=$?
check "refuses without PROBE_URL" "$out" "PROBE_URL is not set"
if [[ $rc -eq 2 ]]; then pass "exits 2 on a configuration error"; else fail "exits 2 on a configuration error (got $rc)"; fi

# A port nothing listens on: must fail, and must say so rather than hanging.
out="$(PROBE_URL=http://127.0.0.1:59997 PROBE_RETRIES=1 PROBE_TIMEOUT=3 bash "$PROBE" 2>&1)"; rc=$?
check "reports an unreachable deployment" "$out" "unreachable"
check_not "does not emit a doubled status code" "$out" "000000"
if [[ $rc -eq 1 ]]; then pass "exits 1 when the deployment is down"; else fail "exits 1 when the deployment is down (got $rc)"; fi

# A stub server lets the healthy and degraded paths be tested without a stack.
PROBE_DIR="$TMP_ROOT/probe"; mkdir -p "$PROBE_DIR"
# A tiny routing stub, since python -m http.server cannot serve two paths with
# different bodies. It lets the healthy and degraded branches be tested without
# standing up a real deployment.
cat >"$PROBE_DIR/stub.py" <<'PY'
import http.server, os, sys, threading
root, port = sys.argv[1], int(sys.argv[2])
class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path.startswith("/api/v1/health/services"):
            body = open(os.path.join(root, "health.json"), "rb").read()
            ctype = "application/json"
        elif self.path == "/":
            body, ctype = b"<html>ok</html>", "text/html"
        else:
            self.send_response(404); self.end_headers(); return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
srv = http.server.HTTPServer(("127.0.0.1", port), H)
srv.serve_forever()
PY

STUB_PORT=59321
printf '%s' '{"services":{"query":"healthy","connector":"healthy","indexing":"healthy","docling":"healthy","embedding":"unhealthy"}}' >"$PROBE_DIR/health.json"
python3 "$PROBE_DIR/stub.py" "$PROBE_DIR" "$STUB_PORT" &
STUB_PID=$!
for _ in $(seq 1 30); do
  curl -sf --max-time 1 "http://127.0.0.1:${STUB_PORT}/" >/dev/null 2>&1 && break
  sleep 0.2
done

out="$(PROBE_URL=http://127.0.0.1:${STUB_PORT} PROBE_RETRIES=1 bash "$PROBE" 2>&1)"; rc=$?
check "passes when the core services are healthy" "$out" "PASS  deployment is serving"
if [[ $rc -eq 0 ]]; then pass "exits 0 when healthy"; else fail "exits 0 when healthy (got $rc)"; fi
check_not "does not require embedding to be healthy" "$out" "FAIL"

printf '%s' '{"services":{"query":"healthy","connector":"unhealthy","indexing":"healthy","docling":"healthy"}}' >"$PROBE_DIR/health.json"
out="$(PROBE_URL=http://127.0.0.1:${STUB_PORT} PROBE_RETRIES=1 bash "$PROBE" 2>&1)"; rc=$?
check "names the unhealthy service" "$out" "connector='unhealthy'"
if [[ $rc -eq 1 ]]; then pass "exits 1 when a core service is down"; else fail "exits 1 when a core service is down (got $rc)"; fi
check "still reports the front door separately" "$out" "front door: HTTP 200"

out="$(PROBE_URL=http://127.0.0.1:${STUB_PORT} PROBE_RETRIES=1 PROBE_SERVICES=query bash "$PROBE" 2>&1)"
check "PROBE_SERVICES narrows what must be healthy" "$out" "PASS  deployment is serving"

kill "$STUB_PID" 2>/dev/null; wait "$STUB_PID" 2>/dev/null

echo
echo "== connector_coverage: test discovery =="

# Both naming conventions must count. `any(a or b)` on two globs silently
# ignores b, because a glob returns a generator and a generator is always truthy.
COV_DIR="$TMP_ROOT/cov"; mkdir -p "$COV_DIR"
for pair in "suffix_style:thing_integration_test.py" "prefix_style:test_thing.py" "helpers_only:thing_test_utils.py" "empty:"; do
  d="$COV_DIR/${pair%%:*}"; f="${pair#*:}"
  mkdir -p "$d"; [[ -n "$f" ]] && : >"$d/$f"
done

cov_out="$(python3 - "$SCRIPTS/connector_coverage.py" "$COV_DIR" <<'PY'
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("cov", sys.argv[1])
cov = importlib.util.module_from_spec(spec); spec.loader.exec_module(cov)
cov.INTEGRATION = pathlib.Path(sys.argv[2])
found = cov.tested_connectors()
print("SUFFIX_FOUND" if "suffix_style" in found else "SUFFIX_MISSING")
print("PREFIX_FOUND" if "prefix_style" in found else "PREFIX_MISSING")
print("HELPERS_EXCLUDED" if "helpers_only" not in found else "HELPERS_COUNTED")
print("EMPTY_EXCLUDED" if "empty" not in found else "EMPTY_COUNTED")
PY
)"
check "counts foo_integration_test.py" "$cov_out" "SUFFIX_FOUND"
check "counts test_foo.py" "$cov_out" "PREFIX_FOUND"
check "does not count a bare _test_utils helper" "$cov_out" "HELPERS_EXCLUDED"
check "does not count an empty directory" "$cov_out" "EMPTY_EXCLUDED"

# --check must fail loudly on a connector that is not in the baseline.
cov_check="$(cd "$ROOT" && python3 scripts/connector_coverage.py --check 2>&1)"; cov_rc=$?
if [[ $cov_rc -eq 0 ]]; then pass "--check passes against the committed baseline"; else fail "--check passes against the committed baseline (got $cov_rc: $cov_check)"; fi

BASE_TMP="$TMP_ROOT/baseline.json"
printf '%s' '{"uncovered_connectors": []}' >"$BASE_TMP"
cov_check="$(cd "$ROOT" && python3 - <<PY 2>&1
import importlib.util, pathlib, sys
spec = importlib.util.spec_from_file_location("cov", "scripts/connector_coverage.py")
cov = importlib.util.module_from_spec(spec); spec.loader.exec_module(cov)
cov.BASELINE = pathlib.Path("$BASE_TMP")
sys.exit(cov.check(cov.build_report()))
PY
)"; cov_rc=$?
check "--check reports connectors missing from the baseline" "$cov_check" "no integration test"
if [[ $cov_rc -ne 0 ]]; then pass "--check exits non-zero on a new gap"; else fail "--check exits non-zero on a new gap"; fi

echo
P="$(wc -l <"$PASS_FILE" | tr -d ' ')"; F="$(wc -l <"$FAIL_FILE" | tr -d ' ')"
echo "Results: ${P} passed, ${F} failed"
[[ "$F" -eq 0 ]] || { echo; echo "Failed:"; sed 's/^/  - /' "$FAIL_FILE"; exit 1; }
