# Contributing to PipesHub Workplace AI 

<div align="center">

**Translations:** [Français](docs/i18n/fr/CONTRIBUTING.md) · [Deutsch](docs/i18n/de/CONTRIBUTING.md) · [简体中文](docs/i18n/zh-CN/CONTRIBUTING.md) · [日本語](docs/i18n/ja/CONTRIBUTING.md) · [Русский](docs/i18n/ru/CONTRIBUTING.md) · [עברית](docs/i18n/he/CONTRIBUTING.md) · [한국어](docs/i18n/ko/CONTRIBUTING.md) · [Español](docs/i18n/es/CONTRIBUTING.md) · [Português](docs/i18n/pt/CONTRIBUTING.md) · [Türkçe](docs/i18n/tr/CONTRIBUTING.md) · [Tiếng Việt](docs/i18n/vi/CONTRIBUTING.md) · [Italiano](docs/i18n/it/CONTRIBUTING.md)

</div>

Welcome to our open source project! We're excited that you're interested in contributing. This document provides guidelines and instructions to help you get started as a contributor.

## 💻 Developer Contribution Build

## Table of Contents
- [Prerequisites](#prerequisites)
- [Graph Database — Two Ways to Run Neo4j](#graph-database--two-ways-to-run-neo4j)
- [Quick Start](#quick-start)
- [Manual Setup (advanced)](#manual-setup-advanced)
- [Project Architecture](#project-architecture)
- [Contribution Workflow](#contribution-workflow)
- [Code Style Guidelines](#code-style-guidelines)
- [Testing](#testing)
- [Documentation](#documentation)
- [Community Guidelines](#community-guidelines)

> **Translations may lag behind this English version, which is authoritative.**

## Prerequisites

Install the following tools (links go to the official installers):

| Tool | Version | Install |
|------|---------|---------|
| **Git** | latest | [git-scm.com](https://git-scm.com/downloads) |
| **GNU Make** | latest | Runs all the `make` targets below. Linux: install `make` (e.g. `build-essential`); macOS: `xcode-select --install`; Windows: use **Git Bash** or **WSL2**. [GNU Make](https://www.gnu.org/software/make/) |
| **Docker** | latest | [Get Docker](https://docs.docker.com/get-docker/) — Docker Desktop on Windows/macOS, Docker Engine on Linux |
| **Node.js** | v22.15.0 | [nodejs.org](https://nodejs.org/) |
| **uv** | latest | [Astral uv install guide](https://docs.astral.sh/uv/getting-started/installation/) — the Python package & project manager used by the backend |
| **Python** | 3.12 | **Not required separately** — `uv` manages it for you (`uv python install 3.12`). Fallback: [python.org](https://www.python.org/downloads/) |
| **LibreOffice** | latest | [libreoffice.org](https://www.libreoffice.org/download/download-libreoffice/) — used by the docling service to parse office documents |
| **MariaDB Connector/C** | latest | Required to build some Python dependencies. Linux: `libmariadb-dev`; macOS: `brew install mariadb-connector-c`; Windows: [MariaDB Connector/C](https://mariadb.com/downloads/connectors/) |
| **OpenCV runtime libs** *(Linux only)* | latest | Required by OpenCV (`cv2`), used in PDF parsing. Linux: `libgl1 libglib2.0-0` (`apt install`).Not needed on macOS/Windows. |

> **Windows:** run the `make` commands below under **Git Bash** or **WSL2** with
> GNU Make installed — the Makefile uses shell recipes that `cmd`/PowerShell
> cannot run. Docker Desktop must be running.

## Graph Database — Two Ways to Run Neo4j

PipesHub stores its knowledge graph in **Neo4j** (selected by `DATA_STORE=neo4j`,
which is the default). Choose **one** of the two options below to provide Neo4j.
This section only sets up the database — you'll launch the app later in
[Quick Start](#quick-start), and each option tells you which command to use there.

### Option 1 — Neo4j Desktop *(preferred)*

A local GUI with the Neo4j Browser for inspecting the graph, multiple DBMSs, and
plugins such as APOC. The most convenient option for day-to-day development.

> ⚠️ **License:** Neo4j Desktop is distributed under the
> [**Neo4j Desktop License**](https://neo4j.com/legal-terms/desktop-license/) —
> intended for **development and evaluation**. Review the terms before commercial use.

1. Install [Neo4j Desktop](https://neo4j.com/download/), create a **local DBMS**,
   set a password, and **Start** it (leave the Bolt listener on `localhost:7687`).
2. Do **not** also run a Neo4j Docker container — Desktop already provides one.
3. In your `.env` files (`backend/nodejs/apps/.env` and `backend/python/.env`,
   both created by `make install`), set `NEO4J_PASSWORD` to the password you gave
   the DBMS. The other Neo4j values already default correctly:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<your DBMS password>
   NEO4J_DATABASE=neo4j
   ```

➡️ In **[Quick Start](#quick-start)**, run **`make services`** — it starts only
the supporting containers (Redis, Qdrant, MongoDB), since Neo4j Desktop already
provides the graph database.

### Option 2 — Neo4j in Docker *(quick alternative)*

No GUI, no license click-through, zero configuration — Neo4j runs in a container
alongside the other services. The default `.env` values already target it
(`NEO4J_URI=bolt://localhost:7687`), so no extra setup is needed.

➡️ In **[Quick Start](#quick-start)**, run **`make services-neo4j`** instead of
`make services` — it starts the supporting containers **and** Neo4j together
(`neo4j:5.26-community`), so you don't run `make services` separately.

## Quick Start

All commands run from the `scripts/` directory.

```bash
cd scripts

make doctor    # verify system deps, Docker, and Neo4j reachability
make install   # install Node.js, Python (via uv), and frontend dependencies
make services  # start supporting Docker services (redis, qdrant, mongo)
make start     # run all PipesHub services (5 Python + Node.js + frontend)
```

> Local dev uses **Redis** for both the config store and messaging (Redis
> Streams), so the supporting stack is just Redis, Qdrant, and MongoDB. The
> `.env` template defaults to `KV_STORE_TYPE=redis` and `MESSAGE_BROKER=redis`.

**What each command does:**

- **`make services`** starts only the supporting containers (Redis, Qdrant,
  MongoDB) — it does **not** start a graph database, because the preferred setup
  runs **Neo4j Desktop** locally (see [Graph Database](#graph-database--two-ways-to-run-neo4j)
  above). To run everything in Docker instead, use **`make services-neo4j`**,
  which starts the supporting containers **and** Neo4j; then skip Neo4j Desktop.
- **`make start`** runs all seven processes together via `npx concurrently`, with
  prefixed, color-coded logs. Press **Ctrl-C once** to stop them all.
- **`make help`** lists every available target.

### Running services individually

Prefer a terminal per service — for example, to restart one without the others
or to watch a single service's logs? Run the per-service `make` targets instead
of `make start`. Start the embedding server first if you use the built-in local
embedding model.

| Service | Command | Port |
|---------|---------|------|
| Embedding   | `make embedding`  | 8002 |
| Connectors  | `make connectors` | 8088 |
| Indexing    | `make indexing`   | 8091 |
| Query       | `make query`      | 8000 |
| Docling     | `make docling`    | 8081 |
| Node.js API | `make nodejs`     | 3000 |
| Frontend    | `make frontend`   | 3001 |

Each Python target activates the project's `uv` environment automatically; the
backing Docker services must already be running (start them with `make services`).

## Manual Setup (advanced)

The `make` targets above are the recommended path. The steps below document the
underlying setup for contributors who want to run things by hand.

<details>
<summary><strong>Show manual setup steps</strong></summary>

### Starting infrastructure containers manually

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (API Key must match with .env)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**Neo4j:** see [Graph Database — Two Ways to Run Neo4j](#graph-database--two-ways-to-run-neo4j)
above for the recommended setup. To start Neo4j in Docker by hand:

```bash
docker run -d --name neo4j --restart always -p 7474:7474 -p 7687:7687 \
  -e NEO4J_AUTH=neo4j/your_password neo4j:5.26.2-community
```

**MongoDB:** (Password must match with .env MONGO URI)

Bash:
```bash
docker run -d --name mongodb --restart always -p 27017:27017 \
  -e MONGO_INITDB_ROOT_USERNAME=admin \
  -e MONGO_INITDB_ROOT_PASSWORD=password \
  mongo:8.0.6
```

Powershell:
```powershell
docker run -d --name mongodb --restart always -p 27017:27017 `
  -e MONGO_INITDB_ROOT_USERNAME=admin `
  -e MONGO_INITDB_ROOT_PASSWORD=password `
  mongo:8.0.6
```

> `make install` performs all three setups below (and copies the `.env` files
> from the templates). Run the steps by hand only if you need to.

### Starting Node.js Backend Service
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Starting Python Backend Services

The Python backend uses [**uv**](https://docs.astral.sh/uv/). `uv` creates the
virtual environment and can also provide Python 3.12 itself.

```bash
cd backend/python
cp ../env.template .env

# Create and activate the virtual environment (uv installs Python 3.12 if needed)
uv venv venv --python 3.12
source venv/bin/activate          # Windows (Git Bash): source venv/Scripts/activate

# Install dependencies into the venv
uv pip install -e . --no-build-isolation-package grpcio-tools

# Install additional language models
python -m spacy download en_core_web_sm
python -c "import nltk; nltk.download('punkt')"

# Run each service (with the venv activated). Start the embedding server before
# indexing and query when using the default local embeddings (HuggingFace /
# SentenceTransformers).
python -m app.embedding_main
python -m app.connectors_main
python -m app.indexing_main
python -m app.query_main
python -m app.docling_main
```

> `make start` runs all of these (plus the Node.js backend and frontend) at once
> with `concurrently`, so you don't need a terminal per service.

### Setting Up Frontend
```bash
cd frontend
cp env.template .env
npm install
npm run dev   # runs on http://localhost:3001 by default
```

Then open your browser to `http://localhost:3001`.

</details>

## Project Architecture

Our project consists of three main components:

1. **Frontend**: Next.js application for the user interface
2. **Node.js Backend**: Handles API requests, authentication, and business logic
3. **Python Services**: Five microservices for:
   - **Embedding** (port 8002): Serves local HuggingFace / SentenceTransformer models via an OpenAI-compatible API (`app.embedding_main`). Indexing and query call this service for default dense embeddings.
   - **Connectors** (port 8088): Handles data source connections
   - **Indexing** (port 8091): Manages document indexing and processing
   - **Query** (port 8000): Processes search and retrieval requests
   - **Docling** (port 8081): Advanced PDF/document parsing for complex formats

When running services locally with `make`, start **embedding** before **indexing** and **query** if you rely on the built-in local embedding model (`BAAI/bge-large-en-v1.5`). Cloud/API embedding providers (OpenAI, Cohere, etc.) do not require the embedding server.

## Contribution Workflow

1. **Fork the repository** to your GitHub account
2. **Clone your fork** to your local machine
3. **Create a new branch** for your feature or bug fix:
   ```bash
   git checkout -b feature/your-feature-name
   ```
4. **Make your changes** following our code style guidelines
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

## Documentation

- Update documentation for any new features or changes
- Document APIs with appropriate comments and examples
- Keep README and other guides up to date

## Community Guidelines

- Be respectful and inclusive in all interactions
- Provide constructive feedback on pull requests
- Help new contributors get started
- Report any inappropriate behavior to the project maintainers

---

Thank you for contributing to our project! If you have any questions or need help, please open an issue or reach out to the maintainers.
