# Advanced Deployment Options

This document covers non-interactive and manual deployment scenarios for PipesHub.
For the standard interactive install, see the [Deployment Guide in the main README](../../README.md#-deployment-guide).

---

## Contents

- [Standalone one-command install](#standalone-one-command-install)
- [Deployment types (slim vs. full vs. eval)](#deployment-types-slim-vs-full-vs-eval)
- [Environment overrides for CI / scripted installs](#environment-overrides-for-ci--scripted-installs)
- [Upgrading an existing Compose install](#upgrading-an-existing-compose-install)
- [A second instance on the same host](#a-second-instance-on-the-same-host)
- [Manual deployment with Compose profiles](#manual-deployment-with-compose-profiles)
- [Secrets and configuration](#secrets-and-configuration)
- [OAuth dynamic registration and device grant](#oauth-dynamic-registration-and-device-grant)
- [Runtime tuning (workers and query-service flags)](#runtime-tuning-workers-and-query-service-flags)
- [Code sandbox capacity](#code-sandbox-capacity)
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

## Deployment types (slim vs. full vs. eval)

| | **Slim** | **Full** | **Eval** |
|---|---|---|---|
| Image | `pipeshubai/pipeshub-ai:slim` | `pipeshubai/pipeshub-ai:latest` | `pipeshubai/pipeshub-ai:slim` |
| Embedding model | Downloaded on first use | Bundled in image (~1.3 GB extra) | Downloaded on first use |
| Graph DB (default) | Neo4j | Neo4j | Neo4j (required) |
| Broker (default) | Redis Streams | Kafka + Zookeeper | Redis Streams |
| KV store (default) | Redis | Redis | Redis |
| Coding sandbox | Pulled (`sandbox` profile) | Pulled | **Omitted** — `run_code` unavailable |
| Slack bot process | Starts | Starts | Skipped (`PIPESHUB_SKIP_SLACKBOT=true`) |
| RAM class | 16 GB host / 8 GB Docker VM | 16 GB+ | 8 GB-class laptop |
| Recommended for | Laptops, servers | Production, air-gapped | MCP search/ask demos |

Eval is **not** a fourth image tag. It is slim plus a smaller Compose topology.
Neo4j stays: records and ACLs live in the graph store, and search is not proven
without it. Do not drop Neo4j to save RAM.

```bash
PIPESHUB_DEPLOY_TYPE=eval ./install.sh --yes
```

Both slim and full default to **Neo4j** for the graph DB and **Redis** for the
KV store; the difference is the bundled embedding model and the message broker
(Redis Streams for slim, Kafka for full). ArangoDB and etcd are opt-in — choose
them at the prompts or via `PIPESHUB_GRAPH_DB` / `PIPESHUB_KV_STORE`.

**Slim** uses no extra broker or KV-store containers (Redis handles both).  
**Full** pre-bakes the [BAAI/bge-large-en-v1.5](https://huggingface.co/BAAI/bge-large-en-v1.5) embedding model so the first query does not stall waiting for a download.
**Eval** is slim without the sandbox image pull and without the Slack bot process.

---

## Environment overrides for CI / scripted installs

All variables are optional. When set, they suppress the corresponding interactive prompt.

| Variable | Values | Default |
|----------|--------|---------|
| `PIPESHUB_DEPLOY_TYPE` | `full` \| `slim` \| `eval` | interactive |
| `PIPESHUB_GRAPH_DB` | `arango` \| `neo4j` | per deploy type |
| `PIPESHUB_BROKER` | `kafka` \| `redis` | per deploy type |
| `PIPESHUB_KV_STORE` | `etcd` \| `redis` | per deploy type |
| `PIPESHUB_VERSION` | image tag, e.g. `latest`, `slim`, `0.7.0` | `latest` / `local` |
| `PIPESHUB_IMAGE_SOURCE` | `prebuilt` \| `local` | `prebuilt` |
| `PIPESHUB_PORT` | host port | `3000` (`3200` for a second copy) |
| `PIPESHUB_PROJECT` | Compose project name | `pipeshub-ai` |
| `PIPESHUB_PUBLIC_URL` | public HTTPS URL | _(none)_ |
| `APP_MEMORY_LIMIT` | memory ceiling for the app container | derived from the machine (see below) |
| `APP_MEMSWAP_LIMIT` | combined memory+swap ceiling | `APP_MEMORY_LIMIT` + 6G |
| `QDRANT_MEMORY_LIMIT` | memory ceiling for the Qdrant container; size it from [vector DB sizing](../../docs/deployment/vector-db-sizing.md) | `6G` |
| `TELEMETRY_COLLECTION_FLAG` | `true` \| `false` — product metrics/event collection in every service; `false` turns all telemetry code off | empty: on |

### App container memory

On a fresh install the installer sizes `APP_MEMORY_LIMIT` from what the machine
actually has — the Docker Desktop VM's allocation on macOS/Windows, host RAM on
Linux — less 4G reserved for the datastores and OS, floored at 6G and capped at
32G. Setting it in the environment pins it:

```bash
APP_MEMORY_LIMIT=24G APP_MEMSWAP_LIMIT=30G ./install.sh --yes
```

An existing `.env` value always wins, so `--reconfigure` never resizes a tuned
deployment.

This is the most effective throughput knob in the stack. The resource governor
sizes indexing and parsing concurrency from the container's cgroup limit, so a
container sitting near its ceiling has every pool braked to its floor — a
couple of documents in flight while the CPU reads as idle. If indexing is slow
and CPU looks free, read `resource_governor.mem_pressure` from the indexing
service's `/health` before tuning anything else: above `GOVERNOR_MEM_SOFT`
(0.70) nothing grows, and above `GOVERNOR_MEM_HARD` (0.80) every pool halves
each sample.

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

### Legacy compose file: secrets are required

`docker-compose.prod-legacy.yml` no longer falls back to built-in values for `SECRET_KEY`, `MONGO_PASSWORD`, `ARANGO_PASSWORD` and `QDRANT_API_KEY`. It refuses to start until each one is set in `.env`.

If your stack ran without these in `.env`, its data was written with the old built-in values. Do not generate new ones: a new `SECRET_KEY` cannot decrypt the credentials already stored, and a new database password does not change the one already in the existing database volume. Put the old values in `.env` first:

```env
SECRET_KEY=your_random_encryption_secret_key
MONGO_PASSWORD=password
ARANGO_PASSWORD=your_password
QDRANT_API_KEY=your_qdrant_secret_api_key
```

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

Both copies run their code sandboxes on the same Docker host, and each sizes its sandbox cap from the whole host. Give each copy its own `SANDBOX_MAX_TOTAL` and its own `SANDBOX_EGRESS_NETWORK` in its `.env` (see [Code sandbox capacity](#code-sandbox-capacity)).

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
| `sandbox` | coding-sandbox image pull + `docker-socket-proxy` (the only container with the Docker socket) | slim/full; **eval omits this** |

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

## OAuth dynamic registration and device grant

PipesHub's authorization server advertises RFC 7591 dynamic client registration
and RFC 8628 device authorization. Coding agents use these to obtain a **user**
access token (never `client_credentials` through DCR).

| Variable | Default | Effect |
|----------|---------|--------|
| `PIPESHUB_ENABLE_DCR` | on (`false` disables it) | `POST /api/v1/oauth2/register` |
| `PIPESHUB_ENABLE_DEVICE_GRANT` | on | `POST /api/v1/oauth2/device_authorization` and the device `grant_type` |
| `PIPESHUB_ENABLE_CIMD` | on (`false` disables it) | An https URL that serves an OAuth Client ID Metadata Document is accepted as `client_id` |
| `MCP_ALLOWED_ORIGINS` | none | Extra browser origins (comma-separated) allowed to call `/mcp`, e.g. a browser-based MCP inspector |

`/mcp` refuses a request whose `Origin` header is not this instance's own
origin, one in `ALLOWED_ORIGINS`, or one in `MCP_ALLOWED_ORIGINS` (403). Desktop,
CLI and hosted AI clients send no `Origin` and are not affected. Each account
may make `MAX_REQUESTS_PER_MINUTE` requests a minute to `/mcp`; the API calls a
tool makes on its behalf are not counted again. An address is limited only on
requests with an invalid or expired token, so many users behind one address
(an office, or a hosted AI client) do not share a limit.

`/mcp` accepts OAuth access tokens and personal access tokens, not web session
tokens. A client that sends the RFC 8707 `resource` parameter (this instance's
`/mcp` URL) gets an access token whose audience is `/mcp`; `/mcp` refuses a
token whose audience is anything else. Tokens without an audience are accepted
as before.

Discovery: `GET {origin}/.well-known/openid-configuration` lists
`device_authorization_endpoint` and the device grant type only while the
device grant is on, and `registration_endpoint` only when DCR is on. When
the instance has an organization, discovery also includes
`pipeshub_device_client_id` (`pipeshub-agent`): the official public device
client, created automatically, device_code + refresh_token only, never
`client_credentials`. Agents use that `client_id` to start the TV-code
flow. It is hidden from Developer Settings. Public clients that
self-register still use `token_endpoint_auth_method: none`. Request
`urn:ietf:params:oauth:grant-type:device_code` in `grant_types` if a
*self-registered* agent will poll the device grant. The user completes
consent at `/oauth/device`. DCR is on by default so MCP clients can
register themselves. Operators who do not want unauthenticated
self-registration set `PIPESHUB_ENABLE_DCR=false`. The consent screen
warns when the app registered itself (`isDynamic`) rather than being the
official agent app or created by an administrator.

DCR refuses `client_credentials`. Tokens issued after device consent carry the
logged-in user's `userId` and `orgId`.

With client ID metadata documents on, discovery includes
`client_id_metadata_document_supported: true`, and MCP clients that publish
their metadata at an https URL (Claude Code, VS Code and others) use that URL
as `client_id` with no registration step. PipesHub fetches the document when
a signed-in user opens the authorization page, accepts only public clients
(`token_endpoint_auth_method: none`, PKCE required) and the redirect URIs the
document lists, and refuses documents on loopback or private addresses. A
loopback redirect URI listed without a port matches any port (RFC 8252). The
consent screen shows the document's host next to the unreviewed-app warning,
because the name and logo come from the client.

---

## Runtime tuning (workers and query-service flags)

These live in `.env` alongside the rest of your configuration. The defaults below keep
the earlier behaviour except two: the stored-record cache is on (up to 512 MiB per query
worker; `PIPESHUB_RECORD_CACHE_BYTES=0` turns it off and restores the earlier memory use),
and storage search runs ripgrep on up to half the container's CPUs (`GREP_THREADS`).

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
| `PIPESHUB_RECORD_CACHE_BACKEND` | `memory` \| `redis` | `memory` |
| `PIPESHUB_RECORD_CACHE_BYTES` | bytes per worker, capped at 512 MiB; `0` turns the cache off (`redis` too) | 512 MiB |
| `GREP_THREADS` | threads per search, capped at half the container's CPUs | half the CPUs |
| `PIPESHUB_AGENT_MAX_TURN_OUTPUT_CHARS` | characters, `0` removes the limit | `1000000` |

`direct` calls model providers without the LangChain layer. A provider with no direct
transport, or with credentials it cannot use, falls back to LangChain for that turn
rather than failing it; an unrecognised value logs a warning and falls back too.

The stored-record cache keeps recently read records for 60 seconds. `memory` keeps them
decoded in each query worker, so it uses up to `PIPESHUB_RECORD_CACHE_BYTES` per worker.
`redis` keeps the stored JSON in Redis instead, shared by all workers and with nothing held
in the process; it is a little slower because each hit is decoded again. The bundled Redis
also holds the configuration and the event queue and has no memory limit, so before choosing
`redis` give Redis a `maxmemory` with the `volatile-ttl` (or `volatile-lru`) policy, which
evicts only keys that expire, such as these cache entries. Records written with
append-only persistence on also go to its file on disk. Only records the storage service
returns inline (local storage) are cached by either backend; reads through signed URLs (S3,
Azure Blob) are not. If Redis cannot be reached when a worker first needs it, that worker
reads records from storage without a cache until it restarts.

Storage search runs grep through ripgrep when the image has it and the command can be
reproduced exactly; otherwise it runs GNU grep. `GREP_THREADS` sets ripgrep's threads per
search. It is capped at half the CPUs the container may use, so concurrent searches leave
CPU for answering, and defaults to that cap.

`PIPESHUB_SIGNED_URL_CACHE_SECONDS` is clamped to 3000 in code, keeping it under the
3600-second signing lifetime so a URL handed out at the end of its cached life still has
time left to use. A value that is not a number falls back to the default rather than
failing to start.

`PIPESHUB_AGENT_MAX_TURN_OUTPUT_CHARS` is a safety net for a model that never stops
writing, most often one stuck repeating itself inside a tool call on a gateway or local
server that sets no output limit of its own. It counts the characters a single agent turn
streams (answer text, reasoning and tool-call arguments together). Past the limit the
stream is closed. If the model was in the middle of a tool call, the call is not run and
the model is asked for a shorter one, as with any reply a provider cuts off. If it was
writing plain text, the user gets a short answer saying the reply grew too long and to
ask again or ask for a shorter one; the overrun itself is not saved or sent back to the
model. (With a limit set low enough that the text still fits in a prompt, the model is
asked to carry on from where it stopped instead.) The default is about twice the longest
reply any supported provider can produce, so normal answers never reach it; raise it only
if you see the "streamed more than ... characters in one turn" warning in the
query-service log for replies you expected. Blank or a value that is not a number falls
back to the default.

These are read from the container environment, so on an existing install add them to
`.env` yourself — `install.sh --upgrade` reuses your current `.env` and does not append
new keys. Every compose reference carries a default, so an absent key behaves exactly as
the table says.

> **Naming note.** The `PIPESHUB_*` variables in
> [Environment overrides for CI / scripted installs](#environment-overrides-for-ci--scripted-installs)
> are read by `install.sh` *before* `.env` is written and control the installer itself.
> The `PIPESHUB_*` variables above are read by the running service. They share a prefix but not a purpose.

---

## Code sandbox capacity

Code the agent runs (`run_code`, `install_packages`) gets a throwaway container per run. The app counts how many run at once for the **whole app container**: every query worker (`QUERY_UVICORN_WORKERS`) shares one count, kept in lock files under the container's `/tmp`. A run that finds every sandbox busy waits up to `SANDBOX_SLOT_WAIT_S` (120 s) and shows "waiting in queue", then is refused with a message the model can act on.

The cap is sized from the Docker host when the app starts (the app asks the Docker proxy, which answers a filtered `/info`). It is the smallest of:

| Term | Formula (defaults) |
| --- | --- |
| Memory | `SANDBOX_MEMORY_SHARE` (0.25) × host memory ÷ 512 MB per run |
| CPU | `SANDBOX_CPU_FACTOR` (0.75) × host CPUs ÷ 0.5 CPU per run |
| Processes | 25 % of the kernel's process limit ÷ 256 per run |

It is never below `SANDBOX_MIN_TOTAL` (2). A 16 GB / 4 CPU host gets 6, 64 GB / 16 CPU gets 24, 192 GB / 48 CPU gets 72. The query log shows the result as one `sandbox capacity:` line. If Docker does not answer at startup, the app uses the minimum and retries (after 5 s, 15 s, 30 s, then every minute).

Sandboxes are marked first to be killed if the host runs out of memory, ahead of the databases and the app.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SANDBOX_MAX_TOTAL` | sized | Replaces the computed cap (up to 1024; 0 or less means 1024). |
| `SANDBOX_MAX_PER_ORG` | none | The most sandboxes one org may hold at once. |
| `SANDBOX_MAX_PER_REQUEST` | 2 | The most sandboxes one request (a chat turn and its sub-agents) runs at once; its other calls wait for one of them, as above, without holding a sandbox. Never above the cap; 0 or unset means 2. Applies to chat requests; the agent library's own `ControlPlane` server shares one sandbox manager across its requests and is bounded by the cap only. |
| `SANDBOX_MIN_TOTAL` | 2 | The floor, and the cap until the host has been read. Once it has, a value above what the host supports is lowered to that (with a warning). |
| `SANDBOX_MEMORY_SHARE` | 0.25 | Share of host memory sandboxes may claim (0.05–0.95). |
| `SANDBOX_CPU_FACTOR` | 0.75 | Share of host CPUs sandboxes may claim (0.1–2.0). |
| `SANDBOX_SLOT_WAIT_S` | 120 | How long a run waits for a free sandbox. |
| `SANDBOX_HOST_MEMORY_BYTES`, `SANDBOX_HOST_CPU_MILLIS` | unset | The host's size, when it cannot be read (set both). |
| `SANDBOX_MAX_RUN_TIMEOUT_S` | 300 | The most seconds one run may take (10–3600). The Docker proxy gets the same value: it removes this stack's sandbox containers older than it (at least 300 s) plus 120 s. |
| `DOCKER_PROXY_INSTANCE` | the Compose project | Names this stack on its sandbox containers; the proxy acts only on its own. |
| `DOCKER_PROXY_REAP_UNLABELED` | false | Also remove sandbox containers with no stack name (left by a proxy from before stack names) once past the same age. Leave it off while another stack on the Docker host may still run an older version; the proxy logs how many there are, at most once an hour. |

The Docker proxy has no client authentication of its own. It listens on 127.0.0.1 unless `DOCKER_PROXY_HOST` is set; the Compose files set `0.0.0.0` because the proxy publishes no port and sits only on the internal `sandbox-control` network. If you run it yourself (`python -m app.docker_proxy_main`), keep it off any network a user can reach. Stacks sharing one Docker daemon do not see each other through it: its network list and inspect show only the sandbox network and this stack's containers on it, and it removes only networks it created itself. It also bounds what a client can send, through variables you set on the `docker-socket-proxy` service: `DOCKER_PROXY_MAX_UPLOAD_MB` (default 10240, the largest file upload into a sandbox: a run's inputs or its installed packages), `DOCKER_PROXY_BODY_IDLE_TIMEOUT_S` (120) and `DOCKER_PROXY_BODY_TIMEOUT_S` (1800) for a stalled or slow upload, and `DOCKER_PROXY_MAX_CONNECTIONS_PER_CLIENT` (512).

Without the Docker proxy (the Docker socket mounted straight into the app), nothing removes a sandbox container left behind by a killed worker; find such leftovers with `docker ps -a --filter label=ai.pipeshub.sandbox` (the `ai.pipeshub.sandbox.state-id` label holds `SANDBOX_STATE_ID` when it is set) and remove them yourself.

The memory share comes on top of the app container's own limit ([App container memory](#app-container-memory)): on a small host lower `SANDBOX_MEMORY_SHARE` or set `SANDBOX_MAX_TOTAL`.

**Several stacks on one Docker host.** Each stack sizes itself from the whole host, so together they would run several times the cap. Give each stack its own `SANDBOX_MAX_TOTAL` that together fit the host, and its own `SANDBOX_EGRESS_NETWORK`: every stack defaults to the same `pipeshub_sandbox_egress` network, and one stack re-creating it can break another stack's networked runs. Stacks whose services run directly on the host as one user (not in containers, e.g. several source checkouts) also share one `/tmp`; give each its own `SANDBOX_STATE_ID` (the same value for its query and indexing processes), or they share one count.

**Network egress.** With `SANDBOX_ALLOW_NETWORK=true`, a sandbox on the egress network reaches public addresses only. Before its code starts, the container rejects link-local and cloud metadata addresses, Azure's `168.63.129.16`, the sandbox bridge, its gateway, and private, CGNAT and reserved ranges. `SANDBOX_EGRESS_ALLOW_CIDRS` (comma-separated IPv4) opens private ranges, for example an internal package mirror, but never the addresses before them in that list. DNS stays open on port 53 only (UDP and TCP) to the container's gateway, to the resolvers the Docker daemon forwards to from inside the container (its `dns` setting), and to the Azure, AWS and GCP resolvers (`168.63.129.16`, `169.254.169.253`, `169.254.169.254`). Every other port on those addresses stays closed. If name resolution still fails in networked runs, add your resolver to `SANDBOX_EGRESS_DNS_SERVERS` (comma-separated IPv4 addresses).

**Where the shared state lives.** The query and indexing workers share their sandbox counts, live limit, indexing back-off and load records through files in a private directory, `$TMPDIR/pipeshub-sandbox[-<id>]-<uid>` (`/tmp` unless `TMPDIR` is set). When `TMPDIR` can be changed by another user, because it is writable by everyone without the sticky bit (a Kubernetes `emptyDir` mounted at `/tmp`, or a 0777 bind mount) or is owned by another user, the directory goes to `~/.cache/` under `$HOME` instead, or under the account's home when `$HOME` is open to others too, and the log shows one WARNING naming both paths. Every process of the same user with the same environment picks the same place. `SANDBOX_STATE_DIR` (an absolute path, owned by the app's user or root and not open to every user) chooses the place outright. Only when no safe place exists does each worker count on its own, which the log reports with one ERROR. In the Compose files `/tmp` is the image's own (mode 1777) and the app runs as uid 1000 with `HOME=/root`, so the state is in `/tmp/pipeshub-sandbox-1000`. The Helm chart does not mount `/tmp` either, so the state is in `/tmp/pipeshub-sandbox-1000` there as well; its `HOME` (`/home/user`) is an `emptyDir` open to every user, so if you mount an `emptyDir` at `/tmp` through `extraVolumeMounts`, the state moves to the image's account home (`/root/.cache/pipeshub-sandbox-1000`), unless you set `SANDBOX_STATE_DIR`.

**Disk.** Each sandbox may write 10 GB (`SANDBOX_DISK_QUOTA`, a Docker size such as `10g`; `off` turns it off). The Docker daemon enforces this itself only on overlay2 over xfs with the `pquota` mount option, or on btrfs/zfs. Other setups, including the containerd image store (storage driver `overlayfs`), accept the setting and ignore it. On those the app checks each run's writable layer every second (`SizeRw`) and stops the run once it is past the quota; the model is told why. A fast writer can go a little past the quota between two checks, so for a hard limit use overlay2 over xfs with `pquota`. The app decides this at the first run, from the storage driver the daemon reports, and logs a warning once when it falls back to checking. `SANDBOX_DISK_WATCHDOG` (`auto`) can be set to `on` (always check) or `off` (never check). Each check has 10 s (`SANDBOX_DISK_WATCH_CHECK_TIMEOUT_S`); a check slower than that, or 3 failed checks in a row (`SANDBOX_DISK_WATCH_MAX_FAILURES`), stops the run, because a run with millions of files makes the check itself slow. Sandbox containers use Docker's `local` log driver, rotated at `10m` × 3 files (`SANDBOX_LOG_MAX_SIZE`, `SANDBOX_LOG_MAX_FILES`), so a run's output cannot fill the daemon's disk through its logs.

What a run hands back to the app is bounded as well. Of stdout and stderr the app keeps the first 192 KiB and the last 64 KiB each (`SANDBOX_STDIO_HEAD_BYTES`, `SANDBOX_STDIO_TAIL_BYTES`) and replaces the middle with a `[... N bytes truncated ...]` line. From the run's output and working directories it collects at most 25 MiB per file (`SANDBOX_OUTPUT_MAX_FILE_BYTES`, the artifact size limit), 100 MiB in total (`SANDBOX_OUTPUT_MAX_TOTAL_BYTES`) and 2000 files (`SANDBOX_OUTPUT_MAX_FILES`). Larger files are left out and the model is told which. Across all the runs of one sandbox the app keeps at most 200 MiB (`SANDBOX_OUTPUT_MAX_SANDBOX_BYTES`) and 5000 files (`SANDBOX_OUTPUT_MAX_SANDBOX_FILES`) of output; past that, new files are not kept and the model is told. A file's full size counts, so a sparse file cannot expand past the limits. A package install may hand back up to 2 GiB (`SANDBOX_DEPS_MAX_TOTAL_BYTES`). Values are in bytes.

### Live limit

Below that cap, the app also holds new runs back while the Docker host is short of memory or CPU. Every 15 s (`SANDBOX_LIVE_INTERVAL_S`) one query worker reads the host's memory and CPU through the app container's `/proc`, once the Docker daemon has been shown to run on the same machine, and moves a **live limit** between `SANDBOX_MIN_TOTAL` and the cap. Every query worker shares it. In local mode it reads the app container's own cgroup instead. Running code is never stopped; a run held back waits as above.

- **Soft line.** The limit shrinks toward the runs that fit in free memory above a soft line (`SANDBOX_HOST_MEM_SOFT_MB`; default 15 % of memory, 2–24 GB), each counted at its full 512 MB. Right after it grew, it shrinks toward the soft line only once two samples in a row are below it.
- **Hard floor.** When free memory falls below a floor (`SANDBOX_HOST_MEM_FLOOR_MB`; default 5 %, 1–8 GB) plus one run, or on memory pressure, it halves. Each run also checks free memory itself before it starts: one that would leave less than the floor gives its slot back and waits. That check is the only thing that holds back the minimum's runs.
- **CPU.** It drops by one per sample after two samples above 90 % CPU (`SANDBOX_HOST_CPU_BRAKE`) or CPU pressure above 0.75 (`SANDBOX_HOST_CPU_PSI_BRAKE`). Indexing brakes on CPU earlier (85 % / 0.60), so it yields CPU first.
- **Growth.** It grows back, by doubling steps up to what memory holds, only while runs wait for it.
- **Indexing slows down first for memory.** When a run has waited 3 s for memory, query asks indexing to slow down: indexing takes a quarter off its in-flight records and one heavy parse per sample, down to its floor, and once runs stop waiting (`SANDBOX_YIELD_TTL_S`, 20 s) waits 60 s before it grows back one step per sample. Heavy parse waits twice as long each time runs start waiting again within 4 minutes of it growing back, up to 16 minutes. Records already in flight are never cancelled. With several indexing workers, their heavy parses together are held to what one worker would keep until that wait is over. A CPU-only hold does not slow indexing. `SANDBOX_YIELD_INDEXING=off` turns this off.

**Minimum host size.** In a simulation of the live limit and indexing together (not a measurement), a 4 GB host running a sync never had the hard floor plus one run free, so code runs waited until indexing was idle and timed out while it ran. A 16 GB host served chat with a p95 wait of a few seconds during a sync. For code runs while indexing is busy, plan on 8–16 GB or more. A `SANDBOX_HOST_MEM_FLOOR_MB` that leaves no room for one run below the host's memory is logged once as an error.

The query log shows each change as `sandbox live limit` and `asking indexing to slow down` / `indexing back-off released`; the indexing log shows `limits changed: … yield=on`. `SANDBOX_LIVE_LIMIT=off` turns the live limit off and keeps the fixed cap. A daemon on another machine (`DOCKER_HOST` remote) gets no live limit. The docker proxy proves the daemon shares the app's machine through `/_pipeshub/host`; with an older proxy image that lacks it, or no proxy, the app falls back to comparing the daemon's memory total and kernel version with its own, which two identical machines would also pass, so set `SANDBOX_LIVE_LIMIT=off` (or upgrade the proxy) when the daemon may be on another machine.

**Over-commit** is off by default. `SANDBOX_MEMORY_OVERCOMMIT=N` (up to 4) counts each run at 1/N of its 512 MB for the cap and the live limit, which raises a memory-bound cap. It applies only while the live limit reads the Docker host. Each run's own check before it starts still counts its full 512 MB, so a burst of runs that use all of it cannot take free memory below the hard floor.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SANDBOX_LIVE_LIMIT` | auto | `off` keeps the fixed cap. |
| `SANDBOX_LIVE_INTERVAL_S` | 15 | Seconds between samples (5–60). |
| `SANDBOX_HOST_MEM_FLOOR_MB` | 5 % (1–8 GB) | Free memory a new run must leave. |
| `SANDBOX_HOST_MEM_SOFT_MB` | 15 % (2–24 GB) | Free memory the limit keeps above. |
| `SANDBOX_HOST_CPU_BRAKE` | 0.90 | Host CPU use that brakes. |
| `SANDBOX_HOST_CPU_PSI_BRAKE` | 0.75 | Host CPU pressure (PSI some avg10) that brakes. |
| `SANDBOX_MEMORY_OVERCOMMIT` | 1.0 (off) | Count each run at 1/N of its memory for the cap (1.0–4.0); the per-run check uses its full memory. |
| `SANDBOX_YIELD_INDEXING` | on | Runs waiting for memory slow indexing down. |
| `SANDBOX_YIELD_TTL_S` | 20 | How long one request lasts once runs stop waiting (10–120). |

### Load alerts

When code runs in chat keep waiting for a sandbox, or are refused, the admins get a notification in the app, and an email if mail (SMTP) is set up. One query worker checks the last 30 minutes (`SANDBOX_ALERT_WINDOW_S`) every minute:

- **Warning** ("Code runs are waiting for capacity"): runs were held back during at least 60 % of the window, and at least 30 runs waited.
- **Critical** ("Code runs are being refused"): at least 10 runs were refused after waiting `SANDBOX_SLOT_WAIT_S`, and they are at least 5 % of all runs.

Not counted: waits for one organisation's own share of sandboxes (a bigger machine would not help), runs cancelled while waiting, and memory waits before indexing has slowed down for them (slowing indexing may still free the memory). CPU and full-slot waits count at once. The alert says why runs were held back (memory, CPU, or all slots in use) and what to do about it. An overload is not alerted again while it lasts. Once neither level has held for a full window, admins get an in-app "Code runs are no longer held back" notice, without email; a new overload after that notice is alerted in the app again, every time. The email for each level is sent at most once a day, and a critical email also counts for the warning. The query log shows `sandbox load alert fired level=…`. E2B installs never alert.

The cooldown is kept in the configuration store, so restarts do not resend. `SANDBOX_ALERT_SCOPE` names the machine whose sandboxes are counted; leave it unset in Compose. Stacks whose services run directly on one host as one user need their own `SANDBOX_STATE_ID` (above), or they share one evaluator.

Delivery limits: the in-app notification is sent every time an alert fires; the email is sent at most once a day per level (the cooldown above). The Node service that delivers alerts also caps them per hour, as a guard against floods: at most 30 alerts of one type and 4 alert emails of one level per host, and 120 alerts of a type and 12 emails of a level across all hosts. Later alerts are dropped, and later emails are skipped while the in-app notification is still sent. Only delivered alerts count; an alert that fails to store, or has no recipients, does not. These limits are kept in memory per Node process, so a restart clears them and each replica applies its own. Alert events are not signed. When envelope signing is enforced (`ENVELOPE_CTX_REQUIRED=true`, or `ENVELOPE_SIGN=true` with `ORG_CONTEXT_MODE=enforce`), alerts are dropped and Node and the query service each log one error at startup; this lasts until alerts can be signed without an organisation.

| Variable | Default | Meaning |
| --- | --- | --- |
| `SANDBOX_ALERTS` | true | `false` turns the alerts off (runs are still recorded). |
| `SANDBOX_ALERT_WINDOW_S` | 1800 | Window both levels look at (120–3600). |
| `SANDBOX_ALERT_EVAL_S` | 60 | Seconds between checks (10–600). |
| `SANDBOX_ALERT_WARN_BUSY_PCT` | 60 | Share of the window with runs held back, for a warning (1–100). |
| `SANDBOX_ALERT_WARN_MIN_WAITED` | 30 | Runs that waited, for a warning. |
| `SANDBOX_ALERT_CRIT_MIN_REFUSED` | 10 | Runs refused, for a critical alert. |
| `SANDBOX_ALERT_CRIT_REFUSED_PCT` | 5 | Refused runs as a share of all runs, for a critical alert (0–100). |
| `SANDBOX_ALERT_COOLDOWN_S` | 86400 | Each level is sent at most once in this time, unless the back-to-normal notice was sent since (60–604800). |
| `SANDBOX_ALERT_SCOPE` | install | Names the cooldown; one per machine whose sandboxes are counted. |

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
light-parse slots per CPU (capped at `GOVERNOR_LIGHT_PARSE_MAX`, default
256), and, for each tier's in-flight record budget, twice *that tier's own*
parse ceiling (capped at `GOVERNOR_INDEX_MAX`, default 512 per tier) — all
capped by `MAX_CONCURRENT_PARSING` / `MAX_CONCURRENT_INDEXING` when those
are set (see [`env.template`](env.template)). Leave them unset so new
images size from CPU. Hub slim still `int()`s empty strings; compose
unsets blanks at start so slim uses its built-in defaults instead of
crashing. Set an integer only if you want a hard cap;
`MAX_CONCURRENT_INDEXING` caps the two index tiers *together* as a hard
aggregate, scaled across them proportionally. Set it to 1 and there is no
room to split at all, so the light tier collapses and every record shares one
pool — light records then queue behind heavy ones, and the governor logs a
warning saying so at startup. 2 is the smallest value that keeps the split.

Heavy and light records get **separate** in-flight budgets
(`index_heavy` / `index_light`). An index permit is held for a record's
whole lifetime — including the time it spends queued for a parse slot — so
a single shared budget let a bulk PDF upload hold every permit while
Jira/Confluence records that finish in seconds never got admitted at all.
The tier is read from the record event's own `extension`/`mimeType`;
anything unrecognised classifies as heavy.

Those budgets are node-local and apply from this release. The *cluster-wide*
lease pool stays shared for one more release: `INDEXING_SPLIT_LEASE_POOLS`
defaults to off, so light records take the same `indexing` lease a
previous-build replica takes, at the same total budget. Without that, a
rolling upgrade runs both builds at once — the old one admitting every record
into `indexing` at the full budget while the new one also fills
`indexing:light` — and the fleet can exceed `MAX_CONCURRENT_INDEXING` by
`index_light` until the rollout finishes. Nothing is lost meanwhile: the
head-of-line blocking above is prevented by the node-local per-tier gates,
which are unaffected. Set `INDEXING_SPLIT_LEASE_POOLS=true` once every replica
runs this build or later.

Every pool now adapts at runtime, index included: each index tier is held
against live free memory (`GOVERNOR_INDEX_*_WORKING_SET_GB`) the same way
heavy parsing is, so one image sizes itself correctly on a 4-core/8 GiB
host and on a 48-core/96 GiB one. These two runs are a manual regression
check before a release; they are not part of CI.

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
   **Expect:** the same outcome — the governor's derived ceilings (visible as
   `ceilings.index_heavy` / `ceilings.index_light` in the `/health` snapshot,
   summed as `ceilings.index`) still cap effective concurrency well under 200,
   so a deliberately reckless operator setting does not change the result.
7. While the PDFs are still parsing, upload a handful of small Markdown/CSV
   files. **Expect:** they reach a terminal status without waiting for the
   PDF batch to drain — `limits.index_light` and `in_use.index_light` move
   independently of `index_heavy`. This is the head-of-line blocking the
   per-tier split exists to prevent; before it, a queue of heavy records held
   every in-flight permit and light records were never admitted.

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
   whole sync; `limits.index_light` ramps toward `ceilings.index_light`
   rather than being held down by heavy records, and `in_use.index_heavy`
   stays near zero because this sync has no heavy records at all;
   `resource_governor.cpu_utilisation` in the same snapshot reads as a real
   interval mean (comparable to what `top`/`docker stats` shows for the
   container), not ~0%, even though each record is milliseconds of work.
