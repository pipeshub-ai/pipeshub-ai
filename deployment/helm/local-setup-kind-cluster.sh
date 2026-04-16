#!/usr/bin/env bash
# Kind Kubernetes Cluster Setup for PipesHub-AI
# Usage: ./local-setup-kind-cluster.sh

set -euo pipefail

CLUSTER_NAME="pipeshub"
KUBECTL_CONTEXT="kind-${CLUSTER_NAME}"
RELEASE_NAME="pipeshub-ai"
NAMESPACE="pipeshub-local"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHART_DIR="${SCRIPT_DIR}/pipeshub-ai"

echo "Kind Kubernetes Setup for PipesHub-AI"
echo "======================================"
echo ""

if ! command -v kind >/dev/null 2>&1; then
  echo "Kind not found, installing..."
  if [[ "${OSTYPE}" == darwin* ]]; then
    if command -v brew >/dev/null 2>&1; then
      brew install kind
    else
      echo "Error: Homebrew required. Install from https://brew.sh"
      exit 1
    fi
  else
    curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
    chmod +x ./kind
    sudo mv ./kind /usr/local/bin/kind
  fi
fi
echo "Kind: installed"

if ! command -v kubectl >/dev/null 2>&1; then
  echo "kubectl not found, installing..."
  if [[ "${OSTYPE}" == darwin* ]]; then
    if command -v brew >/dev/null 2>&1; then
      brew install kubectl
    else
      echo "Error: Homebrew required. Install from https://brew.sh"
      exit 1
    fi
  else
    curl -Lo kubectl https://dl.k8s.io/release/v1.28.0/bin/linux/amd64/kubectl
    chmod +x kubectl
    sudo mv kubectl /usr/local/bin/
  fi
fi
echo "kubectl: installed"

if ! command -v helm >/dev/null 2>&1; then
  echo "Error: helm is required. Install from https://helm.sh/docs/intro/install/"
  exit 1
fi
echo "helm: installed"

if ! docker info >/dev/null 2>&1; then
  echo "Error: Docker is not running. Please start Docker first."
  exit 1
fi
echo "Docker: running"
echo ""

if kind get clusters | grep -q "^${CLUSTER_NAME}$"; then
  echo "Cluster '${CLUSTER_NAME}' already exists, reusing it."
else
  echo "Creating Kubernetes cluster..."
  kind create cluster --name "${CLUSTER_NAME}" --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
- role: worker
- role: worker
kubeadmConfigPatches:
- |
  kind: InitConfiguration
  nodeRegistration:
    kubeletExtraArgs:
      node-labels: "ingress-ready=true"
EOF
  echo "Cluster created."
fi
echo ""

kubectl config use-context "${KUBECTL_CONTEXT}" >/dev/null

echo "Waiting for nodes..."
kubectl wait --for=condition=Ready nodes --all --timeout=180s
kubectl get nodes
echo ""

echo "Ensuring Metrics Server is installed..."
if ! kubectl get deployment metrics-server -n kube-system >/dev/null 2>&1; then
  kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
fi

if ! kubectl get deployment metrics-server -n kube-system -o jsonpath='{.spec.template.spec.containers[0].args}' | grep -q -- "--kubelet-insecure-tls"; then
  kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
    {
      "op": "add",
      "path": "/spec/template/spec/containers/0/args/-",
      "value": "--kubelet-insecure-tls"
    }
  ]'
fi

kubectl wait --for=condition=available --timeout=180s deployment/metrics-server -n kube-system
echo "Metrics Server ready."
echo ""

generate_hex() {
  local length="$1"
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex "$((length / 2))"
  else
    tr -dc 'a-f0-9' </dev/urandom | head -c "${length}"
  fi
}

SECRET_KEY="$(generate_hex 64)"
MONGO_ROOT_PASSWORD="$(generate_hex 24)"
MONGO_APP_PASSWORD="$(generate_hex 24)"
REDIS_PASSWORD="$(generate_hex 24)"
NEO4J_PASSWORD="$(generate_hex 24)"

echo "Building chart dependencies..."
helm dependency build "${CHART_DIR}"
echo ""

APP_IMAGE="pipeshubai/pipeshub-ai:latest"
IMAGE_PULL_POLICY="IfNotPresent"
echo "Ensuring app image is available locally..."
if ! docker image inspect "${APP_IMAGE}" >/dev/null 2>&1; then
  docker pull "${APP_IMAGE}"
fi
echo "Loading app image into kind nodes..."
# kind load uses `docker save -o $TMPDIR/images.tar`. On hosts where /tmp is tmpfs
# (most systemd distros default to ~half of RAM), a large image can exceed that
# budget and fail with "disk quota exceeded". Point TMPDIR at disk-backed storage.
KIND_LOAD_TMPDIR="${HOME}/.cache/pipeshub-kind"
mkdir -p "${KIND_LOAD_TMPDIR}"
if ! TMPDIR="${KIND_LOAD_TMPDIR}" kind load docker-image "${APP_IMAGE}" --name "${CLUSTER_NAME}"; then
  echo "Warning: failed to preload image into kind nodes."
  echo "Falling back to pulling image directly from registry in cluster."
  IMAGE_PULL_POLICY="Always"
fi
rm -rf "${KIND_LOAD_TMPDIR}"
echo ""

kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1 || kubectl create namespace "${NAMESPACE}" >/dev/null

EXISTING_STATUS="$(helm status "${RELEASE_NAME}" -n "${NAMESPACE}" 2>/dev/null | awk -F': ' '/^STATUS:/{print $2}')"
if [[ "${EXISTING_STATUS}" == pending-* ]] || [[ "${EXISTING_STATUS}" == uninstalling ]]; then
  echo "Found stuck Helm release in status '${EXISTING_STATUS}', cleaning it up..."
  helm uninstall "${RELEASE_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1 || true
  kubectl delete secret -n "${NAMESPACE}" -l "owner=helm,name=${RELEASE_NAME}" >/dev/null 2>&1 || true
  echo "Resetting namespace '${NAMESPACE}' to clear stale PVC state..."
  kubectl delete namespace "${NAMESPACE}" --ignore-not-found --wait=true --timeout=600s >/dev/null 2>&1 || true
  for _ in {1..60}; do
    if ! kubectl get namespace "${NAMESPACE}" >/dev/null 2>&1; then
      break
    fi
    sleep 2
  done
  kubectl create namespace "${NAMESPACE}" >/dev/null 2>&1 || true
fi

HELM_BASE_ARGS=(
  --namespace "${NAMESPACE}"
  --create-namespace
  --set autoscaling.enabled=false
  --set resources.requests.cpu=500m
  --set resources.requests.memory=1Gi
  --set resources.limits.cpu=2
  --set resources.limits.memory=6Gi
  --set image.pullPolicy="${IMAGE_PULL_POLICY}"
  --set persistence.size=2Gi
  --set secretKey="${SECRET_KEY}"
  --set mongodb.auth.enabled=true
  --set mongodb.auth.rootUser=root
  --set mongodb.auth.rootPassword="${MONGO_ROOT_PASSWORD}"
  --set "mongodb.auth.usernames[0]=pipeshub"
  --set "mongodb.auth.passwords[0]=${MONGO_APP_PASSWORD}"
  --set "mongodb.auth.databases[0]=pipeshub"
  --set redis.auth.enabled=true
  --set redis.auth.password="${REDIS_PASSWORD}"
  --set redis.architecture=standalone
  --set redis.sentinel.enabled=false
  --set neo4j.auth.password="${NEO4J_PASSWORD}"
  --set config.allowedOrigins="http://localhost:3001\,http://127.0.0.1:3001\,http://localhost:3000\,http://127.0.0.1:3000"
  --set podSecurityContext.runAsUser=0
  --set podSecurityContext.runAsNonRoot=false
  --set zookeeper.replicaCount=1
  --set zookeeper.podSecurityContext.fsGroup=1000
  --set kafka.replicaCount=1
  --set kafka.podSecurityContext.fsGroup=1000
  --set kafka.config.offsetsTopicReplicationFactor=1
  --set kafka.config.transactionStateLogMinIsr=1
  --set kafka.config.transactionStateLogReplicationFactor=1
  --set kafka.config.defaultReplicationFactor=1
  --set kafka.config.minInsyncReplicas=1
  --set qdrant.replicaCount=1
  --set neo4j.replicaCount=1
)

echo "Deploying infrastructure first (app replicas = 0)..."
helm upgrade --install "${RELEASE_NAME}" "${CHART_DIR}" \
  --wait \
  --timeout 30m \
  "${HELM_BASE_ARGS[@]}" \
  --set replicaCount=0

echo "Waiting for infrastructure services to become ready..."
kubectl rollout status deployment/"${RELEASE_NAME}"-mongodb -n "${NAMESPACE}" --timeout=15m
kubectl rollout status statefulset/"${RELEASE_NAME}"-redis-master -n "${NAMESPACE}" --timeout=15m
kubectl rollout status statefulset/"${RELEASE_NAME}"-kafka -n "${NAMESPACE}" --timeout=15m
kubectl rollout status statefulset/"${RELEASE_NAME}"-zookeeper -n "${NAMESPACE}" --timeout=15m
kubectl rollout status statefulset/"${RELEASE_NAME}"-neo4j -n "${NAMESPACE}" --timeout=15m
kubectl rollout status statefulset/"${RELEASE_NAME}"-qdrant -n "${NAMESPACE}" --timeout=15m

echo "Infrastructure is ready. Deploying app (replicas = 1)..."
kubectl delete pod -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=main" --ignore-not-found >/dev/null 2>&1 || true

echo "Waiting 45s for dependency stabilization..."
sleep 45

for attempt in 1 2 3; do
  helm upgrade --install "${RELEASE_NAME}" "${CHART_DIR}" \
    --timeout 35m \
    "${HELM_BASE_ARGS[@]}" \
    --set replicaCount=1

  if kubectl rollout status deployment/"${RELEASE_NAME}" -n "${NAMESPACE}" --timeout=20m; then
    break
  fi

  if [[ "${attempt}" -eq 3 ]]; then
    echo "Error: app deployment failed after retries."
    echo "Recent app pod logs:"
    kubectl logs -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE_NAME},app.kubernetes.io/component=main" --tail=120 || true
    exit 1
  fi

  echo "App deployment attempt ${attempt} failed. Restarting app pod and retrying in 30 seconds..."
  kubectl rollout restart deployment/"${RELEASE_NAME}" -n "${NAMESPACE}" >/dev/null 2>&1 || true
  sleep 30
done

echo ""
echo "Helm deploy complete."
echo ""

echo "Verifying pod readiness..."
kubectl wait --namespace "${NAMESPACE}" --for=condition=Ready pods \
  -l "app.kubernetes.io/instance=${RELEASE_NAME}" --timeout=1200s
echo "All release pods are Ready."
echo ""

echo "Deployment Status"
echo "================="
echo ""
echo "Pods:"
kubectl get pods -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE_NAME}"
echo ""
echo "Services:"
kubectl get svc -n "${NAMESPACE}" -l "app.kubernetes.io/instance=${RELEASE_NAME}"
echo ""

if command -v curl >/dev/null 2>&1; then
  echo "Running API health check..."
  kubectl port-forward -n "${NAMESPACE}" "svc/${RELEASE_NAME}" 3001:3001 >/tmp/pipeshub-port-forward.log 2>&1 &
  PORT_FORWARD_PID=$!
  sleep 5
  if curl -fsS http://127.0.0.1:3001/api/v1/health >/dev/null; then
    echo "Health endpoint is responding: /api/v1/health"
  else
    echo "Warning: health endpoint did not respond yet."
    echo "Try again with: kubectl port-forward -n ${NAMESPACE} svc/${RELEASE_NAME} 3001:3001"
  fi
  kill "${PORT_FORWARD_PID}" >/dev/null 2>&1 || true
  wait "${PORT_FORWARD_PID}" 2>/dev/null || true
  echo ""
fi

echo "Setup Complete"
echo "=============="
echo ""
echo "Access the application:"
echo "  kubectl port-forward -n ${NAMESPACE} svc/${RELEASE_NAME} 3001:3001"
echo "  Open http://localhost:3001"
echo ""
echo "View logs:"
echo "  kubectl logs -n ${NAMESPACE} -l app.kubernetes.io/instance=${RELEASE_NAME} -f"
echo ""
echo "Delete release:"
echo "  helm uninstall ${RELEASE_NAME} -n ${NAMESPACE}"
echo "Delete cluster:"
echo "  kind delete cluster --name ${CLUSTER_NAME}"
