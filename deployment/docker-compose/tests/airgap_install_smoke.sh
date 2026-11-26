#!/usr/bin/env bash
# ==============================================================================
# Air-gapped install smoke (C15.04) and DR drill rehearsal (C20.48 / G-47).
# ==============================================================================
# The pairing an air-gapped operator gets, run the way guides/air-gapped-install.md says: this checkout's
# docker-compose.yml + install.sh with a PipesHub image already on the host (built here or `docker load`ed), started
# with `install.sh --no-pull` on Docker networks with no route out. It proves:
#
#   1. Mirror (a connected host): every image the install names is on the host before it starts, so nothing is
#      pulled; Docling's models go into the install's Hugging Face volume
#      (`python -m app.modules.parsers.pdf.docling_models`).
#   2. Install: `install.sh --yes --no-pull` with PIPESHUB_IMAGE / PIPESHUB_SANDBOX_IMAGE naming the mirror,
#      TELEMETRY_COLLECTION_FLAG=false and HF_HUB_OFFLINE=1 / TRANSFORMERS_OFFLINE=1, layered with docker-compose.airgap-install.yml
#      (PIPESHUB_COMPOSE_OVERRIDES): `pipeshub` internal like the other networks, ports only through an inbound-only
#      ingress, the scripted model in the network standing in for Ollama.
#   3. No route out (deployment/e2e/airgap_check.py AG-N1, AG-N2), then a scripted chat turn, an engine job
#      (opencode in a broker sandbox; coding engines on: HARNESS_ENGINE_RUNS=true) and a PDF through Docling.
#   4. AIRGAP_DR=1: DR-DRILL.md's Compose procedure on that install: back up (mongodump, the blob volume), make the
#      disaster (the Mongo volume and the blobs removed), restore, then check the conversation and the job from
#      before the backup are back, the one made after it is gone, and new turns work. Prints the measured RPO and
#      RTO and writes them to $AIRGAP_OUT/dr-drill.json.
#
# Needs: Docker with Compose v2, python3, the images the install names (the app image, the sandbox image, the
# opencode engine image, the store images), node_modules not needed. No live model: the scripted model answers.
# This lane's host has no gVisor, so engines run on hardened runc (PIPESHUB_ENGINES_WITHOUT_GVISOR=true); a
# production air-gapped install keeps gVisor (GVISOR.md).
#
#   AIRGAP_IMAGE=pxh-pipeshub-ai AIRGAP_TAG=r25ops AIRGAP_SANDBOX_IMAGE=pxh-sandbox:pr1 \
#     bash deployment/docker-compose/tests/airgap_install_smoke.sh
#
# Optional env:
#   AIRGAP_DR=1        also rehearse the DR drill
#   AIRGAP_DOCLING=0   skip Docling's models (the PDF check is skipped too)
#   AIRGAP_OUT         directory for the results (default: a temp dir, printed at the end)
#   AIRGAP_KEEP=1      leave the stack up (prints how to remove it)
#   HEALTH_WAIT_SECS   the installer's health deadline (default 900)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$COMPOSE_DIR/../.." && pwd)"
E2E_DIR="$REPO_ROOT/deployment/e2e"
CHECKS="$SCRIPT_DIR/install_checks.py"
OVERLAY="docker-compose.airgap-install.yml"
LOG_PREFIX="airgap_install"

log() { echo "${LOG_PREFIX}: $*"; }
die() { echo "${LOG_PREFIX}: $*" >&2; exit 1; }
rand() { head -c "$1" /dev/urandom | od -An -tx1 | tr -d ' \n'; }
free_port() { python3 -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1", 0)); print(s.getsockname()[1])'; }

for cmd in docker python3 curl; do command -v "$cmd" >/dev/null 2>&1 || die "$cmd is required"; done
docker info >/dev/null 2>&1 || die "docker daemon is not running"

IMAGE_REPO="${AIRGAP_IMAGE:?set AIRGAP_IMAGE, the PipesHub image repository on this host}"
IMAGE_TAG="${AIRGAP_TAG:?set AIRGAP_TAG, its tag}"
SANDBOX_IMAGE="${AIRGAP_SANDBOX_IMAGE:?set AIRGAP_SANDBOX_IMAGE, the code sandbox image on this host}"
APP_IMAGE="${IMAGE_REPO}:${IMAGE_TAG}"
ENGINE_IMAGE="$(python3 -c 'import json,sys; print(next(e["image"] for e in json.load(open(sys.argv[1]))["engines"] if e["kind"] == "opencode"))' \
  "$REPO_ROOT/backend/python/harness_engines/allowlist.json")"
PROJECT="pxh-airgap-$(rand 3)"
PORT="$(free_port)"
WORK="$(mktemp -d "${TMPDIR:-/tmp}/pxh-airgap.XXXXXX")"
OUT="${AIRGAP_OUT:-$WORK/out}"
RUN_DIR="$WORK/state/$PROJECT"
export HEALTH_WAIT_SECS="${HEALTH_WAIT_SECS:-900}"
mkdir -p "$OUT" "$RUN_DIR"

env_val() { grep -E "^$1=" "$WORK/.env" | tail -1 | cut -d= -f2- | tr -d '"\r'; }
compose_cmd() { docker compose -f "$WORK/docker-compose.yml" -f "$WORK/$OVERLAY" -p "$PROJECT" --env-file "$WORK/.env" "$@"; }
installer() {
  (
    cd "$WORK"
    PIPESHUB_DEPLOY_TYPE=slim PIPESHUB_GRAPH_DB=neo4j PIPESHUB_IMAGE_SOURCE=prebuilt \
    PIPESHUB_IMAGE="$IMAGE_REPO" PIPESHUB_VERSION="$IMAGE_TAG" PIPESHUB_SANDBOX_IMAGE="$SANDBOX_IMAGE" \
    PIPESHUB_PROJECT="$PROJECT" PIPESHUB_PORT="$PORT" PIPESHUB_GVISOR=skip \
    PIPESHUB_COMPOSE_OVERRIDES="$OVERLAY" PIPESHUB_ENV_EXTRA=airgap.env \
      bash ./install.sh "$@"
  )
}
org_id() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("orgId",""))' "$RUN_DIR/seed.json" 2>/dev/null || true; }

cleanup() {
  local ec=$?
  if [[ "$ec" -ne 0 && -f "$WORK/.env" ]]; then
    compose_cmd logs --no-color >"$OUT/stack.log" 2>&1 || true
    echo "${LOG_PREFIX}: failed; the stack's logs are in $OUT/stack.log" >&2
  fi
  if [[ "${AIRGAP_KEEP:-}" == "1" ]]; then
    log "AIRGAP_KEEP=1: stack $PROJECT left up; remove it with: (cd $WORK && PIPESHUB_PROJECT=$PROJECT bash ./install.sh --yes --uninstall)"
    exit "$ec"
  fi
  local org; org="$(org_id)"
  if [[ -f "$WORK/.env" ]]; then
    (cd "$WORK" && PIPESHUB_PROJECT="$PROJECT" bash ./install.sh --yes --uninstall) >/dev/null 2>&1 || true
    compose_cmd down -v --remove-orphans >/dev/null 2>&1 || true
  fi
  if [[ -n "$org" ]]; then
    docker ps -aq --filter label=io.pipeshub.sandbox-broker.managed=1 --filter "label=orgId=$org" | xargs -r docker rm -f >/dev/null 2>&1 || true
    docker network ls -q --filter label=io.pipeshub.sandbox-broker.managed=1 --filter "label=orgId=$org" | xargs -r docker network rm >/dev/null 2>&1 || true
  fi
  docker volume ls -q --filter "label=com.docker.compose.project=$PROJECT" | xargs -r docker volume rm >/dev/null 2>&1 || true
  docker volume rm "$PROJECT-sandbox-data" >/dev/null 2>&1 || true
  [[ -n "$(docker ps -aq --filter "label=com.docker.compose.project=$PROJECT")" ]] && echo "${LOG_PREFIX}: containers of $PROJECT are left" >&2
  [[ -n "${AIRGAP_OUT:-}" ]] || log "results in $OUT (removed with the work dir)"
  rm -rf "$WORK"
  exit "$ec"
}
trap cleanup EXIT

# Health as an operator reads it, through the ingress: the installer's core services healthy (Node's harness
# status alone reads available while the query service, which resolves a turn's route, is still starting) and the
# harness available.
wait_ready() {
  local deadline=$((SECONDS + ${1:-600}))
  while (( SECONDS < deadline )); do
    if curl -sf --max-time 10 "http://127.0.0.1:${PORT}/api/v1/health/services" -o "$WORK/services.json" 2>/dev/null \
      && curl -sf --max-time 10 "http://127.0.0.1:${PORT}/api/v1/health" -o "$WORK/health.json" 2>/dev/null \
      && python3 - "$WORK/services.json" "$WORK/health.json" <<'PY'
import json, sys
services = json.load(open(sys.argv[1])).get("services") or {}
harness = json.load(open(sys.argv[2])).get("agentHarness") or {}
core = all(services.get(k) == "healthy" for k in ("query", "connector", "indexing", "docling"))
sys.exit(0 if core and harness.get("status") == "available" else 1)
PY
    then
      return 0
    fi
    sleep 5
  done
  return 1
}

# --- 1. The mirror: what a connected host prepares ------------------------------------------------------------
cp "$COMPOSE_DIR/docker-compose.yml" "$COMPOSE_DIR/install.sh" "$SCRIPT_DIR/$OVERLAY" "$WORK/"
LLM_KEY="$(rand 16)"
CONTROL="$(rand 16)"
cat >"$WORK/airgap.env" <<EOF
TELEMETRY_COLLECTION_FLAG=false
HF_HUB_OFFLINE=1
TRANSFORMERS_OFFLINE=1
HARNESS_ENGINE_RUNS=true
PIPESHUB_ENGINES_WITHOUT_GVISOR=true
SANDBOX_DATA_VOLUME=$PROJECT-sandbox-data
AIRGAP_E2E_DIR=$E2E_DIR
AIRGAP_LLM_PORT=$(free_port)
AIRGAP_SAAS_PORT=$(free_port)
AIRGAP_LLM_KEY=$LLM_KEY
AIRGAP_CONTROL_TOKEN=$CONTROL
EOF
log "project=$PROJECT port=$PORT image=$APP_IMAGE sandbox=$SANDBOX_IMAGE engine=$ENGINE_IMAGE work=$WORK"
installer --yes --no-pull --print-env-only >"$OUT/install-print-env.log" 2>&1 || die "install.sh --print-env-only failed ($OUT/install-print-env.log)"
[[ "$(env_val SANDBOX_DOCKER_IMAGE)" == "$SANDBOX_IMAGE" ]] || die "install.sh did not write the mirror's sandbox image to .env"
missing=()
while read -r image; do
  [[ -n "$image" ]] || continue
  docker image inspect "$image" >/dev/null 2>&1 || missing+=("$image")
done < <(COMPOSE_PROFILES="$(env_val COMPOSE_PROFILES)" compose_cmd config --images; echo "$ENGINE_IMAGE")
[[ ${#missing[@]} -eq 0 ]] || die "not on this host, so an air-gapped install could not start: ${missing[*]}"
log "every image the install names is on the host ($(COMPOSE_PROFILES="$(env_val COMPOSE_PROFILES)" compose_cmd config --images | wc -l) + the engine image): nothing to pull"

if [[ "${AIRGAP_DOCLING:-1}" == "1" ]]; then
  docker volume create --label "com.docker.compose.project=$PROJECT" --label com.docker.compose.volume=pipeshub_hf_cache \
    "${PROJECT}_pipeshub_hf_cache" >/dev/null
  docker run --rm --label pipeshub.harness.owner=airgap-smoke -v "${PROJECT}_pipeshub_hf_cache:/root/.cache/huggingface" \
    -w /app/python "$APP_IMAGE" python -m app.modules.parsers.pdf.docling_models >"$OUT/docling-models.txt" 2>"$OUT/docling-models.log" \
    || die "could not fill Docling's models ($OUT/docling-models.log)"
  log "Docling's models in the install's Hugging Face volume: $(tr '\n' ' ' <"$OUT/docling-models.txt")"
fi

# --- 2. The install, with no route out ------------------------------------------------------------------------
started=$SECONDS
installer --yes --no-pull --skip-gvisor >"$OUT/install.log" 2>&1 || die "install.sh --yes --no-pull failed ($OUT/install.log)"
grep -q "Skipping image refresh; using locally cached images (--no-pull)" "$OUT/install.log" \
  || die "install.sh did not take the --no-pull path"
wait_ready 300 || die "the install came up but the harness is not available ($WORK/health.json)"
log "install.sh --no-pull: ready in $((SECONDS - started))s"
app="$(compose_cmd ps -q pipeshub-ai)"
[[ "$(docker inspect "$app" --format '{{.Config.Image}}')" == "$APP_IMAGE" ]] || die "the app does not run $APP_IMAGE"
for want in TELEMETRY_COLLECTION_FLAG=false HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HARNESS_ENGINE_RUNS=true; do
  docker exec "$app" printenv "${want%%=*}" | grep -qx "${want#*=}" || die "the app container does not run with $want"
done

# --- 3. No route out, then a turn, a job and a PDF ------------------------------------------------------------
python3 "$E2E_DIR/airgap_check.py" "$PROJECT" >"$OUT/airgap-check.log" 2>&1 || { cat "$OUT/airgap-check.log"; die "the install has a route out"; }
cat "$OUT/airgap-check.log"

admin_email="airgap-$(rand 3)@example.com"
admin_password="Ag-$(rand 8)!x"
cat >"$RUN_DIR/stack.env" <<EOF
E2E_PROJECT=$PROJECT
E2E_APP_PORT=$PORT
E2E_LLM_PORT=$(grep -E '^AIRGAP_LLM_PORT=' "$WORK/airgap.env" | cut -d= -f2)
E2E_SAAS_PORT=$(grep -E '^AIRGAP_SAAS_PORT=' "$WORK/airgap.env" | cut -d= -f2)
E2E_LLM_CONTROL=$CONTROL
E2E_ADMIN_EMAIL=$admin_email
E2E_ADMIN_PASSWORD=$admin_password
EOF
seed() {
  E2E_API="http://127.0.0.1:$PORT" E2E_ADMIN_EMAIL="$admin_email" E2E_ADMIN_PASSWORD="$admin_password" \
    E2E_LLM_KEY="$LLM_KEY" E2E_STATE="$RUN_DIR/seed.json" python3 "$E2E_DIR/seed.py" "$1" >>"$OUT/seed.log" 2>&1 \
    || die "seed.py $1 failed ($OUT/seed.log)"
}
seed seed
seed durable
checks() { python3 "$CHECKS" "$RUN_DIR" "$@" | tee -a "$OUT/checks.log"; return "${PIPESTATUS[0]}"; }
checks turn || die "the install did not serve a scripted chat turn"
checks job || die "the install did not run an engine job"
if [[ "${AIRGAP_DOCLING:-1}" == "1" ]]; then
  checks docling || die "Docling could not parse a PDF with no route out"
fi

# --- 4. DR drill rehearsal (DR-DRILL.md, Compose) -------------------------------------------------------------
if [[ "${AIRGAP_DR:-}" == "1" ]]; then
  record="$OUT/dr-record.json"
  mkdir -p "$WORK/backups"
  checks record "$record" || die "the drill's conversation and job did not run"
  mongo_auth=(--username "$(env_val MONGO_USERNAME)" --password "$(env_val MONGO_PASSWORD)" --authenticationDatabase admin)
  t_backup=$(date +%s)
  compose_cmd exec -T mongodb mongodump --quiet --archive --gzip --oplog "${mongo_auth[@]}" >"$WORK/backups/drill.archive.gz" \
    || die "mongodump failed"
  # The blob copy is consistent with the dump only while nothing writes: the app is stopped for it.
  compose_cmd stop pipeshub-ai >/dev/null
  docker run --rm --network none --entrypoint tar -v "${PROJECT}_pipeshub_root_local:/src:ro" "$APP_IMAGE" \
    -C /src -czf - . >"$WORK/backups/blobs.tgz" || die "copying the blob volume failed"
  compose_cmd start pipeshub-ai >/dev/null
  wait_ready 300 || die "the app did not come back after the backup"
  log "backup: $(du -h "$WORK/backups/drill.archive.gz" | cut -f1) dump, $(du -h "$WORK/backups/blobs.tgz" | cut -f1) blobs"
  checks after-backup "$record" || die "the turn after the backup did not run"

  t_disaster=$(date +%s)
  compose_cmd down >/dev/null 2>&1
  docker volume rm "${PROJECT}_mongodb_data" "${PROJECT}_pipeshub_root_local" >/dev/null \
    || die "could not remove the data volumes"
  log "disaster: the MongoDB volume and the blob store are gone"

  t_decide=$(date +%s)
  compose_cmd create >/dev/null 2>&1 || die "compose create failed"
  compose_cmd up -d mongodb >/dev/null 2>&1 || die "MongoDB did not start"
  mongo="$(compose_cmd ps -q mongodb)"
  deadline=$((SECONDS + 180))
  until [[ "$(docker inspect "$mongo" --format '{{.State.Health.Status}}')" == "healthy" ]]; do
    (( SECONDS < deadline )) || die "MongoDB did not become a writable primary"
    sleep 3
  done
  compose_cmd exec -T mongodb mongorestore --quiet --archive --gzip --oplogReplay "${mongo_auth[@]}" \
    <"$WORK/backups/drill.archive.gz" >"$OUT/mongorestore.log" 2>&1 || die "mongorestore failed ($OUT/mongorestore.log)"
  docker run --rm -i --network none --entrypoint tar -v "${PROJECT}_pipeshub_root_local:/dst" "$APP_IMAGE" \
    -C /dst -xzf - <"$WORK/backups/blobs.tgz" || die "putting the blob copy back failed"
  compose_cmd up -d >/dev/null 2>&1 || die "the stack did not start after the restore"
  wait_ready 600 || die "the harness did not come back after the restore"
  t_ready=$(date +%s)
  checks verify "$record" || die "the restore did not bring back what the backup held"
  t_recovered=$(date +%s)
  python3 - "$OUT/dr-drill.json" "$t_backup" "$t_disaster" "$t_decide" "$t_ready" "$t_recovered" <<'PY'
import json, sys
path, backup, disaster, decide, ready, recovered = sys.argv[1], *map(int, sys.argv[2:])
result = {"rpo_s": disaster - backup, "rto_s": recovered - decide, "ready_s": ready - decide,
          "t_backup": backup, "t_disaster": disaster, "t_decide": decide, "t_ready": ready, "t_recovered": recovered}
json.dump(result, open(path, "w"), indent=2)
print(f"airgap_install: DR drill ok: measured RPO {result['rpo_s']} s (the backup's age at the disaster), "
      f"RTO {result['rto_s']} s (restore decided to every check passed; harness available after {result['ready_s']} s)")
PY
fi

log "ok ($PROJECT: install.sh --no-pull with no route out, chat turn, engine job$([[ "${AIRGAP_DOCLING:-1}" == "1" ]] && echo ", Docling offline")$([[ "${AIRGAP_DR:-}" == "1" ]] && echo ", DR drill"))"
