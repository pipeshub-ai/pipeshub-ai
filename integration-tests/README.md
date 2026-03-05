## Integration tests against `test.pipeshub.com`

Full lifecycle integration tests for all supported storage connectors.
Tests run **only** against the shared remote test environment
(`https://test.pipeshub.com`) and a **remote Neo4j Aura** instance.
No local services are required.

### Quick start

```bash
cd integration-tests
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"   # or just: pip install -e .
pytest -m integration -v
```

### Environment variables

Create or update `integration-tests/.env` with:

| Variable                                                                              | Purpose                                |
| ------------------------------------------------------------------------------------- | -------------------------------------- |
| `PIPESHUB_BASE_URL`                                                                   | `https://test.pipeshub.com`            |
| `CLIENT_ID`, `CLIENT_SECRET`                                                          | OAuth2 client credentials for test org |
| `S3_ACCESS_KEY`, `S3_SECRET_KEY`                                                      | AWS S3 test credentials                |
| `GCS_SERVICE_ACCOUNT_JSON`                                                            | GCS service account JSON               |
| `AZURE_BLOB_CONNECTION_STRING`                                                        | Azure Blob Storage connection string   |
| `AZURE_FILES_CONNECTION_STRING`                                                       | Azure Files connection string          |
| `TEST_NEO4J_URI`, `TEST_NEO4J_USERNAME`, `TEST_NEO4J_PASSWORD`, `TEST_NEO4J_DATABASE` | Neo4j Aura                             |

### Test lifecycle

Each connector test class runs an **ordered 11-step lifecycle**:

```
 1. Create bucket/container/share     (storage SDK)
 2. Upload sample data from GitHub    (storage SDK)
 3. Create connector instance         (Pipeshub API)
 4. Enable sync (init + test conn)    (Pipeshub API)
 5. Full sync → graph validation      (Neo4j: records, groups, edges, orphan check)
 6. Incremental sync → graph check    (Neo4j: count ≥ previous)
 7. Rename file → graph validation    (storage SDK + sync + Neo4j)
 8. Move file → graph validation      (storage SDK + sync + Neo4j)
 9. Disable connector                 (Pipeshub API)
10. Delete connector → graph clean    (Pipeshub API + Neo4j: zero records/groups/edges)
11. Cleanup bucket/container/share    (storage SDK)
```

### Running specific connectors

```bash
pytest -m s3 -v              # S3 only
pytest -m gcs -v             # GCS only
pytest -m azure_blob -v      # Azure Blob only
pytest -m azure_files -v     # Azure Files only
pytest -m integration -v     # all connectors
```

### Key files

| File                     | Purpose                                                       |
| ------------------------ | ------------------------------------------------------------- |
| `conftest.py`            | Loads `.env`, wires Neo4j env vars                            |
| `pipeshub_client.py`     | HTTP client for Pipeshub connector API                        |
| `graph_assertions.py`    | Neo4j graph validation helpers                                |
| `storage_helpers.py`     | Direct storage SDK helpers (S3, GCS, Azure Blob, Azure Files) |
| `sample_data_repo.py`    | Clones GitHub sample data repo on demand                      |
| `connectors/conftest.py` | Session-scoped fixtures (client, driver, storage helpers)     |
| `connectors/*/`          | Per-connector lifecycle test files                            |

### Sample data

Tests clone the [pipeshub-ai/integration-test](https://github.com/pipeshub-ai/integration-test)
repo automatically and use files from `sample-data/entities/files/`.
Override the URL or cache location via:
- `PIPESHUB_INTEGRATION_TEST_REPO_URL`
- `PIPESHUB_INTEGRATION_TEST_CACHE_DIR`
