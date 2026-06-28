#!/usr/bin/env bash
# ==============================================================================
# Tests for the PipesHub installers — pure bash, no Docker, no network.
# ==============================================================================
# Covers:
#   - Syntax validity of both installer scripts (bash -n).
#   - Root wrapper repo mode: delegates to the in-tree installer with args.
#   - Root wrapper standalone mode: downloads files (via a stubbed curl) into
#     PIPESHUB_DIR and execs the downloaded installer with args.
#   - Standalone PIPESHUB_REF resolution: explicit ref, latest-release tag, and
#     the main fallback all hit the correct download URLs.
#   - Regression guards on the in-tree installer edits (16 GB-class RAM floor,
#     host-side reachability check, health-gated "ready" banner, plain compose
#     progress, generous/overridable health-wait timeout).
#   - Compose app healthcheck stays reconciled with the installer's readiness
#     check (core services only; embedding excluded).
#
# Run: bash deployment/docker-compose/tests/installer_test.sh
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$COMPOSE_DIR/../.." && pwd)"
ROOT_INSTALLER="$REPO_ROOT/install.sh"
INNER_INSTALLER="$COMPOSE_DIR/install.sh"

TMP_ROOT="$(mktemp -d)"
trap 'rm -rf "$TMP_ROOT"' EXIT

# Counters live in files so results recorded inside ( ) subshells still count
# toward the final tally and exit status.
PASS_FILE="$TMP_ROOT/.pass"; FAIL_FILE="$TMP_ROOT/.fail"
: >"$PASS_FILE"; : >"$FAIL_FILE"
pass() { printf "  ok   - %s\n" "$1"; echo x >>"$PASS_FILE"; }
fail() { printf "  FAIL - %s\n" "$1"; echo "$1" >>"$FAIL_FILE"; }
check() { # check "desc" actual expected_substring
  if [[ "$2" == *"$3"* ]]; then pass "$1"; else
    fail "$1"; printf "         expected to contain: %s\n         got: %s\n" "$3" "$2"; fi
}

# Extract a top-level function definition (closing brace in column 0) from a
# script so the real implementation can be exercised in isolation.
extract_fn() { awk -v fn="$1" '$0 ~ "^"fn"\\(\\) \\{"{g=1} g{print} g&&/^\}/{exit}' "$2"; }

# A fake "inner installer" that records the args it was called with so we can
# assert the wrapper handed them through unchanged.
make_fake_inner() {
  local path="$1"
  mkdir -p "$(dirname "$path")"
  cat >"$path" <<'EOF'
#!/usr/bin/env bash
echo "INNER_RAN args=[$*]"
EOF
  chmod +x "$path"
}

# A fake curl placed on PATH for standalone-mode tests. It logs each requested
# URL to $CURL_LOG. With -o it writes a file (the inner installer or a stub
# compose file); without -o (the releases/latest fetch) it prints $RELEASE_JSON.
make_fake_curl() {
  local bindir="$1"
  mkdir -p "$bindir"
  cat >"$bindir/curl" <<'EOF'
#!/usr/bin/env bash
out=""; url=""
args=("$@")
for ((i=0; i<${#args[@]}; i++)); do
  case "${args[$i]}" in
    -o) out="${args[$((i+1))]}" ;;
    http://*|https://*) url="${args[$i]}" ;;
  esac
done
[[ -n "${CURL_LOG:-}" ]] && echo "$url" >>"$CURL_LOG"
if [[ -n "$out" ]]; then
  case "$url" in
    *install.sh)       cp "$FAKE_INNER" "$out" ;;
    *docker-compose.yml) echo "fake-compose" >"$out" ;;
    *)                 echo "x" >"$out" ;;
  esac
  exit 0
fi
printf '%s' "${RELEASE_JSON:-}"
exit 0
EOF
  chmod +x "$bindir/curl"
}

echo "== Syntax checks =="
if bash -n "$ROOT_INSTALLER" 2>/dev/null; then pass "root install.sh parses"; else fail "root install.sh parses"; fi
if bash -n "$INNER_INSTALLER" 2>/dev/null; then pass "inner install.sh parses"; else fail "inner install.sh parses"; fi

echo "== Root wrapper: repo mode delegates with args =="
(
  work="$TMP_ROOT/repo"; mkdir -p "$work/deployment/docker-compose"
  cp "$ROOT_INSTALLER" "$work/install.sh"
  make_fake_inner "$work/deployment/docker-compose/install.sh"
  out="$(bash "$work/install.sh" alpha --yes 2>&1)"
  check "repo mode execs inner installer" "$out" "INNER_RAN"
  check "repo mode forwards args" "$out" "args=[alpha --yes]"
)

echo "== Root wrapper: standalone mode downloads + execs =="
(
  work="$TMP_ROOT/standalone"; mkdir -p "$work"
  cp "$ROOT_INSTALLER" "$work/install.sh"   # no deployment/ dir beside it -> standalone
  bindir="$TMP_ROOT/bin-latest"; make_fake_curl "$bindir"
  export FAKE_INNER="$TMP_ROOT/fake_inner.sh"; make_fake_inner "$FAKE_INNER"
  export CURL_LOG="$TMP_ROOT/curl_latest.log"; : >"$CURL_LOG"
  export RELEASE_JSON='{"tag_name":"v9.9.9"}'
  export PIPESHUB_DIR="$work/pipeshub"
  out="$(PATH="$bindir:$PATH" bash "$work/install.sh" beta 2>&1)"
  check "standalone execs downloaded installer" "$out" "INNER_RAN"
  check "standalone forwards args" "$out" "args=[beta]"
  [[ -f "$PIPESHUB_DIR/docker-compose.yml" ]] && pass "compose file downloaded" || fail "compose file downloaded"
  [[ -f "$PIPESHUB_DIR/install.sh" ]] && pass "installer downloaded" || fail "installer downloaded"
  check "uses latest release tag in URL" "$(cat "$CURL_LOG")" "/v9.9.9/"
)

echo "== Root wrapper: PIPESHUB_REF override wins =="
(
  work="$TMP_ROOT/ref"; mkdir -p "$work"
  cp "$ROOT_INSTALLER" "$work/install.sh"
  bindir="$TMP_ROOT/bin-ref"; make_fake_curl "$bindir"
  export FAKE_INNER="$TMP_ROOT/fake_inner.sh"
  export CURL_LOG="$TMP_ROOT/curl_ref.log"; : >"$CURL_LOG"
  export RELEASE_JSON='{"tag_name":"v9.9.9"}'   # should be ignored
  export PIPESHUB_REF="my-branch"
  export PIPESHUB_DIR="$work/pipeshub"
  PATH="$bindir:$PATH" bash "$work/install.sh" >/dev/null 2>&1
  log="$(cat "$CURL_LOG")"
  check "explicit ref used in URL" "$log" "/my-branch/"
  if [[ "$log" == *"/v9.9.9/"* ]]; then fail "explicit ref must not fall back to release"; else pass "explicit ref overrides release tag"; fi
)

echo "== Root wrapper: main fallback when no release =="
(
  work="$TMP_ROOT/main"; mkdir -p "$work"
  cp "$ROOT_INSTALLER" "$work/install.sh"
  bindir="$TMP_ROOT/bin-main"; make_fake_curl "$bindir"
  export FAKE_INNER="$TMP_ROOT/fake_inner.sh"
  export CURL_LOG="$TMP_ROOT/curl_main.log"; : >"$CURL_LOG"
  export RELEASE_JSON=''   # no release found
  unset PIPESHUB_REF
  export PIPESHUB_DIR="$work/pipeshub"
  PATH="$bindir:$PATH" bash "$work/install.sh" >/dev/null 2>&1
  check "falls back to main branch" "$(cat "$CURL_LOG")" "/main/"
)

echo "== In-tree installer: regression guards =="
inner="$(cat "$INNER_INSTALLER")"
check "RAM floor is 16 GB-class (15000 MB)" "$inner" "_RAM_MIN_MB=15000"
check "host-side reachability check present" "$inner" "check_host_reachable"
check "host reachability gates readiness" "$inner" "CONTAINER_HEALTHY && \$HOST_REACHABLE"
check "ready banner is health-gated" "$inner" "PipesHub AI is ready!"
check "not-ready banner exists" "$inner" "not confirmed ready yet"
check "profile repair on reuse present" "$inner" "Repairing to"
check "cross-directory guard present" "$inner" "Existing deployment detected"
check "reuse-path port check present" "$inner" "is already in use by another process"
check "unset DATA_STORE defaults to neo4j" "$inner" "defaulting to Neo4j (no existing graph data found)"
check "unset DATA_STORE reuses arango volume" "$inner" "reusing the existing ArangoDB data volume"
check "unset DATA_STORE reuses neo4j volume" "$inner" "reusing the existing Neo4j data volume"
check "ambiguous both-volumes still errors" "$inner" "data volumes for BOTH graph"
check "lost graph password guidance" "$inner" "cannot be recovered"
check "summary graph DB fallback is honest" "$inner" '"${DATA_STORE:-(unset)}"'
check ".env locked to owner-only" "$inner" 'chmod 600 "$ENV_FILE"'
check ".env backup locked to owner-only" "$inner" 'chmod 600 "$_backup"'
# Compose animates progress with cursor escapes that explode into hundreds of
# duplicated frames when output is captured; force append-only plain progress.
check "plain progress flag defined" "$inner" "_PROGRESS=(--progress plain)"
check "plain progress applied to compose up/pull" "$inner" 'docker compose "${_PROGRESS[@]}"'
# First start (embedding model download + cold stack) can edge past 5 min; the
# default must be generous and overridable so it does not falsely report failure.
check "health wait default is 420s and overridable" "$inner" 'HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-420}"'
# Health wait: clean single-line spinner on TTY, sparse heartbeat when captured,
# and a final probe so a last-interval pass is not reported as a failure.
check "health wait has a TTY spinner (in-place)" "$inner" '\r  ${CYAN}%s${RESET} Starting services'
check "health wait has sparse heartbeat for captured output" "$inner" "still starting (%ds / %ds)"
if [[ "$inner" == *"Waiting... %ds elapsed"* ]]; then fail "old per-interval Waiting spam removed"; else pass "old per-interval Waiting spam removed"; fi
check "health wait runs a final probe after the loop" "$inner" 'if ! $CONTAINER_HEALTHY && app_is_healthy; then'
check "readiness probe factored into app_is_healthy" "$inner" "app_is_healthy() {"
# A restart-looping container must be detected and reported as the cause, with
# cause-neutral, actionable guidance — NOT a hard-coded "it's OOM" claim, since a
# repeatedly restarting container may be crashing (exit 139) rather than OOM-killed.
check "health wait detects crash loops" "$inner" "crash_looping_containers"
check "crash loop reported as the failure cause" "$inner" "keeps restarting"
check "crash loop guidance is cause-neutral (137 vs 139)" "$inner" "exit 137"
check "crash loop guidance covers segfault/corruption" "$inner" "exit 139"
check "crash loop guidance still offers slim profile" "$inner" "drops Kafka/Zookeeper"
# Must not revert to asserting OOM as the definitive cause.
if [[ "$inner" == *"almost always host memory pressure"* ]]; then
  fail "crash-loop message must not assert OOM as the certain cause"
else
  pass "crash-loop message does not over-assert OOM"
fi

echo "== Compose: app healthcheck reconciled with installer =="
compose="$(cat "$COMPOSE_DIR/docker-compose.yml")"
check "app healthcheck gates on core services" "$compose" "required=('query','connector','indexing','docling')"
# embedding may be absent or 'unhealthy' for minutes on first run; gating the app
# container on it leaves docker ps perpetually 'unhealthy' while the app works.
if [[ "$compose" == *"s.get('embedding') in ('healthy','starting')"* ]]; then
  fail "embedding must not gate app container health"
else
  pass "embedding excluded from app container health gate"
fi
# Mongo must cap its WiredTiger cache + carry a memory limit, else it tries to
# grab ~50% of host RAM and gets OOM-killed into a restart loop on small hosts.
check "mongo caps WiredTiger cache" "$compose" "wiredTigerCacheSizeGB"
check "mongo has a memory limit" "$compose" 'memory: ${MONGO_MEMORY_LIMIT:-2G}'
# Mongo image tag must be overridable (default unchanged) so hosts where mongo 8.x
# segfaults can pin a working tag (e.g. 7.0) without editing the compose file.
check "mongo image tag is overridable" "$compose" 'image: mongo:${MONGO_IMAGE_TAG:-8.0.17}'
# Verified fix for the glibc rseq segfault on new kernels: an overridable
# GLIBC_TUNABLES env (empty default, so normal hosts are unaffected).
check "mongo GLIBC_TUNABLES is overridable" "$compose" 'GLIBC_TUNABLES=${MONGO_GLIBC_TUNABLES:-}'

echo "== In-tree installer: crash-loop detection (real function) =="
eval "$(extract_fn crash_looping_containers "$INNER_INSTALLER")"
(
  CRASH_LOOP_THRESHOLD=4
  PROJECT_NAME="pipeshub-ai"
  docker() {
    case "$1 $2" in
      "ps -aq") echo c1; echo c2; return 0 ;;
    esac
    case "$*" in
      *c1*RestartCount*) echo 7 ;; *c1*Name*) echo /mongodb ;; *c1*ExitCode*) echo 139 ;;
      *c2*RestartCount*) echo 1 ;; *c2*Name*) echo /redis ;; *c2*ExitCode*) echo 0 ;;
    esac
  }
  out="$(crash_looping_containers)"
  check "reports container above restart threshold" "$out" "mongodb (7 restarts"
  check "report includes last exit code" "$out" "last exit 139"
  if [[ "$out" == *redis* ]]; then fail "must ignore containers under threshold"; else pass "ignores containers under threshold"; fi
)

# --stop must tear down ALL profile-gated containers (not just the active
# profile) so leftover graph/broker containers do not block network removal.
stop_block="$(awk '/if \$FLAG_STOP; then/{g=1} g{print} g&&/^fi/{exit}' "$INNER_INSTALLER")"
check "stop enables all profiles" "$stop_block" 'COMPOSE_PROFILES="graph-arango,graph-neo4j,kv-etcd,broker-kafka"'
check "stop removes orphans" "$stop_block" "down --remove-orphans"
uninstall_block="$(awk '/if \$FLAG_UNINSTALL; then/{g=1} g{print} g&&/^fi/{exit}' "$INNER_INSTALLER")"
check "uninstall removes orphans" "$uninstall_block" "down -v --remove-orphans"

echo "== In-tree installer: cross-directory + port helpers (real functions) =="
eval "$(extract_fn compose_other_working_dirs "$INNER_INSTALLER")"
eval "$(extract_fn port_owned_by_project "$INNER_INSTALLER")"

(
  PROJECT_NAME="pipeshub-ai"; SCRIPT_DIR="/here"
  docker() { printf '%s\n' "/here" "/other/dir" "/here"; }
  out="$(compose_other_working_dirs)"
  check "reports the other working dir" "$out" "/other/dir"
  if [[ "$out" == *"/here"* ]]; then fail "must exclude current dir"; else pass "excludes current dir"; fi
)
(
  PROJECT_NAME="pipeshub-ai"; SCRIPT_DIR="/here"
  docker() { printf '%s\n' "/here"; }
  [[ -z "$(compose_other_working_dirs)" ]] && pass "silent when only current dir runs" || fail "silent when only current dir runs"
)
(
  PROJECT_NAME="pipeshub-ai"; SCRIPT_DIR="/here"
  docker() { :; }   # nothing running
  [[ -z "$(compose_other_working_dirs)" ]] && pass "silent when nothing running" || fail "silent when nothing running"
)
(
  PROJECT_NAME="pipeshub-ai"
  docker() { printf '%s\n' "0.0.0.0:3000->3000/tcp, :::3000->3000/tcp"; }
  if port_owned_by_project 3000; then pass "detects own published port"; else fail "detects own published port"; fi
  if port_owned_by_project 3001; then fail "must not match a different port"; else pass "ignores unrelated port"; fi
)

echo "== In-tree installer: COMPOSE_PROFILES derivation (real functions) =="
# Pull the real function definitions out of the installer and exercise them in
# isolation. Both are defined at top level with the closing brace in column 0.
eval "$(extract_fn derive_compose_profiles "$INNER_INSTALLER")"
eval "$(extract_fn persist_env_var "$INNER_INSTALLER")"

dp() { DATA_STORE="$1" KV_STORE_TYPE="$2" MESSAGE_BROKER="$3" derive_compose_profiles; }
check "arango + kafka + redis kv" "$(dp arangodb redis kafka)" "graph-arango,broker-kafka"
check "neo4j + redis + redis (slim)" "$(dp neo4j redis redis)" "graph-neo4j"
check "neo4j + etcd + kafka (full custom)" "$(dp neo4j etcd kafka)" "graph-neo4j,kv-etcd,broker-kafka"
[[ -z "$(dp '' '' '')" ]] && pass "all-unset derives empty" || fail "all-unset derives empty"
# The exact stale value from the user's terminal must be corrected, not trusted.
check "repairs stale 'kafka' to real profiles" "$(dp arangodb redis kafka)" "graph-arango,broker-kafka"
# Missing DATA_STORE drops the graph profile (only broker-kafka) — this is why
# the installer hard-validates DATA_STORE before launch.
check "missing DATA_STORE yields no graph profile" "$(dp '' redis kafka)" "broker-kafka"
if [[ "$(dp '' redis kafka)" == *"graph-"* ]]; then fail "must not invent a graph profile"; else pass "no graph profile when DATA_STORE empty"; fi

echo "== In-tree installer: persist_env_var replaces in place =="
(
  ENV_FILE="$TMP_ROOT/env_persist"
  printf 'SECRET_KEY=abc\nCOMPOSE_PROFILES=kafka\nAPP_PORT=3000\n' >"$ENV_FILE"
  persist_env_var COMPOSE_PROFILES "graph-arango,broker-kafka"
  got="$(cat "$ENV_FILE")"
  check "stale profile line replaced" "$got" "COMPOSE_PROFILES=graph-arango,broker-kafka"
  check "other keys preserved (SECRET_KEY)" "$got" "SECRET_KEY=abc"
  check "other keys preserved (APP_PORT)" "$got" "APP_PORT=3000"
  if [[ "$(grep -c '^COMPOSE_PROFILES=' "$ENV_FILE")" == "1" ]]; then pass "no duplicate profile line"; else fail "no duplicate profile line"; fi
  # Append path when the key is absent.
  printf 'SECRET_KEY=abc\n' >"$ENV_FILE"
  persist_env_var COMPOSE_PROFILES "graph-neo4j"
  check "missing key appended" "$(cat "$ENV_FILE")" "COMPOSE_PROFILES=graph-neo4j"
)

echo
PASS="$(wc -l <"$PASS_FILE" | tr -d ' ')"
FAIL="$(wc -l <"$FAIL_FILE" | tr -d ' ')"
printf "Results: %s passed, %s failed\n" "$PASS" "$FAIL"
if [[ "$FAIL" -ne 0 ]]; then
  echo "Failed checks:"; sed 's/^/  - /' "$FAIL_FILE"
fi
[[ "$FAIL" -eq 0 ]]
