# Contributing to PipesHub Workplace AI 

<div align="center">

**Translations:** [Français](docs/i18n/fr/CONTRIBUTING.md) · [Deutsch](docs/i18n/de/CONTRIBUTING.md) · [简体中文](docs/i18n/zh-CN/CONTRIBUTING.md) · [日本語](docs/i18n/ja/CONTRIBUTING.md) · [Русский](docs/i18n/ru/CONTRIBUTING.md) · [עברית](docs/i18n/he/CONTRIBUTING.md) · [한국어](docs/i18n/ko/CONTRIBUTING.md) · [Español](docs/i18n/es/CONTRIBUTING.md) · [Português](docs/i18n/pt/CONTRIBUTING.md) · [Türkçe](docs/i18n/tr/CONTRIBUTING.md) · [Tiếng Việt](docs/i18n/vi/CONTRIBUTING.md) · [Italiano](docs/i18n/it/CONTRIBUTING.md) · [Čeština](docs/i18n/cs/CONTRIBUTING.md)

This English file is current. The translations may lag; use this page for labeled issues and local setup until they catch up.

</div>

Welcome to our open source project! We're excited that you're interested in contributing. This document provides guidelines and instructions to help you get started as a contributor.

Questions: [Discord](https://discord.com/invite/K5RskzJBm2), [GitHub Discussions](https://github.com/pipeshub-ai/pipeshub-ai/discussions), or open an issue. Pick a labeled issue below, or open a pull request with your change.

## Table of Contents
- [Finding something to work on](#finding-something-to-work-on)
- [Documentation](#documentation)
- [New connectors](#new-connectors)
- [Setting Up the Development Environment](#setting-up-the-development-environment)
- [Project Architecture](#project-architecture)
- [Extension points](#extension-points)
- [Contribution Workflow](#contribution-workflow)
- [Contributor License Agreement](#contributor-license-agreement)
- [Code Style Guidelines](#code-style-guidelines)
- [Testing](#testing)
- [Community Guidelines](#community-guidelines)

## Finding something to work on

Labeled issues are a good place to start, but other useful changes are welcome too.

1. [`first-timers-only`](https://github.com/pipeshub-ai/pipeshub-ai/labels/first-timers-only) — small and scoped; a good first open-source PR.
2. [`good first issue`](https://github.com/pipeshub-ai/pipeshub-ai/labels/good%20first%20issue) — one evening, one area.
3. [`help wanted`](https://github.com/pipeshub-ai/pipeshub-ai/labels/help%20wanted) — a few days of independent work.

Comment on the issue so others know you are on it. If you already have a fix, open a pull request and link the issue, or describe the problem in the PR.

## Documentation

Documentation lives in two places:

- This repository: `README.md`, `docs/`, this file, and OpenAPI at `backend/nodejs/apps/src/modules/api-docs/pipeshub-openapi.yaml`. Update the OpenAPI file when you change HTTP routes.
- [`pipeshub-ai/documentation`](https://github.com/pipeshub-ai/documentation), which is what [docs.pipeshub.com](https://docs.pipeshub.com) publishes.

To change a page:

1. Fork the repo that holds the file.
2. Edit it. Match versions, paths, and install commands to this repository on `main`.
3. Open a pull request.

Markdown-only work does not need Docker or a local stack. If you add or change a product feature, update the page that describes it in the same PR when you can.

## New connectors

A new connector is a large piece of work. [CONNECTOR.md](CONNECTOR.md) is the starting point, and [CONNECTOR_CHECKLIST.md](CONNECTOR_CHECKLIST.md) is what a reviewer will check it against; Discord is a good place to talk through scope first.

## Setting Up the Development Environment

Skip this section if you are only editing documentation.

You need a clone of this repository on your machine. Fork it on GitHub, then:

```bash
git clone https://github.com/<your-username>/pipeshub-ai.git
cd pipeshub-ai
```

The product is two kinds of process:

- **Application services** — the Node.js API, the Python FastAPI services, and the Next.js UI. These are the programs in this repository.
- **Stores** — the databases those programs talk to: Redis (config and events), Qdrant (search vectors), Neo4j (the knowledge graph), and MongoDB (sessions and metadata). They are not PipesHub code; they run as their own containers.

Two ways to run it locally:

1. **Everything in Docker** — one installer starts the stores and the application services as containers. Use this to try a full instance without starting each process yourself.
2. **Application services from source** — you still start the stores in Docker, then you run the API, Python services, and UI yourself from the files in this checkout (`npm run dev`, `python -m app.embedding_main`, and so on). Your edits load without rebuilding a Docker image. Use this when you are changing that code.

### Run everything in Docker

The installer starts the stores and the application services together:

```bash
./install.sh --build
```

The rest of this section is for running application services from source.

### System packages

#### Linux

```bash
sudo apt update
sudo apt install python3.12-venv
sudo apt-get install libreoffice
```

#### macOS

Install [Homebrew](https://brew.sh) if `brew` is missing, then:

```bash
brew install python@3.12
brew install libreoffice
brew install mariadb-connector-c
```

#### Windows

Install Python 3.12, or use WSL2 and follow the Linux steps.

### Application tools

1. **Docker** — Redis, Qdrant, Neo4j, and MongoDB (the default stores below)
2. **Node.js 24 LTS** (22 minimum, 22.13 for the frontend; `.nvmrc` names the minimum, `nvm use`) — API in `backend/nodejs/apps`, frontend in `frontend`. There is no root `package.json`.
3. **Python 3.12** — FastAPI services in `backend/python`

### Environment files

Copy `backend/env.template` into both backend trees. Defaults are `DATA_STORE=neo4j`, `MESSAGE_BROKER=redis`, and `KV_STORE_TYPE=redis`.

```bash
cp backend/env.template backend/nodejs/apps/.env
cp backend/env.template backend/python/.env
```

Passwords and the Qdrant API key in those files must match the containers in the next step.

### Default stores

Start Redis, Qdrant, Neo4j, and MongoDB. Passwords and keys must match the `.env` files above.

**Redis** (config KV and Redis Streams as the event bus):

```bash
docker run -d --name redis --restart always -p 6379:6379 redis:7.4.12-bookworm
```

**Qdrant** (vectors). The API key must match `QDRANT_API_KEY` in `.env`:

```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.15.5
```

**Neo4j** (graph). The password must match `NEO4J_PASSWORD` in `.env`. Neo4j Desktop is fine instead of the container; Bolt stays on `localhost:7687`.

```bash
docker run -d --name neo4j --restart always -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_neo4j_password neo4j:5.26.0
```

**MongoDB** (sessions and metadata). Username and password must match `MONGO_URI` in `.env`:

```bash
docker run -d --name mongodb --restart always -p 27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=admin \
  -e MONGO_INITDB_ROOT_PASSWORD=password \
  mongo:8.0.17
```

### Optional stores

Do not start these unless you change the env defaults:

- ArangoDB instead of Neo4j: `DATA_STORE=arangodb`. Do not run it next to Neo4j.
- Kafka instead of Redis Streams: `MESSAGE_BROKER=kafka` (ZooKeeper and Kafka).
- etcd instead of Redis KV: `KV_STORE_TYPE=etcd`.

If you switch graph or KV backend on existing data, reset `dataStoreType` in the KV store first. Python writes that key from `DATA_STORE` on startup; the Node API reads it for health checks, so a leftover value from the previous backend will disagree with `.env`.

### Node.js API (port 3000)

```bash
cd backend/nodejs/apps
npm ci
npm run dev
```

### Python services

Create the virtualenv once. Then start each process in its own terminal, with the venv activated. Start **embedding** before indexing and query when you use the local HuggingFace model.

```bash
cd backend/python
python3.12 -m venv venv
source venv/bin/activate  # Windows: use WSL; install.sh is a POSIX shell script
# The hashed lock CI and the images install (pip --require-hashes): dev adds the test and lint tools CI uses,
# runtime is enough to run services. Changing a dependency: docs/agent-harness-v2/guides/SUPPLY-CHAIN.md.
PYTHON=venv/bin/python requirements/install.sh dev --project . --editable
python -c "import nltk; nltk.download('punkt')"   # into ~/nltk_data; the services need it, the tests do not

python -m app.embedding_main
python -m app.connectors_main
python -m app.indexing_main
python -m app.query_main
python -m app.docling_main
```

`pyproject.toml` excludes `unclecode-litellm`, a fork that crawl4ai pins and that writes into the same `litellm/` directory as the real `litellm`, so the locks never hold it. A venv installed before the exclusion may still carry it. Removing the fork deletes files litellm owns too, so remove both and install the lock again, which puts the locked litellm back whole:

```bash
venv/bin/python -m pip uninstall -y unclecode-litellm litellm \
  && PYTHON=venv/bin/python requirements/install.sh dev --project . --editable
```

`tests/unit/test_single_litellm_owner.py` fails while two packages own `litellm/`.

Parsing (`app.parsing_main`, 8092) and extraction (`app.extraction_main`, 8093) only when `USE_PARSING_SERVICE=true`. That variable is not in `backend/env.template`; set it in `backend/python/.env`.

### Frontend (port 3001)

Next.js uses port 3000 if `PORT` is unset, which collides with the API.

```bash
cd frontend
cp env.template .env
npm ci
PORT=3001 npm run dev
```

Open `http://localhost:3001`. On Windows PowerShell, use `Copy-Item` instead of `cp` and `$env:PORT = '3001'`.

### Health check

```bash
./scripts/check_system_health.sh
```

This pings the API (3000), the UI (3001), and the Python services. Parsing and extraction are checked only if `USE_PARSING_SERVICE=true` in the shell (`export USE_PARSING_SERVICE=true` before the script).

### Agent harness on a laptop (dev-harness)

To work on the agent harness (durable runs, the gateways, the sandbox broker and egress proxy) you need the whole
stack, but not real model keys. One command brings up a stack from this checkout with a scripted model:

```bash
bash deployment/e2e/run.sh dev-up
```

It runs the `dev-harness` stack profile (`deployment/e2e/docker-compose.dev-harness.yml` on top of
`deployment/e2e/docker-compose.e2e.yml`):

- every PipesHub process from this checkout, with Node and the UI built from it (nothing to rebuild an image for);
- its own MongoDB replica set and Redis, Neo4j and Qdrant, all removed again by `run.sh down`;
- the scripted model (`deployment/e2e/fake_llm/server.py`) behind the real model gateway, so no turn calls a paid
  model; it answers the directives in a message, for example `say hello`
  (`backend/python/harness_core/testing/engines/script.py`);
- a scripted SaaS with an MCP server and a Jira API (`deployment/e2e/fake_saas/server.py`) in place of real
  connectors;
- the durable worker for chat-loop runs; coding engines are off unless you run it with `E2E_ENGINE_RUNS=true`, and
  then run on hardened runc through `PIPESHUB_ENGINES_WITHOUT_GVISOR`, the development override, because a laptop
  has no gVisor ([GVISOR.md](./deployment/docker-compose/GVISOR.md));
- a seeded org, admin and agent, and one scripted turn checked before it says it is ready
  (`deployment/e2e/dev_smoke.py`).

It needs Docker with Compose v2, Node 22 (`E2E_NODE_HOME`, default `~/.local/node/current`), the Python venv from the
steps above (`E2E_SITE_PACKAGES`, default `backend/python/venv/lib/python3.12/site-packages`), and `npm ci` in
`backend/nodejs/apps` and `frontend`. It pulls the public images it lacks, including the sandbox image
(`E2E_SANDBOX_IMAGE`, default `pipeshubai/pipeshub-sandbox:latest`). When it is up it prints the URL and the
admin's email; the password is `E2E_ADMIN_PASSWORD` in the run's `stack.env`.

```bash
bash deployment/e2e/run.sh dev-smoke <run>   # the scripted turn again
bash deployment/e2e/run.sh down <run>        # remove the stack and its data
```

Each of the stack's networks gets a /26 of `10.216.0.0/16` that no network on your machine holds, chosen when the
stack is created; nothing is taken from Docker's default address pools.

## Project Architecture

Three application layers, plus stores:

1. **Frontend** — Next.js UI (`frontend/`). Local `npm run dev` on port **3001**.
2. **Node.js API** — Express (`backend/nodejs/apps`). Local `npm run dev` on port **3000**. Auth, orgs, knowledge base, object storage, API gateway.
3. **Python FastAPI services** (`backend/python/`):
   - **Embedding** (8002, `app.embedding_main`) — local HuggingFace / SentenceTransformers via an OpenAI-compatible API. Indexing and query call this for default dense embeddings. Cloud embedding providers do not need this process.
   - **Connectors** (8088, `app.connectors_main`) — OAuth, token refresh, and data-source integrations.
   - **Indexing** (8091, `app.indexing_main`) — parse, chunk, embed; writes vectors and graph nodes.
   - **Query** (8000, `app.query_main`) — search, RAG, agents.
   - **Docling** (8081, `app.docling_main`) — PDF and other complex documents.
   - **Parsing** (8092) and **Extraction** (8093) — optional; only with `USE_PARSING_SERVICE=true`.

**Stores (defaults in `backend/env.template`):** Redis (config KV and Redis Streams as the event bus), Qdrant (vectors), Neo4j (graph), MongoDB (sessions and metadata). ArangoDB can replace Neo4j. Kafka can replace Redis Streams on a larger deployment. etcd can replace Redis as the KV store.

In Docker (`./install.sh --build`), Node serves the API and the built UI together on port **3000**.

## Extension points

Other editions are built on this repository. They plug in only through the files below, each of which holds a
working default here. Keep that default working, and do not add edition-specific logic anywhere else. The
signatures are a public API: change one only in a pull request that updates the default and its tests with it.

| Tier | File | What it holds | Default here |
| --- | --- | --- | --- |
| Python | `backend/python/app/edition.py` | Import-free constants | This build's values |
| Python | `backend/python/app/edition_config.py` | Optional routers and resolvers; a service mounts each one only when it is set | `None` |
| Python | `backend/python/app/edition_services.py` | Service hooks and classes. `harness_edition()` returns the agent harness's ports (`harness_core.edition.HarnessEdition`): a feature that can be replaced defines a port and a default, and reads it with `harness_edition().port(Port, DEFAULT)`; a feature this build does not have reads its port with `harness_edition().supplied(Port)` (`None`: off). Ports: `WriteIdentityPort` (who an external write is from), `OrgReferencesProvider` (an attempt's environment snapshots and org caches, and the worker's background job). Agent turns: `register_extra_agent_hooks` (hooks on both loops, after the completion gate), `extend_tool_grant` (tools a turn is granted after the session's grant), `split_record_continuation` (how one fetch continuing several records is read) | No-op hooks; no harness port, so every read answers its default (every write as the user with the AI disclosure; no snapshot, no org cache); the grant unchanged; a multi-record continuation refused |
| Python | `backend/python/app/edition_containers.py` | Dependency-injection containers | This build's containers |
| Node | `backend/nodejs/apps/src/config.ts` | `editionBoot` (start-up and shutdown hooks). `harnessEdition`: harness binders (run before the built-in ones, so what they bind is what the harness keeps), admin and internal route registrars (added after the built-in routes; each checks its own caller), routers mounted on the app at their own prefix under `/api/`, policy section rules (`CONTROL_PLANE_POLICY_SECTION_RULES`; each feature binds its own list, and a section outside the layer's own members is refused without one), index migrations (run after the built-in ones; an id or an index that repeats one is refused at boot), where audit anchors are written, background workers started and stopped with the harness, erasers a deleted org's erasure runs after the built-in ones, and the steps of a stored org erasure job (`orgErasure`). `orgLifecycleHook` (how an org is deleted, and how sign-in treats an org that is not live) and `scopedTokenGuard` (a sign-in good for its own routes only, which every other route refuses), installed at boot (`src/libs/org-lifecycle`) | `noopEditionBoot`, `noopHarnessEdition` (add nothing; an org job waits unchanged); `orgLifecycleHook` `null` (an org is deleted at once), `scopedTokenGuard` claims no token |
| Node | `backend/nodejs/apps/src/edition.ts` | Import-free constants. `EDITION_DOCSTORE_COVERAGE`: how a move of the document store to PostgreSQL treats the collections an edition writes (ADR-DOCMIG-01), in one place: it classifies them, `held` (preflight refuses the move while one holds documents; `tables` names where its data lives, none when kept in MongoDB only), `ignored` (not copied, with the reason) and `heldFields` (fields an edition adds to a collection the copy maps, held the same way), and it maps them, `carriers` (each a docstore-migrate `Carrier`, `carry/types.ts`, typed `unknown` here so the file stays import-free and checked for its shape when the migrator loads): a carrier copies, follows, verifies, rolls back and resets its collections and fields as the built-in store groups are, may carry only held collections and held fields (this build's or the edition's), none twice, and what it carries is no longer held; the shared coverage test holds both to the same rules as this build's | This build's values; no edition collections and no carrier, so the edition-written collections this build lists stay held |
| Node | `backend/nodejs/apps/src/integrations/slack-bot/src/edition.ts` | The Slack bot's edition, installed with `installSlackBotEdition` by an edition's own bot entry before it imports the bot (`./index`): `orgOfBot` (the organisation a bot configuration serves: whose links name the Slack person, the org on the bot's token, the org its thread map is kept as; a throw means the bot answers nobody) and `threadRepository` (the thread map, which reads the org from the context `asBotOrg` binds) | The install's only organisation; the configured document store's thread map |
| Node | `backend/nodejs/apps/src/libs/services/telemetry/modules/auth-metrics.ts` | `addAuthAttemptMethods`: sign-in methods an edition offers, added to `pipeshub_auth_attempts_total`'s `method` label set when its module loads (lowercase names, checked once), so its sign-ins are counted with `recordAuthAttempt` like this build's | This build's methods only; any other name reads `other` |
| Python | `backend/python/app/services/graph_db/` providers | `_asking_user` and `_user_anchor` (Neo4j), `_user_anchor_filter` (ArangoDB): how the virtual-record-id reads resolve and match the asking user; a subclass may narrow them and bind what it adds | Match by user id |
| Node | `backend/nodejs/apps/src/modules/storage/` | `StorageContainer.createStorageController` (a subclass returns its own `StorageController`); in that controller, `readConfiguredStorageType` (every storage-type read), `getStorageConfig`, `uploadSizeLimitBytes` and `watchStorageType`. Export the subclassed container through `src/config.ts` | The configured key-value store's storage type and the platform upload limit |
| Frontend | `frontend/config.ts` | Component and hook slots, and the stores (`useAuthStore`, `useOrgStore`). `workspaceNavExtras`: workspace sidebar entries, each placed in a section (after a named entry, or at the end); an entry for a built-in route replaces that entry. Shared code takes every name it re-exports from `@/config`, never from the module behind it (`frontend/lib/__tests__/config-seam-imports.test.ts`) | Slots render nothing; no extra entries |
| Frontend | `frontend/config-pages.ts` | Pages | This build's pages |

Contracts follow one directory convention instead of a file. An edition's repository may hold
`contracts/harness/ee/`: schemas in a directory per contract group (`core`, `ext/pipeshub`, `control-plane`), and
`flags.json` or `config-surface.json` that add entries to the documents of the same name (they may add, never
redefine). When it is present, `contracts/harness/generate.sh` generates its schemas into an `ee` package or
directory beneath each group's own output, and the C-5, C-16 and C4.10 tests read it too. This repository has no
such directory, so it generates nothing for it; `FLAGS.md` renders this repository's matrix only.

Tests that scan the source tree for the sites they accept (the org-context, structural-writer and permission-writer
ratchets, the model funnel, the record-access fake, the usage convention, the durable-parity inventory, the
open-file limits' stateless images and the Compose variants' flag values) read an overlay the same way: `backend/python/tests/ee/ratchets/<name>.json` adds entries to the list a test keeps, never redefines one
(`backend/python/tests/edition_overlay.py`). On the Node side the Mongo seam and org-context ratchets add the baselines found under the directories in `SEAM_BASELINE_DIRS`.

Docs cover this repository's code only, and only as reference: design, decisions, operation and testing. Project
tracking (status boards, work logs, evidence captures, work-package records, research and review notes) is kept
outside this repository, as is an edition's own design notes; nothing adds marked rows here for a test to skip.

`scripts/edition_guard.py` keeps the repository neutral (CI: `.github/workflows/edition-guard.yml`). It fails a
change that adds a file under an `ee/` directory or an edition-only build file, adds an import of an `ee` package
or directory, adds a file to a directory that accepts fixes only, adds a proprietary licence marker, or adds a
project-tracking document under `docs/agent-harness-v2/`. Run it
before you push; `scripts/verify.sh` runs it on what you have not committed yet:

```bash
python3 -I scripts/edition_guard.py --base origin/main
```

## Contribution Workflow

1. **Fork the repository** to your GitHub account
2. **Clone your fork** to your local machine
3. **Create a new branch** for your feature or bug fix:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Make your changes** following our code style guidelines. Documentation edits do not need a local stack.
5. **Test your changes** thoroughly
6. **Commit your changes** with meaningful commit messages:
   ```bash
   git commit -m "Add feature: brief description of changes"
   ```
7. **Push your branch** to your GitHub fork:
   ```bash
   git push origin feature/your-feature-name
   ```
8. **Open a Pull Request** against our main repository
   - Provide a clear description of the changes
   - Reference any related issues
   - Add screenshots if applicable
9. **Sign the [Contributor License Agreement](CLA.md)** when the CLA Assistant bot asks, on your first pull request (see below)

Pull requests from forks run the integration tests without repository secrets:
CI runs only the helper unit tests under `integration-tests/unit/`. A maintainer
runs the full suite on a reviewed commit using *Run workflow* on
`integration-tests.yml` with that commit's hash (see
[integration-tests/README.md](integration-tests/README.md)).

## Contributor License Agreement

Before we can merge your first pull request, you need to sign our [Contributor License Agreement](CLA.md) (CLA). You keep the copyright to your work; the CLA gives PipesHub a license to use and distribute it.

Signing takes a minute. The CLA Assistant bot comments on your pull request with a link; follow it, sign in with GitHub and select **I agree**. You only sign once.

If the `license/cla` check is still failing after you signed:

- Select **recheck** in the bot's comment; that re-runs the check.
- Make sure the email in your commits is linked to your GitHub account ([GitHub's guide](https://docs.github.com/en/account-and-profile/setting-up-and-managing-your-personal-account-on-github/managing-email-preferences/adding-an-email-address-to-your-github-account)). Commits made with an unlinked email can't be matched to your signature. To fix your own commits on the branch:

  ```bash
  git config user.email "you@example.com"   # an email linked to your GitHub account
  git remote get-url upstream >/dev/null 2>&1 || git remote add upstream https://github.com/pipeshub-ai/pipeshub-ai.git
  git fetch upstream
  git log --merges --oneline upstream/main..HEAD   # if this prints anything, ask for help on the pull request instead
  # Rewrite only the commits authored with your old email (replace old@example.com); other people's commits keep their author
  git rebase --exec 'if [ "$(git log -1 --format=%ae)" = "old@example.com" ]; then git commit --amend --no-edit --reset-author; fi' upstream/main
  git log --format="%h %ae %s" upstream/main..HEAD   # check your commits now show the new email
  git push --force-with-lease origin HEAD:feature/your-feature-name   # your pull request's branch
  ```

## Code Style Guidelines

- **Python**: Follow PEP 8 guidelines
- **JavaScript/TypeScript**: Use ESLint with our project configuration
- **CSS/SCSS**: Follow BEM naming convention
- **Commit Messages**: Use the conventional commits format

## Testing

- Write unit tests for new features
- Ensure all tests pass before submitting a PR
- Include integration tests where appropriate
- Document manual testing steps for complex features

### Checking everything at once

```bash
scripts/verify.sh          # every suite that runs without Docker or network
scripts/verify.sh --list   # what would run, and why anything is skipped
```

This covers the Python, Node, frontend, Electron and shell suites and prints a
single summary. A suite that cannot run on your machine is reported as skipped
with the reason, never as a pass.

It does not run `integration-tests/`, which needs the whole stack and shared
cloud storage and takes hours — that runs in CI. It does run
`backend/python/tests/integration`, which despite the directory name is mostly
in-process; the suites there that genuinely need a service skip themselves when
it is absent.

The suites that need a service run in the `backend-matrix` workflow, one job per
backend, and any skip fails the job. Tests that read `ARTIFACTS_IT_STORAGE_URL`
(the artifact store and the storage service's routes) run in its `storage` job,
on local disk, S3 (MinIO) and Azure Blob (Azurite), against the real storage
routes started by `backend/nodejs/apps/tests/support/storage-it-server.ts`. To run them
locally, follow the header of
[`backend/python/tests/integration/compose/storage.yml`](backend/python/tests/integration/compose/storage.yml).

### Running Node.js Unit Tests

Tests use **Mocha** as the test runner with **c8** for code coverage. Test files are located in `backend/nodejs/apps/tests/` and follow the `*.test.ts` naming convention. See [`backend/nodejs/apps/tests/README.md`](backend/nodejs/apps/tests/README.md) for full details.

```bash
cd backend/nodejs/apps

# Run all unit tests (parallel, 4 workers)
npm run test

# Run tests with detailed coverage report (text + lcov + html)
npm run test:coverage

# The coverage gate CI enforces: unit tests plus the MongoDB and Redis itests, one threshold
# check on both (needs MONGO_IT_URI and REDIS_IT_URL; see the tests README)
npm run test:coverage-gate

# Run a specific test file
npx mocha --require ts-node/register tests/libs/utils/password.utils.test.ts
```

### Running Python Unit Tests

Tests use **pytest** and are located in `backend/python/tests/`. Test files follow the `test_*.py` naming convention. See [`backend/python/tests/README.md`](backend/python/tests/README.md) for full details.

```bash
cd backend/python
source venv/bin/activate

# Run all unit tests
pytest

# Run tests with verbose output
pytest -v

# Run a specific test file
pytest tests/unit/connectors/sources/test_dropbox_connector.py

# Run a specific test function
pytest tests/unit/connectors/sources/test_dropbox_connector.py::test_function_name

# Run tests matching a keyword expression
pytest -k "gmail"

# Run tests with coverage
pytest --cov=app --cov-report=term-missing

# Run tests in parallel (requires pytest-xdist)
pytest -n auto
```

### Running Frontend Unit Tests

Unit tests use **Vitest** and live in `__tests__` folders beside the code they test under `frontend/app/` and `frontend/lib/`, named `*.test.ts` or `*.test.tsx`.

```bash
cd frontend

# Run all unit tests
npm run test:unit

# Run them with coverage, as CI does (reports land in coverage/unit)
npm run test:unit:coverage
```

CI runs the coverage command and fails when coverage drops below the minimum set in `coverage.thresholds` in `frontend/vitest.config.ts`. If your change trips it, add tests rather than lowering the minimum.

### Running Frontend E2E Tests (Playwright)

The frontend (`frontend/`) uses [Playwright](https://playwright.dev/) for end-to-end testing. Tests cover authentication, navigation, workspace settings, entity CRUD (users, groups, teams), chat, and knowledge base pages. **Authoritative E2E details** live in [`frontend/tests/e2e/README.md`](frontend/tests/e2e/README.md); the following is a contributor-oriented summary.

#### Prerequisites

1. Install dependencies (includes `@playwright/test`):
   ```bash
   cd frontend
   npm ci
   ```

2. Install Playwright browsers:
   ```bash
   npx playwright install chromium
   ```

3. Create a `.env.test` file from the template and fill in test credentials:
   ```bash
   cp .env.test.example .env.test
   ```

   Required variables:
   | Variable | Description |
   |----------|-------------|
   | `TEST_USER_EMAIL` | Email of an existing admin user |
   | `TEST_USER_PASSWORD` | Password for that user |
   | `BASE_URL` | Where Playwright opens the app (default in config: `http://localhost:3001`) |
   | `NEXT_PUBLIC_API_BASE_URL` | Backend URL for API calls (seeding/fixtures); defaults to `http://localhost:3000` in fixtures when unset |

#### Running E2E Tests

All commands below run from the `frontend/` directory.

| Command | Description |
|---------|-------------|
| `npm run test:e2e` | Run all tests (starts dev server automatically) |
| `npm run test:e2e:ui` | Open Playwright UI for interactive debugging |
| `npm run test:e2e:headed` | Run tests in a visible browser |
| `npm run test:e2e:seed` | Seed bulk test data (30 users, 30 groups, 30 teams) |
| `npm run test:e2e:cleanup` | Delete all seeded test data |
| `npm run test:e2e:users` | Run only user-related tests |
| `npm run test:e2e:groups` | Run only group-related tests |
| `npm run test:e2e:teams` | Run only team-related tests |
| `npm run test:e2e:report` | Open the HTML test report |
| `npm run test:e2e:coverage` | Run all tests with V8 code coverage |
| `npm run test:e2e:coverage-report` | Open the coverage HTML report |

#### Code Coverage

Run `npm run test:e2e:coverage` to collect V8 code coverage. Reports are generated in `coverage/e2e/` with V8, LCOV, and console summary formats. Open the HTML report with `npm run test:e2e:coverage-report`.

#### Debugging & Verbose Output

To watch test execution in a visible browser and capture full traces (including passing tests):

```bash
# Visible browser + trace for every test
npx playwright test --headed --trace on

# Slow motion — 1 second pause between each action
npx playwright test --headed --trace on --slow-mo=1000

# Record video of every test
npx playwright test --headed --video on

# Screenshot after every test (pass or fail)
npx playwright test --screenshot on
```

| Flag | What it does |
|------|-------------|
| `--headed` | Opens a visible browser window instead of running headless |
| `--trace on` | Records a trace for every test (default only records on first retry) |
| `--slow-mo=N` | Adds N milliseconds pause between each Playwright action |
| `--video on` | Records a video of every test run |
| `--screenshot on` | Takes a screenshot after every test (not just failures) |

**Interactive UI mode** (recommended for debugging):

```bash
npm run test:e2e:ui
```

This opens Playwright's built-in UI with a live browser, action timeline, and DOM snapshots you can step through.

**Viewing traces and reports after a run:**

```bash
# Open the HTML report — click any test to see its trace
npx playwright show-report

# Open a specific trace file directly
npx playwright show-trace test-results/<test-folder>/trace.zip
```

#### E2E Test Projects

Playwright is configured with four projects that run in order:

1. **setup** — Logs in via the browser and saves auth state to `.auth/user.json`.
2. **seed** — Seeds bulk data using a mix of UI interactions and API calls. Depends on `setup`.
3. **authenticated** — All feature tests that use the saved auth state. Depends on `setup`.
4. **unauthenticated** — Login page tests that run without saved auth.

#### E2E Directory Structure

```
frontend/tests/e2e/
├── setup/           # Auth setup (login + save storageState)
├── fixtures/        # Shared test fixtures (API context, base)
├── helpers/         # Reusable interaction helpers
│   ├── login.helper.ts
│   ├── entity-table.helper.ts
│   ├── pagination.helper.ts
│   ├── search.helper.ts
│   ├── sidebar-form.helper.ts
│   └── tag-input.helper.ts
├── seed/            # Data seeding and cleanup
├── auth/            # Login and logout tests
├── navigation/      # Routing and sidebar navigation tests
├── workspace/       # Workspace settings page tests
├── users/           # Users table, invite, actions, bulk ops
├── groups/          # Groups table, create, actions
├── teams/           # Teams table, create, actions
├── chat/            # Chat interface tests
└── knowledge-base/  # Knowledge base tests
```

#### Writing New E2E Tests

- **Authenticated tests** go in a feature folder under `frontend/tests/e2e/` and import from `@playwright/test`. They automatically use the saved auth state.
- **API-based tests** (seeding, cleanup) import from `../fixtures/api-context.fixture` relative to other specs in `tests/e2e/` (see `seed/` and `setup/`).
- **Helpers** in `tests/e2e/helpers/` provide reusable functions for common UI interactions (table rows, pagination, search, sidebar forms, tag input).

Example test:
```typescript
import { test, expect } from '@playwright/test';

test.describe('My Feature', () => {
  test('loads the page', async ({ page }) => {
    await page.goto('/workspace/my-feature/');
    await expect(page.locator('text="My Feature"')).toBeVisible();
  });
});
```

#### Seed Data Conventions

- Seeded users follow the pattern `e2e-user-XXXX@e2etest.pipeshub.local`
- Seeded groups are named `E2E Group XXX`
- Seeded teams are named `E2E Team XXXX`
- Always run `npm run test:e2e:cleanup` after seeded test runs to remove test data

#### E2E CI Notes

In CI, set the environment variable `CI=true` to enable:
- Retries (2 attempts per test)
- Single worker (sequential execution)
- Fresh dev server (no reuse)

#### E2E Artifacts

The following are generated during test runs and are gitignored:
- `.auth/` — Saved browser auth state
- `test-results/` — Test artifacts (screenshots, traces)
- `playwright-report/` — HTML report

## Community Guidelines

- Be respectful and inclusive in all interactions
- Provide constructive feedback on pull requests
- Help new contributors get started
- Report any inappropriate behavior to the project maintainers

---

Thank you for contributing to our project! If you have any questions or need help, please open an issue, ask in [Discord](https://discord.com/invite/K5RskzJBm2), or reach out to the maintainers.
