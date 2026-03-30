# Local Development with Kubernetes

This guide shows you how to run PipesHub-AI on your local machine using Kubernetes.

## Prerequisites

### Required Software

1. **Docker** - https://docs.docker.com/get-docker/
2. **kubectl** - Kubernetes CLI
3. **Kind** - Kubernetes in Docker (recommended)

### Quick Setup (macOS)

```bash
# Install everything with Homebrew
brew install docker kind kubectl
```

### Quick Setup (Linux)

```bash
# Install Kind
curl -Lo ./kind https://kind.sigs.k8s.io/dl/v0.20.0/kind-linux-amd64
chmod +x ./kind
sudo mv ./kind /usr/local/bin/kind

# Install kubectl
curl -Lo kubectl https://dl.k8s.io/release/v1.28.0/bin/linux/amd64/kubectl
chmod +x kubectl
sudo mv kubectl /usr/local/bin/

# Install Docker (Ubuntu)
sudo apt install docker.io
sudo usermod -aG docker $USER
# Log out and back in for group changes to take effect
```

---

## Option 1: Automated Setup (Recommended) ⭐

### Run the Setup Script

```bash
cd /home/mors/Code/pipeshub-ai/deployment/helm
./setup-kind-cluster.sh
```

This script will:
1. ✅ Install Kind and kubectl if missing
2. ✅ Create a 4-node Kubernetes cluster (1 control-plane + 3 workers)
3. ✅ Install Metrics Server for autoscaling
4. ✅ Deploy PipesHub-AI with development settings
5. ✅ Show you how to access the application

---

## Option 2: Manual Setup

### Step 1: Create Kubernetes Cluster

```bash
# Create cluster with 4 nodes (for HA testing)
kind create cluster --name pipeshub --config - <<EOF
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
- role: control-plane
- role: worker
- role: worker
- role: worker
EOF

# Verify cluster
kubectl cluster-info
kubectl get nodes
```

**Expected Output:**
```
NAME                         STATUS   ROLES           AGE
pipeshub-control-plane       Ready    control-plane   2m
pipeshub-worker              Ready    <none>          1m
pipeshub-worker2             Ready    <none>          1m
pipeshub-worker3             Ready    <none>          1m
```

### Step 2: Install Metrics Server

Metrics Server is **REQUIRED** for HorizontalPodAutoscaler to work.

```bash
# Install
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# Patch for Kind (insecure TLS)
kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
  {
    "op": "add",
    "path": "/spec/template/spec/containers/0/args/-",
    "value": "--kubelet-insecure-tls"
  }
]'

# Verify
kubectl get deployment metrics-server -n kube-system
kubectl top nodes
```

### Step 3: Install PipesHub-AI

#### Development Setup (No Auth)

```bash
cd /home/mors/Code/pipeshub-ai/deployment/helm

helm install pipeshub-ai ./pipeshub-ai \
  --set resources.requests.cpu=100m \
  --set resources.requests.memory=256Mi \
  --set mongodb.auth.enabled=false \
  --set redis.auth.enabled=false \
  --set autoscaling.enabled=false
```

#### Production-like Setup (With Auth)

```bash
cd /home/mors/Code/pipeshub-ai/deployment/helm

helm install pipeshub-ai ./pipeshub-ai \
  --set secretKey="dev-secret-key-123" \
  --set mongodb.auth.rootPassword="dev-password" \
  --set redis.auth.password="dev-password" \
  --set neo4j.auth.password="dev-password" \
   --set config.allowedOrigins="http://localhost:3001" \
  --set resources.requests.cpu=500m \
  --set resources.requests.memory=1Gi
```

### Step 4: Watch Deployment

```bash
# Watch pods come up
kubectl get pods -w

# Check status after a few minutes
kubectl get all
kubectl get svc
kubectl get pods
```

**Expected pods:**
```
NAME                              READY   STATUS    RESTARTS   AGE
pipeshub-ai-7d9f8b5c6-k8v5l      1/1     Running   0          2m
pipeshub-ai-mongodb-0             1/1     Running   0          2m
pipeshub-ai-redis-node-0          1/1     Running   0          2m
pipeshub-ai-redis-node-1          1/1     Running   0          2m
pipeshub-ai-redis-node-2          1/1     Running   0          2m
pipeshub-ai-kafka-0               1/1     Running   0          2m
pipeshub-ai-kafka-1               1/1     Running   0          2m
pipeshub-ai-kafka-2               1/1     Running   0          2m
pipeshub-ai-neo4j-0               1/1     Running   0          2m
pipeshub-ai-neo4j-1               1/1     Running   0          2m
pipeshub-ai-neo4j-2               1/1     Running   0          2m
pipeshub-ai-qdrant-0              1/1     Running   0          2m
pipeshub-ai-qdrant-1              1/1     Running   0          2m
pipeshub-ai-qdrant-2              1/1     Running   0          2m
pipeshub-ai-zookeeper-0           1/1     Running   0          2m
pipeshub-ai-zookeeper-1           1/1     Running   0          2m
pipeshub-ai-zookeeper-2           1/1     Running   0          2m
```

### Step 5: Access the Application

#### Option A: Port Forward (Quickest)

```bash
# Port forward frontend to local machine
kubectl port-forward svc/pipeshub-ai 3001:3001

# In another terminal, port forward backend API
kubectl port-forward svc/pipeshub-ai 3000:3000

# Open browser
open http://localhost:3001
```

#### Option B: Use NodePort

```bash
# Change service type to NodePort
kubectl patch svc pipeshub-ai -p '{"spec":{"type":"NodePort"}}'

# Get the port
kubectl get svc pipeshub-ai

# Access via: http://localhost:<NODE_PORT>
```

#### Option C: Use LoadBalancer (if available)

```bash
# Change service type
kubectl patch svc pipeshub-ai -p '{"spec":{"type":"LoadBalancer"}}'

# Get external IP
kubectl get svc pipeshub-ai -w
```

### Step 6: View Logs

```bash
# Main application logs
kubectl logs -l app.kubernetes.io/name=pipeshub-ai --tail=100 -f

# Specific pod
kubectl logs pipeshub-ai-7d9f8b5c6-k8v5l -f

# Previous logs (if crashed)
kubectl logs pipeshub-ai-7d9f8b5c6-k8v5l --previous
```

### Step 7: Debug Issues

```bash
# Check pod details
kubectl describe pod <pod-name>

# Check service endpoints
kubectl get endpoints pipeshub-ai

# Test database connectivity
kubectl run -it --rm debug --image=busybox --restart=Never -- sh
# Inside the pod:
# nc -zv pipeshub-ai-mongodb 27017
# nc -zv pipeshub-ai-redis 6379
# exit

# Check resource usage
kubectl top pods
kubectl top nodes

# Check events
kubectl get events --sort-by='.lastTimestamp'
```

---

## Testing Autoscaling

If you want to test HPA with Kind:

```bash
# Upgrade installation with autoscaling enabled
helm upgrade pipeshub-ai ./pipeshub-ai \
  --set autoscaling.enabled=true \
  --set autoscaling.minReplicas=1 \
  --set autoscaling.maxReplicas=5

# Check HPA
kubectl get hpa
kubectl describe hpa pipeshub-ai

# Generate load to trigger scaling
kubectl run -it --rm load-generator --image=busybox -- /bin/sh
# Inside the pod:
while true; do wget -q -O- http://pipeshub-ai:3000 > /dev/null; done

# Watch pods scale up
kubectl get pods -w
```

---

## Common Commands Reference

### Check Status
```bash
# All resources
kubectl get all

# Pods with more details
kubectl get pods -o wide

# Services
kubectl get svc

# ConfigMaps and Secrets
kubectl get configmap,secret

# PersistentVolumeClaims
kubectl get pvc

# HorizontalPodAutoscaler
kubectl get hpa
kubectl describe hpa pipeshub-ai
```

### View Logs
```bash
# All pods
kubectl logs -l app.kubernetes.io/name=pipeshub-ai --tail=100

# Specific component
kubectl logs -l app.kubernetes.io/component=mongodb
kubectl logs -l app.kubernetes.io/component=kafka

# Follow logs in real-time
kubectl logs -l app.kubernetes.io/name=pipeshub-ai -f
```

### Debugging
```bash
# Pod details
kubectl describe pod <pod-name>

# Service details
kubectl describe svc pipeshub-ai

# Get events
kubectl get events --field-selector involvedObject.name=<pod-name>

# Execute into pod
kubectl exec -it <pod-name> -- /bin/sh

# Port forward for debugging
kubectl port-forward <pod-name> 8080:8080
```

### Scaling
```bash
# Manual scale (if autoscaling disabled)
kubectl scale deployment pipeshub-ai --replicas=5

# Check current replicas
kubectl get deployment pipeshub-ai
```

### Upgrading
```bash
# Upgrade to latest
helm upgrade pipeshub-ai ./pipeshub-ai --set secretKey="your-key"

# Rollback if issues
helm rollback pipeshub-ai
```

### Cleanup
```bash
# Delete release
helm uninstall pipeshub-ai

# Delete PVCs (careful - data loss!)
kubectl delete pvc -l app.kubernetes.io/instance=pipeshub-ai

# Delete cluster
kind delete cluster --name pipeshub

# Delete all remaining resources
kubectl delete all --all
```

---

## Resource Requirements

### Minimum for Development
- Docker with 4GB RAM
- Kind cluster: 4GB RAM, 2 CPUs
- PipesHub-AI: ~1GB RAM

### Recommended for Full HA Testing
- Docker with 16GB RAM
- Kind cluster: 8GB RAM, 4 CPUs
- PipesHub-AI: ~4GB RAM

---

## Troubleshooting

### Pods Not Starting

1. **Check resources:**
   ```bash
   kubectl describe pod <pod-name>
   # Look for: "Failed to schedule", "Insufficient cpu", "Insufficient memory"
   ```

2. **Solutions:**
   - Reduce resource requests: `--set resources.requests.memory=256Mi`
   - Assign more resources to Docker: Docker Desktop > Settings > Resources

### MongoDB/Redis Connection Issues

1. **Check if services are running:**
   ```bash
   kubectl get svc | grep -E "mongodb|redis"
   ```

2. **Check endpoints:**
   ```bash
   kubectl get endpoints pipeshub-ai-mongodb
   ```

3. **Test connectivity:**
   ```bash
   kubectl run -it --rm debug --image=busybox --restart=Never
   nslookup pipeshub-ai-mongodb
   nc -zv pipeshub-ai-mongodb 27017
   ```

### Kafka/Zookeeper Issues

1. **Check StatefulSets:**
   ```bash
   kubectl get statefulset | grep -E "kafka|zookeeper"
   ```

2. **Check pods are in order:**
   ```bash
   kubectl get pods -l app.kubernetes.io/component=kafka
   ```

3. **Wait for them to be ready (takes time):**
   ```bash
   kubectl wait --for=condition=Ready pods -l app.kubernetes.io/component=kafka --timeout=300s
   ```

### HPA Not Working

1. **Verify metrics-server:**
   ```bash
   kubectl get deployment metrics-server -n kube-system
   kubectl top nodes
   ```

2. **Check HPA status:**
   ```bash
   kubectl describe hpa pipeshub-ai
   ```

3. **Look for errors:**
   ```
   "Unable to get metrics" = metrics-server not working
   "ScalingActive: false" = check conditions
   ```

---

## Next Steps

### 1. Access Application
Open browser: http://localhost:3001

### 2. Monitor Resources
```bash
# Dashboard (if using minikube)
minikube dashboard

# Or use kubectl
kubectl get hpa,pods,services
```

### 3. Test Features
- Create an account
- Upload data
- Test queries
- Monitor logs

### 4. Experiment with Scaling
```bash
# Disable autoscaling
helm upgrade pipeshub-ai ./pipeshub-ai --set autoscaling.enabled=false

# Manual scale
kubectl scale deployment pipeshub-ai --replicas=5

# Watch pods
kubectl get pods -w
```

### 5. Try Production Setup
```bash
# Clean up
helm uninstall pipeshub-ai

# Deploy with full settings
helm install pipeshub-ai ./pipeshub-ai \
  --set secretKey="production-secret-key" \
  --set mongodb.auth.rootPassword="secure-password" \
  --set redis.auth.password="secure-password" \
  --set config.allowedOrigins="https://your-domain.com" \
  --set autoscaling.enabled=true
```

---

## Support

- **Documentation**: https://github.com/anupPradhan0/pipeshub-ai
- **Issues**: https://github.com/anupPradhan0/pipeshub-ai/issues

Happy developing! 🚀
