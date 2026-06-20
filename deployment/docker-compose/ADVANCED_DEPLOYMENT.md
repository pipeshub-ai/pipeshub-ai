# Advanced Deployment Options

This document covers non-interactive and manual deployment scenarios for PipesHub.
For the standard interactive install, see the [Deployment Guide in the main README](../../README.md#-deployment-guide).

---

## Contents

- [Deployment types (slim vs. full)](#deployment-types-slim-vs-full)
- [Environment overrides for CI / scripted installs](#environment-overrides-for-ci--scripted-installs)
- [Manual deployment with Compose profiles](#manual-deployment-with-compose-profiles)
- [Developer / local build](#developer--local-build)

---

## Deployment types (slim vs. full)

| | **Slim** | **Full** |
|---|---|---|
| Image | `pipeshubai/pipeshub-ai:slim` | `pipeshubai/pipeshub-ai:latest` |
| Embedding model | Downloaded on first use | Bundled in image (~1.3 GB extra) |
| Graph DB (default) | Neo4j | ArangoDB |
| Broker (default) | Redis Streams | Kafka + Zookeeper |
| KV store (default) | Redis | etcd |
| Recommended for | Laptops, evaluations | Production, air-gapped servers |

**Slim** uses no extra broker or KV-store containers (Redis handles both).  
**Full** pre-bakes the [BAAI/bge-large-en-v1.5](https://huggingface.co/BAAI/bge-large-en-v1.5) embedding model so the first query does not stall waiting for a download.

---

## Environment overrides for CI / scripted installs

All variables are optional. When set, they suppress the corresponding interactive prompt.

| Variable | Values | Default |
|----------|--------|---------|
| `PIPESHUB_DEPLOY_TYPE` | `full` \| `slim` | interactive |
| `PIPESHUB_GRAPH_DB` | `arango` \| `neo4j` | per deploy type |
| `PIPESHUB_BROKER` | `kafka` \| `redis` | per deploy type |
| `PIPESHUB_KV_STORE` | `etcd` \| `redis` | per deploy type |
| `PIPESHUB_VERSION` | image tag, e.g. `latest`, `slim`, `0.7.0` | `latest` / `local` |
| `PIPESHUB_IMAGE_SOURCE` | `prebuilt` \| `local` | `prebuilt` |
| `PIPESHUB_PORT` | host port | `3000` |
| `PIPESHUB_PUBLIC_URL` | public HTTPS URL | _(none)_ |

### Example — fully non-interactive slim install

```bash
PIPESHUB_DEPLOY_TYPE=slim \
PIPESHUB_GRAPH_DB=neo4j \
PIPESHUB_BROKER=redis \
PIPESHUB_KV_STORE=redis \
  ./install.sh --yes
```

### Example — pin a specific version in CI

```bash
PIPESHUB_DEPLOY_TYPE=full \
PIPESHUB_VERSION=0.7.0 \
  ./install.sh --yes --print-env-only
```

`--print-env-only` writes `.env` and prints the Compose command without starting containers, which is useful for inspecting the generated config in a pipeline before launch.

---

## Manual deployment with Compose profiles

The unified [`docker-compose.yml`](docker-compose.yml) uses [Compose profiles](https://docs.docker.com/compose/profiles/) to toggle optional services. You can drive it directly without the installer:

```bash
cd pipeshub-ai/deployment/docker-compose

# Copy the template and edit secrets / URLs
cp env.template .env
$EDITOR .env
```

### Slim (Neo4j, Redis Streams, Redis KV)

```bash
COMPOSE_PROFILES=graph-neo4j \
  docker compose -p pipeshub-ai up -d
```

### Full (ArangoDB, Kafka, etcd)

```bash
COMPOSE_PROFILES=graph-arango,kv-etcd,broker-kafka \
  docker compose -p pipeshub-ai up -d
```

### Stack lifecycle

```bash
# Stop the stack (data preserved)
docker compose -p pipeshub-ai down

# Stop and remove all data volumes (destructive)
docker compose -p pipeshub-ai down -v
```

### Available profiles

| Profile | Service started | When to use |
|---------|----------------|-------------|
| `graph-arango` | ArangoDB | `DATA_STORE=arangodb` |
| `graph-neo4j` | Neo4j | `DATA_STORE=neo4j` |
| `kv-etcd` | etcd | `KV_STORE_TYPE=etcd` |
| `broker-kafka` | Kafka + Zookeeper | `MESSAGE_BROKER=kafka` |

Always-on services (no profile needed): `redis`, `mongodb`, `qdrant`.

---

## Developer / local build

For building from source instead of pulling prebuilt images:

```bash
cd pipeshub-ai/deployment/docker-compose

# Build and start (Neo4j variant)
docker compose -f docker-compose.build.neo4j.yml -p pipeshub-ai up --build -d

# Build and start (ArangoDB variant)
docker compose -f docker-compose.build.arango.yml -p pipeshub-ai up --build -d

# Stop
docker compose -f docker-compose.build.neo4j.yml -p pipeshub-ai down
```

The main `Dockerfile` pulls pre-built base layers from `pipeshubai/pipeshub-ai-base:python-deps` and `pipeshubai/pipeshub-ai-base:runtime` (see [`Dockerfile.base`](Dockerfile.base) for build/push instructions).  
Override the base images with `PYTHON_DEPS_IMAGE` / `RUNTIME_BASE_IMAGE` environment variables to use locally built tags.
