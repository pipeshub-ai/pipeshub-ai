#!/usr/bin/env bash
# ==============================================================================
# SEC-D-06 lane probe: every bundled store refuses a client without its credential.
# Needs Docker (run through $PXH_LANE when set). Starts only the stores, from the
# real docker-compose.yml, in its own pxh- project, and removes it (volumes too) on exit.
#
#   redis   anonymous PING refused (NOAUTH); the healthcheck and `compose exec redis
#           redis-cli` still sign in (REDISCLI_AUTH)
#   etcd    plain HTTP and TLS without a client certificate refused; the app's and the
#           sandbox plane's certificates work, each from its own volume as uid 1000;
#           an etcd volume written by the old plain-HTTP etcd starts under TLS
#   kafka   a client without SASL gets no metadata; the SCRAM login produces and
#           consumes; ZooKeeper is not on the app's network
#
# Env: STORE_AUTH_SITE_PACKAGES (Python site-packages with aiokafka and etcd3; default the
# lane venv), STORE_AUTH_ONLY=redis|etcd|kafka (one store), STORE_AUTH_PROJECT.
#
# Run: ${PXH_LANE:-} bash deployment/docker-compose/tests/store_auth_test.sh
# ==============================================================================
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COMPOSE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_ROOT="$(cd "$COMPOSE_DIR/../.." && pwd)"
PROJECT="${STORE_AUTH_PROJECT:-pxh-storeauth-$$}"
SITE="${STORE_AUTH_SITE_PACKAGES:-$REPO_ROOT/backend/python/venv/lib/python3.12/site-packages}"
PY_IMAGE="python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f"
ONLY="${STORE_AUTH_ONLY:-}"

WORK="$(mktemp -d "${TMPDIR:-/tmp}/storeauth.XXXXXX")"
PASS=0; FAIL=0
pass() { printf "  ok   - %s\n" "$1"; PASS=$((PASS + 1)); }
fail() { printf "  FAIL - %s\n" "$1"; FAIL=$((FAIL + 1)); }
expect() { if eval "$2"; then pass "$1"; else fail "$1"; fi; }
rand() { LC_ALL=C tr -dc 'a-f0-9' </dev/urandom | head -c "$1"; }

cat >"$WORK/.env" <<EOF
COMPOSE_PROJECT_NAME=$PROJECT
REDIS_PASSWORD=$(rand 32)
QDRANT_API_KEY=$(rand 32)
MONGO_PASSWORD=$(rand 32)
KAFKA_PASSWORD=$(rand 32)
SECRET_KEY=$(rand 64)
EOF
# etcd-tls runs the app image in production; here the lane's Python with this tree, as run.sh runs the sandbox plane.
cat >"$WORK/override.yml" <<EOF
services:
  etcd-tls:
    image: $PY_IMAGE
    entrypoint: ["python", "/e2e/pyrun.py", "app.utils.internal_tls", "/tls"]
    volumes:
      - $REPO_ROOT/backend/python:/app/python:ro
      - $SITE:/site:ro
      - $REPO_ROOT/deployment/e2e:/e2e:ro
EOF

compose() {
  docker compose --env-file "$WORK/.env" -p "$PROJECT" -f "$COMPOSE_DIR/docker-compose.yml" -f "$WORK/override.yml" \
    --profile kv-etcd --profile broker-kafka "$@"
}
cleanup() { compose down -v --remove-orphans >/dev/null 2>&1; rm -rf "$WORK"; }
trap cleanup EXIT

wait_healthy() { # service, seconds
  local id state i
  for ((i = 0; i < $2; i++)); do
    id="$(compose ps -q "$1" 2>/dev/null)"
    state="$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' "$id" 2>/dev/null)"
    [[ "$state" == "healthy" ]] && return 0
    sleep 1
  done
  return 1
}

# A one-off Python client on a project network, as uid 1000, with the given volume mounted at /tls.
client() { # network, volume (or -), python code
  local mount=()
  [[ "$2" != "-" ]] && mount=(-v "${PROJECT}_$2:/tls:ro")
  docker run --rm --network "${PROJECT}_$1" --user 1000:1000 "${mount[@]}" \
    -v "$SITE:/site:ro" -e HOME=/tmp "$PY_IMAGE" python -c "import site; site.addsitedir('/site')
$3" 2>&1
}

if [[ -z "$ONLY" || "$ONLY" == redis ]]; then
  echo "== redis =="
  compose up -d redis >"$WORK/up.log" 2>&1 || { cat "$WORK/up.log"; fail "redis starts"; }
  expect "redis becomes healthy through its authenticated healthcheck" 'wait_healthy redis 60'
  anon="$(client pipeshub - 'import socket; s=socket.create_connection(("redis",6379),5); s.sendall(b"PING\r\n"); print(s.recv(200).decode())')"
  expect "an anonymous PING is refused with NOAUTH" '[[ "$anon" == -NOAUTH* ]]'
  expect "compose exec redis redis-cli signs in without the password on the command line" \
    '[[ "$(compose exec -T redis redis-cli ping 2>/dev/null)" == PONG* ]]'
  expect "the password is not in the container's command" \
    '! docker inspect -f "{{json .Config.Cmd}}" "$(compose ps -q redis)" | grep -q "$(grep ^REDIS_PASSWORD= "$WORK/.env" | cut -d= -f2)"'
fi

if [[ -z "$ONLY" || "$ONLY" == etcd ]]; then
  echo "== etcd: an install upgraded from plain HTTP =="
  # The volume an older etcd wrote, with a key in it.
  docker volume create "${PROJECT}_etcd_data" >/dev/null
  docker run -d --name "${PROJECT}-old-etcd" --network none -v "${PROJECT}_etcd_data:/etcd-data" quay.io/coreos/etcd:v3.5.17 \
    etcd --name etcd-node --data-dir /etcd-data --listen-client-urls http://127.0.0.1:2379 \
    --advertise-client-urls http://etcd:2379 --listen-peer-urls http://127.0.0.1:2380 \
    --initial-advertise-peer-urls http://etcd:2380 --initial-cluster etcd-node=http://etcd:2380 >/dev/null
  for _ in $(seq 1 30); do docker exec "${PROJECT}-old-etcd" etcdctl put /upgrade/kept yes >/dev/null 2>&1 && break; sleep 1; done
  docker rm -f "${PROJECT}-old-etcd" >/dev/null
  compose up -d etcd >"$WORK/up.log" 2>&1 || { cat "$WORK/up.log"; fail "etcd starts"; }
  expect "etcd-tls completes and etcd becomes healthy under TLS" 'wait_healthy etcd 90'
  expect "the key the plain-HTTP etcd wrote is still there" \
    '[[ "$(compose exec -T etcd etcdctl --endpoints=https://localhost:2379 --cacert=/tls/ca.crt --cert=/tls/tls.crt --key=/tls/tls.key get /upgrade/kept --print-value-only 2>/dev/null)" == yes* ]]'
  plain="$(client pipeshub - 'import urllib.request
try:
    urllib.request.urlopen("http://etcd:2379/health", timeout=5); print("answered")
except Exception as e: print("refused", type(e).__name__)')"
  expect "plain HTTP is refused" '[[ "$plain" == refused* ]]'
  insecure="$(client pipeshub - 'import etcd3
try:
    etcd3.client(host="etcd", port=2379, timeout=5).get("/upgrade/kept"); print("answered")
except Exception as e: print("refused", type(e).__name__)')"
  expect "plaintext gRPC is refused" '[[ "$insecure" == *refused* && "$insecure" != *answered* ]]'
  nocert="$(client pipeshub etcd_tls_pipeshub_ai 'import etcd3
try:
    etcd3.client(host="etcd", port=2379, ca_cert="/tls/ca.crt", timeout=5).get("/upgrade/kept"); print("answered")
except Exception as e: print("refused", type(e).__name__)')"
  expect "TLS without a client certificate is refused" '[[ "$nocert" == *refused* && "$nocert" != *answered* ]]'
  for volume in etcd_tls_pipeshub_ai etcd_tls_egress_proxy etcd_tls_sandbox_broker; do
    got="$(client pipeshub "$volume" 'import etcd3
c = etcd3.client(host="etcd", port=2379, ca_cert="/tls/ca.crt", cert_cert="/tls/tls.crt", cert_key="/tls/tls.key", timeout=5)
print(c.get("/upgrade/kept")[0].decode())')"
    expect "$volume: uid 1000 reads its own key and signs in" '[[ "$got" == yes* ]]'
  done
  sandboxkv="$(client sandbox_kv etcd_tls_egress_proxy 'import etcd3
c = etcd3.client(host="etcd", port=2379, ca_cert="/tls/ca.crt", cert_cert="/tls/tls.crt", cert_key="/tls/tls.key", timeout=5)
print(c.get("/upgrade/kept")[0].decode())')"
  expect "the egress proxy's certificate works on sandbox_kv" '[[ "$sandboxkv" == yes* ]]'
  expect "the CA's key is in no consumer's volume" \
    '! docker run --rm -v "${PROJECT}_etcd_tls_pipeshub_ai:/v:ro" "$PY_IMAGE" test -e /v/ca.key'
  first="$(docker run --rm -v "${PROJECT}_etcd_tls_pipeshub_ai:/v:ro" "$PY_IMAGE" sha256sum /v/tls.crt)"
  compose up -d --force-recreate etcd-tls >/dev/null 2>&1
  for _ in $(seq 1 30); do [[ "$(docker inspect -f '{{.State.Status}}' "$(compose ps -aq etcd-tls)" 2>/dev/null)" == exited ]] && break; sleep 1; done
  expect "a second run keeps current certificates" \
    '[[ "$(docker run --rm -v "${PROJECT}_etcd_tls_pipeshub_ai:/v:ro" "$PY_IMAGE" sha256sum /v/tls.crt)" == "$first" ]]'
fi

if [[ -z "$ONLY" || "$ONLY" == kafka ]]; then
  echo "== kafka =="
  compose up -d kafka-1 >"$WORK/up.log" 2>&1 || { cat "$WORK/up.log"; fail "kafka starts"; }
  expect "kafka-init writes the login and kafka-1 becomes healthy through its SASL healthcheck" 'wait_healthy kafka-1 180'
  password="$(grep ^KAFKA_PASSWORD= "$WORK/.env" | cut -d= -f2)"
  scram="$(client pipeshub - "import asyncio
from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
LOGIN = dict(security_protocol='SASL_PLAINTEXT', sasl_mechanism='SCRAM-SHA-512', sasl_plain_username='pipeshub', sasl_plain_password='$password')
async def main():
    p = AIOKafkaProducer(bootstrap_servers='kafka-1:9092', **LOGIN); await p.start()
    await p.send_and_wait('store-auth', b'ok'); await p.stop()
    c = AIOKafkaConsumer('store-auth', bootstrap_servers='kafka-1:9092', auto_offset_reset='earliest', group_id='t', **LOGIN)
    await c.start(); m = await asyncio.wait_for(c.getone(), 30); await c.stop(); print(m.value.decode())
asyncio.run(main())")"
  expect "the SCRAM login produces and consumes" '[[ "$scram" == *ok ]]'
  anon="$(client pipeshub - "import asyncio
from aiokafka import AIOKafkaProducer
async def main():
    p = AIOKafkaProducer(bootstrap_servers='kafka-1:9092', request_timeout_ms=8000, metadata_max_age_ms=8000)
    try:
        await asyncio.wait_for(p.start(), 20); await asyncio.wait_for(p.send_and_wait('store-auth', b'x'), 20); print('answered')
    except Exception as e: print('refused', type(e).__name__)
asyncio.run(main())")"
  expect "a client without SASL gets nothing" '[[ "$anon" == *refused* ]]'
  wrong="$(client pipeshub - "import asyncio
from aiokafka import AIOKafkaProducer
async def main():
    p = AIOKafkaProducer(bootstrap_servers='kafka-1:9092', security_protocol='SASL_PLAINTEXT', sasl_mechanism='SCRAM-SHA-512', sasl_plain_username='pipeshub', sasl_plain_password='wrong')
    try:
        await asyncio.wait_for(p.start(), 20); print('answered')
    except Exception as e: print('refused', type(e).__name__)
asyncio.run(main())")"
  expect "a wrong password is refused" '[[ "$wrong" == *refused* ]]'
  zk="$(client pipeshub - 'import socket
try:
    socket.create_connection(("zookeeper", 2181), 5); print("reached")
except Exception as e: print("unreachable", type(e).__name__)')"
  expect "ZooKeeper is not reachable from the app's network" '[[ "$zk" == unreachable* ]]'
fi

printf "\n%d passed, %d failed\n" "$PASS" "$FAIL"
[[ "$FAIL" -eq 0 ]]
