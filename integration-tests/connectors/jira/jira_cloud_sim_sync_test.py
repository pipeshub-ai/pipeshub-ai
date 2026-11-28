# pyright: ignore-file

"""
Jira Cloud on the simulator: sign-in and the first sync
=======================================================

  TC-JCSIM-SYNC-001  First sync: every ticket, file and comment equals the oracle; each person reads what Jira shows them
  TC-JCSIM-SYNC-002  A second sync with nothing changed creates, removes and re-versions no record
"""

import pytest
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    JiraCloudWorld,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    succeeded,
)


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimSync:

    async def test_tc_jcsim_sync_001_first_sync(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        assert not w.sim.unmodeled()

    async def test_tc_jcsim_sync_002_unchanged(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        before = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert succeeded(await w.sync())
        assert {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()} == before
