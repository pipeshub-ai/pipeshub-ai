# pyright: ignore-file

"""Who can find what a MinIO connector synced, and reindex, against the compose MinIO server.

MinIO has no per-object sharing PipesHub reads: a team connector's files are for the whole
org, a personal connector's for its creator. Every object this module writes is under its
own prefix, removed at teardown.

  TC-VIS-001  A personal connector's file is found by its creator and not by another member
  TC-VIS-002  A team connector's file is found by another member of the org
  TC-VIS-003  Reindex of an unchanged, indexed file queues it again and it is indexed again
"""

import logging
import time
import uuid
from collections.abc import AsyncGenerator
from typing import Any

import pytest
import pytest_asyncio
from connectors.minio.conftest import minio_bucket, minio_connector_config
from connectors.minio.minio_storage_helper import MinioStorageHelper
from helper.connector_visibility import found, search_connector_as, search_connector_as_admin, wait_until_searchable
from helper.graph_provider import GraphProviderProtocol
from helper.second_user import SecondUser, describe_search, has_no_access
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("minio-visibility-test")

FILE_NAME = "quarterly-harbour-report.txt"
# Words unlikely anywhere else in the index, so the search finds this file or nothing.
FILE_TEXT = (
    "The quarterly harbour report records that the lighthouse keeper counted "
    "four hundred and twelve cormorants nesting on the northern breakwater."
)
QUERY = "how many cormorants did the lighthouse keeper count on the breakwater"


async def _connector(
    scope: str, minio_storage: MinioStorageHelper, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
) -> AsyncGenerator[dict[str, Any], None]:
    bucket = minio_bucket()
    prefix = f"it-vis-{scope}-{uuid.uuid4().hex[:8]}"
    key = f"{prefix}/{FILE_NAME}"
    # Text of its own: indexing skips a file whose content another record already holds,
    # so a shared text would make the second connector's reindex a no-op.
    minio_storage._client.put_object(Bucket=bucket, Key=key, Body=f"{FILE_TEXT} Filed as {prefix}.".encode())
    config = minio_connector_config()
    config["filters"] = {
        "sync": {
            "values": {
                "buckets": {"operator": "in", "value": [bucket], "type": "multiselect"},
                "folder_paths": {"operator": "in", "value": [prefix], "type": "list"},
            }
        }
    }
    cid = None
    try:
        cid = pipeshub_client.create_connector(
            "MinIO", f"minio-vis-{scope}-{uuid.uuid4().hex[:6]}", scope=scope, config=config,
        ).connector_id
        pipeshub_client.toggle_sync(cid, enable=True)
        record = await wait_until_searchable(graph_provider, cid, FILE_NAME, timeout=600)
        yield {"connector_id": cid, "record": record, "bucket": bucket, "key": key}
    finally:
        try:
            if cid:
                pipeshub_client.delete_connector(cid)
                await graph_provider.assert_connector_graph_fully_cleaned(cid, timeout=300)
        finally:
            minio_storage._client.delete_object(Bucket=bucket, Key=key)


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def personal(minio_storage, pipeshub_client, graph_provider):
    async for state in _connector("personal", minio_storage, pipeshub_client, graph_provider):
        yield state


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def team(minio_storage, pipeshub_client, graph_provider):
    async for state in _connector("team", minio_storage, pipeshub_client, graph_provider):
        yield state


@pytest.mark.integration
@pytest.mark.minio
@pytest.mark.permissions
@pytest.mark.asyncio(loop_scope="session")
class TestMinioVisibility:
    @pytest.mark.order(1)
    async def test_tc_vis_001_a_personal_connector_stays_with_its_creator(
        self, personal, pipeshub_client: PipeshubClient, second_user: SecondUser,
    ) -> None:
        cid, virtual_id = personal["connector_id"], personal["record"]["virtualRecordId"]
        owner = search_connector_as_admin(pipeshub_client, cid, QUERY)
        assert found(owner, virtual_id), (
            "precondition: the creator could not find their own file, so the other member "
            f"finding nothing proves nothing. {describe_search(owner)}"
        )
        other = search_connector_as(second_user, cid, QUERY)
        assert has_no_access(other), f"another member found a file of someone's personal connector. {describe_search(other)}"

    @pytest.mark.order(2)
    async def test_tc_vis_002_a_team_connector_is_found_by_the_org(self, team, second_user: SecondUser) -> None:
        cid, virtual_id = team["connector_id"], team["record"]["virtualRecordId"]
        other = search_connector_as(second_user, cid, QUERY)
        assert found(other, virtual_id), f"a member of the org could not find a team connector's file. {describe_search(other)}"

    @pytest.mark.order(3)
    async def test_tc_vis_003_reindex_queues_an_unchanged_file_again(
        self, team, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        cid = team["connector_id"]
        record = await graph_provider.get_record_by_external_id(cid, f"{team['bucket']}/{team['key']}")
        assert record is not None and record.indexing_status == "COMPLETED"
        # queuedAtTimestamp is the platform's own clock for putting a record in line for indexing;
        # connectors never write it, so a newer one is the reindex.
        queued_before = (await graph_provider.get_record_by_name(cid, FILE_NAME) or {}).get("queuedAtTimestamp") or 0
        pipeshub_client.reindex_record(record.id)
        deadline = time.monotonic() + 600
        while True:
            node = await graph_provider.get_record_by_name(cid, FILE_NAME) or {}
            if (node.get("queuedAtTimestamp") or 0) > queued_before and node.get("indexingStatus") == "COMPLETED":
                break
            assert time.monotonic() < deadline, f"reindex never queued and finished: {node.get('indexingStatus')}, queued at {node.get('queuedAtTimestamp')}"
            time.sleep(2)
        again = await graph_provider.get_record_by_external_id(cid, f"{team['bucket']}/{team['key']}")
        assert again.version == record.version, "a reindex of unchanged content must not count as a new version"
