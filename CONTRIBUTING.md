# Contributing to PipesHub Workplace AI 

<div align="center">

**Translations:** [Français](docs/i18n/fr/CONTRIBUTING.md) · [Deutsch](docs/i18n/de/CONTRIBUTING.md) · [简体中文](docs/i18n/zh-CN/CONTRIBUTING.md) · [日本語](docs/i18n/ja/CONTRIBUTING.md) · [Русский](docs/i18n/ru/CONTRIBUTING.md) · [עברית](docs/i18n/he/CONTRIBUTING.md) · [한국어](docs/i18n/ko/CONTRIBUTING.md) · [Español](docs/i18n/es/CONTRIBUTING.md) · [Português](docs/i18n/pt/CONTRIBUTING.md) · [Türkçe](docs/i18n/tr/CONTRIBUTING.md) · [Tiếng Việt](docs/i18n/vi/CONTRIBUTING.md) · [Italiano](docs/i18n/it/CONTRIBUTING.md)

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
- [Contribution Workflow](#contribution-workflow)
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

A new connector is a large piece of work. The [connector playbook](CONNECTOR_INTEGRATION_PLAYBOOK.md) is the starting point; Discord is a good place to talk through scope first.

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
sudo apt install libmariadb-dev
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
2. **Node.js 22** — API in `backend/nodejs/apps`, frontend in `frontend`. There is no root `package.json`.
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
docker run -d --name redis --restart always -p 6379:6379 redis:7.4-bookworm
```

**Qdrant** (vectors). The API key must match `QDRANT_API_KEY` in `.env`:

```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.15
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
npm install
npm run dev
```

### Python services

Create the virtualenv once. Then start each process in its own terminal, with the venv activated. Start **embedding** before indexing and query when you use the local HuggingFace model.

```bash
cd backend/python
python3.12 -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install uv
uv pip install -e .
python -c "import nltk; nltk.download('punkt')"

python -m app.embedding_main
python -m app.connectors_main
python -m app.indexing_main
python -m app.query_main
python -m app.docling_main
```

Parsing (`app.parsing_main`, 8092) and extraction (`app.extraction_main`, 8093) only when `USE_PARSING_SERVICE=true`. That variable is not in `backend/env.template`; set it in `backend/python/.env`.

### Frontend (port 3001)

Next.js uses port 3000 if `PORT` is unset, which collides with the API.

```bash
cd frontend
cp env.template .env
npm install
PORT=3001 npm run dev
```

Open `http://localhost:3001`. On Windows PowerShell, use `Copy-Item` instead of `cp` and `$env:PORT = '3001'`.

### Health check

```bash
./scripts/check_system_health.sh
```

This pings the API (3000), the UI (3001), and the Python services. Parsing and extraction are checked only if `USE_PARSING_SERVICE=true` in the shell (`export USE_PARSING_SERVICE=true` before the script).

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

### Running Node.js Unit Tests

Tests use **Mocha** as the test runner with **c8** for code coverage. Test files are located in `backend/nodejs/apps/tests/` and follow the `*.test.ts` naming convention. See [`backend/nodejs/apps/tests/README.md`](backend/nodejs/apps/tests/README.md) for full details.

```bash
cd backend/nodejs/apps

# Run all unit tests (parallel, 4 workers)
npm run test

# Run tests with detailed coverage report (text + lcov + html)
npm run test:coverage

# Run tests with coverage thresholds (90% lines/functions/statements, 80% branches)
npm run test:coverage-check

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

### Running Frontend E2E Tests (Playwright)

The frontend (`frontend/`) uses [Playwright](https://playwright.dev/) for end-to-end testing. Tests cover authentication, navigation, workspace settings, entity CRUD (users, groups, teams), chat, and knowledge base pages. **Authoritative E2E details** live in [`frontend/tests/e2e/README.md`](frontend/tests/e2e/README.md); the following is a contributor-oriented summary.

#### Prerequisites

1. Install dependencies (includes `@playwright/test`):
   ```bash
   cd frontend
   npm install
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
