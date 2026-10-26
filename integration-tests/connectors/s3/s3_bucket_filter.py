# pyright: ignore-file

"""TC-ACCESS-REVOKE-001, shared by the S3-compatible suites (S3, MinIO).

A bucket taken out of the Bucket Names filter leaves the index: its records and
record group go, the other bucket's stay. Both buckets are pre-provisioned; the
connector has no fixed bucket (``auth.bucket``), since a fixed bucket ignores the
Bucket Names filter.
"""

import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncIterator, Dict

from connector_lifecycle import create_connector_and_await_sync, destructor, ensure_resource_exists
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]
from helper.graph_provider import GraphProviderProtocol
from helper.graph_provider_utils import apply_filter_full_sync, wait_until_graph_condition
from helper.run_folder import folder_filter, new_run_folder
from connectors.s3.s3_storage_helper import S3StorageHelper

logger = logging.getLogger("s3-bucket-filter-test")


def bucket_filters(folder: str, buckets: list[str]) -> dict:
    filters = folder_filter(folder)
    filters["sync"]["values"]["buckets"] = {"operator": "in", "type": "multiselect", "value": buckets}
    return filters


@asynccontextmanager
async def two_bucket_connector(
    storage: S3StorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
    *,
    connector_type: str,
    config: dict[str, Any],
    kept: str,
    second: str,
) -> AsyncIterator[Dict[str, Any]]:
    """A connector syncing one object in each of ``kept`` and ``second``."""
    ensure_resource_exists(storage, kept)
    ensure_resource_exists(storage, second)

    folder = new_run_folder()
    token = uuid.uuid4().hex[:8]
    keys = {kept: f"{folder}revoke-kept-{token}.txt", second: f"{folder}revoke-dropped-{token}.txt"}
    state: Dict[str, Any] = {"resource_name": kept, "folder": folder, "second_bucket": second, "keys": keys}
    try:
        for bucket, key in keys.items():
            storage.upload_object(bucket, key, f"{bucket} {token}".encode(), content_type="text/plain")

        config["auth"].pop("bucket", None)
        await create_connector_and_await_sync(
            pipeshub_client,
            graph_provider,
            state,
            connector_type=connector_type,
            connector_name=f"{connector_type.lower()}-bucket-filter-test-{token}",
            connector_config={**config, "filters": bucket_filters(folder, [kept, second])},
            expected_records=len(keys),
        )
        yield state
    finally:
        if "connector_id" in state:
            await destructor(storage, pipeshub_client, graph_provider, state, connector_type=connector_type)
        else:
            storage.clear_objects(kept, folder)
        storage.clear_objects(second, folder)


async def assert_deselected_bucket_leaves_the_index(
    state: Dict[str, Any], pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
) -> None:
    connector_id, kept, second = state["connector_id"], state["resource_name"], state["second_bucket"]
    kept_name = Path(state["keys"][kept]).name
    dropped_name = Path(state["keys"][second]).name

    async def _both_synced() -> bool:
        return await graph_provider.record_paths_or_names_contain(connector_id, [kept_name, dropped_name])

    await wait_until_graph_condition(
        connector_id, check=_both_synced, timeout=180, poll_interval=10, description="both buckets synced",
    )
    assert {kept, second} <= set(await graph_provider.fetch_record_group_names(connector_id))

    await apply_filter_full_sync(pipeshub_client, graph_provider, connector_id, bucket_filters(state["folder"], [kept]))

    async def _second_gone() -> bool:
        return (
            await graph_provider.get_record_by_name(connector_id, dropped_name) is None
            and second not in await graph_provider.fetch_record_group_names(connector_id)
        )

    await wait_until_graph_condition(
        connector_id, check=_second_gone, timeout=180, poll_interval=10, description="de-selected bucket removed",
    )
    await graph_provider.assert_record_not_exists(connector_id, dropped_name)
    assert await graph_provider.get_record_by_name(connector_id, kept_name) is not None
    assert kept in await graph_provider.fetch_record_group_names(connector_id)
    logger.info("TC-ACCESS-REVOKE-001 passed: %s removed, %s kept (connector %s)", second, kept, connector_id)
