# pyright: ignore-file

"""
Jira Data Center on the simulator – sync filters
================================================

The scope of a sync is what the sync account reads in the projects the project-key filter allows, created and modified
inside the date filters (sim_world.Scope). A filter changed later takes effect on the next full sync: what left the scope
is removed, what entered is added.

  TC-JDSIM-FLT-001  Project keys "in", then changed to "not in", then cleared
  TC-JDSIM-FLT-002  Created date: is after, is before, is between a day that falls between two issues
  TC-JDSIM-FLT-003  Modified date: is after
  TC-JDSIM-FLT-004  Project keys and a created date together
  TC-JDSIM-FLT-005  Indexing filters (issues and attachments off; attachments only; manual sync): the records exist and
                    only the ones the filter names are left unindexed
  TC-JDSIM-FLT-006  The project-key options the app offers are the projects the sync account lists
"""

import itertools

import pytest
from connectors.jira_data_center.sim_world import JiraDcWorld, date_filter, merge_filters, project_filter, succeeded


def _gaps(w: JiraDcWorld, field_: str, n: int = 1) -> list[int]:
    """Times in the ``n`` widest gaps between two issues' ``field_`` times (in time order), so minute rounding cannot
    move an issue across a filter."""
    times = sorted(t[field_] for t in w.sim.expected()["ticket_index"].values())
    gaps = sorted(((b - a, (a + b) // 2) for a, b in itertools.pairwise(times)), reverse=True)[:n]
    assert gaps[-1][0] > 3_600_000, "no gap of an hour between two issues to put a date filter in"
    return sorted(at for _width, at in gaps)


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimFilters:

    async def test_tc_jdsim_flt_001_project_keys(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(filters=project_filter("in", ["ATLA", "FALC", "LIGH"])))
        await w.assert_matches_source("with projects ATLA, FALC and LIGH only")
        assert succeeded(await w.restart(project_filter("not_in", ["ATLA", "FALC", "LIGH"])))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the filter was changed to leave those three out")
        assert succeeded(await w.restart({"sync": {"values": {}}}))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the filter was cleared")

    @pytest.mark.parametrize("operator", ["is_after", "is_before"])
    async def test_tc_jdsim_flt_002_created_date(self, jira_dc_world: JiraDcWorld, operator: str) -> None:
        w = jira_dc_world
        w.sim.reset()
        at = _gaps(w, "created_ms")[0]
        f = date_filter("created", operator, at if operator == "is_after" else None, at if operator == "is_before" else None)
        assert succeeded(await w.start(filters=f, reset=False))
        await w.assert_matches_source(f"with created {operator} a day between two issues")

    async def test_tc_jdsim_flt_002_created_date_between(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        lo, hi = _gaps(w, "created_ms", 2)
        assert succeeded(await w.start(filters=date_filter("created", "is_between", lo, hi), reset=False))
        await w.assert_matches_source("with created between two days")

    async def test_tc_jdsim_flt_003_modified_date(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        at = _gaps(w, "updated_ms")[0]
        assert succeeded(await w.start(filters=date_filter("modified", "is_after", at), reset=False))
        await w.assert_matches_source("with modified after a day between two issues")

    async def test_tc_jdsim_flt_004_projects_and_created_together(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        at = _gaps(w, "created_ms")[0]
        f = merge_filters(project_filter("not_in", ["LIGH"]), date_filter("created", "is_after", at))
        assert succeeded(await w.start(filters=f, reset=False))
        await w.assert_matches_source("with a project left out and a created date")

    async def _indexing_statuses(self, w: JiraDcWorld, indexing: dict) -> dict[str, set[str]]:
        """Start a connector with these indexing filters; the indexing status of its ticket and file records."""
        await w.start(enable=False)
        w.client.update_connector_filters_sync_config(w.connector_id, filters={"indexing": {"values": indexing}})
        assert succeeded(await w.enable())
        await w.assert_matches_source("with indexing filters set")
        rows = await w.graph.fetch_records_by_type(w.connector_id, "", scoped=True)
        out: dict[str, set[str]] = {"ticket": set(), "file": set()}
        for r in rows:
            out["file" if str(r["externalRecordId"]).startswith("attachment_") else "ticket"].add(str(r.get("indexingStatus")))
        return out

    async def test_tc_jdsim_flt_005_indexing_filters(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        off = {"operator": "is", "type": "boolean", "value": False}
        got = await self._indexing_statuses(w, {"issues": off, "issue_attachments": off})
        assert got["ticket"] | got["file"] == {"AUTO_INDEX_OFF"}, f"records were queued for indexing: {got}"

    async def test_tc_jdsim_flt_005_attachments_off_only(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        got = await self._indexing_statuses(w, {"issue_attachments": {"operator": "is", "type": "boolean", "value": False}})
        assert got["file"] == {"AUTO_INDEX_OFF"} and "AUTO_INDEX_OFF" not in got["ticket"], got

    async def test_tc_jdsim_flt_005_manual_sync(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        got = await self._indexing_statuses(w, {"enable_manual_sync": {"operator": "is", "type": "boolean", "value": True}})
        assert got["ticket"] | got["file"] == {"AUTO_INDEX_OFF"}, f"manual sync still queued records: {got}"

    async def test_tc_jdsim_flt_006_project_options(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        options = w.client.get_filter_options(w.connector_id, "project_keys", limit=100)
        offered = options.get("options") or (options.get("data") or {}).get("options") or []
        keys = {o["id"] for o in offered}
        listed = {p["key"] for p in w.sim.vendor_get("/rest/api/2/project").json()}
        assert keys == listed and len(listed) == 12, f"the app offers {sorted(keys)}, Jira lists {sorted(listed)}"
