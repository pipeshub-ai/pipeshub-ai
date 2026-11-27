# pyright: ignore-file

"""
Jira Data Center on the simulator – the life of a connector
===========================================================

  TC-JDSIM-LIFE-001  Turned off and on, the connector keeps its records and the next run matches
  TC-JDSIM-LIFE-002  Deleting the connector leaves no record, record group or permission of it behind
  TC-JDSIM-LIFE-003  Two connectors on one Jira each match the oracle, and removing one leaves the other whole
  TC-JDSIM-LIFE-004  A sync requested while one runs is refused (409), and the run in progress ends matching Jira
"""

import pytest
from connectors.jira_data_center.sim_world import JiraDcWorld, succeeded
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimLifecycle:

    async def test_tc_jdsim_life_001_disable_enable(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.disable()
        await w._wait_idle()
        await w.assert_holdings_kept(before, "while the connector is off")
        assert succeeded(await w.enable())
        await w.assert_matches_source("after the connector was turned on again")

    async def test_tc_jdsim_life_002_delete_connector_leaves_nothing(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        connector_id = w.connector_id
        w.disable()
        await w._wait_idle()
        w.client.delete_connector(connector_id)
        w.connector_id = None
        await w.graph.assert_connector_graph_fully_cleaned(connector_id)

    async def test_tc_jdsim_life_003_two_connectors_one_source(self, jira_dc_world: JiraDcWorld) -> None:
        first = jira_dc_world
        assert succeeded(await first.start())
        second = JiraDcWorld(first.sim, first.client, first.graph, first.people)
        try:
            assert succeeded(await second.start(reset=False, auth="basic"))
            await first.assert_matches_source("the first connector, with a second on the same Jira")
            await second.assert_matches_source("the second connector")
            second.disable()
            await second._wait_idle()
            second.client.delete_connector(second.connector_id)
            second.connector_id = None
            await first.assert_matches_source("the first connector, after the second was deleted")
        finally:
            second.remove_connector()

    async def test_tc_jdsim_life_004_sync_requested_while_running(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        w.sim.advance(issue_create=2, issue_edit=2)
        w.client.resync_connector(w.connector_id, full_sync=True, connector_name=w.connector_type)
        # The product refuses a second request while a run is going (409): it neither queues nor interleaves it.
        with pytest.raises(PipeshubClientError, match="409"):
            w.client.resync_connector(w.connector_id, full_sync=False, connector_name=w.connector_type)
        await w._wait_idle()
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a second sync was requested while the first ran")
