# pyright: ignore-file

"""
Jira Cloud on the simulator: lifecycle and reindex
==================================================

  TC-JCSIM-LIFE-001  Disable and enable: nothing is lost while off, the next run equals the source
  TC-JCSIM-LIFE-002  Deleting the connector leaves nothing in the graph
  TC-JCSIM-LIFE-003  Two connectors on one site: each equals the source, deleting one leaves the other whole
  TC-JCSIM-LIFE-004  A sync requested while one runs is refused (409), not queued
  TC-JCSIM-LIFE-005  Changes made while the connector is off are read when it is turned on
  TC-JCSIM-RIDX-001  Reindexing an issue, a file and a restricted comment changes nothing
  TC-JCSIM-RIDX-002  Reindexing an issue changed at the source
  TC-JCSIM-RIDX-003  Reindexing a record after an access change on its project
  TC-JCSIM-RIDX-004  Reindexing the record of a deleted issue: it must not come back
  TC-JCSIM-RIDX-005  Reindexing a file removed at the source
"""

import contextlib

import pytest
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    COMMENT_PREFIX,
    FILE_PREFIX,
    JiraCloudWorld,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    succeeded,
)

from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimLifecycle:

    async def test_tc_jcsim_life_001_disable_enable(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.disable()
        await w._wait_idle()
        await w.assert_holdings_kept(before, "while the connector is off")
        assert succeeded(await w.enable())
        await w.assert_matches_source("after the connector was turned on again")

    async def test_tc_jcsim_life_002_delete_connector_leaves_nothing(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        connector_id = w.connector_id
        w.disable()
        await w._wait_idle()
        w.client.delete_connector(connector_id)
        w.connector_id = None
        await w.graph.assert_connector_graph_fully_cleaned(connector_id)

    async def test_tc_jcsim_life_003_two_connectors_one_site(self, jira_cloud_world: JiraCloudWorld) -> None:
        first = jira_cloud_world
        assert succeeded(await first.start())
        second = JiraCloudWorld(first.sim, first.client, first.graph, first.people)
        try:
            assert succeeded(await second.start(reset=False))
            await first.assert_matches_source("the first connector, with a second on the same site")
            await second.assert_matches_source("the second connector")
            second.disable()
            await second._wait_idle()
            second.client.delete_connector(second.connector_id)
            second.connector_id = None
            await first.assert_matches_source("the first connector, after the second was deleted")
        finally:
            second.remove_connector()

    async def test_tc_jcsim_life_004_sync_requested_while_running(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.advance(issue_create=2, issue_edit=2)
        w.client.resync_connector(w.connector_id, full_sync=True, connector_name=w.connector_type)
        with pytest.raises(PipeshubClientError, match="409"):
            w.client.resync_connector(w.connector_id, full_sync=False, connector_name=w.connector_type)
        await w._wait_idle()
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a second sync was requested while the first ran")

    async def test_tc_jcsim_life_005_changes_while_off(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.disable()
        await w._wait_idle()
        w.sim.advance(issue_create=2, issue_edit=2, delete=1, comment_add=2)
        assert succeeded(await w.enable())
        await w.assert_matches_source("after changes made while the connector was off")


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimReindex:

    async def _pick(self, w: JiraCloudWorld) -> tuple[dict, dict]:
        records = {r["externalRecordId"]: r for r in await w.creator_records()}
        ticket = next(r for e, r in sorted(records.items()) if e.isdigit())
        file = next(r for e, r in sorted(records.items()) if e.startswith(FILE_PREFIX))
        return ticket, file

    async def test_tc_jcsim_ridx_001_reindex_changes_nothing(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        ticket, file = await self._pick(w)
        await w.wait_indexed(ticket["id"])
        before = await w.holdings()
        w.client.reindex_record(ticket["id"])
        w.client.reindex_record(file["id"])
        for comment in [r for r in await w.creator_records() if r["externalRecordId"].startswith(COMMENT_PREFIX)][:2]:
            w.client.reindex_record(comment["id"])
        await w.assert_holdings_kept(before, "after records were reindexed")
        await w.assert_matches_source("after records were reindexed")

    async def test_tc_jcsim_ridx_002_reindex_after_a_change(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        ticket, _ = await self._pick(w)
        await w.wait_indexed(ticket["id"])
        key = w.sim.expected()["ticket_index"][ticket["externalRecordId"]]["key"]
        w.mutate({"op": "issue", "issue": key, "summary": "Renamed at the source"})
        w.client.reindex_record(ticket["id"])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after an issue changed at the source and was reindexed")

    async def test_tc_jcsim_ridx_003_reindex_after_an_access_change(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        ticket, _ = await self._pick(w)
        await w.wait_indexed(ticket["id"])
        project = w.sim.expected()["ticket_index"][ticket["externalRecordId"]]["key"].rsplit("-", 1)[0]
        w.mutate({"op": "grant", "project": project, "holder": "anyone"})
        w.sim.switches(anonymous_access=True)
        w.client.reindex_record(ticket["id"])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a grant on the project of a reindexed record")

    async def test_tc_jcsim_ridx_004_reindex_a_deleted_issue(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        gone = str(w.sim.advance(delete=1)["changes"]["delete"][0]["issue_id"])
        record = await w.record_for(gone)
        assert record is not None
        with contextlib.suppress(PipeshubClientError):  # the product may refuse: either way it must not come back
            w.client.reindex_record(record["id"])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the record of a deleted issue was reindexed and the next sync ran")

    async def test_tc_jcsim_ridx_005_reindex_a_file_removed_at_the_source(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        _, file = await self._pick(w)
        w.sim.rest("DELETE", f"/attachment/{file['externalRecordId'][len(FILE_PREFIX):]}", ok=(404,))
        with contextlib.suppress(PipeshubClientError):
            w.client.reindex_record(file["id"])
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the record of a removed file was reindexed and the next sync ran")
