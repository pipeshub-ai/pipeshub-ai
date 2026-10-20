# pyright: ignore-file

"""
S3 Connector – Bucket Names filter integration test
=====================================================

  TC-ACCESS-REVOKE-001 — A bucket taken out of the Bucket Names filter leaves the index:
                         its records and record group go, the other bucket's stay.

Needs a second pre-provisioned bucket the suite's S3 key can read and write, named
in ``S3_SECOND_TEST_BUCKET``; tests never create buckets. Without it the module skips.
The connector has no fixed bucket (``auth.bucket``), since a fixed bucket ignores
the Bucket Names filter.
"""

import logging
import os
import sys
import uuid
from pathlib import Path
from typing import Any, AsyncGenerator, Dict

import pytest
import pytest_asyncio

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from connector_lifecycle import (  # noqa: E402
    RESOURCE_NAME,
    create_connector_and_await_sync,
    destructor,
    ensure_resource_exists,
)
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]  # noqa: E402
from helper.graph_provider import GraphProviderProtocol  # noqa: E402
from helper.graph_provider_utils import apply_filter_full_sync, wait_until_graph_condition  # noqa: E402
from helper.run_folder import folder_filter, new_run_folder  # noqa: E402
from connectors.s3.conftest import s3_connector_config  # noqa: E402
from connectors.s3.s3_storage_helper import S3StorageHelper  # type: ignore[import-not-found]  # noqa: E402

logger = logging.getLogger("s3-bucket-filter-test")

SECOND_BUCKET_ENV = "S3_SECOND_TEST_BUCKET"


def _filters(folder: str, buckets: list[str]) -> dict:
    filters = folder_filter(folder)
    filters["sync"]["values"]["buckets"] = {"operator": "in", "type": "multiselect", "value": buckets}
    return filters


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def two_bucket_connector(
    s3_storage: S3StorageHelper,
    pipeshub_client: PipeshubClient,
    graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[Dict[str, Any], None]:
    second = os.getenv(SECOND_BUCKET_ENV)
    if not second:
        pytest.skip(f"{SECOND_BUCKET_ENV} is not set: TC-ACCESS-REVOKE-001 needs a second pre-provisioned bucket")
    ensure_resource_exists(s3_storage, RESOURCE_NAME)
    ensure_resource_exists(s3_storage, second)

    folder = new_run_folder()
    token = uuid.uuid4().hex[:8]
    keys = {RESOURCE_NAME: f"{folder}revoke-kept-{token}.txt", second: f"{folder}revoke-dropped-{token}.txt"}
    state: Dict[str, Any] = {"resource_name": RESOURCE_NAME, "folder": folder, "second_bucket": second, "keys": keys}
    try:
        for bucket, key in keys.items():
            s3_storage.upload_object(bucket, key, f"{bucket} {token}".encode(), content_type="text/plain")

        config = s3_connector_config()
        config["auth"].pop("bucket", None)
        await create_connector_and_await_sync(
            pipeshub_client,
            graph_provider,
            state,
            connector_type="S3",
            connector_name=f"s3-bucket-filter-test-{token}",
            connector_config={**config, "filters": _filters(folder, [RESOURCE_NAME, second])},
            expected_records=len(keys),
        )
        yield state
    finally:
        if "connector_id" in state:
            await destructor(s3_storage, pipeshub_client, graph_provider, state, connector_type="S3")
        else:
            s3_storage.clear_objects(RESOURCE_NAME, folder)
        s3_storage.clear_objects(second, folder)


@pytest.mark.integration
@pytest.mark.s3
@pytest.mark.asyncio(loop_scope="session")
class TestS3BucketFilter:
    async def test_tc_access_revoke_001_a_deselected_bucket_leaves_the_index(
        self,
        two_bucket_connector: Dict[str, Any],
        pipeshub_client: PipeshubClient,
        graph_provider: GraphProviderProtocol,
    ) -> None:
        state = two_bucket_connector
        connector_id, second = state["connector_id"], state["second_bucket"]
        kept_name = Path(state["keys"][RESOURCE_NAME]).name
        dropped_name = Path(state["keys"][second]).name

        async def _both_synced() -> bool:
            return await graph_provider.record_paths_or_names_contain(connector_id, [kept_name, dropped_name])

        await wait_until_graph_condition(
            connector_id, check=_both_synced, timeout=180, poll_interval=10, description="both buckets synced",
        )
        assert {RESOURCE_NAME, second} <= set(await graph_provider.fetch_record_group_names(connector_id))

        await apply_filter_full_sync(pipeshub_client, graph_provider, connector_id, _filters(state["folder"], [RESOURCE_NAME]))

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
        assert RESOURCE_NAME in await graph_provider.fetch_record_group_names(connector_id)
        logger.info("TC-ACCESS-REVOKE-001 passed: %s removed, %s kept (connector %s)", second, RESOURCE_NAME, connector_id)
