# pyright: ignore-file

"""
Jira Cloud on the simulator: sync and indexing filters
======================================================

The scope of a sync is what the sync account reads in the projects the project-key filter allows, created and modified
inside the date filters (cloud_sim_world.Scope). A filter changed later takes effect on the next full sync.

  TC-JCSIM-FLT-001  Project keys "in", then changed to "not in", then cleared
  TC-JCSIM-FLT-002  Created date: is after, is before, is between
  TC-JCSIM-FLT-003  Modified date: is after
  TC-JCSIM-FLT-004  Project keys and a created date together
  TC-JCSIM-FLT-005  Indexing filters (issues and attachments off; attachments only; manual sync)
  TC-JCSIM-FLT-006  The project-key options the app offers are the projects the sync account lists
  TC-JCSIM-FLT-007  A project taken out of the filter after a first sync is removed by a full sync
  TC-JCSIM-FLT-008  A child whose parent is outside the filter keeps its place (a placeholder stands for the parent)
"""

import itertools

import pytest
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    JiraCloudWorld,
    date_filter,
    indexing_filter,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    merge_filters,
    project_filter,
    succeeded,
)


def _gaps(w: JiraCloudWorld, field_: str, n: int = 1) -> list[int]:
    """Times in the ``n`` widest gaps between two issues' ``field_`` times, so minute rounding cannot move an issue
    across a filter."""
    times = sorted(t[field_] for t in w.sim.expected()["ticket_index"].values())
    gaps = sorted(((b - a, (a + b) // 2) for a, b in itertools.pairwise(times)), reverse=True)[:n]
    assert gaps[-1][0] > 60_000, "no gap of a minute between two issues to put a date filter in"
    return sorted(at for _width, at in gaps)


def child_under_another_projects_epic(w: JiraCloudWorld) -> tuple[dict, dict]:
    """Move a standard issue under an epic of another project; return the issue and that epic (the oracle's rows)."""
    index = w.sim.expected()["ticket_index"]
    kinds: dict[str, dict] = {}

    def kind(key: str) -> dict:
        if key not in kinds:
            kinds[key] = w.sim.rest("GET", f"/issue/{key}")["fields"]["issuetype"]
        return kinds[key]

    for child in index.values():
        old = index.get(str(child["parent_id"]))
        if not old or old["project_id"] != child["project_id"] or kind(child["key"])["subtask"]:
            continue  # a sub-task is always in its parent's project
        wanted = kind(old["key"])["id"]
        epic = next((t for t in index.values() if t["project_id"] != child["project_id"] and kind(t["key"])["id"] == wanted), None)
        if epic:
            w.mutate({"op": "issue", "issue": child["key"], "parent": epic["key"]})
            return child, epic
    raise AssertionError("no standard issue could be moved under an epic of another project")


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimFilters:

    async def test_tc_jcsim_flt_001_project_keys(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        keys = w.projects()[:3]
        assert succeeded(await w.start(filters=project_filter("in", keys), reset=False))
        await w.assert_matches_source(f"with projects {keys} only")
        assert succeeded(await w.restart(project_filter("not_in", keys)))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the filter was changed to leave those three out")
        assert succeeded(await w.restart({"sync": {"values": {}}}))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the filter was cleared")

    @pytest.mark.parametrize("operator", ["is_after", "is_before"])
    async def test_tc_jcsim_flt_002_created_date(self, jira_cloud_world: JiraCloudWorld, operator: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        at = _gaps(w, "created_ms")[0]
        f = date_filter("created", operator, at if operator == "is_after" else None, at if operator == "is_before" else None)
        assert succeeded(await w.start(filters=f, reset=False))
        await w.assert_matches_source(f"with created {operator} a time between two issues")

    async def test_tc_jcsim_flt_002_created_date_between(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        lo, hi = _gaps(w, "created_ms", 2)
        assert succeeded(await w.start(filters=date_filter("created", "is_between", lo, hi), reset=False))
        await w.assert_matches_source("with created between two times")

    @pytest.mark.parametrize("operator", ["is_after", "is_before"])
    async def test_tc_jcsim_flt_003_modified_date(self, jira_cloud_world: JiraCloudWorld, operator: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        at = _gaps(w, "updated_ms")[0]
        f = date_filter("modified", operator, at if operator == "is_after" else None, at if operator == "is_before" else None)
        assert succeeded(await w.start(filters=f, reset=False))
        await w.assert_matches_source(f"with modified {operator} a time between two issues")

    async def test_tc_jcsim_flt_004_projects_and_created_together(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        at = _gaps(w, "created_ms")[0]
        f = merge_filters(project_filter("not_in", [w.projects()[0]]), date_filter("created", "is_after", at))
        assert succeeded(await w.start(filters=f, reset=False))
        await w.assert_matches_source("with a project left out and a created date")

    async def _statuses(self, w: JiraCloudWorld, **filters: bool) -> dict[str, set[str]]:
        await w.start(enable=False)
        w.client.update_connector_filters_sync_config(
            w.connector_id, filters={"indexing": {"values": {k: {"operator": "is", "type": "boolean", "value": v}
                                                             for k, v in filters.items()}}})
        assert succeeded(await w.enable())
        await w.assert_matches_source("with indexing filters set")
        out: dict[str, set[str]] = {"ticket": set(), "file": set(), "comment": set()}
        for r in await w.records():
            ext = str(r["externalRecordId"])
            out["file" if ext.startswith("attachment_") else "comment" if ext.startswith("comment_") else "ticket"].add(
                str(r.get("indexingStatus")))
        return out

    async def test_tc_jcsim_flt_005_indexing_filters(self, jira_cloud_world: JiraCloudWorld) -> None:
        got = await self._statuses(jira_cloud_world, issues=False, issue_attachments=False)
        assert got["ticket"] | got["file"] == {"AUTO_INDEX_OFF"}, f"records were queued for indexing: {got}"

    async def test_tc_jcsim_flt_005_attachments_off_only(self, jira_cloud_world: JiraCloudWorld) -> None:
        got = await self._statuses(jira_cloud_world, issue_attachments=False)
        assert got["file"] == {"AUTO_INDEX_OFF"} and "AUTO_INDEX_OFF" not in got["ticket"], got

    async def test_tc_jcsim_flt_005_manual_sync(self, jira_cloud_world: JiraCloudWorld) -> None:
        got = await self._statuses(jira_cloud_world, enable_manual_sync=True)
        assert got["ticket"] | got["file"] == {"AUTO_INDEX_OFF"}, f"manual sync still queued records: {got}"

    async def test_tc_jcsim_flt_006_project_options(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        options = w.client.get_filter_options(w.connector_id, "project_keys", limit=100)
        offered = options.get("options") or (options.get("data") or {}).get("options") or []
        keys = {o["id"] for o in offered}
        listed = {p["key"] for p in w.sim.rest("GET", "/project/search", maxResults=100)["values"]}
        assert keys == listed and keys, f"the app offers {sorted(keys)}, Jira lists {sorted(listed)}"

    async def test_tc_jcsim_flt_007_project_taken_out_after_the_first_sync(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        assert succeeded(await w.restart(project_filter("not_in", [w.projects()[0]])))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after a project was taken out and a full sync ran")

    async def test_tc_jcsim_flt_008_parent_outside_the_filter(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        child, parent = child_under_another_projects_epic(w)
        child_key = child["key"].rsplit("-", 1)[0]
        assert succeeded(await w.start(filters=project_filter("in", [child_key]), reset=False))
        await w.assert_matches_source("with a child in scope and its parent in a project outside the filter")
        parent_id = str(next(i for i, t in w.sim.expected()["ticket_index"].items() if t["key"] == parent["key"]))
        assert not any(r["externalRecordId"] == parent_id for r in await w.records()), parent["key"]
