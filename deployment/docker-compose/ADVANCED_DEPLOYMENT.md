# Advanced Deployment Options

This document covers non-interactive and manual deployment scenarios for PipesHub.
For the standard interactive install, see the [Deployment Guide in the main README](../../README.md#-deployment-guide).

---

## Contents

- [Standalone one-command install](#standalone-one-command-install)
- [Deployment types (slim vs. full vs. eval)](#deployment-types-slim-vs-full-vs-eval)
- [Environment overrides for CI / scripted installs](#environment-overrides-for-ci--scripted-installs)
- [Upgrading an existing Compose install](#upgrading-an-existing-compose-install)
- [MongoDB replica set](#mongodb-replica-set)
- [A second instance on the same host](#a-second-instance-on-the-same-host)
- [Manual deployment with Compose profiles](#manual-deployment-with-compose-profiles)
- [Durable agent harness topology](#durable-agent-harness-topology)
- [Code execution: sandbox broker and egress proxy](#code-execution-sandbox-broker-and-egress-proxy)
- [Remote access and HTTPS](#remote-access-and-https)
- [Secrets and configuration](#secrets-and-configuration)
- [Store credentials](#store-credentials)
- [OAuth dynamic registration and device grant](#oauth-dynamic-registration-and-device-grant)
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

## Deployment types (slim vs. full vs. eval)

| | **Slim** | **Full** | **Eval** |
|---|---|---|---|
| Image | `pipeshubai/pipeshub-ai:slim` | `pipeshubai/pipeshub-ai:latest` | `pipeshubai/pipeshub-ai:slim` |
| Embedding model | Downloaded on first use | Bundled in image (~1.3 GB extra) | Downloaded on first use |
| Graph DB (default) | Neo4j | Neo4j | Neo4j (required) |
| Broker (default) | Redis Streams | Kafka + Zookeeper | Redis Streams |
| KV store (default) | Redis | Redis | Redis |
| Coding sandbox | Sandbox broker + egress proxy (`sandbox` profile) | Same | **Omitted** — `run_code` unavailable |
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
| `PIPESHUB_GVISOR` | `auto` (ask; `--yes` skips) \| `install` (same as `--install-gvisor`) \| `skip` (same as `--skip-gvisor`) | `auto` |
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

---

## MongoDB replica set

The `mongodb` service runs a single-node replica set named `rs0`, so the app can
use multi-document transactions (`REPLICA_SET_AVAILABLE=true`, and `MONGO_URI`
carries `replicaSet=rs0&w=majority`). The build, legacy, beta and hot-reload
presets extend the same service. Memory is unchanged: the WiredTiger cache
(`MONGO_CACHE_GB`, 1 GB) and container limit (`MONGO_MEMORY_LIMIT`, 2 GB) are
the same flags as before; the oplog takes up to 1 GB of disk.

How it starts:

1. Before the image entrypoint runs, the container writes the internal-auth key
   to the `mongodb_replset_key` volume if it is empty (from
   `MONGO_REPLICA_SET_KEY` when set, otherwise random) and makes it owner-only.
2. `mongod` starts with authentication, `--replSet rs0`, `--oplogSize 1024` and
   the key file.
3. The healthcheck runs `rs.initiate` when the member has no config yet, and
   reports healthy only once it is a writable primary. The app waits for that.

Host tools that connect through a published 27017 port (hot-reload presets)
need `directConnection=true`: the member advertises `mongodb:27017`, which only
resolves inside the Compose network. To use an external MongoDB instead, set
`MONGO_URI`; if that server is standalone, also set `REPLICA_SET_AVAILABLE=false`.

### Upgrading a standalone install

Installs from before this change have a standalone data volume. `./install.sh`
(any mode that starts the stack, including `--upgrade`) converts it in place
before starting anything:

1. Stops the app and the standalone `mongodb`, then opens the volume with a
   throwaway `mongod` (no port, no credentials needed).
2. Prints the backup command and asks `Back up MongoDB before converting? [Y/n]`.
   With `--yes` it takes the backup without asking. The archive is written to
   `deployment/docker-compose/backups/mongodb-<project>-<timestamp>.archive.gz`
   (mode 600):

   ```bash
   docker exec <project>-mongodb-rs-migrate mongodump --archive --gzip > backups/mongodb-<project>-<timestamp>.archive.gz
   ```

3. Records the document count of every collection, starts `mongodb` as `rs0`,
   waits for a writable primary, and compares the counts. A mismatch stops the
   installer and prints the backup path.

Re-running the installer is a no-op once the volume is a replica-set member.
Starting the new compose file directly (`docker compose up -d`) also converts
the volume, because the healthcheck initiates the set, but without the backup
or the count check. Take a backup first in that case.

To restore an archive into the running service:

```bash
docker compose -p pipeshub-ai exec -T mongodb mongorestore --archive --gzip --drop \
  --username admin --password '<MONGO_PASSWORD from .env>' --authenticationDatabase admin \
  < backups/mongodb-pipeshub-ai-<timestamp>.archive.gz
```

To go back to an older release, restore its compose file and start it: a
standalone `mongod` opens a single-node replica-set volume (it logs a warning
about the replica-set config and runs without transactions).

### Backup

- Use `mongodump --archive --gzip --oplog` against the service for a
  point-in-time dump (`--oplog` needs the replica set).
- Back up the `mongodb_replset_key` volume or keep `MONGO_REPLICA_SET_KEY` in
  your secret store. A single-node set starts with a new key if the volume is
  lost, but every member of a multi-node set must share the same key.
- Back up `.env` (it holds `MONGO_PASSWORD`).

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
| `sandbox` | sandbox image pull, `docker-socket-proxy` (the only container with the Docker socket), `sandbox-broker`, `egress-proxy`; `-f docker-compose.sandbox-legacy.yml` (`SANDBOX_MODE=docker`) lets the app reach the same proxy | `SANDBOX_MODE=broker` (slim/full); **eval omits this** |

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

### Durable agent harness topology

The agent harness (Beta) runs by default (ADR-OPS-06, amended 2026-11-20): with
`AGENT_HARNESS_WORKER_ENABLED` unset or `true` the worker and its gateways start and
stay idle until an org admin turns on **Agent harness** in Labs, which is off by
default. `AGENT_HARNESS_WORKER_ENABLED=false` in `.env`
(`PIPESHUB_AGENT_HARNESS=off ./install.sh` writes it) starts no worker or gateway
and serves no harness route, Labs flag or screen. `./install.sh --upgrade` keeps
the choice an `.env` already holds. What the switch
covers, and what changed for every install whatever it says:
[HARNESS-OFF.md](../../docs/agent-harness-v2/guides/HARNESS-OFF.md).

Durable run admission needs MongoDB transactions, which the base file already
provides (see [MongoDB replica set](#mongodb-replica-set)). The overlay
starts the relay and trusted worker inside the app container:

```bash
docker compose \
  -f docker-compose.yml \
  -f docker-compose.agent-harness.yml \
  -p pipeshub-ai up -d
```

With the switch on, Labs' **Agent harness** then decides per organisation. The replica-set transaction path, including the Node
admission store, is exercised by
`deployment/docker-compose/tests/agent_harness_replset_test.sh`.

## Code execution: sandbox broker and egress proxy

With the `sandbox` profile (slim and full), three more containers start from the
app image. None publishes a port.

| Container | Role | Networks | Docker socket |
|---|---|---|---|
| `docker-socket-proxy` | The Docker API policy proxy: admits only the calls the broker makes on this install's sandboxes (no privileged, host-mount or host-network container, no exec outside a sandbox), with the broker's own credential (`DOCKER_PROXY_BROKER_TOKEN`, generated by `install.sh`). Runs as uid 1000 in the socket's group (`DOCKER_GID`) | `sandbox_docker` (internal: the broker alone) | **yes, the only one** |
| `sandbox-broker` | Starts, runs and removes every code sandbox (`run_code`, agent engines). The app reaches it at `sandbox-broker:8095` with the per-deployment worker credential | `pipeshub`, `sandbox_plane`, `sandbox_docker` | no: through `docker-socket-proxy` |
| `egress-proxy` | The only way out of a sandbox that has an egress allowlist: forwards to the model and tool gateways, swapping the sandbox's placeholder for a short-lived token | `sandbox_plane`, `sandbox_kv` (both internal: no route off the host) | no |

The app container has **no Docker socket**. `SANDBOX_MODE=broker` selects this
path; a blank `SANDBOX_MODE=` turns code execution off (eval).

**Isolation.** On a Linux Docker Engine, `install.sh` installs gVisor (`runsc`)
and registers it with Docker, so sandboxes get their own kernel; it asks first
when interactive and is skipped with `--skip-gvisor` or `PIPESHUB_GVISOR=skip`.
Installing it edits `/etc/docker/daemon.json` and reloads Docker, so `--yes` alone
never does it: pass `--install-gvisor` (or `PIPESHUB_GVISOR=install`) to opt in.
It does not run on macOS, Windows, WSL, Docker Desktop, rootless Docker or a
remote daemon, and needs root or sudo. Without `runsc`, sandboxes run on
hardened runc (read-only root, all capabilities dropped, no network by default),
which shares the host kernel: the installer summary calls this **reduced
isolation**. To install gVisor yourself, register the `runsc` runtime in
`/etc/docker/daemon.json` (`sudo runsc install`) and reload Docker; the broker
finds it on its own. **Coding engines (Codex, Claude Code, opencode) require
gVisor**: without `runsc` they are refused, and only a dev or test install may
run them on hardened runc with `PIPESHUB_ENGINES_WITHOUT_GVISOR=true`. See
[GVISOR.md](./GVISOR.md).

**Addresses.** The app tells Node where the broker, the proxy's control plane
and the gateways are (`SANDBOX_BROKER_BACKEND`, `EGRESS_PROXY_CONTROL_BACKEND`,
`MODEL_GATEWAY_BACKEND`, `TOOL_GATEWAY_BACKEND`); Node writes them into
`/services/endpoints`. If `10.213.0.0/16` overlaps a network this host routes
to, set `SANDBOX_EGRESS_SUBNETS` in `.env`; if `10.212.0.0/16` does (the bridge
that sandboxes with open egress share), set `SANDBOX_OPEN_EGRESS_SUBNETS`.

**Forward proxy and private hosts.** A sandbox still reaches only the hosts its own
allowlist names (an MCP server's hosts, a repository). Two settings on the
`egress-proxy` service change *how* it gets there; set them in `.env`:

| Setting | What it does |
|---|---|
| `EGRESS_UPSTREAM_PROXY` | Your forward proxy, `http://host:port` (no credentials in it). Every public destination goes through it: plain HTTP as an absolute-form request, HTTPS as a `CONNECT`. The proxy is asked for the address PipesHub resolved and checked, never the name, so it cannot be steered somewhere PipesHub refuses; the name still travels in `Host` and in TLS. The model and tool gateways never go through it |
| `EGRESS_UPSTREAM_PROXY_AUTH` | `user:password` for it (`Proxy-Authorization: Basic`). Only your proxy receives it; a sandbox never holds it and it is never logged |
| `EGRESS_UPSTREAM_NO_PROXY` | Public hosts dialled direct: names, `.domain` suffixes, networks. `*` is refused |
| `EGRESS_PRIVATE_ALLOW` | Private destinations, comma-separated: `git.corp.example:443`, `10.1.2.3:443`, `10.1.0.0/16:8443`, `[fd12::1]:443`. Private, CGNAT and other non-public addresses stay refused unless an entry names the host and port (or the address's network and port); listed ones are dialled direct, not through the upstream proxy |

What no entry opens: loopback, link-local and cloud metadata addresses, and
PipesHub's own (the egress proxy and the Docker host's address on each of its
networks, the gateways, Node, the broker, Redis and etcd, both sandbox pools).
The proxy cannot see this host's LAN address or a cluster's pod and service
ranges from inside: list those in `EGRESS_BLOCKED_CIDRS`, which wins over every
entry. A malformed setting stops the proxy at start, naming the setting.

The proxy's own networks are internal, so to reach your forward proxy or a
private host it needs a route off the host: add
`-f docker-compose.egress-tunnels.yml` (it also turns on HTTPS tunnels).

**Upgrading an install that used `SANDBOX_MODE=docker`** (or had no
`SANDBOX_MODE`, which meant docker): `./install.sh --upgrade` pulls the new
image, starts the broker and the proxy first, and switches to
`SANDBOX_MODE=broker` once the broker is healthy. If it does not become healthy
within 3 minutes, the app keeps creating sandboxes itself, through the Docker API policy proxy that
`docker-compose.sandbox-legacy.yml` adds (never the socket), and code keeps running the old way; the
installer says so, and the next run retries. Without the installer:

```bash
# keep the old path for now
docker compose -f docker-compose.yml -f docker-compose.sandbox-legacy.yml -p pipeshub-ai up -d
# switch: set SANDBOX_MODE=broker in .env (keep the sandbox profile), then
docker compose -f docker-compose.yml -p pipeshub-ai up -d
```

---

## Remote access and HTTPS

The app port is published on `127.0.0.1` by default: only this machine can reach `http://localhost:3000`. Docker
publishes ports through its own NAT rules, which bypass `ufw` and `firewalld`, so the bind address is what keeps a
port local. A host firewall does not.

`APP_BIND_ADDRESS` in `.env` sets it. `install.sh` writes it once: `0.0.0.0` when you give it a public URL whose host
is not `localhost`, otherwise `127.0.0.1`. `PIPESHUB_BIND_ADDRESS` (environment) or an existing value in `.env`
always wins. An `.env` written before this setting existed is given the same rule on the next `./install.sh`, which
says so when it closes a port that used to be open.

The port speaks plain HTTP. Sign-in passwords and session tokens must not cross a network in the clear, so put HTTPS
in front before anyone reaches the app from another machine:

- **HTTPS proxy on this machine (recommended).** Keep `APP_BIND_ADDRESS=127.0.0.1` and point the proxy at
  `localhost:${APP_PORT}`. With [Caddy](https://caddyserver.com), which gets and renews the certificate itself:

  ```caddyfile
  pipeshub.example.com {
      reverse_proxy localhost:3000
  }
  ```

  Then set `FRONTEND_PUBLIC_URL=https://pipeshub.example.com` and `TRUST_PROXY=1`, and run `./install.sh`.
- **Cloudflare Tunnel.** `cloudflared` on this machine connects out, so no port is opened at all. Keep
  `APP_BIND_ADDRESS=127.0.0.1` and route the tunnel's hostname to `http://localhost:3000`.
- **A load balancer or proxy on another machine.** Set `APP_BIND_ADDRESS=0.0.0.0` (or the one interface it reaches
  you on). Then restrict port `${APP_PORT}` to that machine where Docker cannot bypass it: a cloud security group,
  or the `DOCKER-USER` iptables chain. `ufw` rules alone do not apply to published container ports.

`install.sh` warns when the app listens on every interface, and warns again when the public URL is `http://`.

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

### Store credentials

Every store starts only with its credential, and the app refuses to start against one that answers without it
(SEC-D-06, ADR-OPS-03):

| Store | Credential | Notes |
|---|---|---|
| Redis | `REDIS_PASSWORD` | `--requirepass` always; `docker compose exec redis redis-cli` signs in by itself (`REDISCLI_AUTH`). |
| Qdrant | `QDRANT_API_KEY` | |
| MongoDB | `MONGO_PASSWORD` | Kept on the volume: never change it in `.env` alone. |
| Kafka (`broker-kafka`) | `KAFKA_PASSWORD` (user `KAFKA_USERNAME`, default `pipeshub`) | SCRAM-SHA-512. `kafka-init` writes the user at each start, so changing the password and restarting rotates it. ZooKeeper is reachable by Kafka alone. |
| etcd (`kv-etcd`) | client certificates | TLS with `--client-cert-auth`. `etcd-tls` keeps a CA in its own volume and gives the app, the egress proxy and the sandbox broker one certificate each, renewed before expiry on any start. |

Compose refuses to render with an empty `REDIS_PASSWORD`, `QDRANT_API_KEY`, `MONGO_PASSWORD` or `KAFKA_PASSWORD`.
`KAFKA_PASSWORD` is required even with Redis Streams, because Compose renders every profile. `install.sh` generates all
of them. `./install.sh --upgrade` fills in any that an older `.env` lacks and says which; MongoDB's and the graph
database's passwords are the exception, because those stores keep theirs on their volume.

**Upgrading a manual (`cp env.template .env`) install:** set the missing values, each from `openssl rand -hex 24`,
then `docker compose up -d`. Redis, Qdrant and Kafka take the new value on restart, and no data is lost. etcd moves to
TLS by itself.

**An external store protected another way** (a private network with no login): the app refuses it by default and
names what to set. If you accept that, set `STORE_AUTH_CHECK=warn`: the app then starts and logs each finding.
For an external Kafka without SASL, also set `KAFKA_USERNAME=` (empty).

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
| `PIPESHUB_ENABLE_DCR` | off (only `true` enables it) | `POST /api/v1/oauth2/register` |
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
consent at `/oauth/device`. Unauthenticated DCR is off by default so a
stock instance cannot be used for consent phishing. Operators who want
agents to self-register set `PIPESHUB_ENABLE_DCR=true`. The consent screen
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
| `PIPESHUB_AGENT_MAX_TURN_OUTPUT_CHARS` | characters, `0` removes the limit | `1000000` |

`direct` calls model providers without the LangChain layer. A provider with no direct
transport, or with credentials it cannot use, falls back to LangChain for that turn
rather than failing it; an unrecognised value logs a warning and falls back too.

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
