# pyright: ignore-file

"""
Linear on the simulator: lifecycle, deletion while off, vendor variants
=======================================================================

  TC-LNSIM-LIFE-001  Turned off and on again: the run equals the workspace, records keep their ids [L]
  TC-LNSIM-LIFE-002  Gone at the source while the connector was off (an issue, a project, a team): a full sync removes it, the
                     rest stays [F]
  TC-LNSIM-LIFE-003  Deleting the connector leaves nothing of it in the graph [L]
  TC-LNSIM-LIFE-004  Two connectors on one workspace each equal it, and removing one leaves the other whole [L]
  TC-LNSIM-LIFE-005  A sync requested while one runs: both end, the result equals the workspace [L]
  TC-LNSIM-LIFE-006  Reindexing a record: the record is queued again and still equals the source; one gone at the source
                     does not break the request [K]
  TC-LNSIM-LIFE-007  Rewinding the workspace (the simulator reset to what was loaded) after records were removed at the
                     source: the next full sync restores them [F]
  TC-LNSIM-VAR-001   Each open behaviour of Linear's API that changes what the connector sees (team filter with sub-teams,
                     a comment moving its issue's update time, 400 for authentication and rate limits, a 400 for an
                     unknown entity, a page limit of 5): a sync and an incremental sync equal the workspace [M]
"""

import asyncio

import pytest
from connectors.linear.sim_instances import small, workspace
from connectors.linear.sim_world import LinearWorld, succeeded

VARIANTS = [
    {"team_filter_includes_subteams": False},
    {"child_changes_bump_parent": True},
    {"max_page": 5, "over_page_limit": "clamp"},
    {"auth_error_status": 400, "rate_limit_status": 400, "not_found_status": 400},
]


@pytest.mark.integration
@pytest.mark.linear_sim
@pytest.mark.asyncio(loop_scope="session")
class TestLinearSimLifecycle:

    async def test_tc_lnsim_life_001_off_and_on(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        before = {r["externalRecordId"]: r["id"] for r in await w.records()}
        assert succeeded(await w.restart())
        assert {r["externalRecordId"]: r["id"] for r in await w.records()} == before
        await w.assert_matches_source("after turning the connector off and on")

    async def test_tc_lnsim_life_002_gone_while_off(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.client.toggle_sync(w.connector_id, enable=False)
        await w._wait_idle()
        w.ops({"op": "delete_issue", "issue": "eng-4"}, {"op": "delete_project", "project": "p4"},
              {"op": "delete_team", "team": "OPS"})
        assert succeeded(await w.enable())
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after what went while the connector was off")

    async def test_tc_lnsim_life_003_deleting_the_connector_leaves_nothing(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(small()))
        connector_id = w.connector_id
        w.client.toggle_sync(connector_id, enable=False)
        w.client.delete_connector(connector_id)
        w.client.wait(25)
        await w.graph.assert_all_records_cleaned(connector_id, timeout=300)
        w.connector_id = None

    async def test_tc_lnsim_life_004_two_connectors_one_workspace(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(small()))
        first = w.connector_id
        second = w.create_connector()
        assert succeeded(await w.enable())
        mine = {r["externalRecordId"] for r in await w.records()}
        w.connector_id = first
        theirs = {r["externalRecordId"] for r in await w.records()}
        assert mine == theirs and mine, "two connectors on one workspace hold different records"
        w.connector_id = second
        await w.assert_matches_source("the second connector")
        w.client.toggle_sync(second, enable=False)
        w.client.delete_connector(second)
        w.connector_id = first
        await w.assert_matches_source("the first connector, after the second was deleted")

    async def test_tc_lnsim_life_005_a_sync_requested_while_one_runs(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(small()))
        w.ops({"op": "add_issue", "team": "OPS", "title": "added for two runs", "creator": "bob"})
        w.client.resync_connector(w.connector_id, full_sync=False, connector_name="Linear")
        try:
            w.client.resync_connector(w.connector_id, full_sync=False, connector_name="Linear")
        except Exception:  # noqa: BLE001, S110 - the product may refuse the second request
            pass
        await asyncio.sleep(1)
        await w._wait_idle()
        assert succeeded(await w.sync())
        await w.assert_matches_source("after two requests")

    async def test_tc_lnsim_life_006_reindex(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        record = await w.record_for(w.ident("issues", "eng-1"))
        assert w.client.reindex_record(record["id"]) is not None
        gone = await w.record_for(w.ident("issues", "eng-2"))
        w.ops({"op": "edit_issue", "issue": "eng-1", "title": "Changed before reindex"},
              {"op": "delete_issue", "issue": "eng-2"})
        w.client.reindex_record(record["id"])
        try:
            w.client.reindex_record(gone["id"])
        except Exception:  # noqa: BLE001, S110 - the request for a record gone at the source may be refused
            pass
        assert succeeded(await w.sync())
        await w.assert_matches_source("after reindexing, a change and a deletion")

    async def test_tc_lnsim_life_007_the_workspace_rewound(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        w.ops({"op": "delete_issue", "issue": "eng-4"}, {"op": "trash_project", "project": "p3"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the deletions")
        w.sim.call("POST", "/_sim/reset")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the workspace was put back")

    @pytest.mark.parametrize("variant", VARIANTS, ids=lambda v: "+".join(v))
    async def test_tc_lnsim_var_001_open_behaviours(self, linear_world: LinearWorld, variant: dict) -> None:
        w = linear_world
        assert succeeded(await w.start(small(), behaviours=variant))
        await w.assert_matches_source(f"first sync under {variant}")
        w.ops({"op": "edit_issue", "issue": "e2", "title": "edited"}, {"op": "add_comment", "issue": "o1", "user": "bob", "body": "hi"},
              {"op": "add_issue", "team": "OPS", "title": "new", "creator": "bob"})
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"incremental sync under {variant}")
