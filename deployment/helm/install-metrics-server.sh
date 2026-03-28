#!/bin/bash
# Metrics Server Installation Script for Kubernetes
# This is REQUIRED for HorizontalPodAutoscaler to work

set -e

echo "============================================"
echo "   Metrics Server Installation Script"
echo "============================================"
echo ""

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "❌ ERROR: kubectl is not installed or not in PATH"
    exit 1
fi

# Check if cluster is accessible
if ! kubectl cluster-info &> /dev/null; then
    echo "❌ ERROR: Cannot connect to Kubernetes cluster"
    echo "   Please check your kubeconfig and cluster connection"
    exit 1
fi

echo "✅ kubectl is available and cluster is accessible"
echo ""

# Check if metrics-server is already installed
echo "Checking if metrics-server is already installed..."
if kubectl get deployment metrics-server -n kube-system &> /dev/null; then
    echo "✅ Metrics Server is already installed!"
    echo ""
    kubectl get deployment metrics-server -n kube-system
    echo ""
    echo "To verify it's working:"
    echo "  kubectl top nodes"
    echo "  kubectl top pods"
    exit 0
fi

echo "⚠️  Metrics Server is NOT installed"
echo ""

# Detect cluster type
echo "Detecting cluster type..."
CLUSTER_TYPE="unknown"

if kubectl get nodes -o json | grep -q "eks.amazonaws.com"; then
    CLUSTER_TYPE="eks"
    echo "Detected: Amazon EKS"
elif kubectl get nodes -o json | grep -q "gke"; then
    CLUSTER_TYPE="gke"
    echo "Detected: Google GKE"
elif kubectl get nodes -o json | grep -q "azure"; then
    CLUSTER_TYPE="aks"
    echo "Detected: Azure AKS"
elif kubectl config current-context | grep -q "kind"; then
    CLUSTER_TYPE="kind"
    echo "Detected: kind (Kubernetes in Docker)"
elif kubectl config current-context | grep -q "minikube"; then
    CLUSTER_TYPE="minikube"
    echo "Detected: Minikube"
else
    echo "Detected: Generic Kubernetes cluster"
fi
echo ""

# Install metrics-server based on cluster type
echo "Installing metrics-server..."
echo ""

case "$CLUSTER_TYPE" in
    "gke")
        echo "For GKE, metrics-server should be installed by default."
        echo "If not, running standard installation..."
        kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
        ;;
    "eks")
        echo "Installing metrics-server for EKS..."
        kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
        ;;
    "aks")
        echo "For AKS, metrics-server should be installed by default."
        echo "If not, running standard installation..."
        kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
        ;;
    "kind"|"minikube")
        echo "Installing metrics-server for local development cluster..."
        echo "⚠️  Note: Adding --kubelet-insecure-tls flag for local development"
        kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
        
        # Patch for local development (insecure TLS)
        kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
          {
            "op": "add",
            "path": "/spec/template/spec/containers/0/args/-",
            "value": "--kubelet-insecure-tls"
          }
        ]'
        ;;
    *)
        echo "Installing metrics-server with standard configuration..."
        kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml
        ;;
esac

echo ""
echo "Waiting for metrics-server to be ready..."
kubectl wait --for=condition=available --timeout=120s deployment/metrics-server -n kube-system

echo ""
echo "✅ Metrics Server installed successfully!"
echo ""
echo "Verifying installation..."
sleep 10  # Give it a moment to collect initial metrics

if kubectl top nodes &> /dev/null; then
    echo "✅ Metrics Server is working!"
    echo ""
    kubectl top nodes
else
    echo "⚠️  Metrics Server is installed but may still be initializing"
    echo "   Wait a minute and try: kubectl top nodes"
fi

echo ""
echo "============================================"
echo "   Installation Complete!"
echo "============================================"
echo ""
echo "Your HorizontalPodAutoscaler will now work correctly."
echo ""
echo "To verify HPA status:"
echo "  kubectl get hpa"
echo "  kubectl describe hpa <hpa-name>"
