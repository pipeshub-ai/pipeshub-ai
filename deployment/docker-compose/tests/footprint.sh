#!/usr/bin/env bash
# ==============================================================================
# Footprint baseline (P0.9, test S1): per-container and per-process memory of
# the all-in-one stack, with and without the durable-harness processes.
# ==============================================================================
# Two modes:
#
#   Isolated (default): installs this checkout's compose + installer into a temp
#   dir under a unique project name and port, waits for health, settles, then
#   measures twice: installed with AGENT_HARNESS_RELAY_ENABLED/
#   AGENT_HARNESS_WORKER_ENABLED=false (harness off), and with both true (the
#   shipped default since DUR3). Uninstalls on exit.
#
#   Read-only: FOOTPRINT_PROJECT=<compose project> measures a stack that is
#   already running and changes nothing (no restart, no harness toggle).
#
# Exits 1 when the harness-off eval measurement is over the eval budget
# (footprint.py: total <= 7.5 GB, app <= 4.5 GB). Other deploy types are
# measured and reported but not judged: the budget is the eval one.
#
#   PIPESHUB_DEPLOY_TYPE=eval bash deployment/docker-compose/tests/footprint.sh
#   FOOTPRINT_PROJECT=pipeshub-ai bash deployment/docker-compose/tests/footprint.sh
#
# Optional env:
#   PIPESHUB_DEPLOY_TYPE   eval (default) | slim | full
#   PIPESHUB_VERSION       image tag (default: slim for eval/slim, latest for full)
#   FOOTPRINT_NO_PULL=1    use the locally cached image (installer --no-pull)
#   FOOTPRINT_PORT         host port (default 3993)
#   FOOTPRINT_SETTLE_SECS  idle wait after health before measuring (default 120)
#   FOOTPRINT_HARNESS=0    skip the harness-on measurement
#   FOOTPRINT_OUT          directory for the JSON results and table.md
#                          (default: a temp dir, printed at the end)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
FOOTPRINT_PY="$SCRIPT_DIR/footprint.py"
LOG_PREFIX="footprint"

die() { echo "${LOG_PREFIX}: $*" >&2; exit 1; }
log() { echo "${LOG_PREFIX}: $*"; }

command -v docker >/dev/null 2>&1 || die "docker is required"
docker info >/dev/null 2>&1 || die "docker daemon is not running"
command -v python3 >/dev/null 2>&1 || die "python3 is required"

DEPLOY_TYPE="${PIPESHUB_DEPLOY_TYPE:-eval}"
case "$DEPLOY_TYPE" in
  eval|slim) DEFAULT_TAG="slim" ;;
  full) DEFAULT_TAG="latest" ;;
  *) die "PIPESHUB_DEPLOY_TYPE must be eval, slim or full (got ${DEPLOY_TYPE})" ;;
esac
SETTLE="${FOOTPRINT_SETTLE_SECS:-120}"
OUT="${FOOTPRINT_OUT:-$(mktemp -d "${TMPDIR:-/tmp}/pipeshub-footprint.XXXXXX")}"
mkdir -p "$OUT"

measure() {
  local project="$1" label="$2"
  python3 "$FOOTPRINT_PY" measure --project "$project" --label "$label" \
    --out "$OUT/${label}.json" >/dev/null
  log "measured ${label} -> $OUT/${label}.json"
}

finish() {
  python3 "$FOOTPRINT_PY" render "$@" | tee "$OUT/table.md"
}

if [[ -n "${FOOTPRINT_PROJECT:-}" ]]; then
  log "read-only measurement of running project ${FOOTPRINT_PROJECT}"
  measure "$FOOTPRINT_PROJECT" "${DEPLOY_TYPE}-running"
  finish "$OUT/${DEPLOY_TYPE}-running.json"
  [[ "$DEPLOY_TYPE" == "eval" ]] && python3 "$FOOTPRINT_PY" check "$OUT/${DEPLOY_TYPE}-running.json"
  exit 0
fi

PORT="${FOOTPRINT_PORT:-3993}"
PROJECT="pipeshub-footprint-${DEPLOY_TYPE}-${GITHUB_RUN_ID:-$$}"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/pipeshub-footprint-stack.XXXXXX")"

compose_cmd() {
  docker compose -f "$WORK/docker-compose.yml" -p "$PROJECT" --env-file "$WORK/.env" "$@"
}

cleanup() {
  local ec=$?
  if [[ -f "$WORK/install.sh" ]]; then
    (cd "$WORK" && PIPESHUB_PROJECT="$PROJECT" bash ./install.sh --yes --uninstall) >/dev/null 2>&1 || true
  fi
  [[ -f "$WORK/.env" ]] && compose_cmd down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$ec"
}
trap cleanup EXIT

wait_healthy() {
  local deadline=$((SECONDS + ${1:-600}))
  while (( SECONDS < deadline )); do
    if curl -sf --max-time 10 "http://localhost:${PORT}/api/v1/health/services" -o "$WORK/health.json" \
      && python3 - "$WORK/health.json" <<'PY'
import json, sys
s = json.load(open(sys.argv[1])).get("services") or {}
sys.exit(0 if all(s.get(k) == "healthy" for k in ("query", "connector", "indexing", "docling")) else 1)
PY
    then
      return 0
    fi
    sleep 10
  done
  return 1
}

cp "$COMPOSE_DIR/docker-compose.yml" "$WORK/docker-compose.yml"
cp "$COMPOSE_DIR/install.sh" "$WORK/install.sh"
log "deploy=${DEPLOY_TYPE} project=${PROJECT} port=${PORT} out=${OUT}"
_no_pull=()
[[ "${FOOTPRINT_NO_PULL:-}" == "1" ]] && _no_pull=(--no-pull)
(
  cd "$WORK"
  PIPESHUB_DEPLOY_TYPE="$DEPLOY_TYPE" \
  PIPESHUB_IMAGE_SOURCE=prebuilt \
  PIPESHUB_VERSION="${PIPESHUB_VERSION:-$DEFAULT_TAG}" \
  PIPESHUB_PROJECT="$PROJECT" \
  PIPESHUB_PORT="$PORT" \
  AGENT_HARNESS_RELAY_ENABLED=false \
  AGENT_HARNESS_WORKER_ENABLED=false \
    bash ./install.sh --yes ${_no_pull[@]+"${_no_pull[@]}"}
) || die "install.sh failed"

# The installer may move to a free port; it records the one it used.
_port="$(grep -E '^APP_PORT=' "$WORK/.env" | tail -1 | cut -d= -f2- | tr -d '"\r' || true)"
[[ -n "$_port" ]] && PORT="$_port"

wait_healthy 600 || die "stack did not become healthy"
log "healthy; settling ${SETTLE}s"
sleep "$SETTLE"
measure "$PROJECT" "${DEPLOY_TYPE}-harness-off"
RESULTS=("$OUT/${DEPLOY_TYPE}-harness-off.json")

if [[ "${FOOTPRINT_HARNESS:-1}" != "0" ]]; then
  # Leading newline: the installer's .env may not end with one.
  printf '\nAGENT_HARNESS_RELAY_ENABLED=true\nAGENT_HARNESS_WORKER_ENABLED=true\n' >>"$WORK/.env"
  compose_cmd up -d --no-deps pipeshub-ai >/dev/null
  app="$(compose_cmd ps -q pipeshub-ai)"
  [[ "$(docker exec "$app" printenv AGENT_HARNESS_WORKER_ENABLED 2>/dev/null)" == "true" ]] \
    || die "app container did not pick up AGENT_HARNESS_WORKER_ENABLED=true"
  # The old container's services answer health until the recreate lands.
  sleep 30
  wait_healthy 600 || die "stack did not become healthy with the harness on"
  log "harness on and healthy; settling ${SETTLE}s"
  sleep "$SETTLE"
  measure "$PROJECT" "${DEPLOY_TYPE}-harness-on"
  python3 - "$OUT/${DEPLOY_TYPE}-harness-on.json" <<'PY' \
    || { compose_cmd logs --tail 80 pipeshub-ai >&2; die "harness worker was not running when measured"; }
import json, sys
sys.exit(0 if "harness-worker" in json.load(open(sys.argv[1]))["app_processes"] else 1)
PY
  RESULTS+=("$OUT/${DEPLOY_TYPE}-harness-on.json")
fi

finish "${RESULTS[@]}"
if [[ "$DEPLOY_TYPE" == "eval" ]]; then
  python3 "$FOOTPRINT_PY" check "$OUT/${DEPLOY_TYPE}-harness-off.json"
fi
