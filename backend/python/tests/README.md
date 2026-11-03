# Python Backend Unit Tests

Unit tests for the PipesHub Python microservices using **pytest**.

## Prerequisites

```bash
cd backend/python
python3.12 -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install uv
uv pip install -e .
```

## Running Tests

```bash
# Run all unit tests
pytest

# Run tests with verbose output
pytest -v

# Run tests with detailed output per test
pytest -v --tb=short
```

## Running Specific Tests

```bash
# Run a specific test file
pytest tests/unit/connectors/sources/test_dropbox_connector.py

# Run a specific test function
pytest tests/unit/connectors/sources/test_dropbox_connector.py::test_function_name

# Run a specific test class
pytest tests/unit/connectors/core/test_connector_factory.py::TestConnectorFactory

# Run tests matching a keyword expression
pytest -k "gmail"

# Run tests matching multiple keywords
pytest -k "connector and not dropbox"
```

## Coverage

```bash
# Run tests with coverage report
pytest --cov=app --cov-report=term-missing

# Generate HTML coverage report
pytest --cov=app --cov-report=html

# Run tests in parallel (requires pytest-xdist)
pytest -n auto
```

## How CI runs these

`.github/workflows/unit-test.yml` splits `tests/unit` into shards, one job each,
and every shard runs its tests across 4 pytest-xdist workers
(`-n logical --dist loadfile`). A later job combines the shards' coverage and
applies the `--fail-under` gate; `python-unit-tests` is the one check that
passes only when everything did.

- **Which shard runs a test**: `scripts/unit_shards.py` maps directories to
  shards. A new directory needs no entry: anything no other shard claims runs
  in `core`. `scripts/test_unit_shards.py` fails if a file would run in no
  shard or in two.
- **Run one shard locally** (from `backend/python`):

  ```bash
  pytest $(python3 ../../scripts/unit_shards.py args agents) -n logical --dist loadfile
  ```

- **Tests must not depend on the order files run in.** A worker runs any
  subset of a shard's files, in any order, in one process. Two ways this has
  broken:
  - Parametrizing over a `set`: its order changes per process, the workers
    collect different test IDs, and xdist refuses to run. Use `sorted(...)`.
  - Replacing module-level state (a singleton, a registry, a module constant)
    without putting it back: every later file on that worker sees it. Use
    `monkeypatch`, or a fixture that restores the original.
- **Time limits**: CI passes `--timeout=60`, not `pytest.ini`'s 30s, because
  4 workers share 2 physical cores. For a performance check (a linear-time
  guard, say), compare against a control input timed in the same test rather
  than a fixed number of milliseconds: coverage slows Python code ~3x and a
  shared core slows it again. `@pytest.mark.no_cover` does not help here, as
  it does not stop coverage inside xdist workers. See
  `TestAdversarialInput` in `tests/unit/utils/test_url_redaction.py`.
- **Keeping shards even**: the run takes as long as the slowest shard.
  `scripts/unit_shard_durations.json` holds measured minutes per test file,
  and `scripts/test_unit_shards.py` fails when one shard grows far past the
  average. See `scripts/unit_shards.py` for how to refresh and rebalance.

## Directory Structure

```
tests/
├── conftest.py                       # Shared fixtures and configuration
└── unit/
    └── connectors/
        ├── core/                     # Core connector framework tests
        │   ├── test_connector_factory.py
        │   ├── test_connector_registry.py
        │   ├── test_connector_service.py
        │   ├── test_token_refresh.py
        │   ├── test_sync_task_manager.py
        │   ├── test_auth_builder.py
        │   ├── test_tool_builder.py
        │   ├── test_graph_data_store.py
        │   ├── test_filters.py
        │   ├── interfaces/           # Interface contract tests
        │   └── registry/             # Registry definition tests
        ├── sources/                  # Individual connector tests
        │   ├── test_google_drive_*.py
        │   ├── test_azure_files_*.py
        │   └── ...
        ├── utils/                    # Connector utility tests
        └── test_oauth_service.py     # OAuth flow tests
```

## Configuration

Test configuration is defined in `pytest.ini`:

| Setting | Value |
|---------|-------|
| Test paths | `tests/` |
| Async mode | `auto` (async tests run automatically) |
| File pattern | `test_*.py` |
| Class pattern | `Test*` |
| Function pattern | `test_*` |
| Default timeout | 30 seconds per test |

## Conventions

- Test files use the `test_*.py` naming convention
- Test classes use the `Test*` naming convention
- Test functions use the `test_*` naming convention
- Shared fixtures go in `conftest.py`
- Async tests are supported natively (asyncio_mode = auto)
