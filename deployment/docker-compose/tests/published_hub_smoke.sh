#!/usr/bin/env bash
# ==============================================================================
# First-run smoke: this checkout's compose + installer vs a published Hub image.
# ==============================================================================
# Unit tests of new Python never start the published container. Integration
# tests build from source. This script is the pairing a new user actually
# gets: this compose file + install.sh, pulling pipeshubai/pipeshub-ai from Hub.
#
# Requires Docker, python3, and ≥4 CPU cores (install.sh dies below 4).
# The installer in this checkout must honour PIPESHUB_PROJECT and an
# overridable HEALTH_WAIT_SECS (main does not; that lands with the
# multi-instance installer). Without those, this script refuses to run
# rather than targeting project pipeshub-ai and deleting its volumes on
# cleanup.
#
#   PIPESHUB_DEPLOY_TYPE=slim bash deployment/docker-compose/tests/published_hub_smoke.sh
#   PIPESHUB_DEPLOY_TYPE=full bash deployment/docker-compose/tests/published_hub_smoke.sh
#   PIPESHUB_DEPLOY_TYPE=eval bash deployment/docker-compose/tests/published_hub_smoke.sh
#   PIPESHUB_GRAPH_DB=arango  bash deployment/docker-compose/tests/published_hub_smoke.sh
#
# Optional env:
#   PIPESHUB_DEPLOY_TYPE   slim (default) | full | eval
#   PIPESHUB_GRAPH_DB      neo4j | arango (default: the installer's choice for the deploy type)
#   PIPESHUB_VERSION       Hub tag (default: slim for slim and eval, latest for full)
#   PIPESHUB_SMOKE_PORT    requested host port (default: 3997 slim, 3998 full, 3995 eval)
#   HEALTH_WAIT_SECS       installer health deadline (default: 600 slim/eval, 720 full)
#   PIPESHUB_SMOKE_KEEP=1  leave the stack running (skip uninstall)
#   PUBLISHED_HUB_SMOKE_DIAG  directory to copy logs/health.json on failure
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INNER_INSTALLER="$COMPOSE_DIR/install.sh"
COMPOSE_FILE_SRC="$COMPOSE_DIR/docker-compose.yml"
LOG_PREFIX="published_hub_smoke"
EXPECTED_IMAGE_PREFIX="pipeshubai/pipeshub-ai"

die() { echo "${LOG_PREFIX}: $*" >&2; exit 1; }

if [[ ! -f "$INNER_INSTALLER" || ! -f "$COMPOSE_FILE_SRC" ]]; then
  die "missing installer or docker-compose.yml"
fi
if ! command -v docker >/dev/null 2>&1; then
  die "docker is required"
fi
if ! docker info >/dev/null 2>&1; then
  die "docker daemon is not running"
fi
if ! command -v python3 >/dev/null 2>&1; then
  die "python3 is required (to parse /health/services JSON)"
fi
if ! command -v curl >/dev/null 2>&1; then
  die "curl is required"
fi

# Refuse to run against an installer that always uses project pipeshub-ai.
# Cleanup calls --yes --uninstall; on that installer that is down -v of the
# real default stack, not this smoke.
if ! grep -q 'PIPESHUB_PROJECT' "$INNER_INSTALLER"; then
  die "installer does not honour PIPESHUB_PROJECT (hardcoded project pipeshub-ai). Rebase onto the multi-instance installer so this smoke cannot delete another stack's volumes."
fi
if ! grep -Fq 'HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-' "$INNER_INSTALLER"; then
  die "installer HEALTH_WAIT_SECS is not overridable (fixed 300s on older installers). Rebase onto an installer that honours HEALTH_WAIT_SECS so a cold Hub/HF cache is not a false failure."
fi

DEPLOY_TYPE="${PIPESHUB_DEPLOY_TYPE:-slim}"
case "$DEPLOY_TYPE" in
  full)
    DEFAULT_TAG="latest"
    DEFAULT_PORT="3998"
    DEFAULT_WAIT="720"
    ;;
  slim)
    DEFAULT_TAG="slim"
    DEFAULT_PORT="3997"
    DEFAULT_WAIT="600"
    ;;
  eval)
    DEFAULT_TAG="slim"
    DEFAULT_PORT="3995"
    DEFAULT_WAIT="600"
    ;;
  *)
    die "PIPESHUB_DEPLOY_TYPE must be slim, full or eval (got ${DEPLOY_TYPE})"
    ;;
esac

GRAPH_DB="${PIPESHUB_GRAPH_DB:-}"
case "$GRAPH_DB" in
  "") EXPECTED_DATA_STORE="" ;;
  neo4j) EXPECTED_DATA_STORE="neo4j" ;;
  arango) EXPECTED_DATA_STORE="arangodb" ;;
  *) die "PIPESHUB_GRAPH_DB must be neo4j or arango (got ${GRAPH_DB})" ;;
esac
# Eval always runs Neo4j (the installer overrides any other choice), so a
# request for ArangoDB there would test nothing.
if [[ "$DEPLOY_TYPE" == "eval" && "$EXPECTED_DATA_STORE" == "arangodb" ]]; then
  die "eval installs always use Neo4j; do not combine it with PIPESHUB_GRAPH_DB=arango"
fi

IMAGE_TAG="${PIPESHUB_VERSION:-$DEFAULT_TAG}"
PORT="${PIPESHUB_SMOKE_PORT:-$DEFAULT_PORT}"
PROJECT="${PIPESHUB_PROJECT:-pipeshub-ci-${DEPLOY_TYPE}${PIPESHUB_GRAPH_DB:+-${PIPESHUB_GRAPH_DB}}-${GITHUB_RUN_ID:-$$}}"
export HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-$DEFAULT_WAIT}"
DIAG_DIR="${PUBLISHED_HUB_SMOKE_DIAG:-}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/pipeshub-hub-smoke.XXXXXX")"

env_file_val() {
  local key="$1" file="${2:-$WORK/.env}"
  [[ -f "$file" ]] || return 0
  grep -E "^${key}=" "$file" | tail -1 | cut -d= -f2- | tr -d '\r' | tr -d '"'
}

compose_cmd() {
  local -a args=(docker compose -f "$WORK/docker-compose.yml" -p "$PROJECT")
  [[ -f "$WORK/.env" ]] && args+=(--env-file "$WORK/.env")
  "${args[@]}" "$@"
}

dump_failure() {
  echo "----- pipeshub-ai logs (tail 80) -----" >&2
  compose_cmd logs --tail 80 pipeshub-ai >&2 || true
  if [[ -n "$DIAG_DIR" ]]; then
    mkdir -p "$DIAG_DIR"
    compose_cmd logs pipeshub-ai >"$DIAG_DIR/pipeshub-ai.log" 2>&1 || true
    [[ -f "$WORK/health.json" ]] && cp "$WORK/health.json" "$DIAG_DIR/health.json" || true
  fi
}

cleanup() {
  local ec=$?
  if [[ "$ec" -ne 0 ]]; then
    dump_failure || true
  fi
  if [[ "${PIPESHUB_SMOKE_KEEP:-}" == "1" ]]; then
    echo "${LOG_PREFIX}: PIPESHUB_SMOKE_KEEP=1 — stack left at $WORK (project $PROJECT)"
    exit "$ec"
  fi
  if [[ -f "$WORK/install.sh" ]]; then
    (cd "$WORK" && PIPESHUB_PROJECT="$PROJECT" bash ./install.sh --yes --uninstall) >/dev/null 2>&1 || true
  fi
  compose_cmd down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$ec"
}
trap cleanup EXIT

cp "$COMPOSE_FILE_SRC" "$WORK/docker-compose.yml"
cp "$INNER_INSTALLER" "$WORK/install.sh"
chmod +x "$WORK/install.sh"

echo "${LOG_PREFIX}: deploy=${DEPLOY_TYPE} graph=${GRAPH_DB:-default} project=${PROJECT} port=${PORT} image=${EXPECTED_IMAGE_PREFIX}:${IMAGE_TAG}"
echo "${LOG_PREFIX}: workdir=${WORK}"

set +e
(
  cd "$WORK"
  PIPESHUB_DEPLOY_TYPE="$DEPLOY_TYPE" \
  PIPESHUB_IMAGE_SOURCE=prebuilt \
  PIPESHUB_VERSION="$IMAGE_TAG" \
  PIPESHUB_PROJECT="$PROJECT" \
  PIPESHUB_PORT="$PORT" \
    bash ./install.sh --yes
)
install_ec=$?
set -e
if [[ "$install_ec" -ne 0 ]]; then
  die "install.sh --yes failed (exit ${install_ec})"
fi

# install.sh exits 0 even when the stack is not ready. The smoke must not.
ENV_FILE="$WORK/.env"
[[ -f "$ENV_FILE" ]] || die "installer did not write .env"

_from_env="$(env_file_val APP_PORT || true)"
if [[ -n "${_from_env:-}" ]]; then
  PORT="$_from_env"
fi
_proj_from_env="$(env_file_val COMPOSE_PROJECT_NAME || true)"
if [[ -n "${_proj_from_env:-}" ]]; then
  PROJECT="$_proj_from_env"
fi

LOGS="$(compose_cmd logs pipeshub-ai 2>&1 || true)"
if grep -F "invalid literal for int" <<<"$LOGS" >/dev/null; then
  die "published image crashed parsing an empty int env (compose/image mismatch)"
fi

APP_ID="$(docker ps -aq --filter "label=com.docker.compose.project=${PROJECT}" \
  --filter "label=com.docker.compose.service=pipeshub-ai" | head -1 || true)"
if [[ -z "$APP_ID" ]]; then
  die "app container is not running (project ${PROJECT})"
fi

GOT_IMAGE="$(docker inspect "$APP_ID" --format '{{.Config.Image}}' 2>/dev/null || true)"
# Allow docker.io/ prefix and a digest suffix. Do not accept a longer tag
# (pipeshubai/pipeshub-ai:latest-canary must not pass IMAGE_TAG=latest).
_got_image="${GOT_IMAGE#docker.io/}"
_got_image="${_got_image%%@*}"
if [[ "$_got_image" != "${EXPECTED_IMAGE_PREFIX}:${IMAGE_TAG}" ]]; then
  die "expected image ${EXPECTED_IMAGE_PREFIX}:${IMAGE_TAG}, container is running ${GOT_IMAGE:-unknown}"
fi

# The stack must run the graph database it was installed with, and only that one.
DATA_STORE="$(env_file_val DATA_STORE || true)"
case "$DATA_STORE" in
  arangodb) GRAPH_SERVICE="arango"; OTHER_GRAPH_SERVICE="neo4j" ;;
  neo4j)    GRAPH_SERVICE="neo4j";  OTHER_GRAPH_SERVICE="arango" ;;
  *) die "installer wrote DATA_STORE=${DATA_STORE:-unset} (expected neo4j or arangodb)" ;;
esac
if [[ -n "$EXPECTED_DATA_STORE" && "$DATA_STORE" != "$EXPECTED_DATA_STORE" ]]; then
  die "asked for PIPESHUB_GRAPH_DB=${GRAPH_DB}, installer wrote DATA_STORE=${DATA_STORE}"
fi
if [[ "$DEPLOY_TYPE" == "eval" && "$DATA_STORE" != "neo4j" ]]; then
  die "eval must run Neo4j, installer wrote DATA_STORE=${DATA_STORE}"
fi
service_running() {
  [[ -n "$(docker ps -q --filter "label=com.docker.compose.project=${PROJECT}" \
    --filter "label=com.docker.compose.service=$1" --filter status=running)" ]]
}
service_running "$GRAPH_SERVICE" || die "graph database container '${GRAPH_SERVICE}' is not running"
if service_running "$OTHER_GRAPH_SERVICE"; then
  die "both graph databases are running; expected only '${GRAPH_SERVICE}'"
fi

# Eval is the 8 GB laptop install: it leaves out the coding sandbox.
PROFILES="$(env_file_val COMPOSE_PROFILES || true)"
if [[ "$DEPLOY_TYPE" == "eval" && ",${PROFILES}," == *",sandbox,"* ]]; then
  die "eval install enabled the sandbox profile (COMPOSE_PROFILES=${PROFILES})"
fi

RESTARTS="$(docker inspect "$APP_ID" --format '{{.RestartCount}}' 2>/dev/null || echo 0)"
if [[ "${RESTARTS:-0}" -ge 2 ]]; then
  die "app container restarted ${RESTARTS} times (crash loop)"
fi

HEALTH_URL="http://localhost:${PORT}/api/v1/health/services"
if ! curl --connect-timeout 10 --max-time 30 -sf "$HEALTH_URL" -o "$WORK/health.json"; then
  die "host cannot reach ${HEALTH_URL}"
fi

if ! python3 - "$WORK/health.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
services = data.get("services") or {}
required = ("query", "connector", "indexing", "docling")
missing = [k for k in required if services.get(k) != "healthy"]
if missing:
    print(
        "published_hub_smoke: core services not healthy: "
        + ", ".join(f"{k}={services.get(k)!r}" for k in missing),
        file=sys.stderr,
    )
    sys.exit(1)
PY
then
  die "core services are not healthy at ${HEALTH_URL}"
fi

UI_CODE="$(curl --connect-timeout 10 --max-time 30 -s -o /dev/null -w '%{http_code}' \
  "http://localhost:${PORT}/" || echo fail)"
if [[ "$UI_CODE" != "200" ]]; then
  die "UI returned HTTP ${UI_CODE} (expected 200)"
fi

echo "${LOG_PREFIX}: ok (deploy=${DEPLOY_TYPE} graph=${DATA_STORE} image=${GOT_IMAGE} UI 200, core services healthy)"
