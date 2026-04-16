# PipesHub-AI Helm Chart

Production-ready Helm chart for deploying PipesHub-AI with optional enterprise features.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.11+
- (Optional) External Secrets Operator for `secretManagement.externalSecrets`
- (Optional) Prometheus Operator for `monitoring.serviceMonitor`

## Quick Start

1. Install dependencies (if needed):

```bash
helm dependency build ./deployment/helm/pipeshub-ai
```

2. Install chart (minimum required secrets):

```bash
helm upgrade --install pipeshub ./deployment/helm/pipeshub-ai \
  --set secretKey="$(openssl rand -hex 32)" \
  --set mongodb.auth.rootPassword="change-me" \
  --set redis.auth.password="change-me" \
  --set neo4j.auth.password="change-me" \
  --set "mongodb.auth.usernames[0]=pipeshub" \
  --set "mongodb.auth.databases[0]=pipeshub"
```

## Secret Management Modes

- `inline` (default): chart creates Kubernetes Secret from values
- `existingSecrets`: reference pre-created Kubernetes Secret names
- `externalSecrets`: use External Secrets Operator to create target secret

### Existing Secrets Example

```bash
helm upgrade --install pipeshub ./deployment/helm/pipeshub-ai \
  --set secretManagement.existingSecrets.enabled=true \
  --set secretManagement.existingSecrets.appSecretName="pipeshub-secrets" \
  --set secretManagement.existingSecrets.mongodbSecretName="pipeshub-secrets" \
  --set secretManagement.existingSecrets.redisSecretName="pipeshub-secrets" \
  --set secretManagement.existingSecrets.neo4jSecretName="pipeshub-secrets" \
  --set secretManagement.existingSecrets.qdrantSecretName="pipeshub-secrets"
```

### External Secrets Example

```bash
helm upgrade --install pipeshub ./deployment/helm/pipeshub-ai \
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
helm template test ./deployment/helm/pipeshub-ai \
  --set secretKey="test" \
  --set mongodb.auth.rootPassword="test" \
  --set redis.auth.password="test" \
  --set neo4j.auth.password="test" \
  --set "mongodb.auth.usernames[0]=pipeshub" \
  --set "mongodb.auth.databases[0]=pipeshub"
```

## Production Checklist

- Set all passwords and `secretKey` securely
- Configure ingress/TLS
- Enable `podDisruptionBudget` for HA
- Enable `networkPolicy` with cluster-specific rules
- Enable `monitoring.serviceMonitor` when Prometheus Operator exists
- Validate upgrade path in non-production first
