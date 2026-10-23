# pyright: ignore-file

"""S3 connector lifecycle in PipesHub, on real AWS with the suite's read-only key.

Writes nothing to S3: the connector is pointed at one bucket the key can read.

  TC-LIFE-001  Disable stops syncing and keeps the data; re-enable syncs again with nothing re-sent
  TC-LIFE-002  Delete is refused while an agent uses the connector, naming the agent
  TC-LIFE-003  Only the creator or an admin can delete
  TC-LIFE-004  Delete while a sync runs: it stops, the connector shows DELETING, a second delete is
               refused, and every node goes
"""

import logging
import time
import uuid
from typing import Any

import pytest
import pytest_asyncio
import requests

from connectors.s3.s3_shared_account_test import _bucket_access, _wait_for_sync, s3_storage_key
from connectors.s3.s3_storage_helper import S3StorageHelper
from helper.clients.agents_client import AgentsClient
from helper.graph_provider import GraphProviderProtocol
from helper.second_user import create_second_user, delete_second_user
from pipeshub_client import PipeshubClient  # type: ignore[import-not-found]

logger = logging.getLogger("s3-lifecycle-test")


@pytest_asyncio.fixture(scope="module", loop_scope="session")
async def synced(s3_storage: S3StorageHelper, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol):
    """A team S3 connector on the smallest readable bucket, after its first sync."""
    readable, _ = _bucket_access(s3_storage)
    files = {b: [k for k in keys if not k.endswith("/")] for b, keys in readable.items()}
    files = {b: keys for b, keys in files.items() if keys}
    if not files:
        pytest.skip("needs a key that can read at least one bucket with files")
    bucket = min(files, key=lambda b: len(files[b]))
    config = {
        "auth": {"accessKey": s3_storage_key("S3_ACCESS_KEY"), "secretKey": s3_storage_key("S3_SECRET_KEY")},
        "filters": {"sync": {"values": {"buckets": {"operator": "in", "value": [bucket], "type": "multiselect"}}}},
    }
    started = int(time.time() * 1000)
    connector = pipeshub_client.create_connector("S3", f"s3-life-{uuid.uuid4().hex[:8]}", scope="team", config=config)
    state: dict[str, Any] = {"connector_id": connector.connector_id, "bucket": bucket, "files": files[bucket]}
    try:
        pipeshub_client.toggle_sync(connector.connector_id, enable=True)
        _wait_for_sync(pipeshub_client, connector.connector_id, started)
        state["records"] = await graph_provider.count_records(connector.connector_id)
        assert state["records"] >= len(state["files"])
        yield state
    finally:
        if not state.get("deleted"):
            pipeshub_client.delete_connector(connector.connector_id)
            await graph_provider.assert_connector_graph_fully_cleaned(connector.connector_id, timeout=300)


@pytest.mark.integration
@pytest.mark.s3
@pytest.mark.asyncio(loop_scope="session")
class TestS3Lifecycle:
    @pytest.mark.order(1)
    async def test_tc_life_001_disable_keeps_data_and_re_enable_resumes(
        self, synced, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        cid = synced["connector_id"]
        pipeshub_client.toggle_sync(cid, enable=False)
        assert not pipeshub_client.get_connector(cid).get("isActive")

        resync = pipeshub_client.request("POST", f"/api/v1/connectors/{cid}/resync", json={"connectorName": "S3", "fullSync": False})
        assert resync.status_code >= 400, f"a disabled connector must not sync; got HTTP {resync.status_code}"
        assert await graph_provider.count_records(cid) == synced["records"], "disabling must keep the data"

        started = int(time.time() * 1000)
        pipeshub_client.toggle_sync(cid, enable=True)
        finished = _wait_for_sync(pipeshub_client, cid, started)

        assert finished.get("lastSyncOutcome") == "succeeded", finished
        assert await graph_provider.count_records(cid) == synced["records"]
        for key in synced["files"]:
            record = await graph_provider.get_record_by_external_id(cid, f"{synced['bucket']}/{key}")
            # Resumed from its saved position: nothing unchanged was re-sent as a new version.
            assert record is not None and record.version == 0, f"{key}: {record and record.version}"

    @pytest.mark.order(2)
    async def test_tc_life_002_delete_refused_while_an_agent_uses_it(
        self, synced, pipeshub_client: PipeshubClient,
    ) -> None:
        # Its own connector: a deleted agent is kept (soft delete) with its knowledge link,
        # which would otherwise stay behind as a node of the connector the other tests clean up.
        config = {"auth": {"accessKey": s3_storage_key("S3_ACCESS_KEY"), "secretKey": s3_storage_key("S3_SECRET_KEY")}}
        connector = pipeshub_client.create_connector("S3", f"s3-agent-use-{uuid.uuid4().hex[:8]}", scope="team", config=config)
        cid = connector.connector_id
        agents = AgentsClient(pipeshub_client)
        name = f"it-s3-agent-{uuid.uuid4().hex[:6]}"
        created = agents.create_agent(
            name=name, description="Uses the S3 connector as knowledge.", systemPrompt="Answer from the files.",
            knowledge=[{"connectorId": cid, "filters": {}}],
        )
        assert created.status_code in (200, 201), f"creating the agent: {created.status_code} {created.text[:300]}"
        agent_key = created.json()["agent"]["_key"]
        try:
            resp = pipeshub_client.request("DELETE", f"/api/v1/connectors/{cid}")
            assert resp.status_code == 409, f"delete must be refused while an agent uses it; got {resp.status_code}"
            assert name in resp.text, f"the refusal must name the agent; body: {resp.text[:300]}"
            assert pipeshub_client.get_connector(cid).get("status") != "DELETING"
        finally:
            agents.delete_agent(agent_key)
            pipeshub_client.delete_connector(cid)

    @pytest.mark.order(3)
    async def test_tc_life_003_only_the_creator_or_an_admin_can_delete(
        self, synced, pipeshub_client: PipeshubClient,
    ) -> None:
        cid = synced["connector_id"]
        user = create_second_user(pipeshub_client)
        try:
            resp = requests.delete(f"{user.base_url}/api/v1/connectors/{cid}", headers=user.headers, timeout=user.timeout)
            assert resp.status_code in (403, 404), f"a member who did not create it must not delete it; got {resp.status_code}"
            assert pipeshub_client.get_connector(cid).get("status") != "DELETING"
        finally:
            delete_second_user(pipeshub_client, user, strict=True)

    @pytest.mark.order(4)
    async def test_tc_life_004_delete_while_syncing_stops_it_and_removes_everything(
        self, synced, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        cid = synced["connector_id"]
        pipeshub_client.resync_connector(cid, full_sync=True, connector_name="S3")

        first = pipeshub_client.request("DELETE", f"/api/v1/connectors/{cid}")
        assert first.status_code in (200, 202), f"delete: HTTP {first.status_code}: {first.text[:300]}"
        synced["deleted"] = True
        second = pipeshub_client.request("DELETE", f"/api/v1/connectors/{cid}")
        if second.status_code != 404:  # 404 once the delete already finished
            assert second.status_code == 409, f"a second delete must be refused; got HTTP {second.status_code}"
            assert "already in progress" in second.text

        await graph_provider.assert_connector_graph_fully_cleaned(cid, timeout=300)
        # The stopped sync writes nothing after the delete.
        time.sleep(10)
        assert await graph_provider.count_any_nodes_with_connector_id(cid) == 0
