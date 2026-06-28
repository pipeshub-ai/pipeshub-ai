# Advanced Deployment Options

This document covers non-interactive and manual deployment scenarios for PipesHub.
For the standard interactive install, see the [Deployment Guide in the main README](../../README.md#-deployment-guide).

---

## Contents

- [Standalone one-command install](#standalone-one-command-install)
- [Deployment types (slim vs. full)](#deployment-types-slim-vs-full)
- [Environment overrides for CI / scripted installs](#environment-overrides-for-ci--scripted-installs)
- [Manual deployment with Compose profiles](#manual-deployment-with-compose-profiles)
- [Secrets and configuration](#secrets-and-configuration)
- [Developer / local build](#developer--local-build)

---

## Standalone one-command install

The recommended quickstart downloads and runs the installer without cloning:

```bash
curl -fsSL https://get.pipeshub.com/install | bash
```

`get.pipeshub.com/install` is a redirect to the root [`install.sh`](../../install.sh)
published as a GitHub release asset — no extra hosting infrastructure is required.
In standalone mode the wrapper downloads `docker-compose.yml` and the in-tree
installer for the resolved release into `./pipeshub`, then runs the same wizard.

Standalone-only overrides:

| Variable | Values | Default |
|----------|--------|---------|
| `PIPESHUB_REF` | branch, tag, or commit SHA to install | latest release, else `main` |
| `PIPESHUB_DIR` | directory to download deployment files into | `./pipeshub` |

```bash
# Install a specific tag into a custom directory
PIPESHUB_REF=v0.7.0 PIPESHUB_DIR=/opt/pipeshub \
  bash -c "$(curl -fsSL https://get.pipeshub.com/install)"
```

Standalone mode installs prebuilt images only. Building from source (`--build`)
requires a full clone — see [Developer / local build](#developer--local-build).

---

## Deployment types (slim vs. full)

| | **Slim** | **Full** |
|---|---|---|
| Image | `pipeshubai/pipeshub-ai:slim` | `pipeshubai/pipeshub-ai:latest` |
| Embedding model | Downloaded on first use | Bundled in image (~1.3 GB extra) |
| Graph DB (default) | Neo4j | Neo4j |
| Broker (default) | Redis Streams | Kafka + Zookeeper |
| KV store (default) | Redis | Redis |
| Recommended for | Laptops, evaluations | Production, air-gapped servers |

Both deployment types default to **Neo4j** for the graph DB and **Redis** for the
KV store; the difference is the bundled embedding model and the message broker
(Redis Streams for slim, Kafka for full). ArangoDB and etcd are opt-in — choose
them at the prompts or via `PIPESHUB_GRAPH_DB` / `PIPESHUB_KV_STORE`.

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

## Secrets and configuration

The installer generates strong, random credentials for you — database passwords,
API keys, and the application secret key — and stores them in
`deployment/docker-compose/.env`, the single configuration file for your
deployment.

What the installer does to protect them:

- Creates `.env` with owner-only permissions (`chmod 600`), so other users on the
  machine cannot read it.
- Keeps `.env` out of version control (it is listed in `.gitignore`), so secrets
  are never committed.
- Generates a unique random value per install — there are no shared or default
  passwords.

Worth knowing:

- As with essentially all Docker Compose deployments, values in `.env` are stored
  as plain text and passed to containers as environment variables. Anyone with
  root or Docker access on the host can read them, so treat host access as
  equivalent to credential access.
- `--reconfigure` saves timestamped `.env.bak.*` backups (also owner-only). These
  contain previous secrets — remove ones you no longer need.

### Using an external secrets manager (optional, for stricter environments)

The defaults above are appropriate for most self-hosted, single-tenant
deployments on a trusted host. If your security policy requires that secrets
never be written to disk in plain text, you can supply them from a secrets
manager instead of `.env` — for example Docker/Swarm secrets, HashiCorp Vault,
or your cloud provider's KMS / Secrets Manager — and inject the values into the
containers at runtime. The Compose services read standard environment variables,
so any tool that can populate the container environment will work.

---

## Developer / local build

For building from source instead of pulling prebuilt images:

```bash
cd pipeshub-ai/deployment/docker-compose

# Build and start (Neo4j variant)
docker compose -f docker-compose.build.neo4j.yml -p pipeshub-ai up --build -d

# Stop
docker compose -f docker-compose.build.neo4j.yml -p pipeshub-ai down
```

The main `Dockerfile` pulls pre-built base layers from `pipeshubai/pipeshub-ai-base:python-deps` and `pipeshubai/pipeshub-ai-base:runtime` (see [`Dockerfile.base`](Dockerfile.base) for build/push instructions).  
Override the base images with `PYTHON_DEPS_IMAGE` / `RUNTIME_BASE_IMAGE` environment variables to use locally built tags.
