"""
Shared fixtures for connector integration tests.

Provides session-scoped fixtures for:
  - pipeshub_client  (PipeshubClient)
  - neo4j_driver     (Neo4j Driver)
  - sample_data_root (Path to sample data files)
  - storage helpers  (S3, GCS, Azure Blob, Azure Files)
"""

import os
import sys
from pathlib import Path
from typing import Generator, Optional

import pytest
from neo4j import Driver, GraphDatabase

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SAMPLE_DATA_DIR = _ROOT / "sample-data"
if str(_SAMPLE_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_SAMPLE_DATA_DIR))

from pipeshub_client import (  # type: ignore[import-not-found]  # noqa: E402
    ConnectorInstance,
    PipeshubClient,
)
from sample_data import ensure_sample_data_files_root  # type: ignore[import-not-found]  # noqa: E402


# ---------------------------------------------------------------------------
# Lookup helper
# ---------------------------------------------------------------------------

def _get_existing_connector_by_name(
    client: PipeshubClient,
    instance_name: str,
    scope: str = "personal",
) -> Optional[ConnectorInstance]:
    """Look up an existing connector instance by name."""
    data = client.list_connectors(scope=scope, search=instance_name, limit=50)
    connectors = data.get("connectors") or []
    for c in connectors:
        if c.get("name") != instance_name:
            continue
        connector_id = c.get("connectorId") or c.get("_key")
        if not connector_id:
            continue
        return ConnectorInstance(
            connector_id=connector_id,
            connector_type=c.get("connectorType") or c.get("type") or "",
            instance_name=c.get("name") or instance_name,
            scope=c.get("scope") or scope,
        )
    return None


# ---------------------------------------------------------------------------
# Session fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pipeshub_client() -> PipeshubClient:
    """Session-scoped Pipeshub client for talking to test.pipeshub.com."""
    return PipeshubClient()


@pytest.fixture(scope="session")
def neo4j_driver() -> Generator[Driver, None, None]:
    """Session-scoped Neo4j driver (uses TEST_NEO4J_* from .env.local / .env.prod)."""
    uri = os.getenv("TEST_NEO4J_URI")
    user = os.getenv("TEST_NEO4J_USERNAME")
    password = os.getenv("TEST_NEO4J_PASSWORD")

    if not uri or not user or not password:
        pytest.skip("TEST_NEO4J_URI / TEST_NEO4J_USERNAME / TEST_NEO4J_PASSWORD not set; skipping connector integration tests.")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        yield driver
    finally:
        driver.aclose()


@pytest.fixture(scope="session")
def sample_data_root() -> Path:
    """Session-scoped path to sample data files from GitHub."""
    return ensure_sample_data_files_root()


# ---------------------------------------------------------------------------
# Storage helper fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def s3_storage():
    """Session-scoped S3StorageHelper."""
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    if not access_key or not secret_key:
        pytest.skip("S3 credentials not set.")
    from storage_helpers import S3StorageHelper  # type: ignore[import-not-found]
    return S3StorageHelper(access_key=access_key, secret_key=secret_key)


@pytest.fixture(scope="session")
def gcs_storage():
    """Session-scoped GCSStorageHelper."""
    sa_json = os.getenv("GCS_SERVICE_ACCOUNT_JSON")
    if not sa_json:
        pytest.skip("GCS_SERVICE_ACCOUNT_JSON not set.")
    from storage_helpers import GCSStorageHelper  # type: ignore[import-not-found]
    return GCSStorageHelper(service_account_json=sa_json)


@pytest.fixture(scope="session")
def azure_blob_storage():
    """Session-scoped AzureBlobStorageHelper."""
    conn_str = os.getenv("AZURE_BLOB_CONNECTION_STRING")
    if not conn_str:
        pytest.skip("AZURE_BLOB_CONNECTION_STRING not set.")
    from storage_helpers import AzureBlobStorageHelper  # type: ignore[import-not-found]
    return AzureBlobStorageHelper(connection_string=conn_str)


@pytest.fixture(scope="session")
def azure_files_storage():
    """Session-scoped AzureFilesStorageHelper."""
    conn_str = os.getenv("AZURE_FILES_CONNECTION_STRING")
    if not conn_str:
        pytest.skip("AZURE_FILES_CONNECTION_STRING not set.")
    from storage_helpers import AzureFilesStorageHelper  # type: ignore[import-not-found]
    return AzureFilesStorageHelper(connection_string=conn_str)
