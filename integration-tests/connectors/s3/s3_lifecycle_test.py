# pyright: ignore-file

"""S3 connector lifecycle in PipesHub, on real AWS with the suite's read-only key.

Writes nothing to S3: the connector is pointed at one bucket the key can read.

  TC-LIFE-001  Disable stops syncing and keeps the data; re-enable resumes from the saved checkpoint
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
    """A team S3 connector on the largest readable bucket (so a full sync runs long enough to watch), after its first sync."""
    readable, _ = _bucket_access(s3_storage)
    files = {b: [k for k in keys if not k.endswith("/")] for b, keys in readable.items()}
    files = {b: keys for b, keys in files.items() if keys}
    if not files:
        pytest.skip("needs a key that can read at least one bucket with files")
    bucket = max(files, key=lambda b: len(files[b]))
    modified = {
        o["Key"]: o["LastModified"]
        for page in s3_storage._client.get_paginator("list_objects_v2").paginate(Bucket=bucket)
        for o in page.get("Contents") or []
    }
    config = {
        "auth": {"accessKey": s3_storage_key("S3_ACCESS_KEY"), "secretKey": s3_storage_key("S3_SECRET_KEY")},
        "filters": {"sync": {"values": {"buckets": {"operator": "in", "value": [bucket], "type": "multiselect"}}}},
    }
    started = int(time.time() * 1000)
    connector = pipeshub_client.create_connector("S3", f"s3-life-{uuid.uuid4().hex[:8]}", scope="team", config=config)
    state: dict[str, Any] = {
        "connector_id": connector.connector_id, "bucket": bucket, "files": files[bucket], "modified": modified,
    }
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


async def _settled_update_times(graph_provider: GraphProviderProtocol, cid: str, synced: dict[str, Any]) -> dict[str, int]:
    """Each file record's last update time, once no file is still waiting for or in indexing."""
    deadline = time.monotonic() + 900
    while True:
        records = {k: await graph_provider.get_record_by_external_id(cid, f"{synced['bucket']}/{k}") for k in synced["files"]}
        busy = [k for k, r in records.items() if r is None or r.indexing_status in ("NOT_STARTED", "QUEUED", "IN_PROGRESS")]
        if not busy:
            return {k: r.updated_at for k, r in records.items()}
        assert time.monotonic() < deadline, f"indexing never settled: {busy[:5]}"
        time.sleep(5)


@pytest.mark.integration
@pytest.mark.s3
@pytest.mark.asyncio(loop_scope="session")
class TestS3Lifecycle:
    @pytest.mark.order(1)
    async def test_tc_life_001_disable_keeps_data_and_re_enable_resumes(
        self, synced, pipeshub_client: PipeshubClient, graph_provider: GraphProviderProtocol,
    ) -> None:
        cid = synced["connector_id"]
        # Indexing writes to records too; only once it is done does a changed timestamp mean the sync re-sent one.
        before = await _settled_update_times(graph_provider, cid, synced)
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
        after = {key: await graph_provider.get_record_by_external_id(cid, f"{synced['bucket']}/{key}") for key in before}
        assert all(r is not None and r.version == 0 for r in after.values())
        # Resumed from the saved checkpoint: objects older than it are not re-sent. Objects modified in
        # the checkpoint's own second are, by design (LastModified has one-second precision).
        newest = max(synced["modified"][k] for k in before)
        older = [k for k in before if synced["modified"][k] < newest]
        if not older:
            pytest.skip("every object of the bucket has the same LastModified second")
        resent = [k for k in older if after[k].updated_at != before[k]]
        assert not resent, f"re-enable re-sent objects older than the checkpoint: {resent}"

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
        agent_key = None
        try:
            created = agents.create_agent(
                name=name, description="Uses the S3 connector as knowledge.", systemPrompt="Answer from the files.",
                knowledge=[{"connectorId": cid, "filters": {}}],
            )
            assert created.status_code in (200, 201), f"creating the agent: {created.status_code} {created.text[:300]}"
            agent_key = created.json()["agent"]["_key"]
            resp = pipeshub_client.request("DELETE", f"/api/v1/connectors/{cid}")
            assert resp.status_code == 409, f"delete must be refused while an agent uses it; got {resp.status_code}"
            assert name in resp.text, f"the refusal must name the agent; body: {resp.text[:300]}"
            assert pipeshub_client.get_connector(cid).get("status") != "DELETING"
        finally:
            if agent_key:
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
        # The resync is only queued; delete once the sync is seen running.
        deadline = time.monotonic() + 60
        while pipeshub_client.get_connector(cid).get("status") not in ("FULL_SYNCING", "SYNCING"):
            assert time.monotonic() < deadline, "the full sync was never seen running"
            time.sleep(0.2)

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
