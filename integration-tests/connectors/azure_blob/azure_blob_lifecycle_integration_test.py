# pyright: ignore-file

"""
Azure Blob Connector – credentials and lifecycle integration tests
==================================================================

Each test makes its own connector in its own run folder:

  TC-WRONG-KEY     — enabling with a wrong account key is refused with Azure's reason, and nothing secret
  TC-EXPIRED-SAS   — a SAS that expires after the first sync is reported as rejected credentials; nothing is removed
  TC-RECREATE      — deleting the connector and adding the same source again is a clean full sync, no duplicates
  TC-DISABLE       — disabling keeps the data; enabling again picks up what changed meanwhile
  TC-TEAM-CLEARED  — clearing a team connector's Container Names keeps syncing; nothing is dropped
"""

import asyncio
import base64
import logging
import sys
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from azure.storage.blob import (
    AccountSasPermissions,
    ResourceTypes,
    generate_account_sas,
)

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connector_lifecycle import RESOURCE_NAME, create_connector_and_await_sync
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

from connectors.azure_blob.azure_blob_storage_helper import AzureBlobStorageHelper
from connectors.azure_blob.conftest import azure_blob_connector_config
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import (
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.run_folder import folder_filter, new_run_folder

logger = logging.getLogger("azure-blob-credentials-test")

# What Azure's own error text holds on a failed signature, and must never reach a user or a log.
SECRET_MARKERS = ("MAC signature", "string to sign", "<?xml", "RequestId")


def wait_for_notification(client: PipeshubClient, title: str, timeout: float = 120) -> dict[str, Any] | None:
    """The user's newest notification with this exact title; notices go through the broker."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        resp = client.request("GET", "/api/v1/notifications", params={"limit": 50})
        if resp.status_code == 200:
            for n in resp.json().get("notifications", []):
                if n.get("title") == title:
                    return n
        time.sleep(3)
    return None


def connection_string(storage: AzureBlobStorageHelper) -> str:
    return azure_blob_connector_config()["auth"]["azureBlobConnectionString"]


def with_wrong_key(conn_str: str) -> str:
    wrong = base64.b64encode(b"not the account key, 32 bytes!!").decode()
    return ";".join(f"AccountKey={wrong}" if p.startswith("AccountKey=") else p for p in conn_str.split(";"))


def sas_connection_string(storage: AzureBlobStorageHelper, expiry: datetime) -> str:
    """A read and list SAS for the whole account, as an admin following the setup guidance would make."""
    service = storage._service
    token = generate_account_sas(
        account_name=service.account_name,
        account_key=service.credential.account_key,
        resource_types=ResourceTypes(service=True, container=True, object=True),
        permission=AccountSasPermissions(read=True, list=True),
        expiry=expiry,
    )
    return f"BlobEndpoint={service.primary_endpoint};SharedAccessSignature={token}"


def delete_quietly(client: PipeshubClient, connector_id: str) -> None:
    try:
        client.toggle_sync(connector_id, enable=False)
        client.delete_connector(connector_id)
    except Exception:
        logger.exception("connector %s cleanup leaked", connector_id)


@pytest.mark.integration
@pytest.mark.azure_blob
@pytest.mark.asyncio(loop_scope="session")
class TestAzureBlobLifecycle:

    async def test_tc_wrong_key_is_refused_with_the_reason(
        self,
        azure_blob_storage: AzureBlobStorageHelper,
        pipeshub_client: PipeshubClient,
    ) -> None:
        """TC-WRONG-KEY: the refusal used to say only "verify your credentials", and the notice held Azure's raw text."""
        name = f"azure-blob-wrong-key-{uuid.uuid4().hex[:8]}"
        config = {"auth": {"azureBlobConnectionString": with_wrong_key(connection_string(azure_blob_storage))}}
        connector = pipeshub_client.create_connector("Azure Blob", name, config=config)
        try:
            resp = pipeshub_client.request("POST", f"/api/v1/connectors/{connector.connector_id}/toggle", json={"type": "sync"})

            assert resp.status_code == 400, f"enable with a wrong key must be refused; got HTTP {resp.status_code}: {resp.text[:300]}"
            assert "HTTP 403 AuthenticationFailed" in resp.text, f"the refusal must give Azure's reason; body: {resp.text[:300]}"
            for marker in SECRET_MARKERS:
                assert marker not in resp.text, f"the refusal holds {marker!r}: {resp.text[:300]}"
            assert not pipeshub_client.get_connector(connector.connector_id).get("isActive")

            notice = wait_for_notification(pipeshub_client, f"{name}: Connection test failed")
            assert notice is not None, "no connection-test notice"
            assert "AuthenticationFailed" in notice["message"]
            for marker in SECRET_MARKERS:
                assert marker not in notice["message"], f"the notice holds {marker!r}"
        finally:
            delete_quietly(pipeshub_client, connector.connector_id)

    async def test_tc_expired_sas_reports_rejected_credentials_and_keeps_records(
        self,
        azure_blob_storage: AzureBlobStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-EXPIRED-SAS: a static credential has no refresh; a sync it fails names the cause and deletes nothing."""
        folder = new_run_folder()
        key = f"{folder}kept.txt"
        azure_blob_storage.upload_blob(RESOURCE_NAME, key, b"expired sas keeps this\n", content_type="text/plain")
        expiry = datetime.now(UTC) + timedelta(minutes=3)
        name = f"azure-blob-sas-{uuid.uuid4().hex[:8]}"
        # Named, so the refusal comes from listing the container's blobs, not from listing containers.
        filters = folder_filter(folder)
        filters["sync"]["values"]["containers"] = {"value": [RESOURCE_NAME], "operator": "in", "type": "multiselect"}
        state: dict[str, Any] = {}
        try:
            await create_connector_and_await_sync(
                pipeshub_client, graph_provider, state,
                connector_type="Azure Blob", connector_name=name,
                connector_config={
                    "auth": {"azureBlobConnectionString": sas_connection_string(azure_blob_storage, expiry)},
                    "filters": filters,
                },
                expected_records=1,
            )
            connector_id = state["connector_id"]
            external_id = f"{RESOURCE_NAME}/{key}"
            assert await graph_provider.get_record_by_external_id(connector_id, external_id) is not None

            wait_s = (expiry - datetime.now(UTC)).total_seconds() + 20
            if wait_s > 0:
                await asyncio.sleep(wait_s)
            # A resync, not a toggle: enabling runs the connection test, which would refuse it first.
            pipeshub_client.resync_connector(connector_id, full_sync=False)

            notice = wait_for_notification(pipeshub_client, f"{name}: Credentials rejected", timeout=180)
            assert notice is not None, "no credentials-rejected notice after the SAS expired"
            assert "AuthenticationFailed" in notice["message"] and RESOURCE_NAME in notice["message"]
            for marker in SECRET_MARKERS:
                assert marker not in notice["message"], f"the notice holds {marker!r}"
            assert await graph_provider.get_record_by_external_id(connector_id, external_id) is not None, (
                "a sync the credentials failed must not delete records"
            )
        finally:
            if state.get("connector_id"):
                delete_quietly(pipeshub_client, state["connector_id"])
            azure_blob_storage.clear_objects(RESOURCE_NAME, folder)

    async def test_tc_delete_and_recreate_is_a_clean_full_sync(
        self,
        azure_blob_storage: AzureBlobStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-RECREATE: the same source added again after a delete syncs in full, with nothing left of the old instance."""
        folder = new_run_folder()
        keys = [f"{folder}a.txt", f"{folder}sub/b.txt", f"{folder}sub/deeper/c.txt"]
        for k in keys:
            azure_blob_storage.upload_blob(RESOURCE_NAME, k, f"recreate {k}\n".encode(), content_type="text/plain")
        config = {**azure_blob_connector_config(), "filters": folder_filter(folder)}
        first: dict[str, Any] = {}
        second: dict[str, Any] = {}
        try:
            await create_connector_and_await_sync(
                pipeshub_client, graph_provider, first, connector_type="Azure Blob",
                connector_name=f"azure-blob-recreate-{uuid.uuid4().hex[:8]}", connector_config=config,
                expected_records=len(keys),
            )
            first_count = await graph_provider.count_records(first["connector_id"])

            pipeshub_client.toggle_sync(first["connector_id"], enable=False)
            pipeshub_client.delete_connector(first["connector_id"])
            await graph_provider.assert_connector_graph_fully_cleaned(first["connector_id"])

            await create_connector_and_await_sync(
                pipeshub_client, graph_provider, second, connector_type="Azure Blob",
                connector_name=f"azure-blob-recreate-{uuid.uuid4().hex[:8]}", connector_config=config,
                expected_records=len(keys),
            )
            connector_id = second["connector_id"]
            assert await graph_provider.count_records(connector_id) == first_count, "the second instance has a different record count"
            for k in keys:
                assert await graph_provider.get_record_by_external_id(connector_id, f"{RESOURCE_NAME}/{k}") is not None, k
            assert await graph_provider.count_records(first["connector_id"]) == 0
        finally:
            if second.get("connector_id"):
                delete_quietly(pipeshub_client, second["connector_id"])
            azure_blob_storage.clear_objects(RESOURCE_NAME, folder)

    async def test_tc_disable_keeps_data_and_enable_resumes(
        self,
        azure_blob_storage: AzureBlobStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-DISABLE: changes made while the connector is off arrive once it is on again."""
        folder = new_run_folder()
        kept, removed, added = f"{folder}kept.txt", f"{folder}removed.txt", f"{folder}added.txt"
        for k in (kept, removed):
            azure_blob_storage.upload_blob(RESOURCE_NAME, k, f"disable {k}\n".encode(), content_type="text/plain")
        state: dict[str, Any] = {}
        try:
            await create_connector_and_await_sync(
                pipeshub_client, graph_provider, state, connector_type="Azure Blob",
                connector_name=f"azure-blob-disable-{uuid.uuid4().hex[:8]}",
                connector_config={**azure_blob_connector_config(), "filters": folder_filter(folder)},
                expected_records=2,
            )
            connector_id = state["connector_id"]
            pipeshub_client.toggle_sync(connector_id, enable=False)
            count_while_off = await graph_provider.count_records(connector_id)

            azure_blob_storage.delete_blob(RESOURCE_NAME, removed)
            azure_blob_storage.upload_blob(RESOURCE_NAME, added, b"added while off\n", content_type="text/plain")
            pipeshub_client.wait(10)
            assert await graph_provider.count_records(connector_id) == count_while_off, "a disabled connector changed its data"
            assert await graph_provider.get_record_by_external_id(connector_id, f"{RESOURCE_NAME}/{removed}") is not None

            pipeshub_client.toggle_sync(connector_id, enable=True)
            await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)

            async def caught_up() -> bool:
                return (
                    await graph_provider.get_record_by_external_id(connector_id, f"{RESOURCE_NAME}/{added}") is not None
                    and await graph_provider.get_record_by_external_id(connector_id, f"{RESOURCE_NAME}/{removed}") is None
                )
            await wait_until_graph_condition(
                connector_id, check=caught_up, timeout=180, poll_interval=5, description="changes made while disabled synced",
            )
            assert await graph_provider.get_record_by_external_id(connector_id, f"{RESOURCE_NAME}/{kept}") is not None
            assert await graph_provider.count_records(connector_id) == count_while_off
        finally:
            if state.get("connector_id"):
                delete_quietly(pipeshub_client, state["connector_id"])
            azure_blob_storage.clear_objects(RESOURCE_NAME, folder)

    async def test_tc_team_selection_cleared_keeps_syncing(
        self,
        azure_blob_storage: AzureBlobStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-TEAM-CLEARED: with no container named the sync covers every container, so nothing is dropped."""
        folder = new_run_folder()
        key = f"{folder}shared.txt"
        azure_blob_storage.upload_blob(RESOURCE_NAME, key, b"team selection cleared\n", content_type="text/plain")
        named = folder_filter(folder)
        named["sync"]["values"]["containers"] = {"value": [RESOURCE_NAME], "operator": "in", "type": "multiselect"}
        state: dict[str, Any] = {}
        try:
            await create_connector_and_await_sync(
                pipeshub_client, graph_provider, state, connector_type="Azure Blob",
                connector_name=f"azure-blob-team-cleared-{uuid.uuid4().hex[:8]}",
                connector_config={**azure_blob_connector_config(), "filters": named},
                expected_records=1, scope="team",
            )
            connector_id = state["connector_id"]
            assert await graph_provider.get_record_by_external_id(connector_id, f"{RESOURCE_NAME}/{key}") is not None

            synced = await graph_provider.count_records(connector_id)

            pipeshub_client.update_connector_filters_sync_safe(connector_id, filters=folder_filter(folder))
            await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)

            assert await graph_provider.count_records(connector_id) == synced, (
                await graph_provider.fetch_record_paths(connector_id)
            )
            assert await graph_provider.get_record_by_external_id(connector_id, f"{RESOURCE_NAME}/{key}") is not None
            assert await graph_provider.get_record_group_by_external_id(connector_id, RESOURCE_NAME) is not None
        finally:
            if state.get("connector_id"):
                delete_quietly(pipeshub_client, state["connector_id"])
            azure_blob_storage.clear_objects(RESOURCE_NAME, folder)
