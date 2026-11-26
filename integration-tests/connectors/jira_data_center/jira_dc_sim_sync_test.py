# pyright: ignore-file

"""
Jira Data Center on the simulator – first sync, unchanged sync, full sync
=========================================================================

After each sync every person reads in PipesHub exactly the tickets and attachments the simulator's oracle (Jira's own
permission rules) lets them read, and every ticket and attachment of the sync account's view has one live record under
its project (sim_world.py).

  TC-JDSIM-SYNC-001  First sync with a personal access token equals the oracle (tickets, attachments, projects, readers)
  TC-JDSIM-SYNC-002  First sync with basic credentials equals the oracle
  TC-JDSIM-SYNC-003  A second sync with nothing changed creates, removes and re-versions nothing
  TC-JDSIM-SYNC-004  A full sync with nothing changed holds the same records and the same readers
"""

import pytest
from connectors.jira_data_center.sim_world import JiraDcWorld, succeeded


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimSync:

    async def test_tc_jdsim_sync_001_first_sync_token(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(auth="token"))
        await w.assert_matches_source("after the first sync with a personal access token")

    async def test_tc_jdsim_sync_002_first_sync_basic(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(auth="basic"))
        await w.assert_matches_source("after the first sync with basic credentials")

    async def test_tc_jdsim_sync_003_unchanged_sync(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        before = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert succeeded(await w.sync())
        after = {r["externalRecordId"]: (r["id"], r["version"]) for r in await w.records()}
        assert after == before, "a sync with nothing changed created, removed or re-versioned records"

    async def test_tc_jdsim_sync_004_unchanged_full_sync(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        before = await w.holdings()
        assert succeeded(await w.sync(full=True))
        await w.assert_holdings_kept(before, "after a full sync with nothing changed")
        await w.assert_matches_source("after a full sync with nothing changed")
