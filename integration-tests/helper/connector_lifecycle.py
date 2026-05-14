# pyright: ignore-file

"""Shared connector setup/teardown for storage integration tests (global helper)."""

from __future__ import annotations

import logging
import os
import uuid
from pathlib import Path
from typing import Any, Dict

import requests
from neo4j.exceptions import Neo4jError

try:
    import aiohttp

    _GRAPH_TEARDOWN_HTTP_ERRORS: tuple[type[BaseException], ...] = (aiohttp.ClientError,)
except ImportError:
    _GRAPH_TEARDOWN_HTTP_ERRORS = ()

from pipeshub_client import (  # type: ignore[import-not-found]
    PipeshubAuthError,
    PipeshubClient,
    PipeshubClientError,
)

from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import wait_until_graph_condition

logger = logging.getLogger("connector-lifecycle")

_CONNECTOR_API_TEARDOWN_ERRORS = (
    AssertionError,
    PipeshubAuthError,
    PipeshubClientError,
    requests.exceptions.RequestException,
)

_CONNECTOR_DELETE_TEARDOWN_ERRORS = (
    _CONNECTOR_API_TEARDOWN_ERRORS + (TimeoutError, Neo4jError) + _GRAPH_TEARDOWN_HTTP_ERRORS
)


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


STORAGE_CLEAR_ERRORS = _storage_clear_error_types()

RESOURCE_NAME = "pipeshub-integration-tests"

# GCS connector tests target this bucket (pre-provisioned; must exist in GCP).
GCS_BUCKET_NAME = "pipeshub_integration_test"


def ensure_resource_exists(storage: object, resource_name: str) -> None:
    """Verify the storage resource is pre-provisioned and accessible.

    Tests must not create or delete buckets/containers/shares — those are
    provisioned out of band. This only performs an accessibility check.
    """
    try:
        objects = storage.list_objects(resource_name)
        assert isinstance(objects, list)
    except Exception as e:
        raise AssertionError(
            f"Pre-existing resource {resource_name} is not accessible. "
            "Ensure it has been created before running these tests."
        ) from e


async def constructor(
    storage: object,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    sample_data_root: Path,
    *,
    storage_name: str,
    connector_type: str,
    connector_config: dict,
    scope: str = "personal",
    auth_type: str | None = None,
    resource_name_override: str | None = None,
) -> Dict[str, Any]:
    """Verify pre-provisioned storage is reachable, upload data, create connector, wait for full sync.

    The bucket/container/share must already exist; tests never create or delete it.
    Only list/access is checked before upload.
    """
    resource_name = (
        resource_name_override if resource_name_override is not None else RESOURCE_NAME
    )
    connector_name = f"{connector_type.lower().replace(' ', '-')}-lifecycle-test-{uuid.uuid4().hex[:8]}"

    state: Dict[str, Any] = {
        "resource_name": resource_name,
        "connector_name": connector_name,
    }

    logger.info("CONSTRUCTOR [%s]: Ensuring %s exists", connector_type, resource_name)
    ensure_resource_exists(storage, resource_name)
    objects = storage.list_objects(resource_name)
    assert isinstance(objects, list), f"{storage_name} should be accessible"

    count = storage.upload_directory(resource_name, sample_data_root)
    logger.info("CONSTRUCTOR [%s]: Uploaded %d files to %s", connector_type, count, resource_name)
    assert count > 0, "Expected at least 1 file in sample data"
    state["uploaded_count"] = count

    objects = storage.list_objects(resource_name)
    picked_files = [k for k in objects if not k.endswith("/")][:2]
    assert len(picked_files) >= 1, "No file objects after upload"

    state["rename_source_key"] = picked_files[0]
    state["rename_source_name"] = Path(picked_files[0]).name
    state["move_source_key"] = state["rename_source_key"]
    state["move_source_name"] = state["rename_source_name"]
    update_key = picked_files[1] if len(picked_files) >= 2 else picked_files[0]
    state["update_target_key"] = update_key
    state["update_target_name"] = Path(update_key).name

    instance = pipeshub_client.create_connector(
        connector_type=connector_type,
        instance_name=connector_name,
        scope=scope,
        config=connector_config,
        auth_type=auth_type,
    )
    assert instance.connector_id, "Connector must have a valid ID"
    connector_id = instance.connector_id
    state["connector_id"] = connector_id
    logger.info("CONSTRUCTOR [%s]: Connector created: %s", connector_type, connector_id)

    pipeshub_client.toggle_sync(connector_id, enable=True)
    logger.info("CONSTRUCTOR [%s]: Sync enabled — waiting for full sync (connector %s)", connector_type, connector_id)

    uploaded = state["uploaded_count"]

    async def _check_full_sync() -> bool:
        return await graph_provider.count_records(connector_id) >= uploaded

    await wait_until_graph_condition(
        connector_id,
        check=_check_full_sync,
        timeout=180,
        poll_interval=10,
        description="full sync",
    )

    full_count = await graph_provider.count_records(connector_id)
    state["full_sync_count"] = full_count
    logger.info("CONSTRUCTOR [%s]: Full sync complete — %d records (connector %s)", connector_type, full_count, connector_id)

    return state


async def destructor(
    storage: object,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    state: Dict[str, Any],
    *,
    connector_type: str,
    cleanup_timeout: int = 300,
) -> None:
    """Disable connector, delete + graph cleanup, clear storage content."""
    connector_id = state["connector_id"]
    resource_name = state["resource_name"]

    logger.info("DESTRUCTOR [%s]: Disabling connector %s", connector_type, connector_id)
    try:
        pipeshub_client.toggle_sync(connector_id, enable=False)
        status = pipeshub_client.get_connector_status(connector_id)
        assert not status.get("isActive"), "Connector should be inactive after disable"
    except _CONNECTOR_API_TEARDOWN_ERRORS:
        logger.exception("DESTRUCTOR [%s]: Failed to disable connector %s", connector_type, connector_id)

    logger.info("DESTRUCTOR [%s]: Deleting connector %s", connector_type, connector_id)
    try:
        pipeshub_client.delete_connector(connector_id)
        pipeshub_client.wait(25)
        cleanup_s = int(
            os.getenv("INTEGRATION_GRAPH_CLEANUP_TIMEOUT", str(cleanup_timeout))
        )
        await graph_provider.assert_all_records_cleaned(connector_id, timeout=cleanup_s)
        logger.info("DESTRUCTOR [%s]: Graph cleaned for connector %s", connector_type, connector_id)
    except _CONNECTOR_DELETE_TEARDOWN_ERRORS:
        logger.exception("DESTRUCTOR [%s]: Failed to delete/clean connector %s", connector_type, connector_id)

    logger.info("DESTRUCTOR [%s]: Clearing content in %s", connector_type, resource_name)
    try:
        storage.clear_objects(resource_name)
        logger.info("DESTRUCTOR [%s]: Content cleared in %s", connector_type, resource_name)
    except STORAGE_CLEAR_ERRORS:
        logger.exception("DESTRUCTOR [%s]: Failed to clear content in %s", connector_type, resource_name)
