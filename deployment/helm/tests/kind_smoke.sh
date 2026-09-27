#!/usr/bin/env bash
# ==============================================================================
# Install the chart into a throwaway kind cluster and check the app comes up.
# ==============================================================================
# check_chart.sh proves the manifests are well formed. This proves they run:
# the published image starts against the databases the chart deploys, every
# core service reports healthy, and the UI loads.
#
# Unlike local-setup-kind-cluster.sh (the interactive developer setup), this
# uses a single node and lets it pull the image itself, so the ~9 GB app image
# exists once rather than once per node, which is what fits a CI runner. It
# fails instead of warning, and always deletes its cluster.
#
#   bash deployment/helm/tests/kind_smoke.sh
#   SMOKE_VARIANT=arangodb-redis bash deployment/helm/tests/kind_smoke.sh
#
# Optional env:
#   SMOKE_VARIANT   neo4j-kafka (default) | arangodb-redis | eks
#                   eks uses values-eks.yaml on a 3-worker cluster, scaled down

#   APP_IMAGE       image to install (default pipeshubai/pipeshub-ai:slim)
#   KIND_CLUSTER    cluster name (default pipeshub-helm-smoke-<pid>)
#   SMOKE_PORT      local port for the port-forward (default 13001)
#   HELM_TIMEOUT    how long helm waits for every pod to be Ready (default 30m)
#   SMOKE_KEEP=1    leave the cluster running
#   SMOKE_DIAG      directory to write pod state and logs to on failure
# ==============================================================================
set -euo pipefail

CHART="$(cd "$(dirname "${BASH_SOURCE[0]}")/../pipeshub-ai" && pwd)"
VARIANT="${SMOKE_VARIANT:-neo4j-kafka}"
APP_IMAGE="${APP_IMAGE:-pipeshubai/pipeshub-ai:slim}"
CLUSTER="${KIND_CLUSTER:-pipeshub-helm-smoke-$$}"
PORT="${SMOKE_PORT:-13001}"
HELM_TIMEOUT="${HELM_TIMEOUT:-30m}"
DIAG="${SMOKE_DIAG:-}"
NAMESPACE="pipeshub-smoke"
# Matches the chart name, so the app Service is simply "pipeshub-ai".
RELEASE="pipeshub-ai"
LOG_PREFIX="kind_smoke"

die() { echo "${LOG_PREFIX}: $*" >&2; exit 1; }

for tool in kind kubectl helm docker curl python3 openssl; do
  command -v "$tool" >/dev/null 2>&1 || die "$tool is required"
done
docker info >/dev/null 2>&1 || die "docker daemon is not running"

VALUES_FILE="$CHART/values-local.yaml"
KIND_NODES=1
case "$VARIANT" in
  neo4j-kafka)
    VARIANT_ARGS=()
    ;;
  arangodb-redis)
    VARIANT_ARGS=(
      --set neo4j.enabled=false
      --set arango.enabled=true
      --set "arango.auth.rootPassword=$(openssl rand -hex 12)"
      --set messageBroker.type=redis
    )
    ;;
  eks)
    VARIANT_ARGS=(-f "$CHART/../tests/values-eks-kind.yaml")
    VALUES_FILE="$CHART/values-eks.yaml"
    KIND_NODES=3
    ;;
  *)
    die "SMOKE_VARIANT must be neo4j-kafka, arangodb-redis, or eks (got ${VARIANT})"
    ;;
esac

WORK="$(mktemp -d "${TMPDIR:-/tmp}/pipeshub-kind-smoke.XXXXXX")"
PF_PID=""

dump_failure() {
  echo "----- pods -----" >&2
  kubectl get pods -n "$NAMESPACE" -o wide >&2 || true
  echo "----- app logs (tail 120) -----" >&2
  kubectl logs -n "$NAMESPACE" "deploy/${RELEASE}" --tail 120 >&2 || true
  if [[ -n "$DIAG" ]]; then
    mkdir -p "$DIAG"
    kubectl get pods,svc,pvc,events -n "$NAMESPACE" -o wide >"$DIAG/state.txt" 2>&1 || true
    kubectl describe pods -n "$NAMESPACE" >"$DIAG/describe-pods.txt" 2>&1 || true
    for pod in $(kubectl get pods -n "$NAMESPACE" -o name 2>/dev/null); do
      kubectl logs -n "$NAMESPACE" "$pod" --all-containers --previous >"$DIAG/${pod#pod/}.previous.log" 2>/dev/null || true
      kubectl logs -n "$NAMESPACE" "$pod" --all-containers >"$DIAG/${pod#pod/}.log" 2>&1 || true
    done
    if [[ -f "$WORK/health.json" ]]; then cp "$WORK/health.json" "$DIAG/" || true; fi
  fi
}

cleanup() {
  local ec=$?
  if [[ -n "$PF_PID" ]]; then kill "$PF_PID" >/dev/null 2>&1 || true; fi
  if [[ "$ec" -ne 0 ]]; then
    dump_failure || true
  fi
  if [[ "${SMOKE_KEEP:-}" == "1" ]]; then
    echo "${LOG_PREFIX}: SMOKE_KEEP=1, cluster ${CLUSTER} left running"
  else
    kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
  fi
  rm -rf "$WORK"
  exit "$ec"
}
trap cleanup EXIT

echo "${LOG_PREFIX}: variant=${VARIANT} image=${APP_IMAGE} cluster=${CLUSTER}"

if [[ "$KIND_NODES" -gt 1 ]]; then
  {
    echo "kind: Cluster"
    echo "apiVersion: kind.x-k8s.io/v1alpha4"
    echo "nodes:"
    echo "- role: control-plane"
    for ((n = 0; n < KIND_NODES; n++)); do echo "- role: worker"; done
  } >"$WORK/kind.yaml"
  kind create cluster --name "$CLUSTER" --config "$WORK/kind.yaml" --wait 180s
else
  kind create cluster --name "$CLUSTER" --wait 180s
fi
kubectl config use-context "kind-${CLUSTER}" >/dev/null

helm dependency build "$CHART" >/dev/null

# values-local.yaml is the preset the README gives for kind. The overrides only
# supply secrets and trim CPU requests so every pod schedules on one 4-CPU node.
echo "${LOG_PREFIX}: installing (helm waits up to ${HELM_TIMEOUT} for every pod to be Ready)"
if ! helm install "$RELEASE" "$CHART" \
  --namespace "$NAMESPACE" --create-namespace \
  -f "$VALUES_FILE" \
  --set image.repository="${APP_IMAGE%:*}" \
  --set image.tag="${APP_IMAGE##*:}" \
  --set secretKey="$(openssl rand -hex 32)" \
  --set mongodb.auth.rootPassword="$(openssl rand -hex 12)" \
  --set 'mongodb.auth.usernames[0]=pipeshub' \
  --set "mongodb.auth.passwords[0]=$(openssl rand -hex 12)" \
  --set 'mongodb.auth.databases[0]=pipeshub' \
  --set redis.auth.password="$(openssl rand -hex 12)" \
  --set neo4j.auth.password="$(openssl rand -hex 12)" \
  --set neo4j.resources.requests.cpu=250m \
  --set qdrant.resources.requests.cpu=250m \
  --set arango.resources.requests.cpu=250m \
  "${VARIANT_ARGS[@]}" \
  --wait --timeout "$HELM_TIMEOUT"; then
  die "helm install did not become Ready within ${HELM_TIMEOUT}"
fi

kubectl get pods -n "$NAMESPACE"

# A few restarts are normal while the databases come up; a pod that is still
# restarting after helm reported Ready is not.
RESTARTS_BEFORE="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/component=main \
  -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{"\n"}{end}' | head -1)"

kubectl port-forward -n "$NAMESPACE" "svc/${RELEASE}" "${PORT}:3001" >"$WORK/port-forward.log" 2>&1 &
PF_PID=$!

# Readiness only checks the Node API; the Python services report through
# /health/services and can lag behind it on a cold start.
HEALTH_URL="http://127.0.0.1:${PORT}/api/v1/health/services"
deadline=$((SECONDS + 600))
healthy=0
while (( SECONDS < deadline )); do
  if curl --connect-timeout 5 --max-time 30 -sf "$HEALTH_URL" -o "$WORK/health.json" \
    && python3 - "$WORK/health.json" <<'PY'
import json, sys
services = (json.load(open(sys.argv[1], encoding="utf-8")).get("services") or {})
required = ("query", "connector", "indexing", "docling")
missing = [f"{k}={services.get(k)!r}" for k in required if services.get(k) != "healthy"]
if missing:
    print("kind_smoke: waiting on " + ", ".join(missing), file=sys.stderr)
    sys.exit(1)
PY
  then
    healthy=1
    break
  fi
  sleep 15
done
[[ "$healthy" -eq 1 ]] || die "core services did not all report healthy at ${HEALTH_URL}"

UI_CODE="$(curl --connect-timeout 5 --max-time 30 -s -o /dev/null -w '%{http_code}' \
  "http://127.0.0.1:${PORT}/" || echo fail)"
[[ "$UI_CODE" == "200" ]] || die "UI returned HTTP ${UI_CODE} (expected 200)"

RESTARTS_AFTER="$(kubectl get pods -n "$NAMESPACE" -l app.kubernetes.io/component=main \
  -o jsonpath='{range .items[*]}{.status.containerStatuses[0].restartCount}{"\n"}{end}' | head -1)"
if [[ "${RESTARTS_AFTER:-0}" != "${RESTARTS_BEFORE:-0}" ]]; then
  die "app restarted after it was Ready (${RESTARTS_BEFORE} -> ${RESTARTS_AFTER} restarts)"
fi

# embedding is reported but not required, matching the installer and the
# compose healthcheck: on slim it downloads its model on first use, and it is
# unused when a hosted embedding model is configured.
echo "${LOG_PREFIX}: services $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["services"])' "$WORK/health.json")"

if [[ "$VARIANT" == "eks" ]]; then
  MONGO_POD="${RELEASE}-mongodb-0"
  QDRANT_POD="${RELEASE}-qdrant-0"
  REDIS_POD="${RELEASE}-redis-master-0"
  kubectl exec -n "$NAMESPACE" "$MONGO_POD" -- mongosh --quiet --username root \
    --password "$(kubectl get secret -n "$NAMESPACE" "${RELEASE}-secrets" -o jsonpath='{.data.mongodb-password}' | base64 -d)" \
    --authenticationDatabase admin --eval 'const s=rs.status(); if (s.members.filter(m => m.health===1).length < 3) quit(1)' \
    || die "mongo replica set does not have 3 healthy members"
  kubectl exec -n "$NAMESPACE" "deploy/${RELEASE}" -- python -c '
import json, os, urllib.request
key = os.environ["QDRANT_API_KEY"]
req = urllib.request.Request("http://pipeshub-ai-qdrant:6333/cluster", headers={"api-key": key})
body = json.load(urllib.request.urlopen(req, timeout=30))
peers = (body.get("result") or {}).get("peers") or {}
print("peers", len(peers))
if len(peers) < 3:
    raise SystemExit(1)
' || die "qdrant does not have 3 peers"
  kubectl exec -n "$NAMESPACE" "deploy/${RELEASE}" -- python -c '
import json, os, urllib.request
key = os.environ["QDRANT_API_KEY"]
create = urllib.request.Request(
    "http://pipeshub-ai-qdrant:6333/collections/kind_smoke",
    data=b"{\"vectors\":{\"size\":4,\"distance\":\"Cosine\"}}",
    headers={"api-key": key, "Content-Type": "application/json"},
    method="PUT",
)
urllib.request.urlopen(create, timeout=30).read()
req = urllib.request.Request("http://pipeshub-ai-qdrant:6333/collections/kind_smoke", headers={"api-key": key})
params = ((json.load(urllib.request.urlopen(req, timeout=30)).get("result") or {}).get("config") or {}).get("params") or {}
print(params.get("replication_factor"))
if params.get("replication_factor") != 2:
    raise SystemExit(1)
' || die "qdrant replication_factor is not 2"
  kubectl exec -n "$NAMESPACE" "$REDIS_POD" -- redis-cli INFO replication | grep -q 'connected_slaves:1' \
    || die "redis replica is not connected"
  kubectl exec -n "$NAMESPACE" "$REDIS_POD" -- redis-cli CONFIG GET maxmemory-policy | grep -q noeviction \
    || die "redis maxmemory-policy is not noeviction"
  kubectl exec -i -n "$NAMESPACE" "deploy/${RELEASE}" -- python - <<'PY' || die "sandbox run_code via DinD failed"
import os
os.environ.setdefault("DOCKER_HOST", "tcp://127.0.0.1:2375")
import docker
client = docker.from_env()
image = os.environ["SANDBOX_DOCKER_IMAGE"]
client.images.pull(image)
out = client.containers.run(image, ["python", "-c", "print(1+1)"], network_mode="none", remove=True)
text = out.decode() if isinstance(out, bytes) else str(out)
if "2" not in text:
    raise SystemExit(text)
print(text)
PY
fi

echo "${LOG_PREFIX}: ok (variant=${VARIANT}, core services healthy, UI 200)"
