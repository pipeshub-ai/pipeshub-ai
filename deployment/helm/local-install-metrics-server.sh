#!/bin/bash
# Metrics Server Installation Script for Kubernetes
# Required for HorizontalPodAutoscaler to work
#
# Usage: ./local-install-metrics-server.sh

set -e

echo "Metrics Server Installation"
echo "==========================="
echo ""

# Check if kubectl is available
if ! command -v kubectl &> /dev/null; then
    echo "Error: kubectl is not installed or not in PATH"
    exit 1
fi

# Check if cluster is accessible
if ! kubectl cluster-info &> /dev/null; then
    echo "Error: Cannot connect to Kubernetes cluster"
    echo "Please check your kubeconfig and cluster connection"
    exit 1
fi

echo "Checking for existing metrics-server..."
if kubectl get deployment metrics-server -n kube-system &> /dev/null; then
    echo "Metrics Server is already installed:"
    echo ""
    kubectl get deployment metrics-server -n kube-system
    echo ""
    echo "To verify it's working, run:"
    echo "  kubectl top nodes"
    echo "  kubectl top pods"
    exit 0
fi

echo "Metrics Server not found, installing..."
echo ""

# Detect cluster type
echo "Detecting cluster type..."
CLUSTER_TYPE="unknown"

if kubectl get nodes -o json | grep -q "eks.amazonaws.com"; then
    CLUSTER_TYPE="eks"
    echo "Detected Amazon EKS"
elif kubectl get nodes -o json | grep -q "gke"; then
    CLUSTER_TYPE="gke"
    echo "Detected Google GKE"
elif kubectl get nodes -o json | grep -q "azure"; then
    CLUSTER_TYPE="aks"
    echo "Detected Azure AKS"
elif kubectl config current-context | grep -q "kind"; then
    CLUSTER_TYPE="kind"
    echo "Detected Kind cluster"
elif kubectl config current-context | grep -q "minikube"; then
    CLUSTER_TYPE="minikube"
    echo "Detected Minikube"
else
    echo "Detected generic Kubernetes cluster"
fi
echo ""

# Install metrics-server
echo "Installing metrics-server..."

kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Patch for local clusters (insecure TLS)
if [[ "$CLUSTER_TYPE" == "kind" || "$CLUSTER_TYPE" == "minikube" ]]; then
    echo "Patching for local development (--kubelet-insecure-tls)..."
    kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
      {
        "op": "add",
        "path": "/spec/template/spec/containers/0/args/-",
        "value": "--kubelet-insecure-tls"
      }
    ]'
fi

echo ""
echo "Waiting for metrics-server to be ready..."
kubectl wait --for=condition=available --timeout=120s deployment/metrics-server -n kube-system

echo ""
echo "Verifying installation..."
sleep 10

if kubectl top nodes &> /dev/null; then
    echo "Metrics Server is working:"
    echo ""
    kubectl top nodes
else
    echo "Metrics Server installed but still initializing."
    echo "Wait a minute and run: kubectl top nodes"
fi

echo ""
echo "Done. HorizontalPodAutoscaler should now work."
echo ""
echo "Verify HPA with:"
echo "  kubectl get hpa"
echo "  kubectl describe hpa <name>"
