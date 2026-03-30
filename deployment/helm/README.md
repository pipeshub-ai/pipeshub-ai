# PipesHub-AI Helm Chart

Production-ready Kubernetes deployment for PipesHub-AI with High Availability and Auto-Scaling.

## Prerequisites

### Required
- Kubernetes 1.24+
- Helm 3.8+
- **Metrics Server** (for autoscaling) - See [Installation](#metrics-server-installation)

### Recommended
- Storage provisioner (for persistent volumes)
- Ingress controller (for external access)
- Certificate manager (for TLS/SSL)

## Quick Start

```bash
# 1. Install Metrics Server (REQUIRED for autoscaling)
./install-metrics-server.sh

# 2. Install dependencies
cd pipeshub-ai
helm dependency update

# 3. Install PipesHub-AI
helm install pipeshub-ai . \
  --set secretKey="your-secret-key" \
  --set mongodb.auth.rootPassword="mongodb-password" \
  --set redis.auth.password="redis-password" \
  --set config.allowedOrigins="https://your-domain.com"
```

## Metrics Server Installation

**⚠️ CRITICAL:** Without Metrics Server, HorizontalPodAutoscaler will NOT work!

### Automated Installation (Recommended)

```bash
# Run the installation script
./install-metrics-server.sh
```

### Manual Installation

```bash
# Standard installation
kubectl apply -f https://github.com/kubernetes-sigs/metrics-server/releases/latest/download/components.yaml

# For local clusters (kind/minikube), add insecure TLS flag:
kubectl patch deployment metrics-server -n kube-system --type='json' -p='[
  {
    "op": "add",
    "path": "/spec/template/spec/containers/0/args/-",
    "value": "--kubelet-insecure-tls"
  }
]'
```

### Verify Installation

```bash
# Check if metrics-server is running
kubectl get deployment metrics-server -n kube-system

# Verify metrics are being collected
kubectl top nodes
kubectl top pods

# Check HPA status
kubectl get hpa
kubectl describe hpa pipeshub-ai
```

## Architecture

### High Availability Configuration

All stateful services run with 3 replicas for fault tolerance:

- **Main Application**: 3-10 replicas (autoscaling)
- **MongoDB**: 3-node replica set + arbiter
- **Redis**: 1 master + 2 replicas + 3 sentinels
- **Zookeeper**: 3-node ensemble
- **Kafka**: 3 brokers
- **Neo4j**: 3-node causal cluster
- **Qdrant**: 3-node distributed cluster

### Auto-Scaling

The main application scales automatically based on resource usage:

- **Min Replicas**: 3 (maintains HA)
- **Max Replicas**: 10 (configurable)
- **Scale Triggers**:
  - CPU > 70%
  - Memory > 80%

## Configuration

### Required Values

These MUST be set for production deployment:

```bash
helm install pipeshub-ai . \
  --set secretKey="<random-256-bit-key>" \
  --set mongodb.auth.rootPassword="<secure-password>" \
  --set mongodb.auth.password="<secure-password>" \
  --set mongodb.auth.username="pipeshub" \
  --set mongodb.auth.database="pipeshub" \
  --set redis.auth.password="<secure-password>" \
  --set config.allowedOrigins="https://app.example.com,https://admin.example.com" \
  --set config.connectorPublicBackend="https://connector.example.com" \
  --set config.frontendPublicUrl="https://app.example.com"
```

### Optional Services

Neo4j is the default graph database. To use ArangoDB instead:

```bash
helm install pipeshub-ai . \
  --set neo4j.enabled=false \
  --set arango.enabled=true
```

To enable etcd:

```bash
helm install pipeshub-ai . \
  --set etcd.enabled=true
```

### Auto-Scaling Configuration

Enable/disable autoscaling:

```bash
# Enable autoscaling (default)
helm install pipeshub-ai . --set autoscaling.enabled=true

# Disable autoscaling (fixed replicas)
helm install pipeshub-ai . --set autoscaling.enabled=false

# Custom scaling limits
helm install pipeshub-ai . \
  --set autoscaling.minReplicas=5 \
  --set autoscaling.maxReplicas=20 \
  --set autoscaling.targetCPUUtilizationPercentage=60
```

### Resource Limits

For small clusters (e.g., kind, minikube):

```bash
helm install pipeshub-ai . \
  --set resources.requests.cpu=500m \
  --set resources.requests.memory=1Gi
```

For production:

```bash
helm install pipeshub-ai . \
  --set resources.requests.cpu=4 \
  --set resources.requests.memory=4Gi \
  --set resources.limits.cpu=8 \
  --set resources.limits.memory=8Gi
```

### Storage Classes

Specify storage class for persistent volumes:

```bash
helm install pipeshub-ai . \
  --set persistence.storageClass=gp3 \
  --set mongodb.persistence.storageClass=gp3 \
  --set redis.persistence.storageClass=gp3
```

### Ingress Configuration

```bash
helm install pipeshub-ai . \
  --set ingress.enabled=true \
  --set ingress.className=nginx \
  --set-json 'ingress.hosts=[
    {
      "host": "pipeshub.example.com",
      "paths": [
        {"path": "/", "pathType": "Prefix", "port": 3000, "serviceName": "pipeshub-ai"}
      ]
    }
  ]' \
  --set-json 'ingress.tls=[
    {
      "secretName": "pipeshub-tls",
      "hosts": ["pipeshub.example.com"]
    }
  ]'
```

## Installation

### Development / Testing

```bash
# Install with minimal resources
helm install pipeshub-ai . \
  --set resources.requests.cpu=500m \
  --set resources.requests.memory=1Gi \
  --set autoscaling.enabled=false \
  --set mongodb.auth.enabled=false \
  --set redis.auth.enabled=false
```

### Production

```bash
# 1. Create values file for your environment
cat > production-values.yaml <<EOF
# Application
replicaCount: 3
autoscaling:
  enabled: true
  minReplicas: 3
  maxReplicas: 10

# Security
secretKey: "YOUR-SECRET-KEY-HERE"

config:
  nodeEnv: "production"
  logLevel: "info"
  allowedOrigins: "https://app.example.com"
  connectorPublicBackend: "https://connector.example.com"
  frontendPublicUrl: "https://app.example.com"

# MongoDB
mongodb:
  auth:
    enabled: true
    rootPassword: "MONGODB-ROOT-PASSWORD"
    username: "pipeshub"
    password: "MONGODB-USER-PASSWORD"
    database: "pipeshub"
  persistence:
    storageClass: "gp3"

# Redis
redis:
  auth:
    enabled: true
    password: "REDIS-PASSWORD"
  persistence:
    storageClass: "gp3"

# Ingress
ingress:
  enabled: true
  className: "nginx"
  hosts:
    - host: pipeshub.example.com
      paths:
        - path: /
          pathType: Prefix
          port: 3001
          serviceName: pipeshub-ai
  tls:
    - secretName: pipeshub-tls
      hosts:
        - pipeshub.example.com

# Storage
persistence:
  storageClass: "gp3"
  size: 10Gi
EOF

# 2. Install with production values
helm install pipeshub-ai . -f production-values.yaml
```

## Upgrading

```bash
# Update dependencies
helm dependency update

# Upgrade release
helm upgrade pipeshub-ai . -f production-values.yaml

# Rollback if needed
helm rollback pipeshub-ai
```

## Monitoring

### Check Pod Status

```bash
# All pods
kubectl get pods

# Specific services
kubectl get pods -l app.kubernetes.io/name=pipeshub-ai
kubectl get pods -l app.kubernetes.io/component=mongodb
kubectl get pods -l app.kubernetes.io/component=kafka
```

### Check HPA Status

```bash
# Get HPA
kubectl get hpa

# Describe HPA (shows current metrics)
kubectl describe hpa pipeshub-ai

# Watch HPA in real-time
kubectl get hpa -w
```

### View Logs

```bash
# Main application
kubectl logs -l app.kubernetes.io/name=pipeshub-ai --tail=100 -f

# Specific pod
kubectl logs <pod-name> -f

# Previous pod (if crashed)
kubectl logs <pod-name> --previous
```

### Resource Usage

```bash
# Node metrics
kubectl top nodes

# Pod metrics
kubectl top pods

# Specific namespace
kubectl top pods -n default
```

## Troubleshooting

### Autoscaling Not Working

1. **Check if Metrics Server is installed:**
   ```bash
   kubectl get deployment metrics-server -n kube-system
   ```

2. **If not installed, run:**
   ```bash
   ./install-metrics-server.sh
   ```

3. **Verify metrics are available:**
   ```bash
   kubectl top nodes
   kubectl top pods
   ```

4. **Check HPA status:**
   ```bash
   kubectl describe hpa pipeshub-ai
   ```

   Look for: "unable to get metrics" or "missing request for cpu/memory"

### Pods Not Starting

1. **Check pod status:**
   ```bash
   kubectl get pods
   kubectl describe pod <pod-name>
   ```

2. **Common issues:**
   - Insufficient resources → Reduce requests
   - Storage class not found → Set correct storageClass
   - Image pull errors → Check image repository/tag
   - Secret not found → Check secret configuration

### Database Connection Errors

1. **Check database pods:**
   ```bash
   kubectl get pods -l app.kubernetes.io/component=mongodb
   kubectl logs -l app.kubernetes.io/component=mongodb
   ```

2. **Verify service endpoints:**
   ```bash
   kubectl get svc
   kubectl get endpoints
   ```

3. **Test connectivity:**
   ```bash
   kubectl exec -it <app-pod> -- nc -zv pipeshub-ai-mongodb 27017
   ```

## Uninstallation

```bash
# Delete release
helm uninstall pipeshub-ai

# Delete PVCs (if needed)
kubectl delete pvc -l app.kubernetes.io/instance=pipeshub-ai
```

## Security Considerations

### ✅ Enabled by Default
- Pod Security Context
- Container Security Context
- MongoDB authentication
- Redis authentication
- Resource limits
- Network isolation (via ClusterIP)

### ⚠️ Requires Configuration
- CORS origins (set `config.allowedOrigins`)
- TLS/SSL (configure ingress TLS)
- Secret keys (set `secretKey`)
- Database passwords (set auth passwords)

### 🔒 Recommended
- Network Policies
- Pod Security Policies/Standards
- External secret management (Vault, AWS Secrets Manager)
- Regular security updates
- Image scanning

## Support

- **Documentation**: https://github.com/anupPradhan0/pipeshub-ai
- **Issues**: https://github.com/anupPradhan0/pipeshub-ai/issues
- **Discussions**: https://github.com/anupPradhan0/pipeshub-ai/discussions

## License

See the main repository for license information.
