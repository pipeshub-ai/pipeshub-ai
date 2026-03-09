# Integration Tests

Full lifecycle integration tests for all supported Pipeshub storage connectors (S3, GCS, Azure Blob, Azure Files). Tests run against either:

- **Remote:** shared test environment (`https://test.pipeshub.com`) and remote Neo4j Aura — no local services required.
- **Local:** your local backend (e.g. `localhost:3000`) and local or remote Neo4j — for development and debugging.

---

## Table of contents

- [Prerequisites](#prerequisites)
- [Quick start (remote)](#quick-start-remote)
- [Environment variables reference](#environment-variables-reference)
- [Setup: step-by-step](#setup-step-by-step)
- [Running tests](#running-tests)
- [Test lifecycle](#test-lifecycle)
- [Local runs](#local-runs)
- [Sample data](#sample-data)
- [Key files](#key-files)
- [Troubleshooting](#troubleshooting)

---

## Prerequisites

- **Python 3.10+**
- **Git** (for cloning the sample data repo)
- **Network access** to:
  - Pipeshub (test.pipeshub.com or your local backend)
  - Neo4j (Aura or local)
  - Storage APIs (AWS, GCP, Azure) for the connectors you want to test

---

## Quick start (remote)

Run against `https://test.pipeshub.com` and remote Neo4j Aura:

```bash
cd integration-tests
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"    # or: pip install -e .
```

1. Set **`.env`** to choose the environment: `PIPESHUB_TEST_ENV=prod` (or `local` for local backend).
2. Create **`.env.prod`** from `.env.prod.example` and fill in: `PIPESHUB_BASE_URL=https://test.pipeshub.com`, `CLIENT_ID`, `CLIENT_SECRET`, `TEST_NEO4J_*`, and storage credentials for the connectors you run (see [Environment variables reference](#environment-variables-reference)).

Then:

```bash
pytest -m integration -v
```

---

## Environment variables reference

### Where to put them

**`.env`** holds only the environment selector (no secrets):

- `PIPESHUB_TEST_ENV=local` → load **`.env.local`** (all credentials for local backend)
- `PIPESHUB_TEST_ENV=prod`  → load **`.env.prod`** (all credentials for test.pipeshub.com)

| File              | When used  | Purpose |
|-------------------|------------|---------|
| `.env`            | Always first | Only `PIPESHUB_TEST_ENV=local` or `prod`. |
| `.env.local`      | When `PIPESHUB_TEST_ENV=local` | Base URL, auth, Neo4j, storage creds for local runs. |
| `.env.prod`       | When `PIPESHUB_TEST_ENV=prod`  | Base URL, auth, Neo4j, storage creds for remote (test.pipeshub.com). |

Do not commit `.env.local` or `.env.prod` — they contain secrets. Use `.env.local.example` and `.env.prod.example` as templates.

---

### Core (required for all runs)

| Variable              | Required | Purpose |
|-----------------------|----------|---------|
| `PIPESHUB_BASE_URL`   | Yes      | Pipeshub API base URL. Remote: `https://test.pipeshub.com`. Local: e.g. `http://localhost:3000`. No trailing slash. |

---

### Authentication (Pipeshub API)

| Variable                      | Required when        | Purpose |
|------------------------------|----------------------|---------|
| `CLIENT_ID`                   | Remote; or Local (if not using test user) | OAuth2 client ID for client_credentials grant. |
| `CLIENT_SECRET`               | Remote; or Local (if not using test user) | OAuth2 client secret. |
| `PIPESHUB_TEST_USER_EMAIL`    | Local only (optional) | Org admin email; used to create OAuth app when `CLIENT_ID`/`CLIENT_SECRET` are empty. |
| `PIPESHUB_TEST_USER_PASSWORD` | Local only (optional) | Org admin password. |
| `PIPESHUB_USER_BEARER_TOKEN`  | Optional (remote)    | Pre-obtained JWT. Session validation may require it for remote runs; the HTTP client uses `CLIENT_ID`/`CLIENT_SECRET` for tokens. |

- **Remote:** You must set `CLIENT_ID` and `CLIENT_SECRET` (from the test org’s OAuth app). The client uses them to get an access token via `POST /api/v1/oauth2/token`. If the session-start warning asks for `PIPESHUB_USER_BEARER_TOKEN`, add a valid JWT to `.env` as well.
- **Local:** Either set `CLIENT_ID` and `CLIENT_SECRET`, or leave them empty and set `PIPESHUB_TEST_USER_EMAIL` and `PIPESHUB_TEST_USER_PASSWORD`; the suite will create an OAuth app and set `CLIENT_ID`/`CLIENT_SECRET` for the run.

---

### Neo4j (graph validation)

| Variable               | Required | Purpose |
|------------------------|----------|---------|
| `TEST_NEO4J_URI`       | Yes (remote) | Neo4j URI (e.g. `neo4j+s://xxxx.databases.neo4j.io` for Aura). |
| `TEST_NEO4J_USERNAME`  | Yes      | Neo4j user. |
| `TEST_NEO4J_PASSWORD`  | Yes      | Neo4j password. |
| `TEST_NEO4J_DATABASE`  | No       | Database name (default: `neo4j`). |

Use `TEST_NEO4J_*` in both `.env.local` and `.env.prod`; the Neo4j driver fixture reads these directly.

---

### Storage credentials (per connector)

Only needed for the connectors you actually run. If a credential is missing, that connector’s tests are skipped.

| Variable                         | Used by      | Purpose |
|----------------------------------|-------------|---------|
| `S3_ACCESS_KEY`                  | S3          | AWS access key for test bucket. |
| `S3_SECRET_KEY`                  | S3          | AWS secret key. |
| `S3_REGION`                      | S3          | Optional; default `us-east-1`. |
| `GCS_SERVICE_ACCOUNT_JSON`       | GCS         | Full JSON key for a GCP service account (single line or multiline string). |
| `AZURE_BLOB_CONNECTION_STRING`   | Azure Blob  | Azure Storage connection string for Blob. |
| `AZURE_FILES_CONNECTION_STRING`  | Azure Files | Azure Storage connection string for File Share. |

---

### Sample data (optional)

| Variable                             | Purpose |
|--------------------------------------|---------|
| `PIPESHUB_INTEGRATION_TEST_REPO_URL` | Override GitHub repo URL for sample data (default: `https://github.com/pipeshub-ai/integration-test.git`). |
| `PIPESHUB_INTEGRATION_TEST_CACHE_DIR` | Override directory for cloning the repo (default: repo root’s `.integration-test-cache`). |

---

## Setup: step-by-step

### 1. Clone and enter the repo

```bash
cd /path/to/pipeshub-ai
cd integration-tests
```

### 2. Create virtualenv and install deps

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

(`.[dev]` is optional if your project doesn’t define dev extras; `pip install -e .` is enough.)

### 3. Create env files

- **`.env`** — copy `.env.example` to `.env` and set **only**:
  - `PIPESHUB_TEST_ENV=prod` for test.pipeshub.com, or
  - `PIPESHUB_TEST_ENV=local` for local backend  
  (No secrets in `.env`.)

**Prod (test.pipeshub.com):**

- Copy `.env.prod.example` to `.env.prod` and set:
  - `PIPESHUB_BASE_URL=https://test.pipeshub.com`
  - `CLIENT_ID`, `CLIENT_SECRET` (and optionally `PIPESHUB_USER_BEARER_TOKEN`)
  - `TEST_NEO4J_URI`, `TEST_NEO4J_USERNAME`, `TEST_NEO4J_PASSWORD` (and `TEST_NEO4J_DATABASE` if needed)
  - Storage credentials for each connector you run (see tables above).

**Local:**

- Copy `.env.local.example` to `.env.local` and set:
  - `PIPESHUB_BASE_URL=http://localhost:3000` (or your backend URL)
  - Either `CLIENT_ID` and `CLIENT_SECRET`, or `PIPESHUB_TEST_USER_EMAIL` and `PIPESHUB_TEST_USER_PASSWORD` (org admin).
  - `TEST_NEO4J_URI`, `TEST_NEO4J_USERNAME`, `TEST_NEO4J_PASSWORD` (and `TEST_NEO4J_DATABASE` if needed).
  - Same storage variables for the connectors you run.

Do not commit `.env.local` or `.env.prod`.

### 4. Run tests

```bash
pytest -m integration -v
```

Environment is chosen by `PIPESHUB_TEST_ENV` in `.env` (local or prod).

---

## Running tests

```bash
pytest -m integration -v
```

**By connector:**

```bash
pytest -m s3 -v
pytest -m gcs -v
pytest -m azure_blob -v
pytest -m azure_files -v
```

**Other options:**

```bash
pytest -m integration -v -k "test_full_lifecycle"   # single test
pytest -m integration -v --tb=long                 # longer tracebacks
pytest -m "integration and not slow" -v             # exclude slow
```

Reports are written to `integration-tests/reports/` with a timestamped filename (e.g. `INTEGRATION_TEST_REPORT_2025-03-09_14-30-45.md`) so you can keep and compare multiple runs.

---

## Test lifecycle

Each connector test class runs an **ordered 11-step lifecycle**:

| Step | Action | How |
|------|--------|-----|
| 1 | Create bucket/container/share | Storage SDK |
| 2 | Upload sample data from GitHub | Storage SDK |
| 3 | Create connector instance | Pipeshub API |
| 4 | Enable sync (init + test connection) | Pipeshub API |
| 5 | Full sync → graph validation | Neo4j: records, groups, edges, orphan check |
| 6 | Incremental sync → graph check | Neo4j: count ≥ previous |
| 7 | Rename file → graph validation | Storage SDK + sync + Neo4j |
| 8 | Move file → graph validation | Storage SDK + sync + Neo4j |
| 9 | Disable connector | Pipeshub API |
| 10 | Delete connector → graph clean | Pipeshub API + Neo4j: zero records/groups/edges |
| 11 | Cleanup bucket/container/share | Storage SDK |

---

## Local runs

To run against your **local backend** (e.g. `localhost:3000`):

1. **`.env`:** Set `PIPESHUB_TEST_ENV=local` so the suite loads `.env.local`.
2. **`.env.local`:** Fill from `.env.local.example` (see [Environment variables reference](#environment-variables-reference) and [Setup step 3](#3-create-env-files)).
3. **Backend:** Start the backend so it is reachable at `PIPESHUB_BASE_URL` from `.env.local`.
4. **Neo4j:** Set `TEST_NEO4J_*` in `.env.local` (local or remote instance).
5. **Auth:** Either set `CLIENT_ID` and `CLIENT_SECRET` in `.env.local`, or leave them empty and set `PIPESHUB_TEST_USER_EMAIL` and `PIPESHUB_TEST_USER_PASSWORD`; the suite will create an OAuth app and use client_credentials.

Run: `pytest -m integration -v` (same command; `.env` with `PIPESHUB_TEST_ENV=local` selects local).

---

## Sample data

Tests clone the [pipeshub-ai/integration-test](https://github.com/pipeshub-ai/integration-test) repo and use files under `sample-data/entities/files/`. Clone is done on demand. Override URL or cache dir with:

- `PIPESHUB_INTEGRATION_TEST_REPO_URL`
- `PIPESHUB_INTEGRATION_TEST_CACHE_DIR`

---

## Key files

| File | Purpose |
|------|---------|
| `.env.example` | Template for `.env` (only `PIPESHUB_TEST_ENV=local` or `prod`). |
| `.env.local.example` | Template for `.env.local` (all vars for local). |
| `.env.prod.example`  | Template for `.env.prod` (all vars for prod). |
| `conftest.py`        | Loads `.env` then `.env.local` or `.env.prod`, exports Neo4j env, local OAuth fixture. |
| `helper/local_auth.py` | Gets OAuth client creds from local backend (initAuth → authenticate → create app). |
| `helper/pipeshub_client.py` | HTTP client for Pipeshub connector API (client_credentials). |
| `helper/graph_assertions.py` | Neo4j graph validation helpers. |
| `helper/storage_helpers.py` | Storage SDK helpers (S3, GCS, Azure Blob, Azure Files). |
| `sample-data/sample_data.py` | Clones sample-data repo and returns path to files. |
| `connectors/conftest.py` | Session fixtures: client, Neo4j driver, sample_data_root, storage helpers. |
| `connectors/*/` | Per-connector lifecycle test modules. |
| `reports/INTEGRATION_TEST_REPORT_<timestamp>.md` | One report per run (timestamp in filename; all kept for comparison). |

---

## Troubleshooting

- **Missing env vars:** At session start, `conftest.py` emits a warning listing missing variables. Fix the listed vars in `.env`, `.env.local`, or `.env.prod`.
- **Skipped connector:** If a storage credential is missing (e.g. `GCS_SERVICE_ACCOUNT_JSON`), that connector’s tests are skipped. Add the credential to run them.
- **Neo4j skip:** If `TEST_NEO4J_URI` / `TEST_NEO4J_USERNAME` / `TEST_NEO4J_PASSWORD` are not set, connector tests that need Neo4j are skipped.
- **Local auth failure:** For local runs with email/password, ensure the user is an org admin and the backend is up at `PIPESHUB_BASE_URL`. Check `helper/local_auth.py` and backend logs.
- **Sample data clone failure:** Ensure `git` is on PATH and the repo URL is reachable. Override with `PIPESHUB_INTEGRATION_TEST_REPO_URL` or use a mirror.
