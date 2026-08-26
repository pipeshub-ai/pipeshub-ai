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
#     host-side reachability check, health-gated "ready" banner, clone vs
#     standalone command directory, plain compose progress, generous/overridable
#     health-wait timeout).
#   - Compose app healthcheck stays reconciled with the installer's readiness
#     check (core services only; embedding excluded).
#   - Compose runtime robustness: HuggingFace offline mode is documented and
#     overridable, but not defaulted on (slim images must download on first use).
#   - env.template documents the above knobs.
#   - Image refresh policy: prebuilt installs refresh the app image by default
#     (so a cached :latest is not run forever), with opt-outs for local builds,
#     --no-pull / PIPESHUB_NO_PULL (air-gapped or keep-current), and pinned tags
#     still refresh to the exact tag.
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
echo "PIPESHUB_INSTALL_REF=${PIPESHUB_INSTALL_REF:-}"
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
  check "standalone exports resolved release as install ref" "$out" "PIPESHUB_INSTALL_REF=v9.9.9"
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
  out="$(PATH="$bindir:$PATH" bash "$work/install.sh" 2>&1)"
  log="$(cat "$CURL_LOG")"
  check "explicit ref used in URL" "$log" "/my-branch/"
  check "standalone exports PIPESHUB_REF as install ref" "$out" "PIPESHUB_INSTALL_REF=my-branch"
  if [[ "$log" == *"/v9.9.9/"* ]]; then fail "explicit ref must not fall back to release"; else pass "explicit ref overrides release tag"; fi
)

check "standalone names rate-limit fallback to main" "$(cat "$ROOT_INSTALLER")" "Falling back to main"

echo "== Root wrapper: standalone mode rejects local builds =="
(
  work="$TMP_ROOT/standalone-build"; mkdir -p "$work"
  cp "$ROOT_INSTALLER" "$work/install.sh"
  set +e
  out="$(cd "$work" && bash ./install.sh --build 2>&1)"
  ec=$?
  check "standalone --build names the clone path" "$out" "Clone the repository"
  if [[ "$ec" -ne 0 ]]; then pass "standalone --build exits non-zero"; else fail "standalone --build exits non-zero"; fi
  out="$(cd "$work" && PIPESHUB_IMAGE_SOURCE=local bash ./install.sh 2>&1)"
  ec=$?
  check "standalone local image source is rejected" "$out" "PIPESHUB_IMAGE_SOURCE=local"
  if [[ "$ec" -ne 0 ]]; then pass "standalone local image source exits non-zero"; else fail "standalone local image source exits non-zero"; fi
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
if grep -qE '^[[:space:]]*(clear\b|tput[[:space:]]+clear)' "$INNER_INSTALLER" "$ROOT_INSTALLER"; then
  fail "banner must not clear the screen"
else
  pass "banner must not clear the screen"
fi
check "banner prints wrapper git ref" "$inner" 'PIPESHUB_INSTALL_REF'
check "banner clone uses repo-root wrapper" "$inner" "From the repository root"
check "banner standalone warns curl does not cd" "$inner" "curl | bash does not cd your shell"
check "banner cds before ./install.sh" "$inner" 'cd %q'
check "banner cd is a preamble not bound to --stop" "$inner" 'cd %q\n\n'
check "banner collapses equal paths to Directory" "$inner" 'Directory:'
check "banner shows Files and Commands when they diverge" "$inner" 'Files:'
check "banner uses resolve_banner_dirs" "$inner" "resolve_banner_dirs"
check "profile repair on reuse present" "$inner" "Repairing to"
check "cross-directory guard present" "$inner" "Existing deployment detected"
check "separate-instance prompt present" "$inner" "Install a separate instance here"
check "--yes never auto-creates a second stack" "$inner" "never invents a second stack"
check "--yes without PIPESHUB_PROJECT manages existing" "$inner" "Non-interactive (--yes): managing the existing"
check "PIPESHUB_PROJECT is documented" "$inner" "PIPESHUB_PROJECT"
check "persists COMPOSE_PROJECT_NAME into .env" "$inner" 'COMPOSE_PROJECT_NAME=${PROJECT_NAME}'
check "validates resolved project name" "$inner" 'require_valid_project_name "$PROJECT_NAME"'
check "pinned-name migration heads-up present" "$inner" "warn_pinned_container_rename"
check "pinned-name heads-up mentions compose exec" "$inner" "Replace docker exec / docker logs of the old container name"
check "reuse-path port check present" "$inner" "is already in use by another process"
check "unset DATA_STORE defaults to neo4j" "$inner" "defaulting to Neo4j (no existing graph data found)"
check "unset DATA_STORE reuses arango volume" "$inner" "reusing the existing ArangoDB data volume"
check "unset DATA_STORE reuses neo4j volume" "$inner" "reusing the existing Neo4j data volume"
check "ambiguous both-volumes still errors" "$inner" "data volumes for BOTH graph"
check "lost graph password guidance" "$inner" "cannot be recovered"
check "summary graph DB fallback is honest" "$inner" '"${DATA_STORE:-(unset)}"'
check ".env locked to owner-only" "$inner" 'chmod 600 "$ENV_FILE"'
check ".env chmod failure is fatal" "$inner" "Could not restrict permissions"
check ".env chmod is guarded" "$inner" '&& ! chmod 600 "$ENV_FILE"; then'
check ".env chmod failure calls die" "$inner" 'die "Could not restrict permissions on $ENV_FILE"'
check ".env backup locked to owner-only" "$inner" 'chmod 600 "$_backup"'
check "crash-loop wait has 90s startup grace" "$inner" "ELAPSED >= 90"
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
echo "== Compose: Hub slim empty ints are unset before start =="
# Compose ${KEY:-} injects "". Hub slim int(os.getenv(KEY, default)) crashes
# on that. The app entrypoint must unset blanks so the key is absent.
compose="$(cat "$COMPOSE_DIR/docker-compose.yml")"
check "app entrypoint unsets blank Hub-int env" "$compose" 'printenv "$$k"'
_hub_int_keys=(
  MAX_CONCURRENT_PARSING
  MAX_CONCURRENT_INDEXING
  MAX_PENDING_INDEXING_TASKS
  EMBEDDING_SERVER_MAX_CONCURRENCY
  EMBEDDING_BATCH_CONCURRENCY
)
for _k in "${_hub_int_keys[@]}"; do
  check "entrypoint lists ${_k}" "$compose" "$_k"
  if grep -E "^[[:space:]]+- ${_k}=\\\$\\{${_k}:-[0-9]+\\}[[:space:]]*$" <<<"$compose" >/dev/null; then
    fail "${_k} must not pin a numeric Compose default (caps new images)"
  else
    pass "${_k} is not numeric-defaulted"
  fi
done
if [[ "$inner" == *$'\nMAX_CONCURRENT_PARSING=5\n'* ]]; then
  fail "installer must not pin MAX_CONCURRENT_PARSING=5"
else
  pass "installer does not pin parse concurrency 5"
fi
if [[ "$inner" == *$'\nMAX_CONCURRENT_INDEXING=7\n'* ]]; then
  fail "installer must not pin MAX_CONCURRENT_INDEXING=7"
else
  pass "installer does not pin index concurrency 7"
fi
if [[ "$inner" == *$'\nMAX_PENDING_INDEXING_TASKS=28\n'* ]]; then
  fail "installer must not pin MAX_PENDING_INDEXING_TASKS=28"
else
  pass "installer does not pin pending indexing tasks 28"
fi
if [[ "$inner" == *$'\nEMBEDDING_SERVER_MAX_CONCURRENCY=2\n'* ]]; then
  fail "installer must not pin EMBEDDING_SERVER_MAX_CONCURRENCY=2"
else
  pass "installer does not pin embedding concurrency 2"
fi
if [[ "$inner" == *$'\nEMBEDDING_BATCH_CONCURRENCY=5\n'* ]]; then
  fail "installer must not pin EMBEDDING_BATCH_CONCURRENCY=5"
else
  pass "installer does not pin embedding batch concurrency 5"
fi
if [[ "$inner" == *$'\nMAX_CONCURRENT_PARSING=\n'* ]]; then
  fail "installer must not write empty MAX_CONCURRENT_PARSING="
else
  pass "installer does not write empty MAX_CONCURRENT_PARSING="
fi
check "wizard port scan skips this project's own port" "$inner" 'port_in_use "$APP_PORT" 2>/dev/null && ! port_owned_by_project "$APP_PORT"'
check "reconfigure seeds port from existing .env" "$inner" 'get_existing_val APP_PORT "$DEFAULT_APP_PORT"'
if [[ "$compose" == *"container_name:"* ]]; then
  fail "compose must not pin container_name (blocks a second project)"
else
  pass "compose does not pin container_name"
fi
if [[ "$compose" == *"pipeshub-ai_network"* ]]; then
  fail "compose must not pin the network name (blocks a second project)"
else
  pass "compose does not pin the network name"
fi
# embedding may be absent or 'unhealthy' for minutes on first run; gating the app
# container on it leaves docker ps perpetually 'unhealthy' while the app works.
if [[ "$compose" == *"s.get('embedding') in ('healthy','starting')"* ]]; then
  fail "embedding must not gate app container health"
else
  pass "embedding excluded from app container health gate"
fi
# HuggingFace offline mode is optional. Defaulting it on breaks slim images,
# which download the embedding model on first use. Sparse BM25 loads cache-first
# in Python so air-gapped hosts do not hang.
if [[ "$compose" == *'HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-1}'* ]]; then
  fail "HF_HUB_OFFLINE must not default to 1 (breaks slim first-use download)"
else
  pass "HF_HUB_OFFLINE is not defaulted on"
fi
check "HF hub offline is overridable" "$compose" 'HF_HUB_OFFLINE=${HF_HUB_OFFLINE:-}'
check "transformers offline is overridable" "$compose" 'TRANSFORMERS_OFFLINE=${TRANSFORMERS_OFFLINE:-}'
# Guard against a non-overridable hard-coded offline flag.
if [[ "$compose" == *"HF_HUB_OFFLINE=1"$'\n'* || "$compose" == *"- HF_HUB_OFFLINE=1 "* ]]; then
  fail "HF_HUB_OFFLINE must be overridable, not hard-coded"
else
  pass "HF_HUB_OFFLINE is not hard-coded"
fi

echo "== env.template documents runtime robustness knobs =="
envtmpl="$(cat "$COMPOSE_DIR/env.template")"
check "env.template documents HF_HUB_OFFLINE" "$envtmpl" "HF_HUB_OFFLINE"
check "env.template documents TRANSFORMERS_OFFLINE" "$envtmpl" "TRANSFORMERS_OFFLINE"
check "env.template documents COMPOSE_PROJECT_NAME" "$envtmpl" "COMPOSE_PROJECT_NAME"
if [[ "$envtmpl" == *$'\nMAX_CONCURRENT_PARSING=5\n'* ]]; then
  fail "env.template must not pin MAX_CONCURRENT_PARSING=5"
else
  pass "env.template does not pin parse concurrency 5"
fi
if [[ "$envtmpl" == *$'\nMAX_CONCURRENT_INDEXING=7\n'* ]]; then
  fail "env.template must not pin MAX_CONCURRENT_INDEXING=7"
else
  pass "env.template does not pin index concurrency 7"
fi
if [[ "$envtmpl" == *$'\nMAX_PENDING_INDEXING_TASKS=28\n'* ]]; then
  fail "env.template must not pin MAX_PENDING_INDEXING_TASKS=28"
else
  pass "env.template does not pin pending indexing tasks 28"
fi

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
check "stop uses COMPOSE_PROJECT_NAME from .env" "$stop_block" 'PROJECT_NAME="$(resolve_project_name)"'
check "stop validates project name from .env" "$stop_block" 'require_valid_project_name "$PROJECT_NAME"'
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

echo "== In-tree installer: image refresh policy (real function + guards) =="
# `docker compose up -d` reuses a cached :latest without re-checking the registry,
# so a host can run a weeks-old build forever. The installer refreshes the app
# image by default, with deliberate opt-outs. Exercise the real decision fn.
eval "$(extract_fn should_pull_image "$INNER_INSTALLER")"
check "prebuilt default refreshes the image" "$(should_pull_image false false '')" "true"
check "local build never pulls" "$(should_pull_image true false '')" "false"
check "--no-pull skips the refresh" "$(should_pull_image false true '')" "false"
check "PIPESHUB_NO_PULL=1 skips the refresh" "$(should_pull_image false false 1)" "false"
check "PIPESHUB_NO_PULL=true skips the refresh" "$(should_pull_image false false true)" "false"
check "PIPESHUB_NO_PULL=yes skips the refresh" "$(should_pull_image false false yes)" "false"
# Pinning a tag still refreshes (fetch that exact tag). should_pull_image has no
# tag argument, so assert the launch path builds _APP_IMAGE from IMAGE_TAG.
check "refresh target honours the pinned tag" "$inner" '_APP_IMAGE="pipeshubai/pipeshub-ai:${IMAGE_TAG:-latest}"'
# Launch-path guards.
check "refreshes app and sandbox images" "$inner" "pull pipeshub-ai sandbox-image"
check "--no-pull flag is parsed" "$inner" "FLAG_NO_PULL=true"
check "refresh decision uses the testable helper" "$inner" 'should_pull_image "$_USE_BUILD" "$FLAG_NO_PULL" "${PIPESHUB_NO_PULL:-}"'
# A pull failure must NOT abort when an image is already cached (flaky network).
check "pull failure tolerated when image cached" "$inner" "continuing with cached"
check "pull fallback inspects sandbox image" "$inner" 'docker image inspect "$_SANDBOX_IMAGE"'
check "air-gapped guidance present" "$inner" "air-gapped host, preload the image"
# Must not have reverted to a blanket pull of every service image on the hot path.
if [[ "$inner" == *"up -d --pull always"* ]]; then fail "must refresh only the app image, not force-pull all services"; else pass "does not force-pull all service images"; fi

echo "== In-tree installer: container outbound connectivity (warn-only) =="
check "defines outbound probe helper" "$inner" "container_has_outbound_internet()"
check "defines docker iptables hint" "$inner" "docker_iptables_disabled()"
check "warn helper present" "$inner" "warn_container_outbound_connectivity"
check "warn mentions air-gapped/local models" "$inner" "air-gapped installs are supported"
check "warn mentions iptables false" "$inner" 'iptables\": false'
check "warn links outbound docs" "$inner" "container-outbound-connectivity"
if [[ "$inner" == *"warn_container_outbound_connectivity"* ]] && ! [[ "$inner" == *"warn_container_outbound_connectivity || die"* ]]; then
  pass "outbound check does not hard-fail install"
else
  fail "outbound check does not hard-fail install"
fi
if [[ "$inner" == *"docker exec pipeshub-ai"* ]]; then
  fail "must not docker exec a pinned container name"
else
  pass "does not docker exec a pinned container name"
fi

echo "== In-tree installer: Compose project name helpers (real functions) =="
eval "$(extract_fn get_existing_val "$INNER_INSTALLER")"
eval "$(extract_fn resolve_project_name "$INNER_INSTALLER")"
eval "$(extract_fn valid_compose_project_name "$INNER_INSTALLER")"
eval "$(extract_fn sanitize_compose_project_name "$INNER_INSTALLER")"
eval "$(extract_fn suggest_separate_project_name "$INNER_INSTALLER")"

valid_compose_project_name "pipeshub-2" && pass "accepts pipeshub-2" || fail "accepts pipeshub-2"
valid_compose_project_name "ab_c" && pass "accepts underscore" || fail "accepts underscore"
if valid_compose_project_name "-bad"; then fail "rejects leading hyphen"; else pass "rejects leading hyphen"; fi
if valid_compose_project_name "BadName"; then fail "rejects uppercase"; else pass "rejects uppercase"; fi
check "sanitizes mixed case directory" "$(sanitize_compose_project_name "My Repo!")" "my-repo"
check "sanitizes leading underscore" "$(sanitize_compose_project_name "_work")" "work"
check "sanitizes leading hyphen" "$(sanitize_compose_project_name "-lead")" "lead"
if valid_compose_project_name "_work"; then fail "raw _work is invalid"; else pass "raw _work is invalid"; fi

(
  unset PIPESHUB_PROJECT
  ENV_FILE="/no/such/.env"
  DEFAULT_PROJECT="pipeshub-ai"
  check "resolve defaults to pipeshub-ai" "$(resolve_project_name)" "pipeshub-ai"
)
(
  PIPESHUB_PROJECT="my-copy"
  ENV_FILE="$TMP_ROOT/env_proj"
  printf 'COMPOSE_PROJECT_NAME=from-env\n' >"$ENV_FILE"
  DEFAULT_PROJECT="pipeshub-ai"
  check "PIPESHUB_PROJECT wins over .env" "$(resolve_project_name)" "my-copy"
)
(
  unset PIPESHUB_PROJECT
  ENV_FILE="$TMP_ROOT/env_proj2"
  printf 'COMPOSE_PROJECT_NAME=from-env\n' >"$ENV_FILE"
  DEFAULT_PROJECT="pipeshub-ai"
  check "COMPOSE_PROJECT_NAME from .env" "$(resolve_project_name)" "from-env"
)
(
  DEFAULT_PROJECT="pipeshub-ai"
  SCRIPT_DIR="/tmp/pipeshub-pr2634"
  check "suggests directory basename" "$(suggest_separate_project_name)" "pipeshub-pr2634"
)
(
  DEFAULT_PROJECT="pipeshub-ai"
  SCRIPT_DIR="/tmp/docker-compose"
  check "docker-compose dir falls back to pipeshub-2" "$(suggest_separate_project_name)" "pipeshub-2"
)
(
  repo="$TMP_ROOT/my-repo"
  mkdir -p "$repo/deployment/docker-compose"
  touch "$repo/Dockerfile"
  DEFAULT_PROJECT="pipeshub-ai"
  SCRIPT_DIR="$repo/deployment/docker-compose"
  check "suggests repo root when run from compose dir" "$(suggest_separate_project_name)" "my-repo"
)

echo "== In-tree installer: success-banner directories (real function) =="
eval "$(extract_fn resolve_banner_dirs "$INNER_INSTALLER")"
(
  set -euo pipefail
  repo="$TMP_ROOT/banner-clone"
  mkdir -p "$repo/deployment/docker-compose"
  touch "$repo/Dockerfile"
  cp "$ROOT_INSTALLER" "$repo/install.sh"
  SCRIPT_DIR="$(cd "$repo/deployment/docker-compose" && pwd)"
  resolve_banner_dirs
  check "clone commands use repo root" "$BANNER_CLI_DIR" "$(cd "$repo" && pwd)"
  if $BANNER_IN_CLONE; then pass "clone is detected"; else fail "clone is detected"; fi
)
(
  set -euo pipefail
  stand="$TMP_ROOT/banner-stand/pipeshub"
  mkdir -p "$stand"
  SCRIPT_DIR="$(cd "$stand" && pwd)"
  resolve_banner_dirs
  check "plain standalone stays in SCRIPT_DIR" "$BANNER_CLI_DIR" "$SCRIPT_DIR"
  if $BANNER_IN_CLONE; then fail "plain standalone is not a clone"; else pass "plain standalone is not a clone"; fi
)
(
  set -euo pipefail
  repo="$TMP_ROOT/banner-nested"
  mkdir -p "$repo/deployment/docker-compose" "$repo/deployment/pipeshub"
  touch "$repo/Dockerfile"
  cp "$ROOT_INSTALLER" "$repo/install.sh"
  SCRIPT_DIR="$(cd "$repo/deployment/pipeshub" && pwd)"
  resolve_banner_dirs
  check "standalone nested in a clone stays SCRIPT_DIR" "$BANNER_CLI_DIR" "$SCRIPT_DIR"
  if $BANNER_IN_CLONE; then fail "nested standalone is not a clone"; else pass "nested standalone is not a clone"; fi
)
(
  set -euo pipefail
  svc="$TMP_ROOT/myservice"
  mkdir -p "$svc/deploy/pipeshub"
  touch "$svc/Dockerfile" "$svc/install.sh"
  SCRIPT_DIR="$(cd "$svc/deploy/pipeshub" && pwd)"
  resolve_banner_dirs
  printf 'survived\n' >"$TMP_ROOT/banner-landmine"
  check "false clone guess stays SCRIPT_DIR" "$BANNER_CLI_DIR" "$SCRIPT_DIR"
  if $BANNER_IN_CLONE; then fail "user Dockerfile is not a pipeshub clone"; else pass "user Dockerfile is not a pipeshub clone"; fi
)
if [[ -f "$TMP_ROOT/banner-landmine" ]]; then
  pass "missing compose dir does not abort under set -e"
else
  fail "missing compose dir does not abort under set -e"
fi
(
  set -euo pipefail
  other="$TMP_ROOT/other-app"
  mkdir -p "$other/deployment/docker-compose"
  touch "$other/Dockerfile"
  printf '%s\n' '#!/bin/sh' 'echo my-app' >"$other/install.sh"
  SCRIPT_DIR="$(cd "$other/deployment/docker-compose" && pwd)"
  resolve_banner_dirs
  check "unrelated compose-layout app stays SCRIPT_DIR" "$BANNER_CLI_DIR" "$SCRIPT_DIR"
  if $BANNER_IN_CLONE; then fail "unrelated root install.sh is not a pipeshub clone"; else pass "unrelated root install.sh is not a pipeshub clone"; fi
)

eval "$(extract_fn project_has_pinned_container_names "$INNER_INSTALLER")"
(
  PROJECT_NAME="pipeshub-ai"
  docker() { printf '%s\n' "pipeshub-ai" "mongodb"; }
  if project_has_pinned_container_names; then pass "detects pinned container names"; else fail "detects pinned container names"; fi
)
(
  PROJECT_NAME="pipeshub-ai"
  docker() { printf '%s\n' "pipeshub-ai-pipeshub-ai-1" "pipeshub-ai-mongodb-1"; }
  if project_has_pinned_container_names; then fail "ignores Compose default names"; else pass "ignores Compose default names"; fi
)
(
  PROJECT_NAME="pipeshub-ai"
  docker() { :; }
  if project_has_pinned_container_names; then fail "silent when no containers"; else pass "silent when no containers"; fi
)

echo
PASS="$(wc -l <"$PASS_FILE" | tr -d ' ')"
FAIL="$(wc -l <"$FAIL_FILE" | tr -d ' ')"
printf "Results: %s passed, %s failed\n" "$PASS" "$FAIL"
if [[ "$FAIL" -ne 0 ]]; then
  echo "Failed checks:"; sed 's/^/  - /' "$FAIL_FILE"
fi
[[ "$FAIL" -eq 0 ]]
