#!/usr/bin/env bash
# ==============================================================================
# Tests for the PipesHub installers — pure bash, no Docker, no network.
# ==============================================================================
# Covers:
#   - Syntax validity of both installer scripts (bash -n).
#   - Syntax validity of bootstrap-first-run.sh (first-run; writes PAT to a file).
#   - Root wrapper repo mode: delegates to the in-tree installer with args.
#   - Root wrapper standalone mode: downloads files (via a stubbed curl) into
#     PIPESHUB_DIR and execs the downloaded installer with args.
#   - Standalone PIPESHUB_REF resolution: explicit ref, latest-release tag, and
#     the main fallback all hit the correct download URLs.
#   - Regression guards on the in-tree installer edits (16 GB-class RAM floor,
#     host-side reachability check, health-gated "ready" banner, clone vs
#     standalone command directory, TTY-aware compose progress, generous/overridable
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
BOOTSTRAP="$COMPOSE_DIR/bootstrap-first-run.sh"

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

# Exact comparison: `check` matches a substring, which cannot show that something was NOT added to a value.
check_exact() { # check_exact "desc" actual expected
  if [[ "$2" == "$3" ]]; then pass "$1"; else
    fail "$1"; printf "         expected exactly: %s\n         got: %s\n" "$3" "$2"; fi
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
if bash -n "$BOOTSTRAP" 2>/dev/null; then pass "bootstrap-first-run.sh parses"; else fail "bootstrap-first-run.sh parses"; fi

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
check "summary graph DB fallback is honest" "$inner" '${DATA_STORE:-(unset)}}"'
check ".env locked to owner-only" "$inner" 'chmod 600 "$ENV_FILE"'
check ".env chmod failure is fatal" "$inner" "Could not restrict permissions"
check ".env chmod is guarded" "$inner" '&& ! chmod 600 "$ENV_FILE"; then'
check ".env chmod failure calls die" "$inner" 'die "Could not restrict permissions on $ENV_FILE"'
check ".env backup locked to owner-only" "$inner" 'chmod 600 "$_backup"'
check "crash-loop wait has 90s startup grace" "$inner" "ELAPSED >= 90"
# Compose progress and the health spinner share one _is_tty. The mapping
# (true→tty, false→plain) is exercised via extract_fn below — grepping for
# both strings would stay green if the branches were swapped.
check "progress flag applied to compose up/pull" "$inner" 'docker compose "${_PROGRESS[@]}"'
check "progress decision uses the testable helper" "$inner" 'resolve_compose_progress "$_is_tty"'
check "one TTY flag for progress and health spinner" "$inner" '_is_tty=false; [[ -t 1 ]] && _is_tty=true'
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
check "eval is a deploy type" "$inner" 'prompt_choice DEPLOY_TYPE "Deployment type?" "slim" "slim" "full" "eval"'
check "eval forces Neo4j even if an Arango volume exists" "$inner" 'Eval requires Neo4j'
# Must not revert to asserting OOM as the definitive cause.
if [[ "$inner" == *"almost always host memory pressure"* ]]; then
  fail "crash-loop message must not assert OOM as the certain cause"
else
  pass "crash-loop message does not over-assert OOM"
fi

echo "== Compose: app healthcheck reconciled with installer =="
compose="$(cat "$COMPOSE_DIR/docker-compose.yml")"
check "app healthcheck gates on core services" "$compose" "required=('query','connector','indexing','docling')"
check "sandbox image is profile-gated" "$compose" 'profiles: ["sandbox"]'
check "eval skip slackbot env is wired" "$compose" "PIPESHUB_SKIP_SLACKBOT"
echo "== Installer: standalone MongoDB converts before the stack starts =="
# The conversion itself runs against Docker in mongodb_replset_test.sh.
_migrate_line="$(grep -n '^migrate_mongo_to_replset$' "$INNER_INSTALLER" | head -1 | cut -d: -f1)"
_start_line="$(grep -n 'compose_up_with_mongo_heal "up"' "$INNER_INSTALLER" | tail -1 | cut -d: -f1)"
if [[ -n "$_migrate_line" && -n "$_start_line" && "$_migrate_line" -lt "$_start_line" ]]; then
  pass "installer converts MongoDB before compose up"
else
  fail "installer converts MongoDB before compose up"
fi
check "base compose runs MongoDB as replica set rs0" "$compose" '- --replSet'
check "base compose app uses the replica-set URI" "$compose" 'replicaSet=rs0'
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
check "reconfigure keeps the private-address switch" "$inner" 'get_existing_val PIPESHUB_BLOCK_PRIVATE_ADDRESSES'
check "the private-address switch is written to .env" "$inner" 'optional_env_line PIPESHUB_BLOCK_PRIVATE_ADDRESSES'
check "reconfigure keeps Qdrant's memory limit" "$inner" 'get_existing_val QDRANT_MEMORY_LIMIT'
# install.sh supports bash 3.2 (macOS); case-conversion expansions need bash 4.
if grep -vE '^[[:space:]]*#' "$INNER_INSTALLER" | grep -E '\$\{[A-Za-z_0-9]+(,,?|\^\^?)[^}]*\}' >/dev/null; then
  fail "installer uses no bash-4 case conversion (\${v,,} / \${v^^})"
else
  pass "installer uses no bash-4 case conversion"
fi
check "Qdrant's memory limit is written to .env" "$inner" 'optional_env_line QDRANT_MEMORY_LIMIT'
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

echo "== OAuth device / DCR launch defaults =="
envtmpl="$(cat "$COMPOSE_DIR/env.template")"
compose="$(cat "$COMPOSE_DIR/docker-compose.yml")"
check "env.template documents PIPESHUB_ENABLE_DCR" "$envtmpl" "PIPESHUB_ENABLE_DCR"
check "env.template documents PIPESHUB_ENABLE_DEVICE_GRANT" "$envtmpl" "PIPESHUB_ENABLE_DEVICE_GRANT"
check "env.template names pipeshub_device_client_id" "$envtmpl" "pipeshub_device_client_id"
if grep -qE '^PIPESHUB_ENABLE_DCR=false' "$COMPOSE_DIR/env.template"; then
  fail "env.template must not disable DCR by default"
else
  pass "env.template does not disable DCR by default"
fi
if grep -qE '^PIPESHUB_ENABLE_DEVICE_GRANT=false' "$COMPOSE_DIR/env.template"; then
  fail "env.template must not disable device grant by default"
else
  pass "env.template does not disable device grant by default"
fi
check "compose wires PIPESHUB_ENABLE_DCR" "$compose" 'PIPESHUB_ENABLE_DCR=${PIPESHUB_ENABLE_DCR:-}'
check "compose wires PIPESHUB_ENABLE_DEVICE_GRANT" "$compose" 'PIPESHUB_ENABLE_DEVICE_GRANT=${PIPESHUB_ENABLE_DEVICE_GRANT:-}'

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
check "stop enables all profiles" "$stop_block" 'COMPOSE_PROFILES="graph-arango,graph-neo4j,kv-etcd,broker-kafka,sandbox,postgres,postgres-migrate,postgres-metrics"'
check "stop removes orphans" "$stop_block" "down --remove-orphans"
check "stop uses COMPOSE_PROJECT_NAME from .env" "$stop_block" 'PROJECT_NAME="$(resolve_project_name)"'
check "stop validates project name from .env" "$stop_block" 'require_valid_project_name "$PROJECT_NAME"'
uninstall_block="$(awk '/if \$FLAG_UNINSTALL; then/{g=1} g{print} g&&/^fi/{exit}' "$INNER_INSTALLER")"
check "uninstall removes orphans" "$uninstall_block" "down -v --remove-orphans"

echo "== In-tree installer: --rotate-signing-secrets =="
check "rotate flag is parsed" "$inner" "FLAG_ROTATE_SIGNING_SECRETS=true"
check "rotate flag is in usage" "$inner" "--rotate-signing-secrets"
check "rotate confirms with ROTATE" "$inner" "Type ROTATE to confirm"
check "rotate force-recreates app container" "$inner" "--force-recreate --no-deps pipeshub-ai"
check "rotate rejected with --stop" "$inner" "cannot be combined with --stop"
check "rotate rejected with --uninstall" "$inner" "cannot be combined with --uninstall"
check "rotate requires existing .env" "$inner" "Signing-secret rotation requires an existing install"
check "rotate writes one-shot id" "$inner" "persist_env_var ROTATE_SIGNING_SECRETS"
check "rotate without upgrade skips image pull" "$inner" "rotating signing secrets only"
compose_yml="$(cat "$COMPOSE_DIR/docker-compose.yml")"
check "compose passes ROTATE_SIGNING_SECRETS" "$compose_yml" "ROTATE_SIGNING_SECRETS=\${ROTATE_SIGNING_SECRETS:-}"

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

echo "== In-tree installer: default broker per deploy type (real code) =="
# Run the installer's own default-selection block (case + volume overrides).
_bsnip="$(awk '/^  case "\$DEPLOY_TYPE" in/{g=1} /if \$DETECTED_ETCD; then/{g=0} g' "$INNER_INSTALLER")"
default_broker_for() ( # <deploy type> <kafka volume detected: true|false>
  info() { :; }
  DEPLOY_TYPE="$1"; DETECTED_KAFKA="$2"; DETECTED_ARANGO=false; DETECTED_NEO4J=false
  eval "$_bsnip"; echo "$DEFAULT_BROKER"
)
check "full, no volumes -> redis" "$(default_broker_for full false)" "redis"
check "full, existing Kafka volume -> kafka" "$(default_broker_for full true)" "kafka"
check "slim, no volumes -> redis" "$(default_broker_for slim false)" "redis"
check "explicit PIPESHUB_BROKER still read" "$(grep -c 'BROKER="${PIPESHUB_BROKER:-}"' "$INNER_INSTALLER")" "1"
check "PIPESHUB_BROKER=kafka beats the default" "$(PIPESHUB_BROKER=kafka; BROKER="${PIPESHUB_BROKER:-}"; DEFAULT_BROKER=redis; echo "${BROKER:-$DEFAULT_BROKER}")" "kafka"

echo "== In-tree installer: volume_exists (real function) =="
eval "$(extract_fn volume_exists "$INNER_INSTALLER")"
(
  docker() { [ "$1 $2" = "volume inspect" ] && [ "$3" = "pipeshub-ai_kafka_data" ]; }   # only that volume exists
  if volume_exists pipeshub-ai_kafka_data; then pass "finds an existing volume by exact name"; else fail "finds an existing volume by exact name"; fi
  if volume_exists pipeshub-ai_kafka; then fail "a name prefix is not a match"; else pass "a name prefix is not a match"; fi
  if volume_exists pipeshub-ai_etcd_data; then fail "an absent volume reads as absent"; else pass "an absent volume reads as absent"; fi
)

echo "== In-tree installer: COMPOSE_PROFILES derivation (real functions) =="
# Pull the real function definitions out of the installer and exercise them in
# isolation. Both are defined at top level with the closing brace in column 0.
eval "$(extract_fn derive_compose_profiles "$INNER_INSTALLER")"
eval "$(extract_fn opt_in_profiles "$INNER_INSTALLER")"
_saved_env_val() { :; }
eval "$(extract_fn persist_env_var "$INNER_INSTALLER")"

# The sandbox profile pairs with SANDBOX_MODE (broker, or docker while it
# migrates); the plan step sets it before this runs (sandbox_plane_test.sh).
dp() { DATA_STORE="$1" KV_STORE_TYPE="$2" MESSAGE_BROKER="$3" SANDBOX_MODE="${4-broker}" derive_compose_profiles; }
check "arango + kafka + redis kv" "$(dp arangodb redis kafka)" "graph-arango,broker-kafka,sandbox"
check "neo4j + redis + redis (slim)" "$(dp neo4j redis redis)" "graph-neo4j,sandbox"
check "eval omits sandbox" "$(DEPLOY_TYPE=eval dp neo4j redis redis '')" "graph-neo4j"
check "neo4j + etcd + kafka (full custom)" "$(dp neo4j etcd kafka)" "graph-neo4j,kv-etcd,broker-kafka,sandbox"
[[ -z "$(DEPLOY_TYPE=eval dp '' '' '' '')" ]] && pass "eval all-unset derives empty" || fail "eval all-unset derives empty"
check "all-unset still includes sandbox" "$(dp '' '' '')" "sandbox"
# The exact stale value from the user's terminal must be corrected, not trusted.
check "repairs stale 'kafka' to real profiles" "$(dp arangodb redis kafka)" "graph-arango,broker-kafka,sandbox"
# Missing DATA_STORE drops the graph profile (only broker-kafka) — this is why
# the installer hard-validates DATA_STORE before launch.
check "missing DATA_STORE yields no graph profile" "$(dp '' redis kafka)" "broker-kafka,sandbox"
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

echo "== In-tree installer: the opt-in postgres profiles =="
# SANDBOX_MODE=docker without the harness (the compatibility default, R51-COMPAT): broker mode, or the harness on in
# docker mode, would add the sandbox-broker profile too.
check "keeps postgres in COMPOSE_PROFILES" "$(COMPOSE_PROFILES=graph-neo4j,postgres,postgres-migrate AGENT_HARNESS_WORKER_ENABLED=false dp neo4j redis redis docker)" "graph-neo4j,sandbox,postgres,postgres-migrate"
check_exact "does not invent postgres" "$(AGENT_HARNESS_WORKER_ENABLED=false dp neo4j redis redis docker)" "graph-neo4j,sandbox"
check_exact "does not match a longer profile name" "$(COMPOSE_PROFILES=postgres-extra AGENT_HARNESS_WORKER_ENABLED=false dp neo4j redis redis docker)" "graph-neo4j,sandbox"
check_exact "keeps exactly the two opt-in profiles and nothing else" "$(COMPOSE_PROFILES=graph-neo4j,postgres,postgres-migrate AGENT_HARNESS_WORKER_ENABLED=false dp neo4j redis redis docker)" "graph-neo4j,sandbox,postgres,postgres-migrate"
# the exact check does fail on a wrongly appended profile (the substring check did not)
[[ "graph-neo4j,sandbox,postgres" != "graph-neo4j,sandbox" ]] && pass "an appended postgres is caught by the exact comparison" || fail "an appended postgres is caught by the exact comparison"
check "uninstall also covers the postgres profiles" "$(awk '/if \$FLAG_UNINSTALL; then/{g=1} g{print} g&&/^fi/{exit}' "$INNER_INSTALLER")" 'sandbox,postgres,postgres-migrate,postgres-metrics"'
check_exact "keeps the metrics profile when it is set" "$(COMPOSE_PROFILES=graph-neo4j,postgres,postgres-metrics AGENT_HARNESS_WORKER_ENABLED=false dp neo4j redis redis docker)" "graph-neo4j,sandbox,postgres,postgres-metrics"
check_exact "the document-store override does not add the metrics profile" "$(PIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-docstore.yml AGENT_HARNESS_WORKER_ENABLED=false dp neo4j redis redis docker)" "graph-neo4j,sandbox,postgres,postgres-migrate"
check "uninstall asks before deleting the PostgreSQL volume" "$(awk '/if \$FLAG_UNINSTALL; then/{g=1} g{print} g&&/^fi/{exit}' "$INNER_INSTALLER")" "Also delete the PostgreSQL data volume?"
# The .env the wizard writes carries one random password per login; an existing value is kept across --reconfigure.
wizard="$(awk '/PostgreSQL profile \(opt-in\): kept across/{g=1} g{print} g&&/^  done/{exit}' "$INNER_INSTALLER")"
(
  get_existing_val() { case "$1" in PG_NODE_PASSWORD) echo kept;; *) echo "${2:-}";; esac; }
  gen_secret() { printf 'x%.0s' $(seq 1 "${1:-32}"); }
  eval "$wizard"
  check "wizard keeps an existing PG password" "$PG_NODE_PASSWORD" "kept"
  check "wizard generates the others" "$PG_BACKUP_PASSWORD:$PG_ADMIN_PASSWORD:$PG_MONITOR_PASSWORD" "xxxxxxxxxxxxxxxx:xxxxxxxxxxxxxxxx:xxxxxxxxxxxxxxxx"
  check "wizard pins the major" "$PG_MAJOR" "18"
)
# The metrics login was set by hand before the installer wrote it, so an exported value is the one its role may hold already.
(
  get_existing_val() { echo "${2:-}"; }
  gen_secret() { printf 'x%.0s' $(seq 1 "${1:-32}"); }
  PG_MONITOR_EXPORTED=exportedmonitor PG_NODE_PASSWORD=exportednode
  eval "$wizard"
  check_exact "wizard takes an exported monitoring password" "$PG_MONITOR_PASSWORD" "exportedmonitor"
  check_exact "wizard still generates the other PG passwords" "$PG_NODE_PASSWORD" "xxxxxxxxxxxxxxxx"
)
check ".env writer lists all ten PG passwords" "$(grep -cE "^PG_[A-Z]*_PASSWORD='?\\\$\{PG_" "$INNER_INSTALLER")" "10"

# --reconfigure rebuilds COMPOSE_PROFILES from the wizard's choices; the saved override needs the PostgreSQL profiles, so they must
# survive it, and the project must still render afterwards.
(
  work="$TMP_ROOT/reconfigure-pg"; mkdir -p "$work"
  cp "$INNER_INSTALLER" "$work/install.sh"
  cp "$COMPOSE_DIR/docker-compose.yml" "$COMPOSE_DIR/docker-compose.postgres-docstore.yml" "$COMPOSE_DIR/env.template" "$work/"
  mkdir -p "$work/postgres"
  printf 'IMAGE_TAG=0.6.0-slim\nSECRET_KEY=abc\nMONGO_PASSWORD=m\nREDIS_PASSWORD=r\nNEO4J_PASSWORD=n\nAPP_PORT=3000\nPROJECT_NAME=testproj\nDATA_STORE=neo4j\nCOMPOSE_PROFILES=graph-neo4j,sandbox,postgres,postgres-migrate\nPIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-docstore.yml\nPG_NODE_PASSWORD=keepnode\n' >"$work/.env"
  out="$(cd "$work" && bash ./install.sh --reconfigure --yes --print-env-only 2>&1)" && pass "--reconfigure with the override exits 0" || fail "--reconfigure with the override exits 0"
  profiles="$(grep '^COMPOSE_PROFILES=' "$work/.env" | cut -d= -f2-)"
  check "--reconfigure keeps both PostgreSQL profiles" ",$profiles," ",postgres,postgres-migrate,"
  check "--reconfigure keeps the override" "$(cat "$work/.env")" "PIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-docstore.yml"
  check "--reconfigure keeps an existing PG password" "$(cat "$work/.env")" "PG_NODE_PASSWORD=keepnode"
  check "the printed command lists the override" "$out" "docker-compose.postgres-docstore.yml"
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    (cd "$work" && docker compose --env-file .env -f docker-compose.yml -f docker-compose.postgres-docstore.yml config -q 2>&1) \
      && pass "the project renders after --reconfigure" || fail "the project renders after --reconfigure"
  else
    pass "the project renders after --reconfigure (skipped: no docker compose here)"
  fi
  # An override with no profiles saved at all still gets both (a hand-written .env).
  printf 'IMAGE_TAG=0.6.0-slim\nSECRET_KEY=abc\nAPP_PORT=3000\nPROJECT_NAME=testproj\nDATA_STORE=neo4j\nCOMPOSE_PROFILES=graph-neo4j\nPIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-docstore.yml\n' >"$work/.env"
  (cd "$work" && bash ./install.sh --reconfigure --yes --print-env-only >/dev/null 2>&1)
  check "the override alone brings both profiles" ",$(grep '^COMPOSE_PROFILES=' "$work/.env" | cut -d= -f2-)," ",postgres,postgres-migrate,"
)

# docker-compose.postgres-only.yml as the override: the installer writes every PostgreSQL password, the project renders with no other
# store, and the summary names PostgreSQL rather than the graph, KV and broker selections the override replaces.
(
  work="$TMP_ROOT/pg-only"; mkdir -p "$work"
  cp "$INNER_INSTALLER" "$work/install.sh"
  cp "$COMPOSE_DIR/docker-compose.yml" "$COMPOSE_DIR/docker-compose.postgres-only.yml" "$COMPOSE_DIR/env.template" "$work/"
  mkdir -p "$work/postgres"
  printf 'IMAGE_TAG=0.6.0-slim\nSECRET_KEY=abc\nAPP_PORT=3000\nPROJECT_NAME=testproj\nDATA_STORE=neo4j\nCOMPOSE_PROFILES=graph-neo4j,sandbox\nPIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-only.yml\n' >"$work/.env"
  out="$(cd "$work" && bash ./install.sh --reconfigure --yes --print-env-only 2>&1)" && pass "--reconfigure with the PostgreSQL-only override exits 0" || fail "--reconfigure with the PostgreSQL-only override exits 0"
  summary="$(printf '%s\n' "$out" | grep -E '^  (Graph DB|KV store|Broker):' | tr -s ' ')"
  check_exact "the summary names PostgreSQL for every store" "$summary" "$(printf ' Graph DB: postgres (docker-compose.postgres-only.yml)\n KV store: postgres (docker-compose.postgres-only.yml)\n Broker: postgres (docker-compose.postgres-only.yml)')"
  check "the printed command lists the PostgreSQL-only override" "$out" "docker-compose.postgres-only.yml"
  check ".env has all ten PG passwords" "$(grep -cE '^PG_(ADMIN|NODE|CONNECTORS|INDEXING|QUERY|JOBS|MIGRATE|BACKUP|KV|MONITOR)_PASSWORD=.+' "$work/.env")" "10"
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    services="$(cd "$work" && docker compose --env-file .env -f docker-compose.yml -f docker-compose.postgres-only.yml config --services 2>&1)"
    check_exact "the project renders with no other store" "$(printf '%s\n' "$services" | grep -cE '^(mongodb|qdrant|redis|neo4j|arango|etcd|zookeeper|kafka-1)$')" "0"
    check "the project renders the PostgreSQL stack" "$services" "pipeshub-placement"
  else
    pass "the project renders with no other store (skipped: no docker compose here)"
  fi
)

# A reused .env from before the metrics login gets PG_MONITOR_PASSWORD once (the exporters cannot log in without it); later runs and
# a value set by hand keep theirs.
(
  work="$TMP_ROOT/monitor-backfill"; mkdir -p "$work"
  cp "$INNER_INSTALLER" "$work/install.sh"
  cp "$COMPOSE_DIR/docker-compose.yml" "$COMPOSE_DIR/env.template" "$work/"
  base='IMAGE_TAG=0.6.0-slim\nSECRET_KEY=abc\nMONGO_PASSWORD=m\nREDIS_PASSWORD=r\nNEO4J_PASSWORD=n\nAPP_PORT=3000\nPROJECT_NAME=testproj\nDATA_STORE=neo4j\nCOMPOSE_PROFILES=graph-neo4j,sandbox\n'
  # The value as the installer reads it back (it sources .env with bash) and as Compose reads it; both must be the password.
  bash_reads() { (unset PG_MONITOR_PASSWORD; set -a; . "$work/.env"; printf '%s' "${PG_MONITOR_PASSWORD-}"); }
  printf 'services:\n  x:\n    image: busybox\n    environment:\n      - P=${PG_MONITOR_PASSWORD}\n' >"$work/reads.yml"
  compose_reads() {
    (cd "$work" && env -u PG_MONITOR_PASSWORD docker compose -f reads.yml --env-file .env config 2>/dev/null) \
      | sed -n 's/^ *P: //p' | sed -E 's/^"(.*)"$/\1/'
  }
  have_compose=false; command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1 && have_compose=true
  printf "$base" >"$work/.env"
  (cd "$work" && bash ./install.sh --yes --print-env-only >/dev/null 2>&1) && pass "reusing an old .env exits 0" || fail "reusing an old .env exits 0"
  first="$(grep '^PG_MONITOR_PASSWORD=' "$work/.env")"
  check_exact "a reused .env gets one monitoring password line" "$(grep -c '^PG_MONITOR_PASSWORD=' "$work/.env")" "1"
  [[ "$(bash_reads)" =~ ^[0-9a-f]{32}$ ]] && pass "a reused .env gets a 32-character monitoring password" || fail "a reused .env gets a 32-character monitoring password"
  (cd "$work" && bash ./install.sh --yes --print-env-only >/dev/null 2>&1)
  check_exact "the next run keeps it" "$(grep '^PG_MONITOR_PASSWORD=' "$work/.env")" "$first"
  printf "${base}PG_MONITOR_PASSWORD=mine\n" >"$work/.env"
  (cd "$work" && bash ./install.sh --yes --print-env-only >/dev/null 2>&1)
  check_exact "a value set by hand is kept" "$(bash_reads)" "mine"
  (cd "$work" && PG_MONITOR_PASSWORD=exportedmonitor bash ./install.sh --yes --print-env-only >/dev/null 2>&1)
  check_exact "a value in .env wins over an exported one" "$(bash_reads)" "mine"
  printf "$base" >"$work/.env"
  (cd "$work" && PG_MONITOR_PASSWORD=exportedmonitor bash ./install.sh --yes --print-env-only >/dev/null 2>&1)
  check_exact "a reused .env takes the exported monitoring password" "$(bash_reads)" "exportedmonitor"
  printf "$base" >"$work/.env"
  (cd "$work" && PG_MONITOR_PASSWORD=exportedmonitor bash ./install.sh --reconfigure --yes --print-env-only >/dev/null 2>&1)
  check_exact "--reconfigure takes the exported monitoring password" "$(bash_reads)" "exportedmonitor"
  # An empty PG_MONITOR_PASSWORD= line is no saved value: sourcing .env must not drop the exported one.
  for mode in "" --upgrade; do
    printf "${base}PG_MONITOR_PASSWORD=\n" >"$work/.env"
    (cd "$work" && PG_MONITOR_PASSWORD=exportedmonitor bash ./install.sh $mode --yes --print-env-only >/dev/null 2>&1)
    check_exact "an empty saved line takes the exported password ${mode:-on reuse}" "$(bash_reads)" "exportedmonitor"
    check_exact "an empty saved line leaves one password line ${mode:-on reuse}" "$(grep -c '^PG_MONITOR_PASSWORD=' "$work/.env")" "1"
  done
  # A leading ~ is allowed by the server rule; an unquoted ~ in .env would be read by bash as the home directory.
  for mode in "" --upgrade --reconfigure; do
    printf "$base" >"$work/.env"
    (cd "$work" && PG_MONITOR_PASSWORD='~' bash ./install.sh $mode --yes --print-env-only >/dev/null 2>&1)
    check_exact "an exported ~ password is read back as ~ ${mode:-on reuse}" "$(bash_reads)" "~"
    if $have_compose; then
      check_exact "Compose reads the same ~ password ${mode:-on reuse}" "$(compose_reads)" "~"
    else
      pass "Compose reads the same ~ password ${mode:-on reuse} (skipped: no docker compose here)"
    fi
  done
  (cd "$work" && bash ./install.sh --reconfigure --yes --print-env-only >/dev/null 2>&1)
  check_exact "a saved quoted password survives --reconfigure" "$(bash_reads)" "~"
  check_exact "a saved quoted password is not quoted twice" "$(grep -c "^PG_MONITOR_PASSWORD='~'$" "$work/.env")" "1"
  # A value the server would refuse at start is refused here first, before .env changes, and never echoed.
  for mode in "" --reconfigure; do
    printf "$base" >"$work/.env"; cp "$work/.env" "$work/env.before"
    status=0
    out="$(cd "$work" && PG_MONITOR_PASSWORD='bad@pw:/x' bash ./install.sh $mode --yes --print-env-only 2>&1)" || status=$?
    [[ $status -ne 0 ]] && pass "a bad exported monitoring password is refused ${mode:-on reuse}" || fail "a bad exported monitoring password is refused ${mode:-on reuse}"
    check "the refusal names the variable and the rule ${mode:-on reuse}" "$out" "PG_MONITOR_PASSWORD may contain only letters, digits and . _ ~ -"
    [[ "$out" != *'bad@pw'* ]] && pass "the refusal does not print the value ${mode:-on reuse}" || fail "the refusal does not print the value ${mode:-on reuse}"
    cmp -s "$work/.env" "$work/env.before" && pass ".env is unchanged after the refusal ${mode:-on reuse}" || fail ".env is unchanged after the refusal ${mode:-on reuse}"
  done
  printf "$base" >"$work/.env"
  (cd "$work" && PG_MONITOR_PASSWORD='a.b_c~d-E9' bash ./install.sh --yes --print-env-only >/dev/null 2>&1)
  check_exact "an exported password using every allowed character is kept" "$(bash_reads)" "a.b_c~d-E9"
)
# The installer checks exported PostgreSQL passwords with the server's own rule: both files must hold the same character class.
_rule_installer="$(extract_fn pg_password_ok "$INNER_INSTALLER" | grep -oE '\*\[![^]]*\]\*')"
_rule_server="$(extract_fn check_password <(sed 's/^  //' "$COMPOSE_DIR/postgres/pg-entrypoint.sh") | grep -oE '\*\[![^]]*\]\*')"
check_exact "the installer and the PostgreSQL entrypoint use one password rule" "$_rule_installer|$_rule_server" '*[!A-Za-z0-9._~-]*|*[!A-Za-z0-9._~-]*'

# When docker needs root the installer runs itself again through sudo, which resets the environment: every setting the installer
# reads must be named in --preserve-env, or a fresh install falls back to the default stores. The recording sudo acts like one with
# env_reset: it sees only the variables --preserve-env names, and logs those and its other arguments.
(
  work="$TMP_ROOT/sudo-reexec"; mkdir -p "$work/bin"
  cp "$INNER_INSTALLER" "$work/install.sh"
  printf '#!/usr/bin/env bash\n[ "$1" = info ] && exit 1\nexit 0\n' >"$work/bin/docker"
  cat >"$work/bin/sudo" <<'STUB'
#!/usr/bin/env bash
: >"$SUDO_LOG"
if [[ "$1" == --preserve-env=* ]]; then
  IFS=, read -r -a names <<<"${1#--preserve-env=}"; shift
  for n in "${names[@]}"; do printf 'kept %s=%s\n' "$n" "${!n-}" >>"$SUDO_LOG"; done
fi
printf 'arg %s\n' "$@" >>"$SUDO_LOG"
STUB
  chmod +x "$work/bin/docker" "$work/bin/sudo"
  if [[ $EUID -eq 0 || ! -S /var/run/docker.sock ]]; then
    pass "sudo re-exec keeps the installer's settings (skipped: running as root or no docker socket)"
  else
    (cd "$work" && env -i PATH="$work/bin:$PATH" HOME="$HOME" SUDO_LOG="$work/sudo.log" \
      PIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-only.yml PIPESHUB_PROJECT='my proj' HEALTH_WAIT_SECS=900 \
      PIPESHUB_UNRELATED_TOKEN=secret bash ./install.sh --yes >/dev/null 2>&1)
    log="$(cat "$work/sudo.log" 2>/dev/null)"
    check "sudo re-exec keeps PIPESHUB_COMPOSE_OVERRIDES" "$log" "kept PIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-only.yml"
    check "sudo re-exec keeps a value with a space" "$log" "kept PIPESHUB_PROJECT=my proj"
    check "sudo re-exec keeps a setting without the PIPESHUB_ prefix" "$log" "kept HEALTH_WAIT_SECS=900"
    check "sudo re-exec keeps the installer's own arguments" "$log" $'/install.sh\narg --yes'
    check_exact "sudo re-exec puts no value on sudo's command line" "$(printf '%s\n' "$log" | grep '^arg ' | grep -c '=')" "0"
    check_exact "sudo re-exec keeps no variable the installer does not read" "$(printf '%s\n' "$log" | grep -c UNRELATED)" "0"
    # With none of them set the call stays plain sudo, which needs no permission to keep variables.
    (cd "$work" && env -i PATH="$work/bin:$PATH" HOME="$HOME" SUDO_LOG="$work/sudo.log" bash ./install.sh --yes >/dev/null 2>&1)
    check_exact "sudo re-exec with no setting is plain sudo" "$(grep -c '^kept ' "$work/sudo.log" 2>/dev/null)" "0"
    check "sudo re-exec with no setting still runs the installer" "$(cat "$work/sudo.log" 2>/dev/null)" "/install.sh"
  fi
)
# Every variable the installer reads before it first sets it (so a value from the caller's environment counts) is in the list it keeps
# across sudo. Not inputs: bash's own variables, internal flags read inside functions defined before they are set (the platform
# probes, IMAGE_SOURCE, SANDBOX_PLAN), and the secrets the installer reads from .env or generates (SECRET_KEY, SECRET_KEY_PREVIOUS,
# the PostgreSQL passwords set with printf -v), never from the environment. PG_MONITOR_PASSWORD is the one exception (kept: checked below).
_read="$(awk '
  { a=""; rest=$0
    if (match($0, /^[[:space:]]*(export |local |readonly )?[A-Z][A-Z0-9_]*=/)) { a=substr($0, RSTART, RLENGTH); sub(/^[[:space:]]*(export |local |readonly )?/, "", a); sub(/=$/, "", a); rest=substr($0, RSTART+RLENGTH) }
    s=rest; while (match(s, /\$\{?[A-Z][A-Z0-9_]*/)) { n=substr(s, RSTART, RLENGTH); gsub(/[${]/, "", n); if (!(n in set)) print n; s=substr(s, RSTART+RLENGTH) }
    if (a != "") set[a]=1 }' "$INNER_INSTALLER" | grep -vE '^(BASH_[A-Z_]+|EUID|SECONDS|USER|IS_MACOS|IS_WINDOWS|IS_LINUX|IS_WSL|ARCH|IMAGE_SOURCE|SANDBOX_PLAN|SECRET_KEY|SECRET_KEY_PREVIOUS|TOTAL_RAM_MB|PG_[A-Z]+_PASSWORD)$' | sort -u)"
_kept="$(awk '/^    _keep=""/{g=1} g&&/; do$/{print; exit} g{print}' "$INNER_INSTALLER" | grep -oE '\b[A-Z][A-Z0-9_]*\b' | sort -u)"
check_exact "the sudo re-exec list covers every setting the installer reads" "$(comm -23 <(printf '%s\n' "$_read") <(printf '%s\n' "$_kept"))" ""
check "the guard finds settings without the PIPESHUB_ prefix" "$_read" "HEALTH_WAIT_SECS"
check "the sudo re-exec keeps an exported monitoring password" "$_kept" "PG_MONITOR_PASSWORD"

# --uninstall keeps the PostgreSQL volume unless --delete-postgres is given, also with --yes (checked with a recording docker).
(
  work="$TMP_ROOT/uninstall-pg"; mkdir -p "$work/bin"
  cp "$INNER_INSTALLER" "$work/install.sh"
  cp "$COMPOSE_DIR/docker-compose.yml" "$COMPOSE_DIR/docker-compose.postgres-docstore.yml" "$work/"
  printf 'COMPOSE_PROJECT_NAME=testproj\nPIPESHUB_COMPOSE_OVERRIDES=docker-compose.postgres-docstore.yml\nCOMPOSE_PROFILES=graph-neo4j,postgres,postgres-migrate\n' >"$work/.env"
  cat >"$work/bin/docker" <<'STUB'
#!/usr/bin/env bash
echo "docker $*" >>"$DOCKER_LOG"
case "$1 $2" in
  "volume ls") printf 'testproj_postgres_data\ntestproj_neo4j_data\ntestproj_redis_data\n' ;;
esac
exit 0
STUB
  chmod +x "$work/bin/docker"
  # run_uninstall <stdin text> <args...>: the text answers the prompts in order (printf %b, so \n separates answers)
  run_uninstall() { local input="$1"; shift; : >"$work/docker.log"; (cd "$work" && printf '%b' "$input" | PATH="$work/bin:$PATH" DOCKER_LOG="$work/docker.log" bash ./install.sh --uninstall "$@" >"$work/out.txt" 2>&1); }
  run_uninstall "" --yes
  log="$(cat "$work/docker.log")"
  check "--uninstall --yes does not run down -v" "$([[ "$log" != *"down -v"* ]] && echo no-down-v)" "no-down-v"
  check "--uninstall --yes removes the other volumes" "$log" "volume rm testproj_neo4j_data"
  check "--uninstall --yes keeps the PostgreSQL volume" "$([[ "$log" != *"volume rm testproj_postgres_data"* ]] && echo kept)" "kept"
  check "--uninstall --yes says how to delete it" "$(cat "$work/out.txt")" "--delete-postgres"
  run_uninstall "" --yes --delete-postgres
  check "--uninstall --yes --delete-postgres removes everything with down -v" "$(cat "$work/docker.log")" "down -v --remove-orphans"
  # interactive: the first answer confirms the uninstall, the second answers the PostgreSQL question (asked unless --delete-postgres)
  run_uninstall "y\nn\n"
  check "an interactive uninstall asks about the PostgreSQL volume" "$(cat "$work/out.txt")" "Also delete the PostgreSQL data volume?"
  check "and keeps it when the answer is no" "$([[ "$(cat "$work/docker.log")" != *"down -v"* && "$(cat "$work/docker.log")" != *"volume rm testproj_postgres_data"* ]] && echo kept)" "kept"
  run_uninstall "y\ny\n"
  check "and deletes it with down -v when the answer is yes" "$(cat "$work/docker.log")" "down -v --remove-orphans"
  run_uninstall "y\n" --delete-postgres
  check "--delete-postgres skips the PostgreSQL question" "$([[ "$(cat "$work/out.txt")" != *"Also delete the PostgreSQL"* ]] && echo no-prompt)" "no-prompt"
  check "--delete-postgres deletes it without being asked" "$(cat "$work/docker.log")" "down -v --remove-orphans"
  # a failed volume lookup is not "no PostgreSQL volume": the uninstall stops with nothing changed, it never runs down -v
  sed -i 's|^  "volume ls") |  "volume ls") [ -n "${LS_FAIL:-}" ] \&\& { echo "Cannot connect to the Docker daemon" >\&2; exit 1; }; |' "$work/bin/docker"
  : >"$work/docker.log"
  (cd "$work" && PATH="$work/bin:$PATH" DOCKER_LOG="$work/docker.log" LS_FAIL=1 bash ./install.sh --uninstall --yes >"$work/out.txt" 2>&1); code=$?
  log="$(cat "$work/docker.log")"
  check "a failed volume lookup aborts the uninstall" "$([[ $code -ne 0 ]] && echo aborted)" "aborted"
  check "and says nothing was changed" "$(cat "$work/out.txt")" "Nothing was changed"
  check "and never runs down or removes a volume" "$([[ "$log" != *" down "* && "$log" != *"volume rm"* ]] && echo untouched)" "untouched"
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
check "refresh target honours the pinned tag" "$inner" '_APP_IMAGE="${APP_IMAGE_REPO}:${IMAGE_TAG:-latest}"'

# Downstream builds: image repository and extra compose files.
check "app image repository defaults to pipeshubai/pipeshub-ai" "$inner" 'APP_IMAGE_REPO="${PIPESHUB_IMAGE:-pipeshubai/pipeshub-ai}"'
check "compose app image follows PIPESHUB_IMAGE" "$(cat "$REPO_ROOT/deployment/docker-compose/docker-compose.yml")" 'image: ${PIPESHUB_APP_PINNED:-${PIPESHUB_IMAGE:-pipeshubai/pipeshub-ai}:${IMAGE_TAG:-latest}}'
check "extra compose files are read from PIPESHUB_COMPOSE_OVERRIDES" "$inner" 'read -r -a _compose_overrides <<< "$PIPESHUB_COMPOSE_OVERRIDES"'
check "image and overrides are saved in .env" "$inner" 'printf '"'"'PIPESHUB_COMPOSE_OVERRIDES=%s\n'"'"' "$PIPESHUB_COMPOSE_OVERRIDES" >> "$ENV_FILE"'
check "later runs read the saved image" "$inner" 'PIPESHUB_IMAGE="${PIPESHUB_IMAGE:-$(_saved_env_val PIPESHUB_IMAGE)}"'
check "an extra env file may not repeat an installer key" "$inner" 'which the installer already writes'
if grep -Eq -- 'docker compose .*-f "\$COMPOSE_FILE"|^ *-f "\$COMPOSE_FILE" \\$' "$INNER_INSTALLER"; then
  fail "every compose call passes the override files too"
else
  pass "every compose call passes the override files too"
fi
# R25-OPS: the sandbox-mode choice once rebuilt the file list from docker-compose.yml alone, so every compose call
# after it dropped PIPESHUB_COMPOSE_OVERRIDES without a word.
if grep -Eq '(^|[^_A-Z])COMPOSE_FILES=\(-f "\$COMPOSE_FILE"' "$INNER_INSTALLER"; then
  fail "no later choice of compose files drops the overrides"
else
  pass "no later choice of compose files drops the overrides"
fi
(
  work="$TMP_ROOT/overrides-kept"; mkdir -p "$work"
  cp "$INNER_INSTALLER" "$COMPOSE_DIR/docker-compose.yml" "$work/"
  printf 'services: {}\n' >"$work/extra.yml"
  out="$(cd "$work" && PIPESHUB_DEPLOY_TYPE=slim PIPESHUB_IMAGE_SOURCE=prebuilt PIPESHUB_VERSION=0.7.0-slim \
    PIPESHUB_PROJECT=installer-test-overrides PIPESHUB_GVISOR=skip PIPESHUB_COMPOSE_OVERRIDES=extra.yml \
    bash ./install.sh --yes --print-env-only </dev/null 2>&1)"
  check "the printed compose command carries the override file" "$out" "-f extra.yml -p installer-test-overrides up -d"
)
# Launch-path guards.
check "refreshes app image via compose pull" "$inner" 'pull "${_PULL_SERVICES[@]}"'
check "sandbox image pulled only with the sandbox profile" "$inner" '[[ ",${COMPOSE_PROFILES:-}," == *",sandbox,"* ]] && _PULL_SERVICES+=(sandbox-image)'
check "--no-pull flag is parsed" "$inner" "FLAG_NO_PULL=true"
check "refresh decision uses the testable helper" "$inner" 'should_pull_image "$_USE_BUILD" "$FLAG_NO_PULL" "${PIPESHUB_NO_PULL:-}"'
# A pull failure must NOT abort when an image is already cached (flaky network).
check "pull failure tolerated when image cached" "$inner" "continuing with cached"
check "pull fallback inspects sandbox image" "$inner" 'docker image inspect "$_SANDBOX_IMAGE"'
check "air-gapped guidance present" "$inner" "air-gapped host, preload the image"
# Must not have reverted to a blanket pull of every service image on the hot path.
if [[ "$inner" == *"up -d --pull always"* ]]; then fail "must refresh only the app image, not force-pull all services"; else pass "does not force-pull all service images"; fi

echo "== In-tree installer: build info for a local build (real function) =="
# A built image has no git history, so --build reads the tag and the commit on
# the host and hands them to the Dockerfile as build args.
eval "$(extract_fn export_build_info "$INNER_INSTALLER")"
BI_REPO="$TMP_ROOT/build-info-repo"
mkdir -p "$BI_REPO"
(
  cd "$BI_REPO" && git init -q . \
    && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m one \
    && git tag v1.2.3 \
    && git -c user.name=t -c user.email=t@t commit -q --allow-empty -m two
)
BI_HEAD="$(git -C "$BI_REPO" rev-parse HEAD)"
check "version is the newest release tag, without the v" \
  "$(unset APP_VERSION GIT_COMMIT; export_build_info "$BI_REPO"; echo "[$APP_VERSION]")" "[1.2.3]"
check "commit is the full HEAD SHA" \
  "$(unset APP_VERSION GIT_COMMIT; export_build_info "$BI_REPO"; echo "[$GIT_COMMIT]")" "[$BI_HEAD]"
check "values already in the environment win" \
  "$(APP_VERSION=9.9.9 GIT_COMMIT=abc; export_build_info "$BI_REPO"; echo "[$APP_VERSION $GIT_COMMIT]")" "[9.9.9 abc]"
check "a directory without git leaves version and commit empty" \
  "$(unset APP_VERSION GIT_COMMIT; export_build_info "$TMP_ROOT"; echo "[$APP_VERSION|$GIT_COMMIT]")" "[|]"
check "local build exports the build info before compose up" "$inner" 'export_build_info "$(cd "${SCRIPT_DIR}/../../" && pwd)"'
for f in docker-compose.yml docker-compose.build.neo4j.yml \
  docker-compose.integration.arango.yml docker-compose.integration.neo4j.yml \
  beta/docker-compose.build.neo4j.slim.yml beta/docker-compose.build.opensearch.neo4j.yml; do
  check "$f passes the build info as build args" "$(cat "$COMPOSE_DIR/$f")" 'GIT_COMMIT: ${GIT_COMMIT:-}'
done

echo "== In-tree installer: compose progress mode (real function) =="
# Mapping must be tied to the TTY flag. Grepping for both "tty" and "plain"
# in the script stays green if the branches are swapped.
eval "$(extract_fn resolve_compose_progress "$INNER_INSTALLER")"
check "tty terminal gets in-place progress" "$(resolve_compose_progress true)" "tty"
check "captured stdout gets append-only progress" "$(resolve_compose_progress false)" "plain"
check "unknown TTY flag defaults to plain" "$(resolve_compose_progress '')" "plain"
if [[ "$(grep -Fc '_is_tty=false; [[ -t 1 ]] && _is_tty=true' "$INNER_INSTALLER")" -eq 1 ]]; then
  pass "TTY flag assigned once"
else
  fail "TTY flag assigned once"
fi

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
echo "== In-tree installer: --upgrade honours --version / PIPESHUB_VERSION =="
(
  # --version is parsed at the top of install.sh but is only consumed inside the
  # configuration wizard, which --upgrade skips. Without apply_requested_tag,
  # `--upgrade --version X` re-reads the old IMAGE_TAG from .env and restarts the
  # version the user was trying to leave, exiting 0 with no warning.
  work="$TMP_ROOT/upgrade-version"; mkdir -p "$work"
  cp "$INNER_INSTALLER" "$work/install.sh"
  [[ -f "$COMPOSE_DIR/docker-compose.yml" ]] && cp "$COMPOSE_DIR/docker-compose.yml" "$work/"

  seed_env() {
    printf 'IMAGE_TAG=%s\nSECRET_KEY=do-not-touch\nMONGO_PASSWORD=keep-me\nAPP_PORT=3000\nPROJECT_NAME=testproj\n' \
      "$1" >"$work/.env"
  }

  # Seed a .env whose SANDBOX_DOCKER_IMAGE is set; $2 is the value to write.
  seed_env_with_sandbox() {
    seed_env "$1"
    printf 'SANDBOX_DOCKER_IMAGE=%s\n' "$2" >>"$work/.env"
  }

  # --print-env-only must exit 0. Without this an installer that prints the
  # right summary and then dies would still satisfy the output assertions.
  run_installer() { # run_installer <desc> [VAR=val ...] -- <installer args...>
    local desc="$1"; shift
    local -a envs=() args=()
    while [[ $# -gt 0 ]]; do
      [[ "$1" == "--" ]] && { shift; args=("$@"); break; }
      envs+=("$1"); shift
    done
    local status=0
    out="$(cd "$work" && env "${envs[@]}" bash ./install.sh "${args[@]}" --print-env-only 2>&1)" || status=$?
    if [[ $status -eq 0 ]]; then pass "$desc exits 0"; else fail "$desc exits 0 (got $status)"; fi
  }

  seed_env "0.6.0-slim"
  run_installer "--upgrade --version" -- --upgrade --version 0.7.0-slim
  check "--upgrade --version reports the tag change" "$out" "0.6.0-slim -> 0.7.0-slim"
  check "--upgrade --version resolves the new tag"   "$out" "Image tag:             0.7.0-slim"
  check "--upgrade --version persists the new tag"   "$(cat "$work/.env")" "IMAGE_TAG=0.7.0-slim"
  check "--upgrade leaves SECRET_KEY alone"          "$(cat "$work/.env")" "SECRET_KEY=do-not-touch"
  check "--upgrade leaves other secrets alone"       "$(cat "$work/.env")" "MONGO_PASSWORD=keep-me"

  seed_env "0.6.0-slim"
  run_installer "--upgrade with PIPESHUB_VERSION" PIPESHUB_VERSION=0.7.0-slim -- --upgrade
  check "--upgrade honours PIPESHUB_VERSION" "$out" "Image tag:             0.7.0-slim"

  # The default sandbox image tracks IMAGE_TAG: re-tag it on upgrade.
  seed_env_with_sandbox "0.6.0-slim" "pipeshubai/pipeshub-sandbox:0.6.0-slim"
  run_installer "--upgrade re-tags the default sandbox image" -- --upgrade --version 0.7.0-slim
  check "default sandbox image moves to the new tag" "$(cat "$work/.env")" \
    "SANDBOX_DOCKER_IMAGE=pipeshubai/pipeshub-sandbox:0.7.0-slim"

  # A custom sandbox image — an air-gapped mirror or private registry — was set
  # deliberately, so an upgrade must leave it exactly as it is.
  seed_env_with_sandbox "0.6.0-slim" "myregistry.example/pipeshub-sandbox:pinned"
  run_installer "--upgrade preserves a custom sandbox image" -- --upgrade --version 0.7.0-slim
  check "custom sandbox image is left untouched" "$(cat "$work/.env")" \
    "SANDBOX_DOCKER_IMAGE=myregistry.example/pipeshub-sandbox:pinned"

  # C15.04: a fresh install from a mirror names the mirror's sandbox image; without it the stack asks for
  # pipeshubai/pipeshub-sandbox:<tag>, which an air-gapped host does not have.
  rm -f "$work/.env"
  run_installer "a fresh install from a mirror" PIPESHUB_DEPLOY_TYPE=slim PIPESHUB_IMAGE_SOURCE=prebuilt \
    PIPESHUB_VERSION=0.7.0-slim PIPESHUB_PROJECT=installer-test-mirror PIPESHUB_GVISOR=skip \
    PIPESHUB_IMAGE=registry.internal:5000/pipeshubai/pipeshub-ai \
    PIPESHUB_SANDBOX_IMAGE=registry.internal:5000/pipeshubai/pipeshub-sandbox:0.7.0-slim -- --yes
  check "the mirror's sandbox image is written to .env" "$(cat "$work/.env")" \
    $'\nSANDBOX_DOCKER_IMAGE=registry.internal:5000/pipeshubai/pipeshub-sandbox:0.7.0-slim\n'
  check "the mirror's app image is written to .env" "$(cat "$work/.env")" \
    $'\nPIPESHUB_IMAGE=registry.internal:5000/pipeshubai/pipeshub-ai'
  rm -f "$work/.env"
  run_installer "a fresh install with no mirror" PIPESHUB_DEPLOY_TYPE=slim PIPESHUB_IMAGE_SOURCE=prebuilt \
    PIPESHUB_VERSION=0.7.0-slim PIPESHUB_PROJECT=installer-test-mirror PIPESHUB_GVISOR=skip -- --yes
  check "with no mirror the sandbox image follows the tag" "$(cat "$work/.env")" $'\nSANDBOX_DOCKER_IMAGE=\n'

  seed_env "0.6.0-slim"
  run_installer "plain --upgrade" -- --upgrade
  check "plain --upgrade keeps the recorded tag" "$out" "Image tag:             0.6.0-slim"
  check "plain --upgrade does not rewrite .env"  "$(cat "$work/.env")" "IMAGE_TAG=0.6.0-slim"

  seed_env "0.7.0-slim"
  run_installer "--upgrade to the tag already in use" -- --upgrade --version 0.7.0-slim
  check "asking for the current tag is a no-op with a clear message" "$out" "Already on image tag 0.7.0-slim"

  # --reconfigure rewrites .env in full; datastore sizing an operator set by hand
  # must survive it, or a small machine silently goes back to the defaults.
  sizing=(NEO4J_HEAP_INITIAL=256M NEO4J_HEAP_MAX=1536M NEO4J_PAGECACHE=256M
          NEO4J_MEMORY_LIMIT=2688M NEO4J_MEMORY_RESERVATION=1G
          MONGO_MEMORY_RESERVATION=256M QDRANT_MEMORY_RESERVATION=512M
          PG_MEMORY_LIMIT=16G PG_SHARED_BUFFERS=4GB PG_SHM=2g)
  seed_env "0.6.0-slim"
  printf '%s\n' "${sizing[@]}" >>"$work/.env"
  run_installer "--reconfigure with datastore sizing" -- --reconfigure --yes
  for kv in "${sizing[@]}"; do
    check "--reconfigure keeps ${kv%%=*}" "$(cat "$work/.env")" $'\n'"$kv"$'\n'
  done

  # Datastore tuning exported for a scripted fresh install is written to .env;
  # a value already saved in .env still wins over the environment.
  tuning=("${sizing[@]}" MONGO_MEMORY_LIMIT=3G MONGO_CACHE_GB=2 MONGO_IMAGE_TAG=8.0.99
             MONGO_GLIBC_TUNABLES=glibc.pthread.rseq=0)
  env_tuning=(NEO4J_HEAP_INITIAL=128M NEO4J_HEAP_MAX=512M NEO4J_PAGECACHE=128M
              NEO4J_MEMORY_LIMIT=1G NEO4J_MEMORY_RESERVATION=512M
              MONGO_MEMORY_RESERVATION=128M QDRANT_MEMORY_RESERVATION=256M
              PG_MEMORY_LIMIT=2G PG_SHARED_BUFFERS=512MB PG_SHM=256m
              MONGO_MEMORY_LIMIT=1G MONGO_CACHE_GB=1 MONGO_IMAGE_TAG=8.0.1
              MONGO_GLIBC_TUNABLES=glibc.pthread.rseq=1)
  rm -f "$work/.env"
  run_installer "a fresh install with datastore tuning exported" PIPESHUB_DEPLOY_TYPE=slim \
    PIPESHUB_IMAGE_SOURCE=prebuilt PIPESHUB_VERSION=0.7.0-slim PIPESHUB_PROJECT=installer-test-tuning \
    PIPESHUB_GVISOR=skip "${tuning[@]}" -- --yes
  for kv in "${tuning[@]}"; do
    check "a fresh install writes the exported ${kv%%=*}" "$(cat "$work/.env")" $'\n'"$kv"$'\n'
  done
  seed_env "0.6.0-slim"
  printf '%s\n' "${tuning[@]}" >>"$work/.env"
  run_installer "--reconfigure with datastore tuning in .env and the environment" \
    "${env_tuning[@]}" -- --reconfigure --yes
  for kv in "${tuning[@]}"; do
    check "the saved ${kv%%=*} wins over the environment" "$(cat "$work/.env")" $'\n'"$kv"$'\n'
  done

  seed_env "0.6.0-slim"
  printf 'DATA_STORE=neo4j\nNEO4J_HEAP_INITIAL=2G\nNEO4J_HEAP_MAX=1G\n' >>"$work/.env"
  status=0
  out="$(cd "$work" && bash ./install.sh --reconfigure --yes --print-env-only 2>&1)" || status=$?
  if [[ $status -ne 0 ]]; then pass "initial heap above the maximum is refused"; else fail "initial heap above the maximum is refused"; fi
  check "the refusal names both heap settings" "$out" "NEO4J_HEAP_INITIAL (2G) must be <= NEO4J_HEAP_MAX (1G)"

  # Neo4j accepts GiB/MiB and plain bytes too; the check must read them.
  for pair in "2GiB 1G" "1536MiB 1g" "2147483648 1024m" "010G 9G" "09G 8G"; do
    seed_env "0.6.0-slim"
    printf 'DATA_STORE=neo4j\nNEO4J_HEAP_INITIAL=%s\nNEO4J_HEAP_MAX=%s\n' ${pair} >>"$work/.env"
    status=0
    out="$(cd "$work" && bash ./install.sh --reconfigure --yes --print-env-only </dev/null 2>&1)" || status=$?
    if [[ $status -ne 0 ]]; then pass "initial heap above the maximum is refused (${pair})"; else fail "initial heap above the maximum is refused (${pair})"; fi
  done
  seed_env "0.6.0-slim"
  printf 'DATA_STORE=neo4j\nNEO4J_HEAP_INITIAL=1GiB\nNEO4J_HEAP_MAX=1024m\n' >>"$work/.env"
  run_installer "an initial heap equal to the maximum in other units" -- --reconfigure --yes

  # ArangoDB does not read the Neo4j heap settings, so they must not block it.
  # A docker stub with no volumes keeps the host's own stacks from steering the
  # graph DB choice. Its own directory: the cases above may run the installer
  # through sudo, and a root-owned .env backup from the same second would block
  # this unprivileged run from writing its backup.
  work="$TMP_ROOT/arango-ignores-neo4j-heap"; mkdir -p "$work"
  cp "$INNER_INSTALLER" "$work/install.sh"
  [[ -f "$COMPOSE_DIR/docker-compose.yml" ]] && cp "$COMPOSE_DIR/docker-compose.yml" "$work/"
  seed_env "0.6.0-slim"
  nodocker="$work/nodocker"; mkdir -p "$nodocker"
  printf '#!/bin/sh\n[ "$1" = volume ] && exit 1\nexit 0\n' >"$nodocker/docker"; chmod +x "$nodocker/docker"
  printf 'NEO4J_HEAP_INITIAL=2G\nNEO4J_HEAP_MAX=1G\n' >>"$work/.env"
  run_installer "an ArangoDB install ignores the Neo4j heap settings" \
    PIPESHUB_GRAPH_DB=arango PATH="$nodocker:$PATH" -- --reconfigure --yes
  check "the ArangoDB install was chosen" "$(cat "$work/.env")" $'\nDATA_STORE=arangodb\n'
)

echo "== SEC-D-08: the app port's bind address (real functions) =="
(
  eval "$(extract_fn app_bind_address "$INNER_INSTALLER")"
  eval "$(extract_fn explain_bind_address "$INNER_INSTALLER")"
  info() { printf '%s\n' "$*"; }; warn() { printf '%s\n' "$*"; }
  check "no public URL: this machine only" "$(app_bind_address "" "")" "127.0.0.1"
  check "a public HTTPS URL opens every interface" "$(app_bind_address "" "https://pipeshub.example.com")" "0.0.0.0"
  check "a localhost URL stays local" "$(app_bind_address "" "http://LOCALHOST:3000")" "127.0.0.1"
  check "an explicit address wins over the URL" "$(app_bind_address "127.0.0.1" "https://pipeshub.example.com")" "127.0.0.1"
  check "local: says how to open it" "$(explain_bind_address 127.0.0.1 3000 "")" "APP_BIND_ADDRESS"
  check "open: says ufw does not close it" "$(explain_bind_address 0.0.0.0 3000 https://p.example.com)" "bypass ufw and firewalld"
  check "open over http: warns of plaintext" "$(explain_bind_address 0.0.0.0 3000 http://p.example.com)" "unencrypted"
)

echo "== SEC-D-08 / SEC-D-06: an .env from an older installer, reused (--print-env-only) =="
(
  work="$TMP_ROOT/bind-upgrade"; mkdir -p "$work"
  cp "$INNER_INSTALLER" "$work/install.sh"
  cp "$COMPOSE_DIR/docker-compose.yml" "$work/"
  nodocker="$work/nodocker"; mkdir -p "$nodocker"
  printf '#!/bin/sh\n[ "$1" = volume ] && exit 1\nexit 0\n' >"$nodocker/docker"; chmod +x "$nodocker/docker"
  for case in local public; do
    printf 'DEPLOY_TYPE=slim\nIMAGE_TAG=0.9.0\nSECRET_KEY=%s\nAPP_PORT=3000\nDATA_STORE=neo4j\nNEO4J_PASSWORD=n\nKV_STORE_TYPE=redis\nMESSAGE_BROKER=redis\nREDIS_PASSWORD=keep\nCOMPOSE_PROFILES=graph-neo4j\nSANDBOX_MODE=\n' \
      "$(head -c 48 /dev/urandom | od -An -tx1 | tr -d ' \n')" >"$work/.env"
    [[ "$case" == public ]] && echo "FRONTEND_PUBLIC_URL=https://pipeshub.example.com" >>"$work/.env"
    # The hardened profile (opted in): an .env from before SEC-D-08 gets the rule a fresh install follows.
    out="$(cd "$work" && PIPESHUB_HARDENED=true PATH="$nodocker:$PATH" bash ./install.sh --print-env-only </dev/null 2>&1)"
    env_now="$(cat "$work/.env")"
    if [[ "$case" == local ]]; then
      check "an old .env with no public URL is closed to this machine" "$env_now" "APP_BIND_ADDRESS=127.0.0.1"
      check "and the installer says how to open it again" "$out" "set APP_BIND_ADDRESS=0.0.0.0"
    else
      check "an old .env with a public URL keeps every interface" "$env_now" "APP_BIND_ADDRESS=0.0.0.0"
    fi
    check "an existing Redis password is kept" "$env_now" $'\nREDIS_PASSWORD=keep\n'
    # The compatibility profile (no opt-in): the port stays on every interface and nothing is written for it.
    sed -i.bak -E '/^(APP_BIND_ADDRESS|PIPESHUB_HARDENED|SANDBOX_BROKER_COMPONENT_KEY|EGRESS_PROXY_COMPONENT_KEY|DOCKER_PROXY_BROKER_TOKEN)=/d' \
      "$work/.env" && rm -f "$work/.env.bak"
    out_compat="$(cd "$work" && PATH="$nodocker:$PATH" bash ./install.sh --print-env-only </dev/null 2>&1)"
    if [[ "$case" == local ]] && ! grep -q '^APP_BIND_ADDRESS=' "$work/.env"; then
      pass "compatibility: an old .env keeps every interface ($case)"
    elif [[ "$case" == public ]] && ! grep -q '^APP_BIND_ADDRESS=' "$work/.env"; then
      pass "compatibility: an old .env keeps every interface ($case)"
    else
      fail "compatibility: an old .env keeps every interface ($case)"
    fi
    check "compatibility: the summary says so ($case)" "$out_compat" "(every interface"
    check "a missing Kafka password is generated" "$(grep -E '^KAFKA_PASSWORD=[a-f0-9]{48}$' "$work/.env")" "KAFKA_PASSWORD="
    [[ "$out" != *"$(grep ^KAFKA_PASSWORD= "$work/.env" | cut -d= -f2)"* ]] && pass "the generated value is not printed ($case)" \
      || fail "the generated value is not printed ($case)"
  done
)

echo "== Image signatures: a distribution names its own signer, never a wider one (SIGNER-FU-1) =="
(
  eval "$(grep -E '^(PIPESHUB_SIGNER_IDENTITY|PIPESHUB_SIGNER_ISSUER)=' "$INNER_INSTALLER")"
  for fn in signer_identity verify_image_signature cosign_run; do eval "$(extract_fn "$fn" "$INNER_INSTALLER")"; done
  error() { printf 'x %s\n' "$*" >&2; }
  matches() { [[ "$2" =~ $1 ]] && echo yes || echo no; }
  release='https://github.com/pipeshub-ai/pipeshub-ai/.github/workflows/release-publish.yml@refs/tags/v1.2.3'
  dist='https://github.com/acme-dist/pipeshub-dist/.github/workflows/release-publish.yml@refs/tags/v1.2.3'

  unset PIPESHUB_SIGNER_REPOSITORY PIPESHUB_SIGNER_WORKFLOW
  check "unset: upstream's release identity" "$(signer_identity)" "$PIPESHUB_SIGNER_IDENTITY"

  id="$(PIPESHUB_SIGNER_REPOSITORY=acme-dist/pipeshub-dist signer_identity)"
  check "a distribution's identity is its release workflow at a tag" "$(matches "$id" "$dist")" yes
  check "and no longer upstream's" "$(matches "$id" "$release")" no
  check "a branch of the distribution is not a release" "$(matches "$id" "${dist/refs\/tags\/v1.2.3/refs/heads/main}")" no
  check "another workflow of the distribution is not the release" "$(matches "$id" "${dist/release-publish/e2e}")" no
  check "a look-alike owner is not the distribution" "$(matches "$id" "${dist/acme-dist/acme-dist-x}")" no
  check "a look-alike repository is not the distribution" "$(matches "$id" "${dist/pipeshub-dist\//pipeshub-distx/}")" no
  check "the dots of a name are literal" "$(matches "$(PIPESHUB_SIGNER_REPOSITORY=acme/pipeshub.ee signer_identity)" "${dist/acme-dist\/pipeshub-dist/acme/pipeshubXee}")" no
  check "the issuer stays GitHub Actions OIDC" "$PIPESHUB_SIGNER_ISSUER" "https://token.actions.githubusercontent.com"
  id="$(PIPESHUB_SIGNER_REPOSITORY=acme-dist/pipeshub-dist PIPESHUB_SIGNER_WORKFLOW=sign-images.yaml signer_identity)"
  check "a distribution may name its workflow file" "$(matches "$id" "${dist/release-publish.yml/sign-images.yaml}")" yes

  for bad in '.*' 'acme/.*' 'acme/x|evil/y' 'acme' 'acme/x/y' '-acme/x' 'acme/..' 'acme/x$(id)' 'acme/x y'; do
    out="$(PIPESHUB_SIGNER_REPOSITORY="$bad" signer_identity 2>&1)"; rc=$?
    check "repository '$bad' is refused" "$rc:$out" "1:x PIPESHUB_SIGNER_REPOSITORY must be owner/repo"
  done
  for bad in '.*' 'x.yml|y' '../a.yml' 'a.yml.evil' 'a'; do
    rc=0; PIPESHUB_SIGNER_REPOSITORY=acme/x PIPESHUB_SIGNER_WORKFLOW="$bad" signer_identity >/dev/null 2>&1 || rc=$?
    check "workflow '$bad' is refused" "$rc" "1"
  done

  log="$TMP_ROOT/cosign.log"; mkdir -p "$TMP_ROOT/sigbin"
  printf '#!/usr/bin/env bash\necho "cosign $*" >>"%s"\nexit 0\n' "$log" >"$TMP_ROOT/sigbin/cosign"; chmod +x "$TMP_ROOT/sigbin/cosign"
  ref="acme/pipeshub-dist:1.2.3@sha256:$(printf 'c%.0s' {1..64})"
  : >"$log"; PATH="$TMP_ROOT/sigbin:$PATH" PIPESHUB_SIGNER_REPOSITORY=acme-dist/pipeshub-dist verify_image_signature "$ref"
  check "cosign is asked for the distribution's identity" "$(cat "$log")" '--certificate-identity-regexp ^https://github\.com/acme-dist/pipeshub-dist/\.github/workflows/release-publish\.yml@refs/tags/v[0-9] --certificate-oidc-issuer https://token.actions.githubusercontent.com'
  : >"$log"; rc=0; PATH="$TMP_ROOT/sigbin:$PATH" PIPESHUB_SIGNER_REPOSITORY='.*' verify_image_signature "$ref" 2>/dev/null || rc=$?
  check "a malformed signer fails the check" "$rc" "1"
  [[ ! -s "$log" ]] && pass "nothing was verified against a malformed signer" || fail "nothing was verified against a malformed signer"
)

echo "== Agent harness switch: on by default, an upgrade keeps a named choice (ADR-OPS-06 as amended 2026-11-20) =="
(
  eval "$(extract_fn get_existing_val "$INNER_INSTALLER")"
  eval "$(extract_fn agent_harness_switch "$INNER_INSTALLER")"
  die() { printf 'x %s\n' "$*" >&2; exit 1; }
  unset PIPESHUB_AGENT_HARNESS
  ENV_FILE="$TMP_ROOT/harness.env"
  rm -f "$ENV_FILE"
  check "a fresh install turns the harness on" "$(agent_harness_switch)" "true"
  printf 'DATA_STORE=neo4j\nREDIS_PASSWORD=x\n' >"$ENV_FILE"
  check "an upgrade whose .env does not name it turns it on" "$(agent_harness_switch)" "true"
  printf 'SANDBOX_BROKER_COMPONENT_KEY=k\nAGENT_HARNESS_WORKER_ENABLED=false\n' >"$ENV_FILE"
  check "an .env that turned it off keeps it off" "$(agent_harness_switch)" "false"
  printf 'AGENT_HARNESS_WORKER_ENABLED=true\n' >"$ENV_FILE"
  check "an .env that turned it on keeps it on" "$(agent_harness_switch)" "true"
  check "PIPESHUB_AGENT_HARNESS=off decides" "$(PIPESHUB_AGENT_HARNESS=off agent_harness_switch)" "false"
  rm -f "$ENV_FILE"
  check "PIPESHUB_AGENT_HARNESS=OFF decides on a fresh install, any case" "$(PIPESHUB_AGENT_HARNESS=OFF agent_harness_switch)" "false"
  check "PIPESHUB_AGENT_HARNESS=ON decides, any case" "$(PIPESHUB_AGENT_HARNESS=ON agent_harness_switch)" "true"
  out="$(PIPESHUB_AGENT_HARNESS=maybe agent_harness_switch 2>&1)"; rc=$?
  check "a value that is not on or off is refused" "$rc:$out" "1:x PIPESHUB_AGENT_HARNESS must be on or off"
  check "the wizard writes the switch into .env" "$(grep -c '^AGENT_HARNESS_WORKER_ENABLED=\${AGENT_HARNESS_WORKER_ENABLED}$' "$INNER_INSTALLER")" "1"
  check "Compose's default is on" "$(grep -c 'AGENT_HARNESS_WORKER_ENABLED=${AGENT_HARNESS_WORKER_ENABLED:-true}' "$COMPOSE_DIR/docker-compose.yml")" "1"
  # Only this run's PIPESHUB_AGENT_HARNESS=on asks for the harness; the switch an .env names is not a request.
  eval "$(extract_fn agent_harness_requested "$INNER_INSTALLER")"
  printf 'AGENT_HARNESS_WORKER_ENABLED=true\n' >"$ENV_FILE"
  check "a switch the .env names is not a request" "$(agent_harness_requested)" "false"
  check "PIPESHUB_AGENT_HARNESS=On is one, any case" "$(PIPESHUB_AGENT_HARNESS=On agent_harness_requested)" "true"
  check "PIPESHUB_AGENT_HARNESS=off is not" "$(PIPESHUB_AGENT_HARNESS=off agent_harness_requested)" "false"
)

echo "== Security profile: unit (ADR-OPS-07, R51-COMPAT) =="
(
  for fn in get_existing_val persist_env_var gen_secret weak_secret_reason security_profile_choice sandbox_plan \
            sync_docker_proxy_app_token secure_secret_key; do
    eval "$(extract_fn "$fn" "$INNER_INSTALLER")"
  done
  die() { printf 'x %s\n' "$*" >&2; exit 1; }
  info() { printf 'i %s\n' "$*"; }; warn() { printf 'w %s\n' "$*"; }
  ENV_FILE="$TMP_ROOT/profile.env"
  PIPESHUB_HARDENED_REQUESTED=""
  rm -f "$ENV_FILE"
  check "a fresh install is hardened" "$(security_profile_choice false)" "true"
  printf 'DATA_STORE=neo4j\nREDIS_PASSWORD=x\nCOMPOSE_PROFILES=graph-neo4j,sandbox\n' >"$ENV_FILE"
  check "an .env from a release without the switch keeps its behaviour" "$(security_profile_choice true)" "false"
  for mark in SANDBOX_BROKER_COMPONENT_KEY EGRESS_PROXY_COMPONENT_KEY DOCKER_PROXY_BROKER_TOKEN APP_BIND_ADDRESS; do
    printf 'DATA_STORE=neo4j\n%s=v\n' "$mark" >"$ENV_FILE"
    check "an .env this installer wrote before the switch ($mark) stays hardened" "$(security_profile_choice true)" "true"
  done
  printf 'SANDBOX_BROKER_COMPONENT_KEY=k\nPIPESHUB_HARDENED=false\n' >"$ENV_FILE"
  check "an .env that names false keeps it" "$(security_profile_choice true)" "false"
  printf 'PIPESHUB_HARDENED=true\n' >"$ENV_FILE"
  check "an .env that names true keeps it" "$(security_profile_choice true)" "true"
  printf 'DATA_STORE=neo4j\n' >"$ENV_FILE"
  check "PIPESHUB_HARDENED=true given to the installer opts in" "$(PIPESHUB_HARDENED_REQUESTED=TRUE security_profile_choice true)" "true"
  printf 'PIPESHUB_HARDENED=true\n' >"$ENV_FILE"
  check "PIPESHUB_HARDENED=false given to the installer decides" "$(PIPESHUB_HARDENED_REQUESTED=false security_profile_choice true)" "false"
  out="$(PIPESHUB_HARDENED_REQUESTED=maybe security_profile_choice true 2>&1)"; rc=$?
  check "a value that is not a boolean is refused" "$rc:$out" "1:x PIPESHUB_HARDENED must be true or false"

  check "compatibility keeps docker" "$(sandbox_plan slim __unset__ false false)" "docker"
  check "compatibility keeps a named docker" "$(sandbox_plan slim docker false false)" "docker"
  check "hardened migrates docker to the broker" "$(sandbox_plan slim docker true false)" "migrate"
  check "the harness migrates docker to the broker" "$(sandbox_plan slim __unset__ false true)" "migrate"
  check "the broker stays the broker under compatibility" "$(sandbox_plan slim broker false false)" "broker"
  check "eval stays off" "$(sandbox_plan eval __unset__ false false)" "disabled"

  printf 'DOCKER_PROXY_TOKEN=\n' >"$ENV_FILE"
  sync_docker_proxy_app_token docker >/dev/null
  check "docker mode gives the app its proxy token" "$(grep -E '^DOCKER_PROXY_TOKEN=[a-f0-9]{64}$' "$ENV_FILE")" "DOCKER_PROXY_TOKEN="
  sync_docker_proxy_app_token broker >/dev/null
  [[ "$(cat "$ENV_FILE")" == "DOCKER_PROXY_TOKEN=" ]] && pass "on the broker the app holds no proxy token" \
    || fail "on the broker the app holds no proxy token"
  [[ "$(grep -c '^DOCKER_PROXY_TOKEN=' "$ENV_FILE")" == 1 ]] && pass "one token line" || fail "one token line"

  SECRET_KEY="your_random_encryption_secret_key"; SECRET_KEY_PREVIOUS=""
  out="$(secure_secret_key false; echo "rc=$?"; echo "key=$SECRET_KEY")"
  check "compatibility keeps a weak SECRET_KEY" "$out" "key=your_random_encryption_secret_key"
  check "and says how to replace it" "$out" "PIPESHUB_HARDENED=true ./install.sh --upgrade"
  check "and reports nothing to persist" "$out" "rc=1"
  secure_secret_key true >/dev/null
  check "hardened moves the weak key to SECRET_KEY_PREVIOUS" "$SECRET_KEY_PREVIOUS" "your_random_encryption_secret_key"
  [[ "$SECRET_KEY" =~ ^[a-f0-9]{64}$ ]] && pass "and generates a random key" || fail "and generates a random key"
)

echo "== Security profile: an upstream .env upgraded, opted in, a fresh install (--print-env-only, R51-COMPAT) =="
(
  # This release's image (the stub docker below has no labels to read); the older image has its own block below.
  export PIPESHUB_IMAGE_CAPABILITIES="sandbox-broker,secret-key-file,agent-harness"
  work="$TMP_ROOT/profile-upgrade"; mkdir -p "$work"
  cp "$INNER_INSTALLER" "$work/install.sh"
  cp "$COMPOSE_DIR/docker-compose.yml" "$work/"
  nodocker="$work/nodocker"; mkdir -p "$nodocker"
  printf '#!/bin/sh\n[ "$1" = volume ] && exit 1\nexit 0\n' >"$nodocker/docker"; chmod +x "$nodocker/docker"
  rnd() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
  # The keys upstream main's install.sh --yes writes (no KAFKA_PASSWORD, SANDBOX_MODE, APP_BIND_ADDRESS, sandbox keys);
  # SECRET_KEY is the template's placeholder, as a hand-made upstream .env has it.
  upstream_env() {
    printf '%s\n' "DEPLOY_TYPE=slim" "IMAGE_TAG=0.9.0" "IMAGE_SOURCE=prebuilt" "SANDBOX_DOCKER_IMAGE=" \
      "PIPESHUB_SKIP_SLACKBOT=false" "COMPOSE_PROJECT_NAME=pxh-r51-upstream" "COMPOSE_PROFILES=graph-neo4j,sandbox" \
      "NODE_ENV=production" "LOG_LEVEL=info" "SECRET_KEY=your_random_encryption_secret_key" "ROTATE_SIGNING_SECRETS=" \
      "FRONTEND_PUBLIC_URL=" "APP_PORT=3000" "DATA_STORE=neo4j" "NEO4J_USERNAME=neo4j" "NEO4J_PASSWORD=$(rnd 8)" \
      "NEO4J_DATABASE=neo4j" "KV_STORE_TYPE=redis" "MESSAGE_BROKER=redis" "REDIS_PASSWORD=$(rnd 8)" \
      "MONGO_USERNAME=admin" "MONGO_PASSWORD=$(rnd 8)" "QDRANT_API_KEY=$(rnd 10)"
  }
  upgrade() { (cd "$work" && PATH="$nodocker:$PATH" bash ./install.sh --upgrade --print-env-only </dev/null 2>&1); }
  keys() { grep -E '^[A-Z_]+=' "$1" | cut -d= -f1 | sort -u; }
  val() { grep -E "^$1=" "$work/.env" | tail -1 | cut -d= -f2-; }

  upstream_env >"$work/.env"; cp "$work/.env" "$work/env.before"
  out="$(upgrade)"; rc=$?
  check "the upstream .env upgrades (exit 0)" "$rc" "0"
  check "the compatibility profile is written down" "$(cat "$work/.env")" $'\nPIPESHUB_HARDENED=false'
  check "and said" "$out" "Security profile: compatibility"
  check "and how to opt in" "$out" "PIPESHUB_HARDENED=true ./install.sh --upgrade"
  check "the weak SECRET_KEY is kept (rollback reads it)" "$(val SECRET_KEY)" "your_random_encryption_secret_key"
  [[ -z "$(val SECRET_KEY_PREVIOUS)" ]] && pass "no SECRET_KEY_PREVIOUS" || fail "no SECRET_KEY_PREVIOUS"
  [[ -z "$(val APP_BIND_ADDRESS)" ]] && pass "no bind address: every interface, as before" || fail "no bind address: every interface, as before"
  check "summary says every interface" "$out" "0.0.0.0:3000 (every interface"
  # The harness switch is on by default (ADR-OPS-06 as amended 2026-11-20), but its default is not a request: code
  # execution stays on docker and no broker starts until the operator opts in (ADR-OPS-07).
  check "code execution stays on docker" "$out" "SANDBOX_MODE=docker (the app"
  check "written down" "$(val SANDBOX_MODE)" "docker"
  check "and no broker starts" "$(val COMPOSE_PROFILES)" "graph-neo4j,sandbox"
  check "and how to move to the broker is said" "$out" "PIPESHUB_AGENT_HARNESS=on ./install.sh --upgrade"
  [[ "$(val KAFKA_PASSWORD)" =~ ^[a-f0-9]{48}$ ]] && pass "credentials healed (invisible)" || fail "credentials healed (invisible)"
  [[ "$(val DOCKER_PROXY_TOKEN)" =~ ^[a-f0-9]{64}$ ]] && pass "docker mode: the app gets its proxy token" || fail "docker mode: the app gets its proxy token"
  check "the harness is written on (the default; Labs keeps it off for every org)" "$(val AGENT_HARNESS_WORKER_ENABLED)" "true"
  cp "$work/.env" "$work/env.compat"
  upgrade >/dev/null
  diff -q "$work/env.compat" "$work/.env" >/dev/null && pass "a second upgrade changes nothing" || fail "a second upgrade changes nothing"

  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    for envf in env.before env.compat; do
      # Before and after the upgrade alike: docker code execution through the proxy, no broker.
      expected="0.0.0.0 false docker docker-socket-proxy"
      rendered="$(env -i PATH="$PATH" HOME="${HOME:-/tmp}" docker compose -f "$work/docker-compose.yml" -p pxh-r51-render \
        --env-file "$work/$envf" config --format json 2>&1)"; rc=$?
      check "plain compose renders the $envf .env (exit 0)" "$rc" "0"
      summary="$(printf '%s' "$rendered" | python3 -c 'import json,sys
d=json.load(sys.stdin)["services"]; a=d["pipeshub-ai"]
print(a["ports"][0]["host_ip"], a["environment"]["PIPESHUB_HARDENED"], a["environment"]["SANDBOX_MODE"],
      ",".join(sorted(set(d) & {"sandbox-broker","egress-proxy","docker-socket-proxy"})))' 2>&1)"
      check "it publishes every interface, compatibility, docker code execution ($envf)" "$summary" "$expected"
    done
  else
    pass "docker compose is not installed: render skipped"
  fi

  # The operator opts in: exactly the documented set changes.
  cp "$work/env.compat" "$work/.env"
  out="$(cd "$work" && PIPESHUB_HARDENED=true PATH="$nodocker:$PATH" bash ./install.sh --upgrade --print-env-only </dev/null 2>&1)"
  changed="$(diff <(grep -E '^[A-Z_]+=' "$work/env.compat" | sort) <(grep -E '^[A-Z_]+=' "$work/.env" | sort) \
    | grep -E '^[<>]' | sed -E 's/^[<>] ([A-Z_]+)=.*/\1/' | sort -u | tr '\n' ' ')"
  # SANDBOX_MODE=docker stays written until the broker is healthy (migrate_sandbox_to_broker).
  check "the opt-in changes exactly the profiles, the security profile, the key and the bind address" "$changed" \
    "APP_BIND_ADDRESS COMPOSE_PROFILES PIPESHUB_HARDENED SECRET_KEY SECRET_KEY_PREVIOUS "
  [[ "$changed" == "APP_BIND_ADDRESS COMPOSE_PROFILES PIPESHUB_HARDENED SECRET_KEY SECRET_KEY_PREVIOUS " ]] \
    && pass "and nothing else" || fail "and nothing else ($changed)"
  check "hardened" "$(val PIPESHUB_HARDENED)" "true"
  check "the weak key is retired to SECRET_KEY_PREVIOUS" "$(val SECRET_KEY_PREVIOUS)" "your_random_encryption_secret_key"
  check "the app port binds this machine" "$(val APP_BIND_ADDRESS)" "127.0.0.1"
  check "the broker's profile starts for the move" "$(val COMPOSE_PROFILES)" "graph-neo4j,sandbox,sandbox-broker"
  check "code execution moves to the broker once it is healthy" "$out" "moving to the sandbox broker"

  # An .env this branch's installer wrote before the switch: it ran hardened, and keeps doing so.
  { upstream_env | grep -v '^SECRET_KEY='; echo "SECRET_KEY=$(rnd 32)"; echo "SANDBOX_MODE=broker"
    echo "SANDBOX_BROKER_COMPONENT_KEY=$(rnd 32)"; echo "APP_BIND_ADDRESS=127.0.0.1"; echo "DOCKER_PROXY_TOKEN=$(rnd 32)"
    echo "COMPOSE_PROFILES=graph-neo4j,sandbox"; } >"$work/.env"
  upgrade >/dev/null
  check "an earlier hardened .env stays hardened" "$(val PIPESHUB_HARDENED)" "true"
  check "and on the broker" "$(val SANDBOX_MODE)" "broker"
  check "with the broker's profile" "$(val COMPOSE_PROFILES)" "graph-neo4j,sandbox,sandbox-broker"
  [[ -z "$(val DOCKER_PROXY_TOKEN)" ]] && pass "and no app token the proxy would accept" || fail "and no app token the proxy would accept"

  # A fresh install.
  rm -f "$work/.env"
  out="$(cd "$work" && PATH="$nodocker:$PATH" bash ./install.sh --yes --print-env-only </dev/null 2>&1)"; rc=$?
  check "a fresh install writes its .env (exit 0)" "$rc" "0"
  check "a fresh install is hardened" "$(val PIPESHUB_HARDENED)" "true"
  check "on the broker" "$(val SANDBOX_MODE)" "broker"
  check "with the broker's profile" "$(val COMPOSE_PROFILES)" ",sandbox,sandbox-broker"
  check "bound to this machine" "$(val APP_BIND_ADDRESS)" "127.0.0.1"
  [[ -z "$(val DOCKER_PROXY_TOKEN)" ]] && pass "no app proxy token on the broker" || fail "no app proxy token on the broker"
  sk="$(val SECRET_KEY)"; [[ ${#sk} -ge 32 ]] && pass "a random SECRET_KEY" || fail "a random SECRET_KEY"
)

echo "== App image capabilities: an image without the label is set up as upstream did (published before the broker) =="
(
  for fn in app_image_capabilities image_can sandbox_plan; do eval "$(extract_fn "$fn" "$INNER_INSTALLER")"; done
  eval "$(grep -E '^APP_IMAGE_CAPABILITIES_(LABEL|ALL)=' "$INNER_INSTALLER")"
  APP_IMAGE_REPO=pipeshubai/pipeshub-ai IMAGE_TAG=slim IMAGE_SOURCE=prebuilt
  unset PIPESHUB_IMAGE_CAPABILITIES
  capbin="$TMP_ROOT/capbin"; mkdir -p "$capbin"
  # docker: LOCAL is the local label (unset: no local copy), REGISTRY the registry's config JSON (unset: unreadable).
  cat >"$capbin/docker" <<'STUB'
#!/usr/bin/env bash
if [[ "$1 $2" == "image inspect" ]]; then [[ -n "${LOCAL+set}" ]] || exit 1; printf '%s\n' "$LOCAL"; exit 0; fi
if [[ "$1 $2 $3" == "buildx imagetools inspect" ]]; then [[ -n "${REGISTRY+set}" ]] || exit 1; printf '%s\n' "$REGISTRY"; exit 0; fi
exit 1
STUB
  chmod +x "$capbin/docker"
  caps() { PATH="$capbin:$PATH" app_image_capabilities; }
  check "the local image's label is read" "$(LOCAL='sandbox-broker,agent-harness' caps)" "sandbox-broker,agent-harness"
  check "a local image without the label is older" "$(LOCAL='<no value>' caps)" "none"
  check "the registry's label is read without pulling" \
    "$(REGISTRY='{"config":{"Labels":{"ai.pipeshub.capabilities": "sandbox-broker,secret-key-file"}}}' caps)" "sandbox-broker,secret-key-file"
  check "a registry image without the label is older" "$(REGISTRY='{"config":{"Labels":{"x":"y"}}}' caps)" "none"
  check "unreadable is unknown" "$(caps)" "unknown"
  check "a local build is this release's image" "$(IMAGE_SOURCE=local caps)" "$APP_IMAGE_CAPABILITIES_ALL"
  check "PIPESHUB_IMAGE_CAPABILITIES decides" "$(PIPESHUB_IMAGE_CAPABILITIES=none caps)" "none"
  image_can sandbox-broker unknown && pass "unknown assumes this release's image" || fail "unknown assumes this release's image"
  image_can sandbox-broker none && fail "an older image cannot run the broker" || pass "an older image cannot run the broker"
  check "hardened, but an image without the broker keeps docker" "$(sandbox_plan slim __unset__ true true false)" "docker"
  check "and an image with it migrates" "$(sandbox_plan slim __unset__ true true true)" "migrate"

  work="$TMP_ROOT/old-image"; mkdir -p "$work"
  cp "$INNER_INSTALLER" "$work/install.sh"; cp "$COMPOSE_DIR/docker-compose.yml" "$work/"
  nodocker="$work/nodocker"; mkdir -p "$nodocker"
  printf '#!/bin/sh\n[ "$1" = volume ] && exit 1\nexit 0\n' >"$nodocker/docker"; chmod +x "$nodocker/docker"
  val() { grep -E "^$1=" "$work/.env" | tail -1 | cut -d= -f2-; }
  out="$(cd "$work" && PIPESHUB_IMAGE_CAPABILITIES=none PATH="$nodocker:$PATH" bash ./install.sh --yes --print-env-only </dev/null 2>&1)"; rc=$?
  check "a fresh install on an older image writes its .env (exit 0)" "$rc" "0"
  check "it runs code on docker, as upstream did" "$(val SANDBOX_MODE)" "docker"
  check "with no broker" "$(val COMPOSE_PROFILES)" ",sandbox"
  [[ "$(val COMPOSE_PROFILES)" != *sandbox-broker* ]] && pass "no broker profile" || fail "no broker profile"
  check "and says why" "$out" "predates the sandbox broker"
  out="$(cd "$work" && PIPESHUB_IMAGE_CAPABILITIES=none PIPESHUB_HARDENED=true PATH="$nodocker:$PATH" bash ./install.sh --upgrade --print-env-only </dev/null 2>&1)"
  check "an opt-in on an older image does not move to the broker" "$(val SANDBOX_MODE)" "docker"
  [[ "$out" != *"moving to the sandbox broker"* ]] && pass "and says no move" || fail "and says no move"
  out="$(cd "$work" && PIPESHUB_IMAGE_CAPABILITIES=sandbox-broker,secret-key-file,agent-harness PIPESHUB_AGENT_HARNESS=on PATH="$nodocker:$PATH" bash ./install.sh --upgrade --print-env-only </dev/null 2>&1)"
  check "once the image has the broker, asking for the harness moves it" "$out" "moving to the sandbox broker"
)

echo "== R50OFF-FU-2: an empty MONGO_PASSWORD on an existing volume gets a root user (mocked docker) =="
(
  for fn in get_existing_val persist_env_var gen_secret volume_exists mongo_container_ids mongo_probe_name \
            stop_mongo_probe start_mongo_probe ensure_mongo_root_user; do
    eval "$(extract_fn "$fn" "$INNER_INSTALLER")"
  done
  die() { printf 'x %s\n' "$*" >&2; exit 1; }
  info() { printf 'i %s\n' "$*"; }; warn() { printf 'w %s\n' "$*"; }; success() { printf 's %s\n' "$*"; }
  PROJECT_NAME=pxh-r51; COMPOSE_FILES=(-f /c/docker-compose.yml); ENV_FILE="$TMP_ROOT/mongo.env"; MONGO_PROBE_WAIT_SECS=2
  calls="$TMP_ROOT/mongo.calls"
  # users: what countDocuments answers. The password reaches mongosh only through the environment (-e).
  docker() {
    printf '%s\n' "$*" >>"$calls"
    case "$*" in
      "volume inspect pxh-r51_mongodb_data") return 0 ;;
      ps*) return 0 ;;
      *"config --images mongodb") echo mongo:8.0.17 ;;
      *countDocuments*) echo "$MOCK_USERS" ;;
      *createUser*) [[ "$*" == *"PXH_MONGO_PASSWORD="* ]] || return 1 ;;
    esac
    return 0
  }
  unset MONGO_URI MONGO_USERNAME
  : >"$calls"; printf 'MONGO_PASSWORD=\nOTHER=1\n' >"$ENV_FILE"; MOCK_USERS=0
  out="$(ensure_mongo_root_user 2>&1; echo "PW=$MONGO_PASSWORD")"
  written="$(grep '^MONGO_PASSWORD=' "$ENV_FILE" | cut -d= -f2)"
  [[ "$written" =~ ^[a-f0-9]{48}$ ]] && pass "a password is generated and written" || fail "a password is generated and written"
  check "the root user is created on the volume" "$(cat "$calls")" "createUser"
  check "as root of admin" "$(cat "$calls")" 'roles: [{role: "root", db: "admin"}]'
  [[ "$(sed "s/PW=$written//" <<<"$out")" != *"$written"* ]] && pass "the password is never printed" || fail "the password is never printed"
  check "the shell carries it for Compose" "$out" "PW=$written"
  check "the app was stopped before the probe opened the volume" "$(cat "$calls")" "run -d --name pxh-r51-mongodb-rs-migrate"

  : >"$calls"; printf 'MONGO_PASSWORD=\n' >"$ENV_FILE"; MOCK_USERS=1
  out="$(ensure_mongo_root_user 2>&1)"
  check "a volume that already has the user is left without authentication" "$(cat "$ENV_FILE")" "MONGO_PASSWORD="
  [[ "$(grep -c createUser "$calls")" == 0 ]] && pass "and no user is created" || fail "and no user is created"
  check "and the operator is told" "$out" "keeps running without"

  : >"$calls"; printf 'MONGO_PASSWORD=set\n' >"$ENV_FILE"
  ensure_mongo_root_user >/dev/null 2>&1
  [[ ! -s "$calls" ]] && pass "a set MONGO_PASSWORD touches nothing" || fail "a set MONGO_PASSWORD touches nothing"
  grep -n '^ensure_mongo_root_user$' "$INNER_INSTALLER" >/dev/null && pass "the launch runs it" || fail "the launch runs it"
  heal="$(grep -n '^ensure_mongo_root_user$' "$INNER_INSTALLER" | cut -d: -f1)"
  conv="$(grep -n '^migrate_mongo_to_replset$' "$INNER_INSTALLER" | cut -d: -f1)"
  [[ -n "$heal" && -n "$conv" && "$heal" -lt "$conv" ]] && pass "before MongoDB restarts with a key" || fail "before MongoDB restarts with a key"
)

PASS="$(wc -l <"$PASS_FILE" | tr -d ' ')"
FAIL="$(wc -l <"$FAIL_FILE" | tr -d ' ')"
printf "Results: %s passed, %s failed\n" "$PASS" "$FAIL"
if [[ "$FAIL" -ne 0 ]]; then
  echo "Failed checks:"; sed 's/^/  - /' "$FAIL_FILE"
fi
[[ "$FAIL" -eq 0 ]]
