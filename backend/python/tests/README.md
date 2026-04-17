# Python Backend Unit Tests

Unit tests for the PipesHub Python microservices using **pytest**.

## Prerequisites

```bash
cd backend/python
python3.12 -m venv venv
source venv/bin/activate   # On Windows: venv\Scripts\activate
pip install -e .
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
