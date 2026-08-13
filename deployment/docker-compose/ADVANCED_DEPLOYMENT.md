# Advanced Deployment Options

This document covers non-interactive and manual deployment scenarios for PipesHub.
For the standard interactive install, see the [Deployment Guide in the main README](../../README.md#-deployment-guide).

---

## Contents

- [Deployment types (slim vs. full)](#deployment-types-slim-vs-full)
- [Environment overrides for CI / scripted installs](#environment-overrides-for-ci--scripted-installs)
- [Manual deployment with Compose profiles](#manual-deployment-with-compose-profiles)
- [Developer / local build](#developer--local-build)
- [Soak-testing adaptive concurrency](#soak-testing-adaptive-concurrency)

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

# Stop
docker compose -f docker-compose.build.neo4j.yml -p pipeshub-ai down
```

The main `Dockerfile` pulls pre-built base layers from `pipeshubai/pipeshub-ai-base:python-deps` and `pipeshubai/pipeshub-ai-base:runtime` (see [`Dockerfile.base`](Dockerfile.base) for build/push instructions).  
Override the base images with `PYTHON_DEPS_IMAGE` / `RUNTIME_BASE_IMAGE` environment variables to use locally built tags.

---

## Soak-testing adaptive concurrency

The indexing/parsing pipeline sizes its own concurrency from the
`pipeshub-ai` container's CPU quota — one heavy-parse slot per CPU, three
light-parse slots per CPU, and twice the wider parse tier for indexing —
capped by `MAX_CONCURRENT_PARSING` / `MAX_CONCURRENT_INDEXING` (see
[`env.template`](env.template)). The indexing figure is the budget for
heavy and light records *combined*, and it is fixed for the life of the
process: an indexing slot is pipeline width, not a resource reservation —
what a record actually consumes is bounded by the parse slots, the download
byte budget and the LLM-call limit. Only parsing and downloads adapt at
runtime: they ramp toward their ceiling while resources allow and shrink
under memory or CPU pressure, and heavy parsing is held further back
whenever free memory can't hold that many Docling working sets at once.
These two runs are a manual regression check for that behaviour before a
release; they are not part of CI.

Both commands below assume the compose project is up (`docker compose -p
pipeshub-ai up -d`) and run against the always-on `pipeshub-ai` container —
`8091`/`8092` (indexing/parsing health) and the app's own logs are not
published to the host, so reach them with `docker compose exec`/`logs`
rather than a host-side `curl`.

```bash
# Resource-governor snapshot for each service (ceilings, current limits,
# per-pool demand). Re-run this throughout a soak to watch limits move.
docker compose -p pipeshub-ai exec pipeshub-ai curl -s http://localhost:8091/health | jq .resource_governor
docker compose -p pipeshub-ai exec pipeshub-ai curl -s http://localhost:8092/health | jq .resource_governor

# Every limit change the governor makes, with the pressure/CPU reading that
# triggered it (one INFO line per change, not per sample).
docker compose -p pipeshub-ai logs -f pipeshub-ai | grep --line-buffered "ResourceGovernor limits changed"

# Container RSS vs. its cgroup ceiling, live.
docker stats pipeshub-ai
```

### 1. Mixed-size upload under a tight memory ceiling

Reproduces plan section 10.1: a constrained container must shrink under
memory pressure, keep small files completing while large ones are still
parsing, and finish every record without an OOM kill — with or without an
operator-pinned `MAX_CONCURRENT_INDEXING`.

1. In `.env`, set `APP_MEMORY_LIMIT=8G` and leave `MAX_CONCURRENT_PARSING` /
   `MAX_CONCURRENT_INDEXING` / `MAX_PENDING_INDEXING_TASKS` empty (the
   shipped default — derived from the container's own limits).
2. `docker compose -p pipeshub-ai up -d --force-recreate pipeshub-ai`
3. Upload roughly 200 files through a knowledge base: ~150 small
   Markdown/CSV files and ~50 large PDFs, including a few scanned
   (image-only, OCR-requiring) ones.
4. While the batch runs, watch `docker stats pipeshub-ai` and the two
   commands above.
5. **Expect:** RSS plateaus below ~85% of `APP_MEMORY_LIMIT` (the governor's
   hard-pressure threshold) and the container is never OOM-killed
   (`docker compose -p pipeshub-ai ps` shows no restart); the limits-changed
   log shows at least one shrink while the PDFs are parsing, and a later
   recovery once the batch drains; several Markdown/CSV records reach a
   terminal `indexingStatus` while PDFs are still `IN_PROGRESS`; every
   record eventually reaches a terminal status (`COMPLETED`, `EMPTY`, or
   `FAILED` — none stuck `IN_PROGRESS`/`QUEUED`).
6. Repeat with `MAX_CONCURRENT_INDEXING=200` pinned and re-run step 2-5.
   **Expect:** the same outcome — the governor's derived ceiling (visible in
   `ceilings.index` from the `/health` snapshot) still caps effective
   concurrency well under 200, so a deliberately reckless operator setting
   does not change the result.

### 2. Small-record connector sync (Confluence/Jira shape)

Reproduces plan section 10.2 and is the operational proof for the
regression tests in `tests/integration/test_small_record_scaling.py`: many
millisecond-scale records must ramp `LIGHT_PARSE` concurrency up on an
idle-CPU host, rather than aliasing to "no demand" or capping on
`cpu_quota`.

1. On a host with idle/low background CPU load, run a Confluence or Jira
   sync of at least 5,000 records (a full-space/project backfill, or a
   connector's reindex action).
2. Poll `.resource_governor.limits` and `.resource_governor.demand` from the
   indexing `/health` snapshot (command above) every few seconds for the
   duration of the sync.
3. **Expect:** `limits.light_parse` ramps up from its floor (half the
   ceiling) over the first several samples rather than sitting there for the
   whole sync; `limits.index` sits at `ceilings.index` from the first
   sample and never moves;
   `resource_governor.cpu_utilisation` in the same snapshot reads as a real
   interval mean (comparable to what `top`/`docker stats` shows for the
   container), not ~0%, even though each record is milliseconds of work.
