# pyright: ignore-file

"""
Jira Data Center on the simulator – gone at the source, and the full sync
=========================================================================

  TC-JDSIM-DEL-001  Issues deleted while the connector is off: a full sync removes them and their files
  TC-JDSIM-DEL-002  Issues deleted while the connector is off: turning it on again (a run that reads the audit log)
                    removes them
  TC-JDSIM-DEL-003  The audit log refused (403): an incremental run keeps what it cannot know is gone, the full sync
                    removes it
  TC-JDSIM-DEL-004  A project deleted at the source: a full sync removes its records and nobody reads them
  TC-JDSIM-DEL-005  A full sync that could not read a project's issues (search refused after the first page) keeps what it
                    held; the next clean full sync removes what is gone
  TC-JDSIM-DEL-006  Such a full sync is not recorded as a success unless sim_campaign.PARTIAL_SUCCESS names it
"""

import pytest
from connectors.jira_data_center.sim_campaign import PARTIAL_SUCCESS
from connectors.jira_data_center.sim_world import JiraDcWorld, fault, succeeded

SEARCH = r"^/rest/api/2/search$"
AUDIT = r"^/rest/auditing/1\.0/events$"


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimDeletion:

    async def test_tc_jdsim_del_001_deleted_while_disabled_full_sync(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        w.disable()
        w.sim.advance(delete=3)
        assert succeeded(await w.enable())
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync of a site with three issues gone")

    async def test_tc_jdsim_del_002_deleted_while_disabled_enable(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        w.disable()
        w.sim.advance(delete=3)
        assert succeeded(await w.enable())
        await w.assert_matches_source("after the connector was turned on again")

    async def test_tc_jdsim_del_003_audit_log_refused(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        w.sim.advance(delete=3)
        w.faults(fault(AUDIT, 403, max_times=500))
        await w.sync()
        w.clear_faults()
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a full sync that does not need the audit log")

    async def test_tc_jdsim_del_004_project_deleted(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        w.mutate({"op": "project", "project": "KEYS", "status": "deleted"})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a project was deleted at the source")

    async def test_tc_jdsim_del_005_full_sync_that_could_not_read(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.sim.advance(delete=3)
        w.faults(fault(SEARCH, 502, after_n=1, max_times=500))
        await w.sync(full=True)
        w.clear_faults()
        await w.assert_holdings_kept(before, "after a full sync that could not read the issues", allow_gains=True)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a clean full sync")

    async def test_tc_jdsim_del_006_partial_full_sync_is_not_a_success(self, jira_dc_world: JiraDcWorld) -> None:
        """OUTCOME of DEL-005: the run is a success only if its pair is in PARTIAL_SUCCESS."""
        w = jira_dc_world
        assert succeeded(await w.start())
        w.faults(fault(SEARCH, 502, after_n=1, max_times=500))
        outcome = await w.sync(full=True)
        w.clear_faults()
        listed = ("POST /rest/api/2/search after the first page", "502") in PARTIAL_SUCCESS
        assert succeeded(outcome) == listed, (
            f"a full sync whose search failed after the first page ended {outcome!r}; "
            f"PARTIAL_SUCCESS {'lists' if listed else 'does not list'} it")
