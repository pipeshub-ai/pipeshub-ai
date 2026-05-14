# Contributing to PipesHub Workplace AI 

Welcome to our open source project! We're excited that you're interested in contributing. This document provides guidelines and instructions to help you get started as a contributor.

## 💻 Developer Contribution Build

## Table of Contents
- [Setting Up the Development Environment](#setting-up-the-development-environment)
- [Project Architecture](#project-architecture)
- [Contribution Workflow](#contribution-workflow)
- [Code Style Guidelines](#code-style-guidelines)
- [Testing](#testing)
- [Documentation](#documentation)
- [Community Guidelines](#community-guidelines)

## Setting Up the Development Environment

### System Dependencies

#### Linux
```bash
sudo apt update
sudo apt install python3.12-venv
sudo apt-get install libreoffice
```

#### Mac
```bash
# Install Homebrew if not already installed
/bin/bash -c "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"


# Install required packages
brew install python@3.12
brew install libreoffice
```

#### Windows
```bash
- Install Python 3.12
- Consider using WSL2 for a Linux-like environment
```

### Application Dependencies
```bash
1. **Docker** - Install Docker for your platform
2. **Node.js** - Install Node.js(v22.15.0)
3. **Python 3.12** - Install as shown above
4. **Optional debugging tools:**
   - MongoDB Compass or Studio 3T
   - etcd-manager
```

### Starting Required Docker Containers

**Redis:**
```bash
docker run -d --name redis --restart always -p 6379:6379 redis:bookworm
```

**Qdrant:** (API Key must match with .env)
```bash
docker run -p 6333:6333 -p 6334:6334 -e QDRANT__SERVICE__API_KEY=your_qdrant_secret_api_key qdrant/qdrant:v1.13.6
```

**ETCD Server:**


Bash:
```bash
docker run -d --name etcd-server --restart always -p 2379:2379 -p 2380:2380 quay.io/coreos/etcd:v3.5.17 /usr/local/bin/etcd \
  --name etcd0 \
  --data-dir /etcd-data \
  --listen-client-urls http://0.0.0.0:2379 \
  --advertise-client-urls http://0.0.0.0:2379 \
  --listen-peer-urls http://0.0.0.0:2380
```

Powershell:
```powershell
docker run -d --name etcd-server --restart always `
  -p 2379:2379 -p 2380:2380 `
  quay.io/coreos/etcd:v3.5.17 /usr/local/bin/etcd `
  --name etcd0 `
  --data-dir /etcd-data `
  --listen-client-urls http://0.0.0.0:2379 `
  --advertise-client-urls http://0.0.0.0:2379 `
  --listen-peer-urls http://0.0.0.0:2380
```

**ArangoDB:** (Password must match with .env)
```bash
docker run -e ARANGO_ROOT_PASSWORD=your_password -p 8529:8529 --name arango --restart always -d arangodb:3.12.4
```

**Neo4j Desktop (instead of ArangoDB):** PipesHub can use **Neo4j** as the graph database (`DATA_STORE=neo4j`) instead of ArangoDB. This is useful if you prefer a local GUI and do not want the ArangoDB container.

1. Install [Neo4j Desktop](https://neo4j.com/download/), create a **local DBMS**, set its password, and **Start** it.
2. Leave the default Bolt listener on **localhost:7687** (or note the host/port shown in Desktop if you changed them).
3. **Do not** start the ArangoDB Docker container above when using Neo4j.
4. In `backend/.env` (the template you copy into `backend/nodejs/apps/.env` and `backend/python/.env`), set at least:
   ```bash
   DATA_STORE=neo4j
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USERNAME=neo4j
   NEO4J_PASSWORD=<same password as your DBMS>
   NEO4J_DATABASE=neo4j
   ```
   The Python services read `DATA_STORE` and write `dataStoreType` into the KV store (etcd/Redis) on startup; the Node.js API uses that for health checks and treats `NEO4J_*` as the live Neo4j connection.
5. Start the **connectors** Python service (`python -m app.connectors_main`) before or with the rest of the stack so deployment metadata stays consistent. If you already bootstrapped against ArangoDB on the same etcd data, reset etcd or the deployment key in KV store before switching graph backends to avoid mismatched state.

For a full stack in Docker with Neo4j instead of ArangoDB, see `docker-compose.build.neo4j.yml` in `deployment/docker-compose/` (documented in the repository `README.md`).

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

**Zookeeper:**

Bash:
```bash
docker run -d --name zookeeper --restart always -p 2181:2181 \
  -e ZOOKEEPER_CLIENT_PORT=2181 \
  -e ZOOKEEPER_TICK_TIME=2000 \
  confluentinc/cp-zookeeper:7.9.0
```

Powershell:
```powershell
docker run -d --name zookeeper --restart always -p 2181:2181 `
  -e ZOOKEEPER_CLIENT_PORT=2181 `
  -e ZOOKEEPER_TICK_TIME=2000 `
  confluentinc/cp-zookeeper:7.9.0
```


**Apache Kafka:**

Bash:
```bash
docker run -d --name kafka --restart always --link zookeeper:zookeeper -p 9092:9092 \
  -e KAFKA_BROKER_ID=1 \
  -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 \
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 \
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=PLAINTEXT:PLAINTEXT \
  -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT \
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 \
  confluentinc/cp-kafka:7.9.0
```

Powershell:
```powershell
docker run -d --name kafka --restart always --link zookeeper:zookeeper -p 9092:9092 `
  -e KAFKA_BROKER_ID=1 `
  -e KAFKA_ZOOKEEPER_CONNECT=zookeeper:2181 `
  -e KAFKA_ADVERTISED_LISTENERS=PLAINTEXT://localhost:9092 `
  -e KAFKA_LISTENER_SECURITY_PROTOCOL_MAP=PLAINTEXT:PLAINTEXT `
  -e KAFKA_INTER_BROKER_LISTENER_NAME=PLAINTEXT `
  -e KAFKA_OFFSETS_TOPIC_REPLICATION_FACTOR=1 `
  confluentinc/cp-kafka:7.9.0
```

### Starting Node.js Backend Service
```bash
cd backend/nodejs/apps
cp ../../env.template .env  # Create .env file from template
npm install
npm run dev
```

### Starting Python Backend Services
```bash
cd backend/python
cp ../env.template .env
# Create and activate virtual environment
python3.12 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .

# Install additional language models
python -m spacy download en_core_web_sm
python -c "import nltk; nltk.download('punkt')"

# Run each service in a separate terminal: First, cd backend/python and activate the existing virtual environment
python -m app.connectors_main
python -m app.indexing_main
python -m app.query_main
python -m app.docling_main
```

### Setting Up Frontend
```bash
cd frontend
cp env.template .env  # Modify port if Node.js backend uses a different one
npm install
PORT=3001 npm run dev
```

Then open your browser to the displayed URL (typically `http://localhost:3001` when using `PORT=3001`; Next.js defaults to port 3000 if `PORT` is unset).

## Project Architecture

Our project consists of three main components:

1. **Frontend**: Next.js application for the user interface
2. **Node.js Backend**: Handles API requests, authentication, and business logic
3. **Python Services**: Three microservices for:
   - Connectors: Handles data source connections
   - Indexing: Manages document indexing and processing
   - Query: Processes search and retrieval requests

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
