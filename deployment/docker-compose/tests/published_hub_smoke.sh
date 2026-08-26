#!/usr/bin/env bash
# ==============================================================================
# First-run smoke: this checkout's compose + installer vs a published Hub image.
# ==============================================================================
# Unit tests of *new* Python cannot catch Adaptive Parse-class bugs: those are
# "current docker-compose.yml / install.sh talking to an already-published
# image." This script is that pairing.
#
# It runs the real non-interactive installer in an isolated directory, then
# checks the same things a user would: the UI answers, core /health/services
# are healthy, the app container is not crash-looping, and Python did not die
# on empty env ints.
#
# Needs Docker, ≥4 CPU cores (install.sh hard-requires that), and ~10–20 min
# on a cold Hub/HF cache (longer for full / :latest).
#
#   PIPESHUB_DEPLOY_TYPE=slim bash deployment/docker-compose/tests/published_hub_smoke.sh
#   PIPESHUB_DEPLOY_TYPE=full bash deployment/docker-compose/tests/published_hub_smoke.sh
#
# Optional env:
#   PIPESHUB_DEPLOY_TYPE   slim (default) | full
#   PIPESHUB_VERSION       Hub tag (default: slim for slim, latest for full)
#   PIPESHUB_SMOKE_PORT    host port (default: 3997 slim, 3998 full)
#   HEALTH_WAIT_SECS       installer health deadline (default: 600 slim, 720 full)
#   PIPESHUB_SMOKE_KEEP=1  leave the stack running (skip uninstall)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INNER_INSTALLER="$COMPOSE_DIR/install.sh"
COMPOSE_FILE_SRC="$COMPOSE_DIR/docker-compose.yml"
LOG_PREFIX="published_hub_smoke"

if [[ ! -f "$INNER_INSTALLER" || ! -f "$COMPOSE_FILE_SRC" ]]; then
  echo "${LOG_PREFIX}: missing installer or docker-compose.yml" >&2
  exit 1
fi
if ! command -v docker >/dev/null 2>&1; then
  echo "${LOG_PREFIX}: docker is required" >&2
  exit 1
fi
if ! docker info >/dev/null 2>&1; then
  echo "${LOG_PREFIX}: docker daemon is not running" >&2
  exit 1
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
    echo "${LOG_PREFIX}: PIPESHUB_DEPLOY_TYPE must be slim or full (got ${DEPLOY_TYPE})" >&2
    exit 1
    ;;
esac

IMAGE_TAG="${PIPESHUB_VERSION:-$DEFAULT_TAG}"
PORT="${PIPESHUB_SMOKE_PORT:-$DEFAULT_PORT}"
PROJECT="${PIPESHUB_PROJECT:-pipeshub-ci-${DEPLOY_TYPE}-${GITHUB_RUN_ID:-$$}}"
export HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-$DEFAULT_WAIT}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/pipeshub-hub-smoke.XXXXXX")"
cleanup() {
  local ec=$?
  if [[ "${PIPESHUB_SMOKE_KEEP:-}" == "1" ]]; then
    echo "${LOG_PREFIX}: PIPESHUB_SMOKE_KEEP=1 — stack left at $WORK (project $PROJECT)"
    exit "$ec"
  fi
  if [[ -f "$WORK/install.sh" ]]; then
    (cd "$WORK" && PIPESHUB_PROJECT="$PROJECT" bash ./install.sh --yes --uninstall) >/dev/null 2>&1 || true
  fi
  docker compose -f "$WORK/docker-compose.yml" -p "$PROJECT" down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$ec"
}
trap cleanup EXIT

cp "$COMPOSE_FILE_SRC" "$WORK/docker-compose.yml"
cp "$INNER_INSTALLER" "$WORK/install.sh"
chmod +x "$WORK/install.sh"

dump_failure() {
  echo "----- pipeshub-ai logs (tail 80) -----" >&2
  docker compose -f "$WORK/docker-compose.yml" -p "$PROJECT" --env-file "$WORK/.env" \
    logs --tail 80 pipeshub-ai >&2 || true
}

echo "${LOG_PREFIX}: deploy=${DEPLOY_TYPE} project=${PROJECT} port=${PORT} image=pipeshubai/pipeshub-ai:${IMAGE_TAG}"
echo "${LOG_PREFIX}: workdir=${WORK}"

(
  cd "$WORK"
  PIPESHUB_DEPLOY_TYPE="$DEPLOY_TYPE" \
  PIPESHUB_IMAGE_SOURCE=prebuilt \
  PIPESHUB_VERSION="$IMAGE_TAG" \
  PIPESHUB_PROJECT="$PROJECT" \
  PIPESHUB_PORT="$PORT" \
    bash ./install.sh --yes
)

# install.sh exits 0 even when the stack is not ready. The smoke must not.
ENV_FILE="$WORK/.env"
[[ -f "$ENV_FILE" ]] || { echo "${LOG_PREFIX}: installer did not write .env" >&2; exit 1; }

LOGS="$(docker compose -f "$WORK/docker-compose.yml" -p "$PROJECT" --env-file "$ENV_FILE" \
  logs pipeshub-ai 2>&1 || true)"
if grep -E "invalid literal for int|ValueError: invalid literal" <<<"$LOGS" >/dev/null; then
  echo "${LOG_PREFIX}: published image crashed parsing an empty int env (compose/image mismatch)" >&2
  dump_failure
  exit 1
fi

APP_ID="$(docker ps -aq --filter "label=com.docker.compose.project=${PROJECT}" \
  --filter "label=com.docker.compose.service=pipeshub-ai" | head -1 || true)"
if [[ -z "$APP_ID" ]]; then
  echo "${LOG_PREFIX}: app container is not running" >&2
  dump_failure
  exit 1
fi
RESTARTS="$(docker inspect "$APP_ID" --format '{{.RestartCount}}' 2>/dev/null || echo 0)"
if (( RESTARTS >= 2 )); then
  echo "${LOG_PREFIX}: app container restarted ${RESTARTS} times (crash loop)" >&2
  dump_failure
  exit 1
fi

HEALTH_URL="http://localhost:${PORT}/api/v1/health/services"
if ! curl -sf "$HEALTH_URL" -o "$WORK/health.json"; then
  echo "${LOG_PREFIX}: host cannot reach ${HEALTH_URL}" >&2
  dump_failure
  exit 1
fi

python3 - "$WORK/health.json" <<'PY'
import json, sys
path = sys.argv[1]
with open(path, encoding="utf-8") as fh:
    data = json.load(fh)
services = data.get("services") or {}
required = ("query", "connector", "indexing", "docling")
missing = [k for k in required if services.get(k) != "healthy"]
if missing:
    raise SystemExit(
        "published_hub_smoke: core services not healthy: "
        + ", ".join(f"{k}={services.get(k)!r}" for k in missing)
    )
PY

UI_CODE="$(curl -s -o /dev/null -w '%{http_code}' "http://localhost:${PORT}/" || echo fail)"
if [[ "$UI_CODE" != "200" ]]; then
  echo "${LOG_PREFIX}: UI returned HTTP ${UI_CODE} (expected 200)" >&2
  dump_failure
  exit 1
fi

echo "${LOG_PREFIX}: ok (deploy=${DEPLOY_TYPE} UI 200, core services healthy, no empty-int crash)"
