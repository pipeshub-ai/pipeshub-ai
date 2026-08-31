# Advanced Deployment Options

This document covers non-interactive and manual deployment scenarios for PipesHub.
For the standard interactive install, see the [Deployment Guide in the main README](../../README.md#-deployment-guide).

---

## Contents

- [Standalone one-command install](#standalone-one-command-install)
- [Deployment types (slim vs. full)](#deployment-types-slim-vs-full)
- [Environment overrides for CI / scripted installs](#environment-overrides-for-ci--scripted-installs)
- [Upgrading an existing Compose install](#upgrading-an-existing-compose-install)
- [A second instance on the same host](#a-second-instance-on-the-same-host)
- [Manual deployment with Compose profiles](#manual-deployment-with-compose-profiles)
- [Secrets and configuration](#secrets-and-configuration)
- [Runtime tuning (workers and query-service flags)](#runtime-tuning-workers-and-query-service-flags)
- [Container outbound connectivity](#container-outbound-connectivity)
- [Developer / local build](#developer--local-build)
- [Soak-testing adaptive concurrency](#soak-testing-adaptive-concurrency)

---

## Standalone one-command install

The recommended quickstart downloads and runs the installer without cloning:

```bash
curl -fsSL https://get.pipeshub.com/install | bash
```

`get.pipeshub.com/install` is a 302 to
[`https://raw.githubusercontent.com/pipeshub-ai/pipeshub-ai/main/install.sh`](https://raw.githubusercontent.com/pipeshub-ai/pipeshub-ai/main/install.sh)
(the root [`install.sh`](../../install.sh) wrapper). No extra hosting is required.
The wrapper then downloads `docker-compose.yml` and the in-tree installer for the
latest GitHub **release** (or `PIPESHUB_REF`) into `./pipeshub` and runs the same
wizard.

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
| `PIPESHUB_PORT` | host port | `3000` (`3200` for a second copy) |
| `PIPESHUB_PROJECT` | Compose project name | `pipeshub-ai` |
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

## Upgrading an existing Compose install

The next `./install.sh` or `./install.sh --upgrade` on a stack that still uses the old pinned names (`container_name: pipeshub-ai`, network `pipeshub-ai_network`) recreates every container. Data volumes are unchanged (they key off the Compose project name).

After that start:

- Container names are `{project}-{service}-1` (for the default project: `pipeshub-ai-pipeshub-ai-1`, `pipeshub-ai-mongodb-1`, …).
- `docker exec pipeshub-ai …` and `docker logs pipeshub-ai` no longer find a container. Use the service name:

```bash
docker compose -p pipeshub-ai exec -T pipeshub-ai bash
docker compose -p pipeshub-ai logs -f pipeshub-ai
```

- Compose creates a new network (`pipeshub-ai_pipeshub`) and does not delete `pipeshub-ai_network`. If that old network is unused: `docker network rm pipeshub-ai_network`.

The installer prints this when it detects the old pinned names.

---

## A second instance on the same host

The first install is always Compose project `pipeshub-ai` on port `3000` (or the next free port). The installer does **not** ask new users for a project name.

If `pipeshub-ai` is already running from another directory, the interactive installer offers:

1. **Update the existing stack** — manage that copy from this directory (same data and port).
2. **Install a separate instance here** — new project name, new volumes, default port **3200**. This is a full extra copy (RAM); prefer slim.
3. **Abort**

`--yes` without `PIPESHUB_PROJECT` always targets `pipeshub-ai`. It never creates a second stack on its own.

```bash
# Scripted second copy (does not touch the stack on port 3000)
PIPESHUB_PROJECT=pipeshub-eval PIPESHUB_PORT=3200 PIPESHUB_DEPLOY_TYPE=slim \
  ./install.sh --yes
```

The installer writes `COMPOSE_PROJECT_NAME` into `.env`. `--stop` and `--uninstall` in that directory read it, so they tear down **this** copy only.

Exec and logs use the service name with `-p` set to that project (see [Upgrading an existing Compose install](#upgrading-an-existing-compose-install)).

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
DATA_STORE=neo4j \
COMPOSE_PROFILES=graph-neo4j \
  docker compose -p pipeshub-ai up -d
```

### Full (ArangoDB, Kafka, etcd)

```bash
DATA_STORE=arangodb KV_STORE_TYPE=etcd MESSAGE_BROKER=kafka \
COMPOSE_PROFILES=graph-arango,kv-etcd,broker-kafka \
  docker compose -p pipeshub-ai up -d
```

### Stack lifecycle

```bash
# Stop the stack (data preserved)
docker compose -p pipeshub-ai down

# Stop and remove all data volumes (destructive)
docker compose -p pipeshub-ai down -v

# Logs / exec — service name, not a fixed container name
docker compose -p pipeshub-ai logs -f pipeshub-ai
docker compose -p pipeshub-ai exec -T pipeshub-ai bash
```

### Available profiles

| Profile | Service started | When to use |
|---------|----------------|-------------|
| `graph-arango` | ArangoDB | `DATA_STORE=arangodb` |
| `graph-neo4j` | Neo4j | `DATA_STORE=neo4j` |
| `kv-etcd` | etcd | `KV_STORE_TYPE=etcd` |
| `broker-kafka` | Kafka + Zookeeper | `MESSAGE_BROKER=kafka` |

Always-on services (no profile needed): `redis`, `mongodb`, `qdrant`.

> **A profile and its variable must be set together.** `COMPOSE_PROFILES` only decides
> which containers start. Which backend the application talks to comes from the variable
> in the right-hand column. Set the profile alone and you start a container the app never
> connects to, while the app carries on using whatever the variable falls back to.
>
> Those fallbacks live in `docker-compose.yml` and apply **only when no `.env` supplies a
> value**, which is the case for the manual commands above:
>
> | Variable | Fallback in `docker-compose.yml` |
> |----------|----------------------------------|
> | `DATA_STORE` | `arangodb` (line 111) |
> | `KV_STORE_TYPE` | `redis` (line 126) |
> | `MESSAGE_BROKER` | `redis` (line 134) |
>
> These are **not** the defaults you get from `install.sh`, which chooses Neo4j and writes
> `DATA_STORE=neo4j` into `.env`. If you installed with `install.sh`, your `.env` already
> sets all three and none of this applies. The Compose fallback for `DATA_STORE` differs
> from the installer's choice and is due to be aligned; until then, set the variable
> explicitly whenever you drive Compose by hand.

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

## Runtime tuning (workers and query-service flags)

These live in `.env` alongside the rest of your configuration. Every default below
reproduces current behaviour, so an upgrade changes nothing until you opt in.

Compose forwards **only** the variables it enumerates, so a name that is not listed in
the compose file never reaches the container. All of these are wired; setting them in
`.env` is enough.

### Query-service workers

The query service runs in one process by default. Raising this starts that many separate
query processes.

| Variable | Values | Default |
|----------|--------|---------|
| `QUERY_UVICORN_WORKERS` | integer | `1` |

Two per-process budgets the query service can reach are divided by the worker count
(`backend/python/app/utils/worker_scaling.py`), so N workers do not each claim the whole
amount: concurrent LLM calls for chat-attachment enrichment (24, so 6 each at 4 workers)
and the storage connection limit (100, so 25 each).

Everything else still multiplies, so size the host accordingly:

- The PDF rasteriser and docling process pools have a floor of one worker each, so at 4
  query workers you get 4 rasteriser subprocesses rather than the 2 a single process uses.
- Database and broker connection pools are not divided at all — N query workers means N
  Neo4j, Qdrant, Mongo and Redis pools.

Two things to know before raising it:

- Each worker re-imports the connector SDKs at startup (~25 modules), so start-up time and
  resident memory scale with the count. Budget against `APP_MEMORY_LIMIT`.
- Telemetry is reported per process. Each worker's metrics carry a `processId`, so a
  collector that ignores that field will see the workers' counters as one noisy series.

The other services' `*_UVICORN_WORKERS` variables are already forwarded by Compose but are
not documented here.

### Query-service flags

| Variable | Values | Default |
|----------|--------|---------|
| `PIPESHUB_AGENT_TRANSPORT` | `langchain` \| `direct` (`azure_direct` is a deprecated alias for `direct`) | `langchain` |
| `PIPESHUB_ACCESSIBLE_RECORDS_CACHE` | blank (on) \| a disabled value | on |
| `PIPESHUB_ACCESSIBLE_RECORDS_CACHE_TTL` | seconds | `300` |
| `PIPESHUB_SIGNED_URL_CACHE_SECONDS` | seconds, `0` disables, capped at `3000` | `0` |

`direct` calls model providers without the LangChain layer. A provider with no direct
transport, or with credentials it cannot use, falls back to LangChain for that turn
rather than failing it; an unrecognised value logs a warning and falls back too.

`PIPESHUB_SIGNED_URL_CACHE_SECONDS` is clamped to 3000 in code, keeping it under the
3600-second signing lifetime so a URL handed out at the end of its cached life still has
time left to use. A value that is not a number falls back to the default rather than
failing to start.

These are read from the container environment, so on an existing install add them to
`.env` yourself — `install.sh --upgrade` reuses your current `.env` and does not append
new keys. Every compose reference carries a default, so an absent key behaves exactly as
the table says.

> **Naming note.** The `PIPESHUB_*` variables in
> [Environment overrides for CI / scripted installs](#environment-overrides-for-ci--scripted-installs)
> are read by `install.sh` *before* `.env` is written and control the installer itself.
> The four above are read by the running service. They share a prefix but not a purpose.

---

## Container outbound connectivity

PipesHub starts and indexes documents **without** outbound internet when models are
already cached locally. The default **slim** image downloads the embedding model
on first use, so leave `HF_HUB_OFFLINE` unset there. Set `HF_HUB_OFFLINE=1` only
on air-gapped hosts that already have the models (or use the full `latest` image).
**Cloud LLMs** (Gemini, OpenAI,
Anthropic, …) and **external connectors** (Google, Slack, Microsoft, …) require
the **`pipeshub-ai` container** to reach the public internet — not just your
browser or host shell.

### Symptoms

- **Add model** for a cloud provider hangs or fails with a connectivity / timeout error
- Connectors fail OAuth or sync with network errors
- Host can reach the internet, but `docker compose exec pipeshub-ai curl …` times out

### Diagnose

```bash
# From the installation directory (Compose reads COMPOSE_PROJECT_NAME from .env).
# Should return quickly (404 is fine; 000 means no route).
docker compose exec -T pipeshub-ai \
  curl -s -o /dev/null -m 6 -w "%{http_code}\n" https://1.1.1.1/
```

The installer prints a **warning** (not a failure) when this check fails, so
air-gapped deployments still complete.

### Common fixes

| Cause | Fix |
|-------|-----|
| Docker `"iptables": false` in `/etc/docker/daemon.json` | Remove the setting or set `"iptables": true`, then `sudo systemctl restart docker` and `docker compose up -d` |
| UFW blocking forwarded traffic (Linux) | Set `DEFAULT_FORWARD_POLICY="ACCEPT"` in `/etc/default/ufw`, then `sudo ufw reload` |
| Corporate VPN / firewall | Allow egress from the Docker bridge subnet (e.g. `172.18.0.0/16`) |
| Intentionally air-gapped | Use local models only (Ollama, LM Studio, built-in embeddings) — no fix required |

After changing Docker daemon settings, **restart Docker and bring the stack back up**:

```bash
sudo systemctl restart docker
cd deployment/docker-compose && ./install.sh --stop && ./install.sh
```

---

## Developer / local build

For building from source instead of pulling prebuilt images, clone the repository and run the installer from the repo root:

```bash
git clone https://github.com/pipeshub-ai/pipeshub-ai.git
cd pipeshub-ai
./install.sh --build
```

Equivalent Compose command if you prefer to drive the build file yourself:

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
`pipeshub-ai` container's CPU quota — one heavy-parse slot per CPU, ten
light-parse slots per CPU, and 100× the wider parse tier for indexing —
capped by `MAX_CONCURRENT_PARSING` / `MAX_CONCURRENT_INDEXING` when those
are set (see [`env.template`](env.template)). Leave them unset so new
images size from CPU. Hub slim still `int()`s empty strings; compose
unsets blanks at start so slim uses its built-in defaults instead of
crashing. Set an integer only if you want a hard cap. The indexing
figure is the budget for heavy and light records *combined*, and it is
fixed for the life of the process. Only parsing and downloads adapt at
runtime. These two runs are a manual regression check before a release;
they are not part of CI.

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

1. In `.env`, set `APP_MEMORY_LIMIT=8G`. Leave `MAX_CONCURRENT_PARSING` /
   `MAX_CONCURRENT_INDEXING` / `MAX_PENDING_INDEXING_TASKS` unset.
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
