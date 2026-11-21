# PipesHub-AI Helm Chart

Production-ready Helm chart for deploying PipesHub-AI with optional enterprise features.

## Prerequisites

- Kubernetes 1.24+
- Helm 3.11+
- (Optional) External Secrets Operator for `secretManagement.externalSecrets`
- An open-file limit of at least 64000 for containers on the nodes that run the data stores. Kubernetes has no
  per-pod setting: a container gets the limit of the node's container runtime (`LimitNOFILE` of containerd's
  systemd unit). See [Open-file limits](#open-file-limits).

## Configuration presets

Two ready-to-use values files live next to this chart. Pick the one that
matches your target environment and override secrets/hosts via `--set` (or
your own values file layered on top).

| Preset | File | Use for | Footprint |
|--------|------|---------|-----------|
| Local  | `values-local.yaml` | kind, minikube, k3d, k3s, Docker Desktop | 1× app, single-node Mongo replica set, 1× Kafka/Redis/Qdrant/Neo4j, RWO storage |
| AWS EKS | `values-eks.yaml` | Amazon EKS. No NFS. One command: `deployment/helm/aws/deploy.sh --domain HOST --region REGION`. Guide: [docs/deployment/aws-eks.md](../../../docs/deployment/aws-eks.md) | gp3 per database, MongoDB ×3, Redis master+replica, Qdrant ×3, Neo4j ×1 |
| Cloud HA | `values-cloud.yaml` | GKE, AKS, any cluster that already has RWX storage | 2× app + HPA, Bitnami Mongo ×3 data members (no arbiter), Kafka. Not for EKS |

Both files document the required `--set` overrides at the top.

## Quick start

### 1. Build chart dependencies

MongoDB and Redis are vendored in `deployment/helm/vendor`. This packages those local charts. It does not log in to Docker Hub.

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
  --set hardened=true \
  --set secretKey="$(openssl rand -hex 32)" \
  --set mongodb.auth.rootPassword="change-me" \
  --set redis.auth.password="change-me" \
  --set neo4j.auth.password="change-me" \
  --set qdrant.apiKey="$(openssl rand -hex 32)" \
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

Query (8000), indexing (8091), embedding (8002), and docling (8081) run inside the pod; the Node API on 3001 calls them in-cluster. You do not need to forward those ports for normal UI use.

### 2b. Cloud (HA)

```bash
helm upgrade --install pipeshub-ai ./deployment/helm/pipeshub-ai \
  --namespace pipeshub --create-namespace \
  -f ./deployment/helm/pipeshub-ai/values-cloud.yaml \
  --set hardened=true \
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
  --set qdrant.apiKey="$(openssl rand -hex 32)" \
  --set "mongodb.auth.usernames[0]=pipeshub" \
  --set "mongodb.auth.passwords[0]=..." \
  --set "mongodb.auth.databases[0]=pipeshub"
```

The ingress template defaults `serviceName` to the chart's rendered Service
name and `port` to `3001`, so you do not need to override either unless you
want to point at a different Service or expose port 8088 (see below).

### Public exposure

Only **port 3001** (UI + Node API) is exposed via the Ingress by default. Query (8000), indexing (8091), embedding (8002), docling (8081), and connector (8088) stay on the in-cluster `ClusterIP` Service because the Node API calls them directly inside the cluster.

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
| AWS EKS | use [values-eks.yaml](values-eks.yaml); do not create an EFS class | not used |
| GCP GKE  | `standard-rwo`                               | `filestore-csi`                                  |
| Azure AKS| `managed-csi`                                | `azurefile-csi`                                  |
| On-prem  | any block (Longhorn/Ceph)                    | any RWX (NFS, CephFS, GlusterFS)                 |

The chart **refuses to install** when `replicaCount > 1` and persistence is not
`ReadWriteMany` — to avoid silently leaving replicas stuck on `Multi-Attach`.

## MongoDB topology

Every preset runs MongoDB as a replica set, so multi-document transactions work
and `REPLICA_SET_AVAILABLE` is `true`. The chart derives both that flag and the
`replicaSet=` parameter of `MONGO_URI` from `mongodb.architecture`.

| Preset | Members | `MONGO_URI` write concern | Durable agent runs |
|--------|---------|---------------------------|--------------------|
| Local | 1 data member, oplog 1 GB | `w=majority` | on |
| Cloud HA, AWS EKS | 3 data members, no arbiter (PSS) | `w=majority` | on |
| Chart defaults (`values.yaml`) | 2 data members + arbiter (PSA) | server default (`w:1` with an arbiter) | off: Node's journal probe refuses an arbiter (`GET /api/v1/health` → `agentHarness.reason: arbiter`) |

`w=majority` is only rendered for arbiter-free sets: with an arbiter, one data
member down stalls every majority write. Prefer PSS for anything that must not
lose acknowledged writes on failover.

### Upgrading a release that runs an arbiter (Cloud HA)

`values-cloud.yaml` no longer deploys an arbiter. Helm removes the arbiter
StatefulSet on upgrade, but the replica-set config still lists it. Remove it
once the upgrade is done:

```bash
kubectl exec -n <ns> <release>-mongodb-0 -- mongosh -u root -p "$MONGO_ROOT_PASSWORD" \
  --authenticationDatabase admin --eval \
  'rs.remove("<release>-mongodb-arbiter-0.<release>-mongodb-arbiter-headless.<ns>.svc.cluster.local:27017")'
```

### Standalone MongoDB to a replica set

Releases installed from the old standalone `values-local.yaml` keep their data
in a PVC named `<release>-mongodb`. Switching that release to a replica set
would make Helm delete the PVC, so the upgrade stops with one of:

- `PVC <release>-mongodb holds data from a standalone MongoDB ...` (this chart)
- `PASSWORDS ERROR: ... "mongodb-replica-set-key"` (Bitnami: an upgrade must be given the key)

To stay standalone, add `--set mongodb.architecture=standalone` (durable agent
runs stay unavailable). To convert, move the data across:

```bash
NS=<ns>; REL=<release>; PW=<mongodb.auth.rootPassword>
# 1. Back up (this file is your restore point).
kubectl exec -n $NS deploy/$REL-mongodb -- mongodump --quiet --archive --gzip \
  -u root -p "$PW" --authenticationDatabase admin > pipeshub-mongo.archive.gz
# 2. Remove the standalone Deployment and its PVC.
kubectl delete -n $NS deployment $REL-mongodb
kubectl delete -n $NS pvc $REL-mongodb
# 3. Upgrade once with a replica-set key; later upgrades read it from the secret.
helm upgrade $REL . -n $NS -f values-local.yaml <your usual --set flags> \
  --set mongodb.auth.replicaSetKey="$(openssl rand -hex 32)"
# 4. Restore into the new primary, then restart the app.
kubectl wait -n $NS --for=condition=Ready pod/$REL-mongodb-0 --timeout=300s
kubectl exec -i -n $NS $REL-mongodb-0 -c mongodb -- mongorestore --quiet --archive --gzip --drop \
  -u root -p "$PW" --authenticationDatabase admin < pipeshub-mongo.archive.gz
kubectl rollout restart -n $NS deployment/$REL-pipeshub-ai
```

## Open-file limits

mongod and Qdrant keep the open-file limit they are started with. Where containerd runs with systemd's defaults
that is 1024 soft (and 524288 hard), and mongod at 1024 stops accepting connections and then crashes once its
collections, indexes and client connections pass it. The chart's own MongoDB (`mongodb.builtin`) and Qdrant
containers raise their soft limit before the server starts, to `mongodb.builtin.openFiles` (64000) and
`qdrant.openFiles` (50000); that needs no privilege while the node's hard limit is at least that high. When it is
lower the container says so on stderr (`open files: the hard limit is ...`) and starts on the hard limit: raise
`LimitNOFILE` of the node's container runtime.

The other stores raise their own soft limit to the hard one at startup (Redis to what `maxclients` needs, the
JVM for Neo4j, Kafka and ZooKeeper, arangod, etcd), so they need only the node's hard limit.

The Bitnami MongoDB subchart (`mongodb.enabled`, the default) is vendored unchanged and does not raise the
limit: its mongod runs on the node's soft limit. Check what a pod really has, for any store:

```bash
kubectl exec <pod> -- sh -c 'grep "open files" /proc/1/limits'
```

## Redis: standalone, self-hosted cluster, or AWS MemoryDB

By default the chart deploys the bundled Bitnami `redis` subchart in
`replication` mode (`redis.mode: standalone`) — no extra configuration
needed.

To point at ElastiCache with cluster mode disabled (one primary, automatic
failover, and a primary endpoint the app can use as a normal Redis), skip
in-cluster Redis and set the host. Celery uses the same endpoint:

```bash
helm upgrade --install pipeshub-ai ./deployment/helm/pipeshub-ai \
  ... \
  --set redis.enabled=false \
  --set redis.builtin.enabled=false \
  --set redis.mode=standalone \
  --set redis.external.enabled=true \
  --set redis.external.host=master.my-redis.abc.use1.cache.amazonaws.com \
  --set redis.clientTls.enabled=true \
  --set redis.auth.password="<the AUTH token>"
```

To point at a self-hosted Redis Cluster or AWS MemoryDB instead, skip the
bundled subchart and give the app the external endpoints. Cluster mode has
no Celery transport, so also set `celery.brokerUrl` to a standalone Redis:

```bash
helm upgrade --install pipeshub-ai ./deployment/helm/pipeshub-ai \
  ... \
  --set redis.enabled=false \
  --set redis.mode=cluster \
  --set redis.external.enabled=true \
  --set redis.external.clusterEndpoints="my-cluster.abcdef.memorydb.us-east-1.amazonaws.com:6379" \
  --set redis.clientTls.enabled=true \
  --set redis.auth.password="<the cluster/MemoryDB AUTH token>"
```

| Value | Purpose |
|---|---|
| `redis.enabled` | Set `false` to skip deploying the Bitnami subchart entirely (wired via `condition: redis.enabled` in `Chart.yaml`). Leave `true` for the default bundled Redis. |
| `redis.mode` | `standalone` (default) or `cluster`. Any other value is resolved via `redis.providerModule`, e.g. an enterprise-only MemoryDB provider that adds IAM auth rotation. |
| `redis.external.enabled` / `redis.external.host` | Standalone primary, such as ElastiCache with cluster mode disabled. Requires `redis.mode=standalone`. Do not set `clusterEndpoints` as well. |
| `redis.external.clusterEndpoints` | External Redis Cluster or MemoryDB. Comma-separated `host:port` list. Requires a cluster-capable `redis.mode`. |
| `redis.clientTls.*` | TLS for the app's client connection to `redis.external.host` or `redis.external.clusterEndpoints`. Deliberately not named `redis.tls` — that key is reserved by the Bitnami subchart for its own in-cluster server TLS and configures a different thing. |
| `redis.keyNamespace` | Prefix applied inside key builders (not a client-level prefix) so multiple releases/tenants can share one Redis/MemoryDB. Covers the config KV store and its invalidation channel, the session/app cache, the accessible-records cache, retry counters, leases, and the BullMQ crawling queue. It does **not** cover Redis Streams topic or consumer-group names, so setting it together with `MESSAGE_BROKER=redis` is **rejected at startup** rather than silently letting two releases consume each other's messages. Use a separate Redis per deployment for the stream broker, or `MESSAGE_BROKER=kafka`. |
| `redis.clusterScaleReads` | `master` (default) \| `slave` \| `all` — cluster-mode read distribution. |
| `redis.providerModule` | npm/Python module path for a `REDIS_MODE` this chart doesn't know natively (enterprise providers). |
| `celery.brokerUrl` / `celery.resultBackend` | Celery has no Redis Cluster transport; required whenever `redis.mode` isn't `standalone` — point these at a plain standalone Redis (can be a small dedicated instance, does not need to be the same backend as the KV/cache/streams Redis). |

Redis Cluster and MemoryDB never support `SELECT` (`REDIS_DB`); it is only
honoured when `redis.mode` is `standalone` (and a non-standalone `redis.mode` requires `redis.external.enabled=true` — the bundled subchart is a replication deployment, not a Redis Cluster). Use `redis.keyNamespace` for
isolation instead.

## Qdrant API key

Qdrant and the app share one API key, stored as `qdrant-api-key` in the
release Secret. The chart has no default for it. With chart-created secrets
(the inline mode) it refuses to render unless `qdrant.apiKey` is set, and it
refuses the old shared defaults (`api_key`, `qdrant`, `your_qdrant_api_key`,
`your_qdrant_secret_api_key`). ArangoDB likewise refuses `arango.auth.rootPassword=root`.

Generate the key once and keep it with your other secrets:

```bash
openssl rand -hex 32
```

On a later `helm upgrade` against the cluster you may leave `qdrant.apiKey`
unset: the chart reuses the key already in `<release>-pipeshub-ai-secrets`
(or `pipeshub-ai-secrets` for the release name used above). `helm template`,
`--dry-run=client`, Argo CD and Flux cannot read the cluster, so they need the
key in values every time.

### Upgrading a release that still uses `api_key`

Inline-mode releases (the default `secretManagement`) installed from earlier
chart versions without `qdrant.apiKey` run with the public default `api_key`.
The first upgrade to this chart fails until you set a new key. Releases on
`existingSecrets` or `externalSecrets` upgrade without one; rotate the key in
your secret store as described at the end of this section. Qdrant stores no data under the key; it only checks it on each
request, so collections stay readable after the change.

In inline mode, both the Qdrant StatefulSet and the app Deployment carry a
`checksum/qdrant-api-key` annotation, so changing the key restarts both. Until
both have restarted, requests made with the old key to a pod that has the new
one fail with 401. To avoid that window, stop the app first:

```bash
KEY="$(openssl rand -hex 32)"   # store it before continuing

# 1. App stopped, and its pods gone: a terminating pod still holds the old key.
#    kubectl, not helm: this chart refuses to render while the release holds
#    `api_key`. A HorizontalPodAutoscaler leaves a Deployment at 0 alone.
kubectl scale deployment/pipeshub-ai -n <namespace> --replicas=0
kubectl wait --for=delete pod -n <namespace> --timeout=10m \
  -l app.kubernetes.io/instance=pipeshub-ai,app.kubernetes.io/component=main

# 2. Qdrant restarted on the new key, app still stopped
helm upgrade pipeshub-ai ./deployment/helm/pipeshub-ai --reuse-values \
  --set qdrant.apiKey="$KEY" --set replicaCount=0 --set autoscaling.enabled=false
kubectl rollout status statefulset/pipeshub-ai-qdrant -n <namespace>

# 3. App back, with your usual replicaCount / autoscaling values
helm upgrade pipeshub-ai ./deployment/helm/pipeshub-ai --reuse-values \
  --set replicaCount=<n> --set autoscaling.enabled=<true|false>
```

A single `helm upgrade --set qdrant.apiKey="$KEY"` also works if a short burst
of failed indexing and search requests is acceptable.

The services pick up the new key on the first start. The app pod receives it
as `QDRANT_API_KEY`. The Node.js process writes it to the KV store
(`/services/qdrant`) during startup, before its health check passes. The
Python services start only after that check, and read the key once when they
connect to Qdrant. There is no KV migration to run. The KV entry is shared by
every app pod, and an old pod that is still running can write its old key back
when certain admin requests reach it. Stopping the app first, as above, avoids
pods with mixed keys.

`helm rollback` restores the previous Secret, and the annotation restarts both
sides again.

With `existingSecrets` or `externalSecrets` the chart neither reads nor checks
the key, and adds no checksum annotation. After rotating the key there, restart
both sides yourself:

```bash
kubectl rollout restart statefulset/pipeshub-ai-qdrant deployment/pipeshub-ai -n <namespace>
```

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

The Secret must hold a random `qdrant-api-key` (for example
`openssl rand -hex 32`). The chart does not check its value.

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

## Coding sandbox

`config.sandboxMode` picks where `run_code` executes: `broker` (the default with `hardened=true`), `docker` (the default
otherwise),
`e2b` (off-cluster), or `local` (no isolation, development only, needs
`config.sandboxAllowLocal=true`). `docker` needs a daemon, and the chart
refuses to install without one:

- `sandbox.dind.enabled=true` (the `values-eks.yaml` default) adds two sidecars.
  `dind` is a **privileged** Docker daemon that listens only on a unix socket
  in the `dind-sock` emptyDir. `docker-proxy` runs `python -m app.docker_proxy_main`
  from the application image, which is the same policy proxy the Compose
  stacks use. It listens on `127.0.0.1:<sandbox.dind.port>` (the app's
  `DOCKER_HOST`) and admits only sandbox-shaped requests: creates from the
  allowed images (`config.sandboxDockerImage`, or `sandbox.proxy.allowedImages`)
  with no privileged mode, host mounts, devices or host namespaces, acting
  only on containers it created. Only `dind` and `docker-proxy` mount
  `dind-sock`. The application container cannot reach the daemon directly.
- `config.dockerHost` points at a daemon you run. Put the same proxy in front
  of it. Never expose a raw dockerd.

Mounting a container-runtime socket into the application container would give
the app, and any code it runs, root on the node. At render time the chart refuses
`extraVolumes`/`volumes` and `extraVolumeMounts`/`volumeMounts` that:

- use a `hostPath` of type `Socket`;
- use a `hostPath` that is a Docker, containerd (including k3s), CRI-O, podman or
  cri-dockerd socket, or a directory that holds one, such as `/`, `/run` or
  `/var/run`;
- name one of those sockets in `mountPath`, `subPath` or `subPathExpr`.

These checks are best-effort, not a security boundary. A runtime socket in an
unusual place, or one reached through a symlink, is not recognised. Review any
custom `hostPath`. Where you can, enforce it in the cluster too, for example with
a Kyverno or Gatekeeper policy that forbids `hostPath` volumes on the application
pod.

**Image requirement:** the `docker-proxy` sidecar needs an application image
that contains `app.docker_proxy_main` (the release that added the Compose
Docker proxy, or later). On an older `image.tag` the sidecar crash-loops and
`run_code` fails. Upgrade `image.tag`, or set `sandbox.proxy.image` to a newer
image.

## High-Value Features

- Secret validation with fail-fast messages
- Optional startup/readiness/liveness probe tuning
- Optional extra volumes and mounts
- Optional service account creation
- Optional PDB, NetworkPolicy
- Product telemetry switch: `telemetry.collection.enabled: false` sets `TELEMETRY_COLLECTION_FLAG=false`

## Security profile: `hardened`

`hardened` (default `false`) picks the defaults that would break a release upgraded from a chart without them
(ADR-OPS-07). The chart never guesses whether a render is an install or an upgrade (`helm template` in GitOps cannot
tell), so:

- **New install:** pass `--set hardened=true`.
- **Upgrade of an existing release:** leave it unset. Nothing below changes until you set it; NOTES says which
  profile the release runs on every `helm upgrade`.

| Value (unset follows `hardened`) | `hardened=false` (compatibility) | `hardened=true` |
|---|---|---|
| `networkPolicy.enabled`, `networkPolicy.namespaceDefaultDeny` | no NetworkPolicy, as before ADR-OPS-04 | default-deny plus one allow per component pair |
| `securityContext.readOnlyRootFilesystem` (app, sandbox broker) | `false` | `true` |
| `config.sandboxMode` | `docker` (app-pod DinD behind the policy proxy) | `broker` (a separate sandbox-broker pod) |
| `kafka.auth.enabled` | PLAINTEXT listeners, no login in the app | SCRAM-SHA-512 on both listeners (SEC-D-06) |
| `mongodb.builtin.nonRoot` | the entrypoint starts as root and drops to `mongodb` | `mongodb` (999) from the start, restricted container |
| `qdrant.podSecurityContext` / `securityContext` (empty) | the `-unprivileged` image run as root, so files an earlier root-running image wrote stay writable | uid 1000, non-root, restricted container |
| `PIPESHUB_HARDENED` in the app | `false`: the services' compatibility profile | `true`: the hardened profile |

An explicit value for any row wins over `hardened`. Before you turn on `hardened` for an existing release: Qdrant's
volume must be writable by uid 1000 (`fsGroupChangePolicy: OnRootMismatch` hands over only a volume whose root is not
yet group 1000), turning on Kafka's login restarts the brokers one by one onto SASL, and the network policy needs a
CNI that enforces it and the right `networkPolicy.ingressControllers`. What changes in the services is in
[HARNESS-OFF.md](../../../docs/agent-harness-v2/guides/HARNESS-OFF.md), "Security profile".

Unchanged by the profile: the image tag. Empty `image.tag` renders the chart's `appVersion`, where the chart before
rendered `latest`; a chart upgrade then moves to that release's image, which is what upgrading the chart means.

## Agent harness

The agent harness (Beta) runs by default: `agentHarness.enabled: true` (it renders `AGENT_HARNESS_WORKER_ENABLED`
and `AGENT_HARNESS_RELAY_ENABLED`) starts the relay, worker and gateways, idle until an org admin turns on Agent
harness in Labs, which is off by default. `--set agentHarness.enabled=false` runs none of them and serves no harness
route, Labs flag or screen. What the switch covers, and the platform
changes every install gets whatever it says (network policy, non-root stores, store credentials):
[HARNESS-OFF.md](../../../docs/agent-harness-v2/guides/HARNESS-OFF.md).

## Metrics

The services push product telemetry to the collector (see `telemetry.collection.enabled`). The one scrape port is Node's: with `metrics.enabled` (the default) it serves its event-loop utilization on `metrics.port` at `/metrics`, the signal `autoscaling.eventLoopUtilization` scales on. Each pod carries `prometheus.io/scrape`, `prometheus.io/port` and `prometheus.io/path` annotations for it. Scrape the pods (Prometheus' `kubernetes_sd_configs` role `pod`, or a ServiceMonitor, which scrapes the Service's endpoints one by one), never the Service's address: that answers from a different replica each time, so with two replicas a counter or gauge alternates between them. The earlier `monitoring.serviceMonitor` and `telemetry.otlp` values configured nothing and were removed.

**Agent harness metrics.** Node's merged harness route (its own counters such as `pipeshub_auth_attempts_total`, the gateways', the sandbox broker's and the run store's gauges; the alert rules in `deployment/monitoring/prometheus/agent-harness.rules.yml`) listens only on the pod's loopback (`config.agentHarnessInternalPort`) and takes a scrape credential Node rotates, so no Prometheus outside the pod can read it. Set `metrics.harness.enabled=true` and `metrics.harness.remoteWrite.url` (a Prometheus started with `--web.enable-remote-write-receiver`, or any remote-write receiver): each pod then runs a Prometheus agent that scrapes its own Node with the credential Node writes to a memory-backed volume only the two containers share, and remote-writes every series with `pod` and `namespace` labels. Replicas are separate series: the rules add counters with `sum` and read gauges with `max`, which is right for both per-pod and shared series (the sandbox broker and the run store appear once per pod). With NetworkPolicy on and an in-cluster receiver, list it in `metrics.harness.remoteWrite.peers` (and `remoteWrite.port`); `remoteWrite.bearerTokenSecret` names a Secret key for a receiver that wants a token. Design: ADR-OPS-05.

## Validation

```bash
helm lint ./deployment/helm/pipeshub-ai
helm template pipeshub-ai ./deployment/helm/pipeshub-ai \
  -f ./deployment/helm/pipeshub-ai/values-local.yaml \
  --set secretKey="test" \
  --set mongodb.auth.rootPassword="test" \
  --set redis.auth.password="test" \
  --set neo4j.auth.password="test" \
  --set qdrant.apiKey="test" \
  --set "mongodb.auth.usernames[0]=pipeshub" \
  --set "mongodb.auth.passwords[0]=test" \
  --set "mongodb.auth.databases[0]=pipeshub"
```

CI runs two broader checks on every pull request that touches the chart, and
weekly (`.github/workflows/helm-chart.yml`). Both run locally too:

```bash
# Lint, render and schema-check every supported configuration, and confirm
# the misconfigurations the chart refuses are still refused (helm, kubeconform)
bash deployment/helm/tests/check_chart.sh

# Install into a throwaway single-node kind cluster and wait for every core
# service to report healthy (kind, kubectl, helm, docker)
bash deployment/helm/tests/kind_smoke.sh
SMOKE_VARIANT=arangodb-redis bash deployment/helm/tests/kind_smoke.sh
```

## Production Checklist

- Set all passwords, `secretKey` and `qdrant.apiKey` securely. The app reads `secretKey` from a file in a Secret
  volume (`secretKeyAsFile: true`, the default; ADR-KEY-02 D8), so it is not in the pod's environment
- Configure ingress/TLS
- Enable `podDisruptionBudget` for HA
- Set `hardened=true` (new installs), which turns `networkPolicy` on (ADR-OPS-04); keep it on, on a CNI that enforces it, and set `networkPolicy.ingressControllers`, `clusterCidrs` and, for a KV store outside the cluster, `networkPolicy.sandboxBroker.extraEgress`
- Check the open-file limit the MongoDB pods run with (see Open-file limits); 64000 is what mongod asks for
- Validate upgrade path in non-production first
