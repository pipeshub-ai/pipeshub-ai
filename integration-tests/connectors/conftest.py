# pyright: ignore-file

"""
Shared fixtures for connector integration tests.

Provides:
  - Session-scoped: pipeshub_client, neo4j_driver, sample_data_root, storage helpers
  - Module-scoped constructor/destructor fixtures for each connector:
    s3_connector, gcs_connector, azure_blob_connector, azure_files_connector
"""

import logging
import os
import random
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Generator, Optional

import pytest
import requests
from neo4j import Driver, GraphDatabase
from neo4j.exceptions import Neo4jError

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

_SAMPLE_DATA_DIR = _ROOT / "sample-data"
if str(_SAMPLE_DATA_DIR) not in sys.path:
    sys.path.insert(0, str(_SAMPLE_DATA_DIR))

from graph_assertions import (  # type: ignore[import-not-found]  # noqa: E402
    assert_all_records_cleaned,
    count_records,
)
from pipeshub_client import (  # type: ignore[import-not-found]  # noqa: E402
    ConnectorInstance,
    PipeshubAuthError,
    PipeshubClient,
    PipeshubClientError,
)
from sample_data import ensure_sample_data_files_root  # type: ignore[import-not-found]  # noqa: E402

logger = logging.getLogger("connector-fixtures")

_CONNECTOR_API_TEARDOWN_ERRORS = (
    AssertionError,
    PipeshubAuthError,
    PipeshubClientError,
    requests.exceptions.RequestException,
)

_CONNECTOR_DELETE_TEARDOWN_ERRORS = _CONNECTOR_API_TEARDOWN_ERRORS + (TimeoutError, Neo4jError)


def _storage_clear_error_types() -> tuple[type[BaseException], ...]:
    """Exception types cloud SDKs typically raise from list/delete operations."""
    types_list: list[type[BaseException]] = [
        OSError,
        requests.exceptions.RequestException,
    ]
    try:
        from botocore.exceptions import ClientError

        types_list.append(ClientError)
    except ImportError:
        pass
    try:
        from google.api_core.exceptions import GoogleAPIError

        types_list.append(GoogleAPIError)
    except ImportError:
        pass
    try:
        from azure.core.exceptions import AzureError

        types_list.append(AzureError)
    except ImportError:
        pass
    return tuple(types_list)


_STORAGE_CLEAR_ERRORS = _storage_clear_error_types()


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
# Internal: shared constructor / destructor logic
# ---------------------------------------------------------------------------

_RESOURCE_NAME = "pipeshub-integration-tests"


def _ensure_resource_exists(storage, resource_name: str, create_fn: str) -> None:
    """Create storage resource if it doesn't exist, otherwise reuse. Retries for S3 eventual consistency."""
    conflict_markers = (
        "BucketAlreadyOwnedByYou",
        "BucketAlreadyExists",
        "OperationAborted",
        "already exists",
    )
    for attempt in range(6):  # ~30s+ of retries (base delay + jitter)
        try:
            objects = storage.list_objects(resource_name)
            if isinstance(objects, list):
                return  # already exists
        except Exception:
            pass
        try:
            getattr(storage, create_fn)(resource_name)
            return
        except Exception as e:
            error_str = str(e)
            if any(marker in error_str for marker in conflict_markers):
                logger.info("Resource %s not ready yet (attempt %d), waiting...", resource_name, attempt + 1)
                time.sleep(5 + random.uniform(0, 2))
            else:
                raise
    # Final attempt — let it fail with a clear error if still not accessible
    objects = storage.list_objects(resource_name)
    assert isinstance(objects, list), f"Resource {resource_name} still not accessible after retries"


def _constructor(
    storage,
    pipeshub_client: PipeshubClient,
    neo4j_driver: Driver,
    sample_data_root: Path,
    *,
    storage_name: str,
    connector_type: str,
    connector_config: dict,
    create_fn: str = "create_bucket",
) -> Dict[str, Any]:
    """Shared setup: ensure storage exists, upload data, create connector, wait for sync."""
    resource_name = _RESOURCE_NAME
    connector_name = f"{connector_type.lower().replace(' ', '-')}-lifecycle-test-{uuid.uuid4().hex[:8]}"

    state: Dict[str, Any] = {
        "resource_name": resource_name,
        "connector_name": connector_name,
    }

    # 1. Ensure storage resource exists (create if needed, reuse if already there)
    logger.info("CONSTRUCTOR [%s]: Ensuring %s exists", connector_type, resource_name)
    _ensure_resource_exists(storage, resource_name, create_fn)
    objects = storage.list_objects(resource_name)
    assert isinstance(objects, list), f"{storage_name} should be accessible"

    # 2. Upload sample data
    count = storage.upload_directory(resource_name, sample_data_root)
    logger.info("CONSTRUCTOR [%s]: Uploaded %d files to %s", connector_type, count, resource_name)
    assert count > 0, "Expected at least 1 file in sample data"
    state["uploaded_count"] = count

    # Pick files for rename / move / update tests
    objects = storage.list_objects(resource_name)
    picked_files = [k for k in objects if not k.endswith("/")][:2]
    assert len(picked_files) >= 1, "No file objects after upload"

    state["rename_source_key"] = picked_files[0]
    state["rename_source_name"] = Path(picked_files[0]).name
    update_key = picked_files[1] if len(picked_files) >= 2 else picked_files[0]
    state["update_target_key"] = update_key
    state["update_target_name"] = Path(update_key).name

    # 3. Create connector
    instance = pipeshub_client.create_connector(
        connector_type=connector_type,
        instance_name=connector_name,
        scope="personal",
        config=connector_config,
    )
    assert instance.connector_id, "Connector must have a valid ID"
    connector_id = instance.connector_id
    state["connector_id"] = connector_id
    logger.info("CONSTRUCTOR [%s]: Connector created: %s", connector_type, connector_id)

    # 4. Enable sync + wait for full sync
    pipeshub_client.toggle_sync(connector_id, enable=True)
    logger.info("CONSTRUCTOR [%s]: Sync enabled — waiting for full sync (connector %s)", connector_type, connector_id)

    uploaded = state["uploaded_count"]
    pipeshub_client.wait_for_sync(
        connector_id,
        check_fn=lambda: count_records(neo4j_driver, connector_id) >= uploaded,
        timeout=180,
        poll_interval=10,
        description="full sync",
    )
    full_count = count_records(neo4j_driver, connector_id)
    state["full_sync_count"] = full_count
    logger.info("CONSTRUCTOR [%s]: Full sync complete — %d records (connector %s)", connector_type, full_count, connector_id)

    return state


def _destructor(
    storage,
    pipeshub_client: PipeshubClient,
    neo4j_driver: Driver,
    state: Dict[str, Any],
    *,
    connector_type: str,
    cleanup_timeout: int = 180,
) -> None:
    """Shared teardown: disable, delete connector + graph cleanup, clear storage content."""
    connector_id = state["connector_id"]
    resource_name = state["resource_name"]

    # 1. Disable connector
    logger.info("DESTRUCTOR [%s]: Disabling connector %s", connector_type, connector_id)
    try:
        pipeshub_client.toggle_sync(connector_id, enable=False)
        status = pipeshub_client.get_connector_status(connector_id)
        assert not status.get("isActive"), "Connector should be inactive after disable"
    except _CONNECTOR_API_TEARDOWN_ERRORS:
        logger.exception("DESTRUCTOR [%s]: Failed to disable connector %s", connector_type, connector_id)

    # 2. Delete connector + graph cleanup
    logger.info("DESTRUCTOR [%s]: Deleting connector %s", connector_type, connector_id)
    try:
        pipeshub_client.delete_connector(connector_id)
        pipeshub_client.wait(15)
        assert_all_records_cleaned(neo4j_driver, connector_id, timeout=cleanup_timeout)
        logger.info("DESTRUCTOR [%s]: Graph cleaned for connector %s", connector_type, connector_id)
    except _CONNECTOR_DELETE_TEARDOWN_ERRORS:
        logger.exception("DESTRUCTOR [%s]: Failed to delete/clean connector %s", connector_type, connector_id)

    # 3. Clear storage content (keep the resource, just remove files)
    logger.info("DESTRUCTOR [%s]: Clearing content in %s", connector_type, resource_name)
    try:
        storage.clear_objects(resource_name)
        logger.info("DESTRUCTOR [%s]: Content cleared in %s", connector_type, resource_name)
    except _STORAGE_CLEAR_ERRORS:
        logger.exception("DESTRUCTOR [%s]: Failed to clear content in %s", connector_type, resource_name)


# ---------------------------------------------------------------------------
# Session-scoped fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def pipeshub_client() -> PipeshubClient:
    """Session-scoped Pipeshub client."""
    return PipeshubClient()


@pytest.fixture(scope="session")
def neo4j_driver() -> Generator[Driver, None, None]:
    """Session-scoped Neo4j driver."""
    uri = os.getenv("TEST_NEO4J_URI")
    user = os.getenv("TEST_NEO4J_USERNAME")
    password = os.getenv("TEST_NEO4J_PASSWORD")

    if not uri or not user or not password:
        pytest.skip("TEST_NEO4J_URI / TEST_NEO4J_USERNAME / TEST_NEO4J_PASSWORD not set; skipping connector integration tests.")

    driver = GraphDatabase.driver(uri, auth=(user, password))
    try:
        yield driver
    finally:
        driver.close()


@pytest.fixture(scope="session")
def sample_data_root() -> Path:
    """Session-scoped path to sample data files from GitHub."""
    return ensure_sample_data_files_root()


# ---------------------------------------------------------------------------
# Storage helper fixtures (session-scoped)
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


# ---------------------------------------------------------------------------
# Module-scoped connector fixtures (constructor / destructor)
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def s3_connector(s3_storage, pipeshub_client, neo4j_driver, sample_data_root) -> Generator[Dict[str, Any], None, None]:
    """S3 connector lifecycle."""
    access_key = os.getenv("S3_ACCESS_KEY")
    secret_key = os.getenv("S3_SECRET_KEY")
    # S3Connector / run_sync: without auth.bucket the worker lists every account bucket.
    # Least-privilege IAM often allows only the integration bucket — scope explicitly.
    config = {
        "auth": {
            "accessKey": access_key,
            "secretKey": secret_key,
            "bucket": _RESOURCE_NAME,
        }
    }
    region = os.getenv("S3_REGION")
    if region:
        config["auth"]["region"] = region

    state = _constructor(
        s3_storage, pipeshub_client, neo4j_driver, sample_data_root,
        storage_name="S3 bucket", connector_type="S3", connector_config=config,
        create_fn="create_bucket",
    )
    state["bucket_name"] = state["resource_name"]
    yield state
    _destructor(s3_storage, pipeshub_client, neo4j_driver, state, connector_type="S3")


@pytest.fixture(scope="module")
def gcs_connector(gcs_storage, pipeshub_client, neo4j_driver, sample_data_root) -> Generator[Dict[str, Any], None, None]:
    """GCS connector lifecycle."""
    sa_json = os.getenv("GCS_SERVICE_ACCOUNT_JSON")
    config = {"auth": {"serviceAccountJson": sa_json}}

    state = _constructor(
        gcs_storage, pipeshub_client, neo4j_driver, sample_data_root,
        storage_name="GCS bucket", connector_type="GCS", connector_config=config,
        create_fn="create_bucket",
    )
    state["bucket_name"] = state["resource_name"]
    yield state
    _destructor(gcs_storage, pipeshub_client, neo4j_driver, state, connector_type="GCS")


@pytest.fixture(scope="module")
def azure_blob_connector(azure_blob_storage, pipeshub_client, neo4j_driver, sample_data_root) -> Generator[Dict[str, Any], None, None]:
    """Azure Blob connector lifecycle."""
    conn_str = os.getenv("AZURE_BLOB_CONNECTION_STRING")
    config = {"auth": {"azureBlobConnectionString": conn_str}}

    state = _constructor(
        azure_blob_storage, pipeshub_client, neo4j_driver, sample_data_root,
        storage_name="Azure Blob container", connector_type="Azure Blob", connector_config=config,
        create_fn="create_container",
    )
    state["container_name"] = state["resource_name"]
    yield state
    _destructor(azure_blob_storage, pipeshub_client, neo4j_driver, state, connector_type="Azure Blob")


@pytest.fixture(scope="module")
def azure_files_connector(azure_files_storage, pipeshub_client, neo4j_driver, sample_data_root) -> Generator[Dict[str, Any], None, None]:
    """Azure Files connector lifecycle."""
    conn_str = os.getenv("AZURE_FILES_CONNECTION_STRING")
    config = {"auth": {"connectionString": conn_str}}

    state = _constructor(
        azure_files_storage, pipeshub_client, neo4j_driver, sample_data_root,
        storage_name="Azure Files share", connector_type="Azure Files", connector_config=config,
        create_fn="create_share",
    )
    state["share_name"] = state["resource_name"]
    yield state
    _destructor(azure_files_storage, pipeshub_client, neo4j_driver, state, connector_type="Azure Files")
