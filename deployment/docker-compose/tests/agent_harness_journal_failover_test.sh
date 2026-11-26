#!/usr/bin/env bash
# J-7 (nightly): a 3-member MongoDB replica set in one container, the harness
# journal committing through a hard kill of the primary. Every commit the
# worker saw acknowledged (w:majority) must survive, exactly once.
#
#   deployment/docker-compose/tests/agent_harness_journal_failover_test.sh
#
# Needs Docker and `npm ci` in backend/nodejs/apps. Ports 27041-27043 must be
# free (override with AGENT_HARNESS_RS3_BASE_PORT).
set -euo pipefail

base_port=${AGENT_HARNESS_RS3_BASE_PORT:-27041}
ports=("${base_port}" "$((base_port + 1))" "$((base_port + 2))")
container=${AGENT_HARNESS_RS3_CONTAINER:-agent-harness-journal-rs3}
repo_dir=$(cd "$(dirname "$0")/../../.." && pwd)

cleanup() { docker rm -f "${container}" >/dev/null 2>&1 || true; }
trap cleanup EXIT
cleanup

publish=()
for port in "${ports[@]}"; do publish+=(-p "${port}:${port}"); done
docker run -d --name "${container}" "${publish[@]}" --entrypoint bash mongo:8 -c "
  for port in ${ports[*]}; do
    mkdir -p /data/rs\${port}
    mongod --replSet rs3 --port \${port} --bind_ip_all --dbpath /data/rs\${port} \
      --fork --logpath /data/rs\${port}.log
  done
  sleep infinity" >/dev/null

members=""
for index in 0 1 2; do
  members+="{_id:${index},host:'localhost:${ports[${index}]}'},"
done
for _ in $(seq 1 60); do
  if docker exec "${container}" mongosh --quiet --port "${ports[0]}" --eval "
      try { rs.status().ok } catch (e) {
        rs.initiate({_id:'rs3', members:[${members}],
          settings:{electionTimeoutMillis:2000, heartbeatIntervalMillis:500}}).ok
      }" >/dev/null 2>&1; then
    break
  fi
  sleep 1
done
for _ in $(seq 1 60); do
  primary=$(docker exec "${container}" mongosh --quiet --port "${ports[0]}" \
    --eval 'db.hello().primary || ""' 2>/dev/null || true)
  [[ -n "${primary}" ]] && break
  sleep 1
done
echo "replica set rs3 primary: ${primary}"

hosts=$(printf 'localhost:%s,' "${ports[@]}")
cd "${repo_dir}/backend/nodejs/apps"
MONGO_RS3_IT_URI="mongodb://${hosts%,}/?replicaSet=rs3" \
MONGO_RS3_CONTAINER="${container}" \
TS_NODE_PROJECT=tsconfig.test.json \
  npx mocha --no-config \
    --require reflect-metadata \
    --require ts-node/register \
    --timeout 300000 \
    --extension ts \
    --exit \
    tests/modules/agent-harness/journal-failover.rs3.itest.ts
