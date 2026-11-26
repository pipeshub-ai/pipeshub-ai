# pyright: ignore-file

"""
Zoom on the simulator: turning the connector off and on, deleting it, running two
=================================================================================

  TC-ZMSIM-LIFE-001  Disabled and enabled again: nothing is created, removed or re-versioned, and a meeting that
                     arrived meanwhile is read [L]
  TC-ZMSIM-LIFE-002  Deleting the connector leaves no record, no record group and no user of it in the graph [L]
  TC-ZMSIM-LIFE-003  Two connectors on one account: each holds the whole account, neither disturbs the other, and
                     deleting one leaves the other as it was [L]
  TC-ZMSIM-LIFE-004  A sync requested while one runs is refused (409) and the run ends once, with no duplicate records [L]
"""

import pytest
from helper.graph_provider_utils import async_poll_until
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]

from app.config.constants.arangodb import AppStatus

from connectors.zoom.sim_instances import small
from connectors.zoom.sim_world import ZoomWorld, succeeded


@pytest.mark.integration
@pytest.mark.zoom_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZoomSimLifecycle:

    async def test_tc_zmsim_life_001_disable_and_enable(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        before = {r["externalRecordId"]: (r["id"], r["version"], r["revision"]) for r in await w.records()}
        w.client.toggle_sync(w.connector_id, enable=False)
        await w._wait_idle()
        w.ops({"op": "new_meeting_ended", "meeting": "review",
               "instance": {"key": "r9", "days_ago": 1, "participants": [{"user": "cara"}]}})
        assert succeeded(await w.enable())
        after = {r["externalRecordId"]: (r["id"], r["version"], r["revision"]) for r in await w.records()}
        assert {k: v for k, v in after.items() if k in before} == before
        assert set(after) - set(before) == {w.instance("r9")["uuid"]}
        await w.assert_matches_source("after disabling and enabling")

    async def test_tc_zmsim_life_002_delete_leaves_nothing(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        connector_id = w.connector_id
        w.client.toggle_sync(connector_id, enable=False)
        w.client.delete_connector(connector_id)
        await w.graph.assert_all_records_cleaned(connector_id, timeout=300)
        assert not await w.graph.fetch_records_by_type(connector_id, "")
        w.connector_id = None

    async def test_tc_zmsim_life_003_two_connectors_one_account(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        first = w.connector_id
        held = await w.holdings()
        w.create_connector()
        await w.sign_in()
        assert succeeded(await w.enable())
        second = w.connector_id
        await w.assert_matches_source("the second connector")
        w.connector_id = first
        await w.assert_holdings_kept(held, "the first connector, after a second one synced the same account")
        w.connector_id = second
        w.client.toggle_sync(second, enable=False)
        w.client.delete_connector(second)
        await w.graph.assert_all_records_cleaned(second, timeout=300)
        w.connector_id = first
        await w.assert_holdings_kept(held, "the first connector, after the second was deleted")

    async def test_tc_zmsim_life_004_sync_requested_while_one_runs(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        w.ops({"op": "new_meeting_ended", "meeting": "review",
               "instance": {"key": "r9", "days_ago": 1, "participants": [{"user": "cara"}]}})
        w.faults({"match": {"path_regex": r"^/v2/report/users/"}, "effect": {"type": "latency", "ms": 5000}})
        await w._wait_idle()
        before = w._connector().get("lastSyncFinishedAtTimestamp") or 0
        w.client.resync_connector(w.connector_id, full_sync=False, connector_name="Zoom")
        running = {AppStatus.SYNCING.value, AppStatus.FULL_SYNCING.value, AppStatus.QUEUED.value}

        async def is_running():
            return True if w._connector().get("status") in running else None

        await async_poll_until(is_running, timeout=60, interval=0.5, description="the requested run to start")
        refused = ""
        try:
            w.client.resync_connector(w.connector_id, full_sync=False, connector_name="Zoom")
        except PipeshubClientError as exc:
            refused = str(exc)
        assert "409" in refused, f"the second request was not refused while a sync ran: {refused!r}"
        outcome = await w._await_end(before)
        w.sim.call("DELETE", "/_sim/faults")
        w._fault_rules = []
        assert succeeded(outcome)
        await w.assert_matches_source("after two requests for one run")
