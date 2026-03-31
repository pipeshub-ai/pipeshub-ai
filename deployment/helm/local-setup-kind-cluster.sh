#!/bin/bash
# Kind Kubernetes Cluster Setup for PipesHub-AI
# Sets up a local Kubernetes cluster and deploys PipesHub-AI
#
# Usage: ./local-setup-kind-cluster.sh

set -eo pipefail

echo "Kind Kubernetes Setup for PipesHub-AI"
echo "======================================"
echo ""

# Check if kind is installed
if ! command -v kind &> /dev/null; then
    echo "Kind not found, installing..."
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        if command -v brew &> /dev/null; then
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

# Check if kubectl is installed
if ! command -v kubectl &> /dev/null; then
    echo "kubectl not found, installing..."
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install kubectl
    else
        curl -Lo kubectl https://dl.k8s.io/release/v1.28.0/bin/linux/amd64/kubectl
        chmod +x kubectl
        sudo mv kubectl /usr/local/bin/
    fi
fi
echo "kubectl: installed"

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo "Error: Docker is not running. Please start Docker first."
    exit 1
fi
echo "Docker: running"
echo ""

# Handle existing cluster
if kind get clusters | grep -q "pipeshub"; then
    echo "Cluster 'pipeshub' already exists."
    read -p "Delete and recreate? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Deleting existing cluster..."
        kind delete cluster --name pipeshub
    else
        echo "Exiting."
        exit 0
    fi
fi

# Create cluster
echo ""
echo "Creating Kubernetes cluster..."
kind create cluster --name pipeshub --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
- role: worker
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
echo ""

# Wait for nodes
echo "Waiting for nodes..."
kubectl wait --for=condition=Ready nodes --all --timeout=120s
kubectl get nodes
echo ""

# Install Metrics Server
echo "Installing Metrics Server..."
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
  {
    "op": "add",
    "path": "/spec/template/spec/containers/0/args/-",
    "value": "--kubelet-insecure-tls"
  }
]'

kubectl wait --for=condition=available --timeout=120s deployment/metrics-server -n kube-system
echo "Metrics Server ready."
echo ""

# Verify metrics
sleep 5
if kubectl top nodes &> /dev/null; then
    echo "Metrics collection working:"
    kubectl top nodes
else
    echo "Metrics still initializing (this is normal)."
fi
echo ""

# Deploy PipesHub-AI
echo "Deploying PipesHub-AI"
echo "====================="
echo ""

cd "$(dirname "$0")"

echo "Running helm install..."
helm install pipeshub-ai ./pipeshub-ai \
  --set secretKey="local-dev-secret-key-change-in-prod" \
  --set replicaCount=1 \
  --set resources.requests.cpu=100m \
  --set resources.requests.memory=256Mi \
  --set mongodb.auth.enabled=false \
  --set redis.auth.enabled=false \
  --set redis.architecture=standalone \
  --set redis.sentinel.enabled=false \
  --set redis.master.count=1 \
  --set autoscaling.enabled=false \
  --set neo4j.auth.password="dev-password" \
  --set config.allowedOrigins="http://localhost:3001\,http://127.0.0.1:3001\,http://localhost:3000\,http://127.0.0.1:3000" \
  --set podSecurityContext.runAsUser=0 \
  --set podSecurityContext.runAsNonRoot=false \
  --set zookeeper.replicaCount=1 \
  --set kafka.replicaCount=1 \
  --set kafka.config.offsetsTopicReplicationFactor=1 \
  --set kafka.config.transactionStateLogMinIsr=1 \
  --set kafka.config.transactionStateLogReplicationFactor=1 \
  --set kafka.config.defaultReplicationFactor=1 \
  --set kafka.config.minInsyncReplicas=1 \
  --set qdrant.replicaCount=1 \
  --set neo4j.replicaCount=1

echo ""
echo "Helm install complete."
echo ""

# Wait for pods
echo "Waiting for pods (this may take a few minutes)..."
kubectl wait --for=condition=Ready pods \
  -l "app.kubernetes.io/instance=pipeshub-ai" \
  --timeout=300s \
  --field-selector='status.phase=Running' 2>/dev/null || true

if kubectl wait --for=condition=available deployment/pipeshub-ai --timeout=600s 2>/dev/null; then
  echo "All pods ready."
else
  echo "Some pods still starting. This is normal for first deployment."
  echo "Check with: kubectl get pods"
fi
echo ""

# Status
echo "Deployment Status"
echo "================="
echo ""
echo "Pods:"
kubectl get pods -l app.kubernetes.io/name=pipeshub-ai
echo ""
echo "Services:"
kubectl get svc -l app.kubernetes.io/name=pipeshub-ai
echo ""

echo "Setup Complete"
echo "=============="
echo ""
echo "Access the application:"
echo "  kubectl port-forward svc/pipeshub-ai 3001:3001"
echo "  Open http://localhost:3001"
echo ""
echo "View logs:"
echo "  kubectl logs -l app.kubernetes.io/name=pipeshub-ai -f"
echo ""
echo "Delete cluster:"
echo "  kind delete cluster --name pipeshub"
