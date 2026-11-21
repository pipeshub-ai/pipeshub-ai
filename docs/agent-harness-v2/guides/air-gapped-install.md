# Agent harness: air-gapped install

Checklist item C15.04 (OP-04), part of the [operator guide](operator-guide.md). An air-gapped install has no
route to the internet: images come from a private registry or from archives, models run inside the network, and
nothing phones home. This page lists everything a PipesHub install with the agent harness fetches, and how to
supply each piece offline. The settings named here are checked against the shipped files by
`backend/python/tests/cohesion/test_wp87_air_gapped.py`.

## What an install fetches

| What | Where it normally comes from | Offline |
|---|---|---|
| The PipesHub image (app, sandbox broker and egress proxy share it) | Docker Hub `pipeshubai/pipeshub-ai` | A private registry (`PIPESHUB_IMAGE`) or `docker load` |
| The code sandbox image (`run_code`) | Docker Hub `pipeshubai/pipeshub-sandbox` | A private registry (`SANDBOX_DOCKER_IMAGE`) or `docker load` |
| Store images (MongoDB, Redis, Qdrant, Neo4j, …) | Docker Hub, Quay | Load them under the same names (Compose), or set each repository (Helm) |
| Coding-engine images (`pxh-*-tools`) | Built on the deployment by `deployment/sandbox-images/engine-build.sh` | Build on a connected host, then `docker load` on the Docker host the sandbox broker uses |
| Engine binaries | The engines' release downloads, at image build time only | Inside the images; nothing is downloaded at run time |
| Skills | Built-in packs ship in the image; an org's own skills are stored in its graph | Nothing to mirror; import a skill from an uploaded archive, not from npm or GitHub |
| Embedding and layout models | Hugging Face, at image build time (the `latest` image) or at first use (`slim`) | Use the `latest` image, or carry a filled model cache; set `HF_HUB_OFFLINE=1` |
| Chat and engine models | A hosted provider | An in-network endpoint: Ollama, LM Studio or any OpenAI-compatible server ([ai-models.md](../../ai-models.md)) |

## Images

### Docker Compose

`install.sh` and `docker-compose.yml` read the PipesHub images from two settings, written to `.env`:

- `PIPESHUB_IMAGE` (default `pipeshubai/pipeshub-ai`) with `IMAGE_TAG`: the app, the sandbox broker and the
  egress proxy;
- `SANDBOX_DOCKER_IMAGE` (default `pipeshubai/pipeshub-sandbox:${IMAGE_TAG}`): the code sandbox.

Name both when you install; the installer writes them to `.env` (the sandbox image as `SANDBOX_DOCKER_IMAGE`):

```sh
PIPESHUB_IMAGE=registry.internal:5000/pipeshubai/pipeshub-ai PIPESHUB_VERSION=<tag> \
PIPESHUB_SANDBOX_IMAGE=registry.internal:5000/pipeshubai/pipeshub-sandbox:<tag> \
  ./install.sh --no-pull
```

**From the release's offline bundle (recommended).** Each signed release attaches, per CPU architecture,
`pipeshub-offline-<version>-<arch>.manifest`, its signature `….manifest.sigstore.json`, the archive parts it names
(`….tar.gz.00`, `.01`, …: the app and sandbox images) and `sigstore-trusted-root.json`. Copy them into one folder
on the host, put `cosign` (v2.4 or newer, checked against its published checksum) on the `PATH`, and run:

```sh
./install.sh --offline-bundle /media/pipeshub/pipeshub-offline-<version>-amd64.manifest
```

Before anything is loaded, the installer checks the manifest's signature offline: it was signed by
`release-publish.yml` at a version tag (keyless Sigstore), and the transparency-log proof in the `.sigstore.json` is
checked against the Sigstore trusted root the installer pins by SHA-256. Then each part's SHA-256 against the
signed manifest, then `docker load`, then that every image the manifest names is present. Any failure stops the
install with what to do; nothing is loaded or started. `--offline-bundle` implies `--no-pull` and takes the version
from the manifest. `PIPESHUB_IMAGE_VERIFY=warn` does not skip the signature here (the release signs every bundle it
publishes); `off` skips it and still checks the checksums. A bundle you built and signed yourself is checked with
`PIPESHUB_IMAGE_PUBKEY`.

With `--no-pull` (or `PIPESHUB_NO_PULL=1`) the installer uses the images already on the host instead of refreshing
them, and an upgrade keeps the sandbox image you set. Docker still pulls an image the install names and the host
lacks, so load every image first: `docker compose config --images` lists them. The installer's outbound check
only warns, so the install completes without a route out. Further Compose files, such as your own network or
proxy settings, go in `PIPESHUB_COMPOSE_OVERRIDES` (colon-separated); every later `install.sh` command reads them
from `.env`.

The store images are named in `docker-compose.yml` without a registry setting. Load them onto the host under
the names the file uses (pull from your mirror and `docker tag` to the name, or `docker load` an archive saved on
a connected host):

| Service | Image |
|---|---|
| `mongodb` | `mongo:8.0.17` (`MONGO_IMAGE_TAG` sets the tag) |
| `redis` | `redis:7.4.12-bookworm` |
| `qdrant` | `qdrant/qdrant:v1.15.5` |
| `neo4j` | `neo4j:5.26.0` |
| `arango` | `arangodb:3.12.4` |
| `etcd` | `quay.io/coreos/etcd:v3.5.17` |
| `zookeeper` | `confluentinc/cp-zookeeper:7.9.0` |
| `kafka-1` | `confluentinc/cp-kafka:7.9.0` |
| `kafka-init` | `confluentinc/cp-kafka:7.9.0` (the same image) |
| `web-readers-gateway` | `nginx:1.27.5-alpine` |
| `searxng` | `searxng/searxng:2026.9.30-a9d990033` |
| `crawl4ai` | `unclecode/crawl4ai:0.9.4` |
| `postgres` | `pipeshubai/postgres:18-1` (`PG_IMAGE`, `PG_MAJOR` and `PG_IMAGE_REV` set it; built from `deployment/docker-compose/postgres`) |
| `pgbouncer` | `pipeshubai/postgres:18-1` (the same image) |
| `pipeshub-migrate` | `pipeshubai/pipeshub-migrate:latest` (`PIPESHUB_MIGRATE_IMAGE` and `IMAGE_TAG` set it; built from `backend/db/Dockerfile`) |

Only the services of your profile run (`slim` uses Neo4j and Redis; `full` adds ArangoDB, etcd and Kafka; web
search is optional and needs the internet anyway). The PostgreSQL services run only under the `postgres` and `postgres-migrate` profiles, and Compose builds
their two images (`pull_policy: build`), which needs the internet: build them on a connected host and `docker load` them
under these names. The monitoring overlay takes `PROMETHEUS_IMAGE` and
`GRAFANA_IMAGE`.

### Helm

The chart prefixes `global.imageRegistry` to every image it renders: the PipesHub image (app and sandbox broker
pods), every store, the sandbox image (`SANDBOX_DOCKER_IMAGE`), the broker's Docker-in-Docker sidecar and the docker
proxy; it also passes it to the bundled MongoDB and Redis charts. The image path is kept, so the mirror holds
`quay.io/coreos/etcd` as `registry.internal:5000/coreos/etcd`. An image you set to a reference naming its own
registry (for example `other.example/etcd`) keeps that registry. Coding-engine images use
`agentHarness.engineImageRegistry`, which defaults to `global.imageRegistry`. The Bitnami charts refuse a registry
they do not know unless `global.security.allowInsecureImages` is true. A values file for a mirror at
`registry.internal:5000`:

```yaml
global:
  imageRegistry: registry.internal:5000
  security:
    allowInsecureImages: true
```

Add `imagePullSecrets` if the registry needs credentials. The chart's MongoDB and Redis charts are vendored, so
`helm dependency build` needs no network. With this file every image the chart renders comes from the mirror
(the test renders the chart with it and checks every `image:`).

### Coding-engine images

The engine images are pinned by name in `backend/python/harness_engines/allowlist.json`. The sandbox broker runs
only these names, and only an image it finds on its own Docker daemon or can pull under that exact name. With
`HARNESS_ENGINE_IMAGE_REGISTRY` set (Compose `.env`, passed to `pipeshub-ai` and `sandbox-broker`; Helm
`agentHarness.engineImageRegistry`), the worker asks for and the broker allows `{registry}/{name}` instead, and
the bare name is no longer allowed; digest and binary pins still hold:

| Engine | Image |
|---|---|
| opencode, ACP-opencode | `pxh-opencode-tools:1.18.34-ph7` |
| Codex | `pxh-codex-tools:0.160.0-ph7` |
| Claude Code | `pxh-claude-code-tools:2.1.319-ph7` |
| Gemini CLI | `pxh-gemini-tools:0.63.0-ph7` |
| Goose | `pxh-goose-tools:1.57.0-ph7` |
| Qwen Code | `pxh-qwen-tools:0.26.5-ph7` |
| DeepSeek Harness | `pxh-dsh-tools:0.2.2-rc.1-ph7` |

On a connected host with the same CPU architecture, build each engine you will run
(`deployment/sandbox-images/engine-build.sh KIND`; DeepSeek Harness's build also installs the npm tree of its pinned lock, so it needs the npm registry; Claude Code takes the admin's own binary with `--binary PATH`,
and `--asset PATH` takes a release download you carried in), save them, and load them on the Docker host the
broker uses:

```sh
docker save pxh-opencode-tools:1.18.34-ph7 pxh-codex-tools:0.160.0-ph7 | gzip > pxh-engines.tar.gz
docker load -i pxh-engines.tar.gz
```

Or push them to your mirror under the same names and set `HARNESS_ENGINE_IMAGE_REGISTRY=registry.internal:5000`.
Coding engines are off until `.env` sets `HARNESS_ENGINE_RUNS=true` (and, in production, gVisor:
[GVISOR.md](../../../deployment/docker-compose/GVISOR.md)).
Without that setting a copy in your registry under another name is not used: pull it and `docker tag` it to the
name above. An image
the broker can neither find nor pull makes its engine unavailable (`broker.engine_image_unavailable`), and admins
see it refused before any run starts; the other engines keep working. The chart ships no egress proxy, which
engine sandboxes need, so coding engines run on Compose until the deferred Helm agent-sandbox work (P5.3).

## Models

- **Chat and engine models.** Add an Ollama, LM Studio or OpenAI-compatible model in Workspace → **AI models**
  with its in-network address. The Model Gateway serves engines from the same configured models, so engines need
  no route out either.
- **Embeddings.** The built-in embedding server (port 8002) runs `BAAI/bge-large-en-v1.5`. The `latest` image
  carries it; the `slim` image downloads it on first use. Offline, use `latest`, or fill the `pipeshub_hf_cache`
  volume on a connected host, then set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` in `.env`.
- **Document layout.** The image carries the layout model of the pdfplumber parser. Docling's own models (its
  layout model `docling-project/docling-layout-heron` and its table model `docling-project/docling-models`) are
  not in the image: Docling downloads them the first time it parses a complex PDF, which fails with no route out.
  Fill them into the Hugging Face volume on a connected host with the same image, which builds the parser's own
  converter and prints each model it now holds:

  ```sh
  docker compose -p pipeshub-ai run --rm --no-deps -w /app/python pipeshub-ai \
    python -m app.modules.parsers.pdf.docling_models
  ```

  Then move the `pipeshub_hf_cache` volume to the air-gapped host (for example `tar` it out of a container that
  mounts it) before the first start, and keep `HF_HUB_OFFLINE=1`. The layout model has no pinned revision upstream:
  the cache holds what was current when you filled it, and stays so offline.

## Checked with no route out

`deployment/e2e/run.sh airgap` (C15.04) runs the stack from a source tree on Docker networks with no route out
(`docker-compose.airgap.yml`): the `pipeshub` network is made internal like the others, only `pxh-ingress` (an
inbound-only forwarder, `ingress.py`) joins a routed network to publish the ports, nothing is pulled, and the
scripted model in the network serves chat, engines and embeddings. Once the stack is up, `airgap_check.py` proves
that every network of the stack is internal and that the app container can neither connect to a public address
nor resolve a public name; then a durable coding-engine run (opencode in a broker sandbox, its egress through the
proxy to the gateways only) and the chat-loop harness project run on it. It runs the app from the tree on
`python:3.12-slim`, not the shipped image.

`deployment/docker-compose/tests/airgap_install_smoke.sh` (R25-OPS) runs this page as written, with the image built
from the repository's `Dockerfile`: it lists every image the install names with `docker compose config --images`
and stops if one is missing (nothing is pulled), fills Docling's models with
`python -m app.modules.parsers.pdf.docling_models`, then runs `install.sh --yes --no-pull` with `PIPESHUB_IMAGE`,
`PIPESHUB_SANDBOX_IMAGE`, `TELEMETRY_COLLECTION_FLAG=false`, `HF_HUB_OFFLINE=1` and `HARNESS_ENGINE_RUNS=true`, and
an overlay (`PIPESHUB_COMPOSE_OVERRIDES`) that makes the `pipeshub` network internal and publishes the app only
through the inbound-only ingress. On that install, `airgap_check.py` holds (every network internal but the
ingress's, the app can neither connect out nor resolve a name), a scripted chat turn answers, an engine agent's
job runs opencode in a broker sandbox and finishes, and Docling parses a PDF with its own models.
Run with the drill option, it also rehearses [DR-DRILL.md](../DR-DRILL.md) on that install. This lane's host has no gVisor, so its engines
run on hardened runc (`PIPESHUB_ENGINES_WITHOUT_GVISOR=true`); keep gVisor in production.

## Phoning home

- **Product telemetry** is on unless you set `TELEMETRY_COLLECTION_FLAG=false` (Helm: `telemetry.collection.enabled`).
  Turn it off on an air-gapped install.
- **Engines** run with their telemetry, update checks and sharing off, fixed in their launch settings, and their
  sandboxes reach only PipesHub's gateways through the egress proxy.
- **Connectors and web search** need the services they connect to; an air-gapped install indexes only sources
  inside the network.
