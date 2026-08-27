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
#
# Optional env:
#   PIPESHUB_DEPLOY_TYPE   slim (default) | full
#   PIPESHUB_VERSION       Hub tag (default: slim for slim, latest for full)
#   PIPESHUB_SMOKE_PORT    requested host port (default: 3997 slim, 3998 full)
#   HEALTH_WAIT_SECS       installer health deadline (default: 600 slim, 720 full)
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
  *)
    die "PIPESHUB_DEPLOY_TYPE must be slim or full (got ${DEPLOY_TYPE})"
    ;;
esac

IMAGE_TAG="${PIPESHUB_VERSION:-$DEFAULT_TAG}"
PORT="${PIPESHUB_SMOKE_PORT:-$DEFAULT_PORT}"
PROJECT="${PIPESHUB_PROJECT:-pipeshub-ci-${DEPLOY_TYPE}-${GITHUB_RUN_ID:-$$}}"
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

echo "${LOG_PREFIX}: deploy=${DEPLOY_TYPE} project=${PROJECT} port=${PORT} image=${EXPECTED_IMAGE_PREFIX}:${IMAGE_TAG}"
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

echo "${LOG_PREFIX}: ok (deploy=${DEPLOY_TYPE} image=${GOT_IMAGE} UI 200, core services healthy)"
