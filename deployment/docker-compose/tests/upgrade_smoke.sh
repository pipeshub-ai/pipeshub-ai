#!/usr/bin/env bash
# ==============================================================================
# Upgrade smoke: install the previous release, put data in it, upgrade, check
# the data survived.
# ==============================================================================
# The first-run smoke proves a fresh install works. It says nothing about the
# path every existing self-hosted customer takes. A migration that drops a
# collection, a schema change the old data does not satisfy, or an installer
# that rewrites .env and loses a password all pass a fresh install and break
# every upgrade — and reach every customer at once.
#
# What this does:
#   1. installs PIPESHUB_BASE_VERSION (default: latest published release)
#   2. seeds it through the running API — an org, an admin, a knowledge base
#      record — so there is real data in Mongo and the graph
#   3. captures a fingerprint of that data
#   4. upgrades in place to PIPESHUB_TARGET_VERSION with ./install.sh --upgrade
#   5. asserts the stack comes back healthy and the fingerprint still matches
#
# Seeding goes through the API, not by writing to the databases, so the test
# exercises the same write path a user does and does not encode schema
# assumptions that a migration is allowed to change.
#
#   PIPESHUB_TARGET_VERSION=0.7.1 bash deployment/docker-compose/tests/upgrade_smoke.sh
#
# Optional env:
#   PIPESHUB_BASE_VERSION    version to upgrade FROM (default: newest Hub tag)
#   PIPESHUB_TARGET_VERSION  version to upgrade TO   (default: latest)
#   PIPESHUB_DEPLOY_TYPE     slim (default) | full
#   PIPESHUB_UPGRADE_PORT    host port (default 3996)
#   HEALTH_WAIT_SECS         per-stage health deadline (default 600)
#   PIPESHUB_UPGRADE_KEEP=1  leave the stack up for inspection
#   PIPESHUB_UPGRADE_DIAG    directory for logs on failure
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INNER_INSTALLER="$COMPOSE_DIR/install.sh"
COMPOSE_FILE_SRC="$COMPOSE_DIR/docker-compose.yml"
LOG_PREFIX="upgrade_smoke"

log()  { echo "${LOG_PREFIX}: $*"; }
die()  { echo "${LOG_PREFIX}: $*" >&2; exit 1; }
step() { echo; echo "${LOG_PREFIX}: ── $* ──"; }

for cmd in docker python3 curl; do
  command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required"
done
docker info >/dev/null 2>&1 || die "docker daemon is not running"
[[ -f "$INNER_INSTALLER" && -f "$COMPOSE_FILE_SRC" ]] || die "missing installer or docker-compose.yml"

# Same guards as the first-run smoke: without these the cleanup path would
# uninstall the developer's real stack instead of this one.
grep -q 'PIPESHUB_PROJECT' "$INNER_INSTALLER" \
  || die "installer does not honour PIPESHUB_PROJECT; refusing to run"
grep -Fq 'HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-' "$INNER_INSTALLER" \
  || die "installer HEALTH_WAIT_SECS is not overridable; refusing to run"
grep -q -- '--upgrade' "$INNER_INSTALLER" \
  || die "installer has no --upgrade flag; nothing to test"

DEPLOY_TYPE="${PIPESHUB_DEPLOY_TYPE:-slim}"
case "$DEPLOY_TYPE" in slim) SUFFIX="-slim" ;; full) SUFFIX="" ;; *) die "PIPESHUB_DEPLOY_TYPE must be slim or full" ;; esac

TARGET_VERSION="${PIPESHUB_TARGET_VERSION:-latest}"
PORT="${PIPESHUB_UPGRADE_PORT:-3996}"
PROJECT="${PIPESHUB_PROJECT:-pipeshub-upgrade-${DEPLOY_TYPE}-${GITHUB_RUN_ID:-$$}}"
export HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-600}"
DIAG_DIR="${PIPESHUB_UPGRADE_DIAG:-}"

# Resolve the version to upgrade from: the newest published release, so this
# always tests the jump a real customer is about to make.
resolve_base_version() {
  local v="${PIPESHUB_BASE_VERSION:-}"
  if [[ -n "$v" ]]; then printf '%s' "$v"; return; fi
  v="$(curl -fsSL --max-time 20 \
        'https://hub.docker.com/v2/repositories/pipeshubai/pipeshub-ai/tags?page_size=100' 2>/dev/null \
      | python3 -c '
import json,sys,re
try: tags=[t["name"] for t in json.load(sys.stdin).get("results",[])]
except Exception: sys.exit(0)
sem=re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
best=max((t for t in tags if sem.match(t)),
         key=lambda t: tuple(int(x) for x in sem.match(t).groups()), default="")
print(best)' 2>/dev/null || true)"
  [[ -n "$v" ]] || die "could not resolve the newest published release; set PIPESHUB_BASE_VERSION"
  printf '%s' "$v"
}

BASE_VERSION="$(resolve_base_version)"
[[ "$BASE_VERSION" != "$TARGET_VERSION" ]] \
  || die "base and target are both ${BASE_VERSION}; nothing would be upgraded"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/pipeshub-upgrade.XXXXXX")"

compose_cmd() {
  local -a args=(docker compose -f "$WORK/docker-compose.yml" -p "$PROJECT")
  [[ -f "$WORK/.env" ]] && args+=(--env-file "$WORK/.env")
  "${args[@]}" "$@"
}

env_file_val() {
  [[ -f "$WORK/.env" ]] || return 0
  grep -E "^${1}=" "$WORK/.env" | tail -1 | cut -d= -f2- | tr -d '\r"'
}

dump_failure() {
  echo "----- pipeshub-ai logs (tail 120) -----" >&2
  compose_cmd logs --tail 120 pipeshub-ai >&2 2>&1 || true
  if [[ -n "$DIAG_DIR" ]]; then
    mkdir -p "$DIAG_DIR"
    compose_cmd logs pipeshub-ai >"$DIAG_DIR/upgrade-pipeshub-ai.log" 2>&1 || true
    for f in before.json after.json; do
      [[ -f "$WORK/$f" ]] && cp "$WORK/$f" "$DIAG_DIR/upgrade-$f" || true
    done
  fi
}

cleanup() {
  local ec=$?
  [[ "$ec" -ne 0 ]] && dump_failure || true
  if [[ "${PIPESHUB_UPGRADE_KEEP:-}" == "1" ]]; then
    log "PIPESHUB_UPGRADE_KEEP=1 — stack left at $WORK (project $PROJECT)"
    exit "$ec"
  fi
  [[ -f "$WORK/install.sh" ]] && \
    (cd "$WORK" && PIPESHUB_PROJECT="$PROJECT" bash ./install.sh --yes --uninstall) >/dev/null 2>&1 || true
  compose_cmd down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
  exit "$ec"
}
trap cleanup EXIT

cp "$COMPOSE_FILE_SRC" "$WORK/docker-compose.yml"
cp "$INNER_INSTALLER"  "$WORK/install.sh"
chmod +x "$WORK/install.sh"

log "deploy=${DEPLOY_TYPE} project=${PROJECT} port=${PORT}"
log "upgrade path: ${BASE_VERSION}${SUFFIX} -> ${TARGET_VERSION}"
log "workdir=${WORK}"

# ── wait helpers ─────────────────────────────────────────────────────────────
BASE_URL="http://localhost:${PORT}"

wait_healthy() {
  local label="$1" deadline=$(( SECONDS + HEALTH_WAIT_SECS ))
  while (( SECONDS < deadline )); do
    if curl --connect-timeout 5 --max-time 20 -sf "${BASE_URL}/api/v1/health/services" -o "$WORK/health.json" 2>/dev/null; then
      # /health/services returns {"services": {"query": "healthy", ...}} — plain
      # strings, not objects. Same four services the first-run smoke gates on.
      if python3 - "$WORK/health.json" <<'PY'
import json, sys
data = json.load(open(sys.argv[1], encoding="utf-8"))
services = data.get("services") or {}
required = ("query", "connector", "indexing", "docling")
bad = [f"{k}={services.get(k)!r}" for k in required if services.get(k) != "healthy"]
if bad:
    print("  not ready: " + ", ".join(bad), file=sys.stderr)
    sys.exit(1)
PY
      then
        log "${label}: healthy after $(( SECONDS - (deadline - HEALTH_WAIT_SECS) ))s"
        return 0
      fi
    fi
    sleep 10
  done
  die "${label}: not healthy within ${HEALTH_WAIT_SECS}s"
}

running_image() {
  local id
  id="$(docker ps -aq --filter "label=com.docker.compose.project=${PROJECT}" \
        --filter "label=com.docker.compose.service=pipeshub-ai" | head -1 || true)"
  [[ -n "$id" ]] || return 1
  docker inspect "$id" --format '{{.Config.Image}}' 2>/dev/null
}

# ── 1. install the previous release ──────────────────────────────────────────
step "1/5  install ${BASE_VERSION}${SUFFIX}"
set +e
(
  cd "$WORK"
  PIPESHUB_DEPLOY_TYPE="$DEPLOY_TYPE" \
  PIPESHUB_IMAGE_SOURCE=prebuilt \
  PIPESHUB_VERSION="${BASE_VERSION}${SUFFIX}" \
  PIPESHUB_PROJECT="$PROJECT" \
  PIPESHUB_PORT="$PORT" \
    bash ./install.sh --yes
)
ec=$?
set -e
[[ "$ec" -eq 0 ]] || die "install of ${BASE_VERSION} failed (exit ${ec})"

_p="$(env_file_val APP_PORT || true)"; [[ -n "${_p:-}" ]] && { PORT="$_p"; BASE_URL="http://localhost:${PORT}"; }
_n="$(env_file_val COMPOSE_PROJECT_NAME || true)"; [[ -n "${_n:-}" ]] && PROJECT="$_n"

wait_healthy "base install"
log "running image: $(running_image || echo unknown)"

# Keep a copy of the .env the old version wrote. An upgrade that regenerates
# secrets silently orphans every encrypted value already in the database.
cp "$WORK/.env" "$WORK/.env.before"

# ── 2. seed data through the API ─────────────────────────────────────────────
step "2/5  seed data"
if ! python3 "$SCRIPT_DIR/upgrade_seed.py" seed "$BASE_URL" "$WORK/before.json"; then
  die "seeding failed against ${BASE_URL}"
fi
log "seeded: $(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d.get("summary","?"))' "$WORK/before.json")"

# ── 3. upgrade in place ──────────────────────────────────────────────────────
step "3/5  upgrade to ${TARGET_VERSION}"

# Pin the target in .env rather than passing --version, so the test does not
# depend on how the installer under test resolves that flag. This is also what
# rollback.sh does, and what a deployment pinned to an exact tag looks like; one
# left on a floating tag (IMAGE_TAG=slim) moves on its own.
if grep -qE '^IMAGE_TAG=' "$WORK/.env"; then
  sed -i.bak -E "s|^IMAGE_TAG=.*|IMAGE_TAG=${TARGET_VERSION}|" "$WORK/.env" && rm -f "$WORK/.env.bak"
else
  printf 'IMAGE_TAG=%s\n' "$TARGET_VERSION" >> "$WORK/.env"
fi
log "set IMAGE_TAG=${TARGET_VERSION} in .env"

set +e
(
  cd "$WORK"
  PIPESHUB_PROJECT="$PROJECT" \
    bash ./install.sh --yes --upgrade
)
ec=$?
set -e
[[ "$ec" -eq 0 ]] || die "install.sh --upgrade failed (exit ${ec})"

# ── 4. the upgraded stack must come back ─────────────────────────────────────
step "4/5  verify the upgraded stack"
wait_healthy "after upgrade"

got="$(running_image || true)"
case "$got" in
  *"${TARGET_VERSION}") log "running image: $got" ;;
  *) die "expected an image tagged ${TARGET_VERSION}, container is running ${got:-unknown}" ;;
esac

# Secrets must survive. Regenerating them leaves every encrypted row unreadable
# — a failure that looks like data loss and is not caught by a health check.
# IMAGE_TAG is deliberately changed above; everything else must survive.
for key in SECRET_KEY MONGO_PASSWORD REDIS_PASSWORD QDRANT_API_KEY; do
  before="$(grep -E "^${key}=" "$WORK/.env.before" | tail -1 | cut -d= -f2- || true)"
  after="$(grep -E "^${key}=" "$WORK/.env"        | tail -1 | cut -d= -f2- || true)"
  if [[ -n "$before" && "$before" != "$after" ]]; then
    die "${key} changed during upgrade — existing encrypted data would be unreadable"
  fi
done
log "secrets preserved across upgrade"

restarts="$(docker inspect "$(docker ps -aq \
  --filter "label=com.docker.compose.project=${PROJECT}" \
  --filter "label=com.docker.compose.service=pipeshub-ai" | head -1)" \
  --format '{{.RestartCount}}' 2>/dev/null || echo 0)"
[[ "${restarts:-0}" -lt 2 ]] || die "app restarted ${restarts} times after upgrade (crash loop)"

# ── 5. the data must still be there ──────────────────────────────────────────
step "5/5  verify seeded data survived"
if ! python3 "$SCRIPT_DIR/upgrade_seed.py" verify "$BASE_URL" "$WORK/before.json" "$WORK/after.json"; then
  die "seeded data did not survive the upgrade"
fi

echo
log "PASS  ${BASE_VERSION}${SUFFIX} -> ${TARGET_VERSION}: stack healthy, secrets intact, data preserved"
