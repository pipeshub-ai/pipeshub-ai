# PipesHub-AI Helm Chart

Production-ready Helm chart for deploying PipesHub-AI with optional enterprise features.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.11+
- (Optional) External Secrets Operator for `secretManagement.externalSecrets`
- (Optional) Prometheus Operator for `monitoring.serviceMonitor`

## Configuration presets

Two ready-to-use values files live next to this chart. Pick the one that
matches your target environment and override secrets/hosts via `--set` (or
your own values file layered on top).

| Preset | File | Use for | Footprint |
|--------|------|---------|-----------|
| Local  | `values-local.yaml` | kind, minikube, k3d, k3s, Docker Desktop | 1× app, 1× Mongo/Kafka/Redis/Qdrant/Neo4j, RWO storage |
| Cloud HA | `values-cloud.yaml` | EKS, GKE, AKS, any managed K8s | 3× app + HPA, 3-node Mongo replica set, 3× Kafka/Zookeeper/Qdrant, RWX storage |

Both files document the required `--set` overrides at the top.

## Quick start

### 1. Build chart dependencies

```bash
helm dependency build ./deployment/helm/pipeshub-ai
```

### 2a. Local (single replica)

> The release name below (`pipeshub-ai`) is intentional: it matches the chart
> name so the rendered Service is also `pipeshub-ai` (otherwise the chart's
> fullname template prefixes the release name and you get
> `<release>-pipeshub-ai`). All sample `port-forward` / ingress snippets below
> assume this convention.

```bash
helm upgrade --install pipeshub-ai ./deployment/helm/pipeshub-ai \
  --namespace pipeshub-local --create-namespace \
  -f ./deployment/helm/pipeshub-ai/values-local.yaml \
  --set secretKey="$(openssl rand -hex 32)" \
  --set mongodb.auth.rootPassword="change-me" \
  --set redis.auth.password="change-me" \
  --set neo4j.auth.password="change-me" \
  --set "mongodb.auth.usernames[0]=pipeshub" \
  --set "mongodb.auth.passwords[0]=$(openssl rand -hex 16)" \
  --set "mongodb.auth.databases[0]=pipeshub"
```

To use the slim image (~1.3 GB smaller; models download on first use), add:

```bash
  --set image.tag=slim
```

For kind/minikube the image must be loaded into the cluster first:

```bash
kind load docker-image pipeshubai/pipeshub-ai:slim --name pipeshub      # kind
minikube image load pipeshubai/pipeshub-ai:slim                         # minikube
```

Or use the bundled bootstrap script which handles cluster creation + image
loading + Helm install in one go:

```bash
# Defaults: cluster=pipeshub, namespace=pipeshub-local, image=pipeshubai/pipeshub-ai:latest
./deployment/helm/local-setup-kind-cluster.sh

# Override image / namespace / preset via env vars:
APP_IMAGE=pipeshubai/pipeshub-ai:slim \
NAMESPACE=pipeshub-dev \
./deployment/helm/local-setup-kind-cluster.sh

# Layer custom overrides on top of values-local.yaml:
EXTRA_HELM_ARGS="-f my-overrides.yaml --set neo4j.enabled=false" \
./deployment/helm/local-setup-kind-cluster.sh

# Wipe namespace + PVCs and reinstall from scratch:
FORCE_FRESH=1 ./deployment/helm/local-setup-kind-cluster.sh
```

Re-running the bootstrap script **reuses existing secrets** when the release is already installed, so MongoDB/Neo4j PVC passwords stay in sync. Use `FORCE_FRESH=1` only when you want a clean slate.

**Local access (port-forward from your laptop):**

```bash
# UI + API — enough for everyday use
kubectl port-forward -n pipeshub-local svc/pipeshub-ai 3001:3001

# Add 8088 only for connector OAuth / integration setup
kubectl port-forward -n pipeshub-local svc/pipeshub-ai 3001:3001 8088:8088
```

Query (8000), indexing (8091), and docling (8081) run inside the pod; the Node API on 3001 calls them in-cluster. You do not need to forward those ports for normal UI use.

### 2b. Cloud (HA)

```bash
helm upgrade --install pipeshub-ai ./deployment/helm/pipeshub-ai \
  --namespace pipeshub --create-namespace \
  -f ./deployment/helm/pipeshub-ai/values-cloud.yaml \
  --set global.storageClass=<block-class> \
  --set persistence.storageClass=<rwx-class> \
  --set ingress.className=<alb|gce|nginx> \
  --set 'ingress.hosts[0].host=pipeshub.example.com' \
  --set 'ingress.hosts[0].paths[0].path=/' \
  --set 'ingress.hosts[0].paths[0].pathType=Prefix' \
  --set config.frontendPublicUrl=https://pipeshub.example.com \
  --set config.allowedOrigins=https://pipeshub.example.com \
  --set secretKey="$(openssl rand -hex 32)" \
  --set mongodb.auth.rootPassword="..." \
  --set redis.auth.password="..." \
  --set neo4j.auth.password="..." \
  --set "mongodb.auth.usernames[0]=pipeshub" \
  --set "mongodb.auth.passwords[0]=..." \
  --set "mongodb.auth.databases[0]=pipeshub"
```

The ingress template defaults `serviceName` to the chart's rendered Service
name and `port` to `3001`, so you do not need to override either unless you
want to point at a different Service or expose port 8088 (see below).

### Public exposure

Only **port 3001** (UI + Node API) is exposed via the Ingress by default. Query (8000), indexing (8091), docling (8081), and connector (8088) stay on the in-cluster `ClusterIP` Service because the Node API calls them directly inside the cluster.

**When to expose 8088:** if you use OAuth-based connectors (Google, Microsoft, Slack, ...) or receive provider webhooks. Add a second ingress host *and* set `config.connectorPublicBackend` to that public URL so OAuth callbacks resolve correctly:

```bash
  --set 'ingress.hosts[1].host=pipeshub-connector.example.com' \
  --set 'ingress.hosts[1].paths[0].path=/' \
  --set 'ingress.hosts[1].paths[0].pathType=Prefix' \
  --set 'ingress.hosts[1].paths[0].port=8088' \
  --set config.connectorPublicBackend=https://pipeshub-connector.example.com
```

If you don't use OAuth/webhooks, omit `config.connectorPublicBackend` entirely — it's optional and only consumed when something external needs to call port 8088.

Provider-specific values for the storage classes:

| Provider | `global.storageClass` (block, stateful pods) | `persistence.storageClass` (RWX, shared app PVC) |
|----------|----------------------------------------------|--------------------------------------------------|
| AWS EKS  | `gp3`                                        | `efs-sc` (EFS CSI)                               |
| GCP GKE  | `standard-rwo`                               | `filestore-csi`                                  |
| Azure AKS| `managed-csi`                                | `azurefile-csi`                                  |
| On-prem  | any block (Longhorn/Ceph)                    | any RWX (NFS, CephFS, GlusterFS)                 |

The chart **refuses to install** when `replicaCount > 1` and persistence is not
`ReadWriteMany` — to avoid silently leaving replicas stuck on `Multi-Attach`.

## Secret Management Modes

- `inline` (default): chart creates Kubernetes Secret from values
- `existingSecrets`: reference pre-created Kubernetes Secret names
- `externalSecrets`: use External Secrets Operator to create target secret

### Existing Secrets Example

```bash
helm upgrade --install pipeshub-ai ./deployment/helm/pipeshub-ai \
  --set secretManagement.existingSecrets.enabled=true \
  --set secretManagement.existingSecrets.appSecretName="pipeshub-secrets" \
  --set secretManagement.existingSecrets.mongodbSecretName="pipeshub-secrets" \
  --set secretManagement.existingSecrets.redisSecretName="pipeshub-secrets" \
  --set secretManagement.existingSecrets.neo4jSecretName="pipeshub-secrets" \
  --set secretManagement.existingSecrets.qdrantSecretName="pipeshub-secrets"
```

### External Secrets Example

```bash
helm upgrade --install pipeshub-ai ./deployment/helm/pipeshub-ai \
  --set secretManagement.externalSecrets.enabled=true \
  --set secretManagement.externalSecrets.secretStoreRef.name="cluster-secrets" \
  --set secretManagement.externalSecrets.remoteRefs.secretKey="pipeshub/secret-key" \
  --set secretManagement.externalSecrets.remoteRefs.mongodbUsername="pipeshub/mongodb/username" \
  --set secretManagement.externalSecrets.remoteRefs.mongodbPassword="pipeshub/mongodb/password" \
  --set secretManagement.externalSecrets.remoteRefs.redisPassword="pipeshub/redis/password" \
  --set secretManagement.externalSecrets.remoteRefs.neo4jPassword="pipeshub/neo4j/password" \
  --set secretManagement.externalSecrets.remoteRefs.qdrantApiKey="pipeshub/qdrant/api-key"
```

## High-Value Features

- Secret validation with fail-fast messages
- Optional startup/readiness/liveness probe tuning
- Optional extra volumes and mounts
- Optional service account creation
- Optional PDB, NetworkPolicy, ServiceMonitor
- Optional OTLP telemetry env wiring

## Validation

```bash
helm lint ./deployment/helm/pipeshub-ai
helm template pipeshub-ai ./deployment/helm/pipeshub-ai \
  -f ./deployment/helm/pipeshub-ai/values-local.yaml \
  --set secretKey="test" \
  --set mongodb.auth.rootPassword="test" \
  --set redis.auth.password="test" \
  --set neo4j.auth.password="test" \
  --set "mongodb.auth.usernames[0]=pipeshub" \
  --set "mongodb.auth.passwords[0]=test" \
  --set "mongodb.auth.databases[0]=pipeshub"
```

## Production Checklist

- Set all passwords and `secretKey` securely
- Configure ingress/TLS
- Enable `podDisruptionBudget` for HA
- Enable `networkPolicy` with cluster-specific rules
- Enable `monitoring.serviceMonitor` when Prometheus Operator exists
- Validate upgrade path in non-production first
