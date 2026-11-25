#!/usr/bin/env bash
# ==============================================================================
# D1 functional smoke (DEV-ONLY): the real sandbox-broker and egress-proxy
# services of docker-compose.yml on this host's Docker daemon, without building
# the app image. They run from python:3.12-slim with this tree's backend/python
# and a dev venv's site-packages mounted read-only
# (tests/docker-compose.sandbox-smoke.yml), next to Redis, a fake model gateway,
# a seeding job standing in for Node and a client standing in for the app.
#
#   SM-1  broker and proxy become healthy
#   SM-2  a sandbox acquired through the broker with an allowlist calls
#         model-gateway through the proxy: the gateway gets the injected token,
#         the sandbox sees only its placeholder; the proxy fetched the token
#         from (a stand-in for) Node's gateway-token route with its own credential,
#         read from the document Node sealed for it (ADR-KEY-02)
#   SM-3  no direct route out of the sandbox, none to the proxy's control port;
#         a host off the allowlist is refused
#   SM-4  docker inspect: the socket only in the broker, the proxy only on the
#         two internal networks, nothing published, neither holding SECRET_KEY
#         or the Redis password (each logs in as its own ACL user)
#   SM-5  teardown leaves nothing of the project or of the broker's sandboxes
#
# Needs: Docker with Compose v2, python:3.12-slim, the sandbox image
# (SANDBOX_DOCKER_IMAGE, default pxh-sandbox:x1), and SMOKE_SITE_PACKAGES (a
# Python 3.12 venv with backend/python's dependencies).
#
#   SMOKE_SITE_PACKAGES=/path/to/venv/lib/python3.12/site-packages \
#     bash deployment/docker-compose/tests/sandbox_plane_smoke.sh
# ==============================================================================
# ok/bad never fail, so `[[ … ]] && ok … || bad …` is an if/else here.
# shellcheck disable=SC2015
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
PROJECT="${SMOKE_PROJECT:-pxh-d1}"
export SMOKE_SOURCE="${SMOKE_SOURCE:-$(cd "$COMPOSE_DIR/../../backend/python" && pwd)}"
export SMOKE_SITE_PACKAGES="${SMOKE_SITE_PACKAGES:?set SMOKE_SITE_PACKAGES to a Python 3.12 venv site-packages}"
export SANDBOX_DOCKER_IMAGE="${SANDBOX_DOCKER_IMAGE:-pxh-sandbox:x1}"

WORK="$(mktemp -d)"
ENV_FILE="$WORK/smoke.env"
{
  echo "COMPOSE_PROFILES=sandbox,sandbox-broker"
  echo "SANDBOX_MODE=broker"
  echo "SECRET_KEY=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "REDIS_PASSWORD=$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  # SEC-D-06: Compose renders only with every store's credential.
  for name in QDRANT_API_KEY MONGO_PASSWORD KAFKA_PASSWORD; do echo "$name=$(head -c 12 /dev/urandom | od -An -tx1 | tr -d ' \n')"; done
  echo "SMOKE_WORKER_CREDENTIAL=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "SMOKE_PROXY_CREDENTIAL=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "SMOKE_BROKER_CREDENTIAL=$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  # ADR-KEY-02: each component's own key; the broker and the proxy never hold SECRET_KEY or the Redis password.
  echo "EGRESS_PROXY_COMPONENT_KEY=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "SANDBOX_BROKER_COMPONENT_KEY=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  echo "SANDBOX_DOCKER_IMAGE=${SANDBOX_DOCKER_IMAGE}"
  # ADR-OPS-02: the broker runs as uid 1000 and reaches the socket through its group, as install.sh writes it.
  echo "DOCKER_GID=$(stat -c %g /var/run/docker.sock)"
} >"$ENV_FILE"

# A --profile flag replaces COMPOSE_PROFILES, so the sandbox profile is named
# again wherever the jobs' profile is; otherwise `down` would keep the broker.
compose() {
  docker compose -f "$COMPOSE_DIR/docker-compose.yml" -f "$SCRIPT_DIR/docker-compose.sandbox-smoke.yml" \
    -p "$PROJECT" --env-file "$ENV_FILE" --profile sandbox --profile sandbox-broker "$@"
}

FAILED=0
ok()  { printf '  ok   - %s\n' "$1"; }
bad() { printf '  FAIL - %s\n' "$1"; FAILED=1; }

teardown() {
  compose --profile smoke-jobs down -v --remove-orphans >/dev/null 2>&1 || true
  rm -rf "$WORK"
}
trap teardown EXIT

echo "== the broker and the proxy start before Node has written anything (project ${PROJECT}) =="
compose up -d --wait --wait-timeout 120 redis pxh-gateway >/dev/null || { bad "redis did not become healthy"; exit 1; }
compose up -d sandbox-broker egress-proxy >/dev/null || { bad "the broker and the proxy did not start"; exit 1; }

# ADR-KEY-02: each component logs in to Redis as its own ACL user and waits for the document Node seals for it, so
# neither is healthy until Node's writes land; they must come up from that state without a restart.
echo "== Node's startup writes (worker credential, sealed component documents, ACL users, /services/endpoints) =="
compose --profile smoke-jobs run --rm pxh-seed || { bad "seeding failed"; exit 1; }

echo "== SM-1 broker and proxy healthy =="
compose up -d --wait --wait-timeout 180 redis sandbox-broker egress-proxy pxh-gateway >/dev/null \
  && ok "redis, sandbox-broker, egress-proxy and the fake gateway are up and healthy" \
  || { bad "the stack did not become healthy"; compose logs --tail 40 sandbox-broker egress-proxy; exit 1; }

echo "== SM-2, SM-3 through the broker and the proxy =="
if compose --profile smoke-jobs run --rm pxh-client; then
  ok "client cases"
else
  bad "client cases"; compose logs --tail 60 sandbox-broker egress-proxy
fi
gateway_log="$(compose logs --no-log-prefix pxh-gateway 2>/dev/null)"
[[ "$gateway_log" == *'"auth": "Bearer pxt_'* ]] && ok "SM-2 the gateway received the proxy-injected token" \
  || bad "SM-2 the gateway received the proxy-injected token"
[[ "$gateway_log" != *"ph-d1-smoke-"* ]] && ok "SM-2 the gateway never saw a placeholder" \
  || bad "SM-2 the gateway never saw a placeholder"
[[ "$gateway_log" == *"TOKEN issued"* && "$gateway_log" != *"TOKEN refused"* ]] && ok "SM-2 the proxy got the token from Node with its own credential" \
  || bad "SM-2 the proxy got the token from Node with its own credential"

echo "== SM-4 docker inspect of the running project =="
holders=""
for cid in $(compose ps -q); do
  name="$(docker inspect --format '{{index .Config.Labels "com.docker.compose.service"}}' "$cid")"
  mounts="$(docker inspect --format '{{range .Mounts}}{{.Source}} {{end}}' "$cid")"
  [[ "$mounts" == *"/var/run/docker.sock"* ]] && holders="$holders $name"
  published="$(docker inspect --format '{{range $p, $b := .NetworkSettings.Ports}}{{if $b}}{{$p}} {{end}}{{end}}' "$cid")"
  [[ -z "$published" ]] || bad "SM-4 $name publishes $published"
done
[[ "$holders" == " sandbox-broker" ]] && ok "SM-4 only sandbox-broker mounts the Docker socket" \
  || bad "SM-4 socket holders:${holders:- none}"
# ADR-KEY-02: names only, never a value: neither component holds the root key or the default Redis login.
for service in sandbox-broker egress-proxy; do
  held="$(docker inspect --format '{{range .Config.Env}}{{println .}}{{end}}' "$(compose ps -q "$service")" \
    | grep -E '^(SECRET_KEY|SECRET_KEY_PREVIOUS|SECRET_KEY_NEXT|REDIS_PASSWORD)=.' | cut -d= -f1 | tr '\n' ' ' || true)"
  [[ -z "$held" ]] && ok "SM-4 $service holds no SECRET_KEY and no Redis password" || bad "SM-4 $service holds: $held"
done
proxy_networks="$(docker inspect --format '{{range $n, $_ := .NetworkSettings.Networks}}{{$n}} {{end}}' "$(compose ps -q egress-proxy)")"
[[ "$proxy_networks" == "${PROJECT}_sandbox_kv ${PROJECT}_sandbox_plane " ]] \
  && ok "SM-4 the proxy is on ${PROJECT}_sandbox_kv and ${PROJECT}_sandbox_plane only (sandbox network detached on release)" \
  || bad "SM-4 proxy networks: ${proxy_networks}"
for network in sandbox_plane sandbox_kv; do
  [[ "$(docker network inspect --format '{{.Internal}}' "${PROJECT}_${network}")" == "true" ]] \
    && ok "SM-4 ${network} is internal" || bad "SM-4 ${network} is not internal"
done

echo "== SM-5 teardown =="
leftover_sandboxes="$(docker ps -aq --filter label=smoke=pxh-d1)"
[[ -z "$leftover_sandboxes" ]] && ok "SM-5 the released sandbox is gone" || bad "SM-5 sandbox containers left: $leftover_sandboxes"
teardown
trap - EXIT
left="$(docker ps -aq --filter "label=com.docker.compose.project=${PROJECT}")$(docker network ls -q --filter "label=com.docker.compose.project=${PROJECT}")$(docker volume ls -q --filter "label=com.docker.compose.project=${PROJECT}")"
[[ -z "$left" ]] && ok "SM-5 no ${PROJECT} container, network or volume left" || bad "SM-5 left behind: $left"

if (( FAILED )); then echo "sandbox_plane_smoke: FAILED"; exit 1; fi
echo "sandbox_plane_smoke: ok"
