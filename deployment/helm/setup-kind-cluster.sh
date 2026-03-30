#!/bin/bash
# Kind Kubernetes Cluster Setup for PipesHub-AI
# Run this script to set up a local Kubernetes cluster and deploy PipesHub-AI

set -eo pipefail

echo "============================================"
echo "  Kind Kubernetes Setup for PipesHub-AI"
echo "============================================"
echo ""

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Check if kind is installed
if ! command -v kind &> /dev/null; then
    echo -e "${RED}❌ Kind is not installed${NC}"
    echo "Installing Kind..."
    
    # Detect OS
    if [[ "$OSTYPE" == "darwin"* ]]; then
        # macOS
        if command -v brew &> /dev/null; then
            brew install kind
        else
            echo "Please install Homebrew first: https://brew.sh"
            exit 1
        fi
    else
        # Linux
        curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
        chmod +x ./kind
        sudo mv ./kind /usr/local/bin/kind
    fi
fi

echo -e "${GREEN}✅ Kind is installed${NC}"

# Check if kubectl is installed
if ! command -v kubectl &> /dev/null; then
    echo -e "${RED}❌ kubectl is not installed${NC}"
    echo "Installing kubectl..."
    
    if [[ "$OSTYPE" == "darwin"* ]]; then
        brew install kubectl
    else
        curl -Lo kubectl https://dl.k8s.io/release/v1.28.0/bin/linux/amd64/kubectl
        chmod +x kubectl
        sudo mv kubectl /usr/local/bin/
    fi
fi

echo -e "${GREEN}✅ kubectl is installed${NC}"

# Check if Docker is running
if ! docker info &> /dev/null; then
    echo -e "${RED}❌ Docker is not running${NC}"
    echo "Please start Docker Desktop first"
    exit 1
fi

echo -e "${GREEN}✅ Docker is running${NC}"
echo ""

# Delete existing cluster if exists
if kind get clusters | grep -q "pipeshub"; then
    echo -e "${YELLOW}⚠️  Cluster 'pipeshub' already exists${NC}"
    read -p "Do you want to delete it and create a new one? (y/N): " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        echo "Deleting existing cluster..."
        kind delete cluster --name pipeshub
    else
        echo "Exiting..."
        exit 0
    fi
fi

# Create new cluster
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

echo -e "${GREEN}✅ Cluster created${NC}"
echo ""

# Wait for nodes to be ready
echo "Waiting for nodes to be ready..."
kubectl wait --for=condition=Ready nodes --all --timeout=120s
kubectl get nodes

echo ""
echo -e "${GREEN}✅ All nodes are ready${NC}"
echo ""

# Install Metrics Server
echo "Installing Metrics Server..."
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Patch metrics-server for insecure TLS (required for kind)
kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
  {
    "op": "add",
    "path": "/spec/template/spec/containers/0/args/-",
    "value": "--kubelet-insecure-tls"
  }
]'

echo -e "${GREEN}✅ Metrics Server installed${NC}"
echo ""

# Wait for metrics-server to be ready
kubectl wait --for=condition=available --timeout=120s deployment/metrics-server -n kube-system
echo -e "${GREEN}✅ Metrics Server is ready${NC}"
echo ""

# Verify metrics collection
echo "Verifying metrics collection..."
sleep 5
if kubectl top nodes &> /dev/null; then
    echo -e "${GREEN}✅ Metrics collection working${NC}"
    kubectl top nodes
else
    echo -e "${YELLOW}⚠️  Metrics may still be initializing, this is normal${NC}"
fi
echo ""

# Deploy PipesHub-AI
echo "============================================"
echo "  Deploying PipesHub-AI"
echo "============================================"
echo ""

# Change to helm directory
cd "$(dirname "$0")"

# Deploy with development settings
echo "Installing with development settings..."
helm install pipeshub-ai ./pipeshub-ai \
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
  --set qdrant.replicaCount=1 \
  --set neo4j.replicaCount=1

echo ""
echo -e "${GREEN}✅ PipesHub-AI installed${NC}"
echo ""

# Wait for infrastructure pods first (kafka, neo4j, qdrant, zookeeper)
echo "Waiting for infrastructure pods to be ready..."
kubectl wait --for=condition=Ready pods \
  -l "app.kubernetes.io/instance=pipeshub-ai" \
  --timeout=300s \
  --field-selector='status.phase=Running' 2>/dev/null || true

# Wait for the main app deployment to be available (up to 10 minutes — app starts slowly)
echo "Waiting for main app deployment to be available..."
if kubectl wait --for=condition=available deployment/pipeshub-ai --timeout=600s 2>/dev/null; then
  echo ""
  echo -e "${GREEN}✅ All pods are ready${NC}"
else
  echo ""
  echo -e "${YELLOW}⚠️  Some pods are still starting up — this is normal for a first deployment.${NC}"
  echo -e "${YELLOW}   The app takes 2-3 minutes to fully initialize.${NC}"
  echo -e "${YELLOW}   Check status with: kubectl get pods${NC}"
fi
echo ""

# Show status
echo "============================================"
echo "  Deployment Status"
echo "============================================"
echo ""

echo "Pods:"
kubectl get pods -l app.kubernetes.io/name=pipeshub-ai

echo ""
echo "Services:"
kubectl get svc -l app.kubernetes.io/name=pipeshub-ai

echo ""
echo "All Resources:"
kubectl get all -l app.kubernetes.io/name=pipeshub-ai

echo ""
echo "============================================"
echo "  🎉 Deployment Complete!"
echo "============================================"
echo ""
echo "To access the application:"
echo ""
echo "1. Port forward frontend to local machine (3001):"
echo "   kubectl port-forward svc/pipeshub-ai 3001:3001"
echo ""
echo "2. Open browser (frontend):"
echo "   http://localhost:3001"
echo ""
echo "3. Port forward backend API (3000):"
echo "   kubectl port-forward svc/pipeshub-ai 3000:3000"
echo ""
echo "4. To see logs:"
echo "   kubectl logs -l app.kubernetes.io/name=pipeshub-ai -f"
echo ""
echo "5. To delete the cluster:"
echo "   kind delete cluster --name pipeshub"
echo ""
echo "For production-like deployment with auth:"
echo "   helm upgrade pipeshub-ai ./pipeshub-ai \\"
echo "     --set secretKey='your-secret-key' \\"
echo "     --set mongodb.auth.rootPassword='your-password' \\"
echo "     --set redis.auth.password='your-password' \\"
echo "     --set autoscaling.enabled=true"
echo ""
