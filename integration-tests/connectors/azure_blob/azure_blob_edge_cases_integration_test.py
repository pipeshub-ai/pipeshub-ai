# pyright: ignore-file

"""
Azure Blob Connector – edge-case integration tests (PP-314 review, PP-340)
=========================================================================

Each test makes its own connector, so the cases do not depend on each other:

  TC-SPECIAL-NAMES   — names with '#', '?' and '%25' sync, download byte-for-byte and index
  TC-REINDEX-STABLE  — reindexing an unchanged blob keeps its revision and version
  TC-CONTAINER-DELETE — a container deleted in Azure leaves the graph on the next sync
  TC-TEAM-SCOPE      — a team connector syncs with or without a container named

An ADLS Gen2 (hierarchical namespace) run needs a live HNS account; the unit tests cover
its directory blobs (tests/unit/.../test_azure_blob_sync_behaviour.py).
"""

import contextlib
import logging
import sys
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connector_lifecycle import RESOURCE_NAME, create_connector_and_await_sync  # noqa: E402
from connectors.azure_blob.azure_blob_storage_helper import AzureBlobStorageHelper  # noqa: E402
from connectors.azure_blob.conftest import azure_blob_connector_config  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import (  # noqa: E402
    wait_for_sync_completion,
    wait_until_graph_condition,
)
from helper.indexing_wait import wait_until_record_indexing_completed  # noqa: E402
from helper.run_folder import folder_filter, new_run_folder  # noqa: E402
from helper.storage_incremental import restart_sync  # noqa: E402
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402

logger = logging.getLogger("azure-blob-edge-cases-test")

SPECIAL_NAMES = ["Invoice #1.txt", "a?b.txt", "100%25.txt"]


def containers_filter(names: list[str]) -> dict:
    return {"sync": {"values": {"containers": {"value": names, "operator": "in", "type": "multiselect"}}}}


@asynccontextmanager
async def azure_connector(
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    *,
    filters: dict,
    expected_records: int,
    scope: str = "personal",
) -> AsyncIterator[dict[str, Any]]:
    """A synced connector, deleted afterwards."""
    state: dict[str, Any] = {}
    try:
        await create_connector_and_await_sync(
            pipeshub_client,
            graph_provider,
            state,
            connector_type="Azure Blob",
            connector_name=f"azure-blob-edge-{uuid.uuid4().hex[:8]}",
            connector_config={**azure_blob_connector_config(), "filters": filters},
            expected_records=expected_records,
            scope=scope,
        )
        yield state
    finally:
        if state.get("connector_id"):
            pipeshub_client.toggle_sync(state["connector_id"], enable=False)
            pipeshub_client.delete_connector(state["connector_id"])


@pytest.mark.integration
@pytest.mark.azure_blob
@pytest.mark.asyncio(loop_scope="session")
class TestAzureBlobEdgeCases:

    async def test_tc_special_names_download_and_index(
        self,
        azure_blob_storage: AzureBlobStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-SPECIAL-NAMES: '#' and '?' used to cut the SAS URL short and '%25' was decoded to '%'."""
        folder = new_run_folder()
        bodies = {f"{folder}{name}": f"pp314 special name {i}\n".encode() for i, name in enumerate(SPECIAL_NAMES)}
        for key, body in bodies.items():
            azure_blob_storage.upload_blob(RESOURCE_NAME, key, body, content_type="text/plain")
        try:
            async with azure_connector(
                pipeshub_client, graph_provider, filters=folder_filter(folder), expected_records=len(bodies),
            ) as state:
                connector_id = state["connector_id"]
                for key, body in bodies.items():
                    record = await graph_provider.get_record_by_external_id(connector_id, f"{RESOURCE_NAME}/{key}")
                    assert record is not None, f"no record for {key!r}"
                    response = pipeshub_client.stream_record(record.id)
                    assert response.status_code == 200, f"{key!r}: HTTP {response.status_code}"
                    assert response.content == body, f"{key!r}: downloaded bytes differ"
                    await wait_until_record_indexing_completed(
                        graph_provider, connector_id, f"{RESOURCE_NAME}/{key}", pipeshub_client=pipeshub_client,
                    )
        finally:
            azure_blob_storage.clear_objects(RESOURCE_NAME, folder)

    async def test_tc_reindex_stable_revision(
        self,
        azure_blob_storage: AzureBlobStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-REINDEX-STABLE: reindex used to compare the etag with the stored Content-MD5 and rewrite it."""
        folder = new_run_folder()
        key = f"{folder}stable.txt"
        external_id = f"{RESOURCE_NAME}/{key}"
        azure_blob_storage.upload_blob(RESOURCE_NAME, key, b"pp314 reindex stays put\n", content_type="text/plain")
        try:
            async with azure_connector(
                pipeshub_client, graph_provider, filters=folder_filter(folder), expected_records=1,
            ) as state:
                connector_id = state["connector_id"]
                before = await wait_until_record_indexing_completed(
                    graph_provider, connector_id, external_id, pipeshub_client=pipeshub_client,
                )
                assert before.external_revision_id, "sync stores the blob's Content-MD5"

                pipeshub_client.reindex_record(before.id)
                await wait_until_record_indexing_completed(graph_provider, connector_id, external_id)

                after = await graph_provider.get_record_by_external_id(connector_id, external_id)
                assert after.external_revision_id == before.external_revision_id
                assert after.version == before.version
                assert after.md5_hash == before.md5_hash
        finally:
            azure_blob_storage.clear_objects(RESOURCE_NAME, folder)

    async def test_tc_container_delete_removes_its_records(
        self,
        azure_blob_storage: AzureBlobStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-CONTAINER-DELETE: a container named in the filter and then deleted in Azure leaves the graph."""
        # This test's own container: deleting it is the case under test.
        container = f"pp314-it-gone-{uuid.uuid4().hex[:8]}"
        service = azure_blob_storage._service
        service.create_container(container)
        try:
            azure_blob_storage.upload_blob(container, "old-data.txt", b"pp314 deleted container\n", content_type="text/plain")
            async with azure_connector(
                pipeshub_client, graph_provider, filters=containers_filter([container]), expected_records=1,
            ) as state:
                connector_id = state["connector_id"]
                assert await graph_provider.get_record_group_by_external_id(connector_id, container) is not None

                service.delete_container(container)
                restart_sync(pipeshub_client, connector_id)
                await wait_for_sync_completion(pipeshub_client, graph_provider, connector_id)

                async def gone() -> bool:
                    return (
                        await graph_provider.count_records(connector_id) == 0
                        and await graph_provider.get_record_group_by_external_id(connector_id, container) is None
                    )
                await wait_until_graph_condition(
                    connector_id, check=gone, timeout=120, poll_interval=5, description="deleted container removed",
                )
        finally:
            # Normally already deleted by the test.
            with contextlib.suppress(Exception):
                service.delete_container(container)

    async def test_tc_team_scope_syncs_with_or_without_named_containers(
        self,
        azure_blob_storage: AzureBlobStorageHelper,
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        """TC-TEAM-SCOPE: a team connector with an empty Container Names filter syncs every container."""
        folder = new_run_folder()
        key = f"{folder}team.txt"
        azure_blob_storage.upload_blob(RESOURCE_NAME, key, b"pp314 team scope\n", content_type="text/plain")
        try:
            async with azure_connector(
                pipeshub_client, graph_provider, filters=folder_filter(folder), expected_records=1, scope="team",
            ) as state:
                assert await graph_provider.get_record_by_external_id(state["connector_id"], f"{RESOURCE_NAME}/{key}")

            filters = folder_filter(folder)
            filters["sync"]["values"].update(containers_filter([RESOURCE_NAME])["sync"]["values"])
            async with azure_connector(
                pipeshub_client, graph_provider, filters=filters, expected_records=1, scope="team",
            ) as state:
                assert await graph_provider.get_record_by_external_id(state["connector_id"], f"{RESOURCE_NAME}/{key}")
        finally:
            azure_blob_storage.clear_objects(RESOURCE_NAME, folder)
