# pyright: ignore-file

"""
Jira Data Center on the simulator – hierarchy, issue links and mentioned keys
=============================================================================

Against the oracle's ``ticket_index`` (per ticket: its parent, the tickets it links to, the tickets its summary and
description name): a ticket is filed under its parent when Jira has one in this view (a sub-task under its issue, an issue
under its epic by Epic Link, an epic under its initiative by Parent Link, a parent in another project too), and joined by a
record link to every ticket it links to or names. Read through the graph provider's protocol
(``get_record_parent_external_id``, ``get_record_link_targets``).

  TC-JDSIM-HIER-001  First sync: every ticket's parent and link targets equal the oracle
  TC-JDSIM-HIER-002  Issues re-parented and linked by the engine: after each sync the parents and links equal the oracle
  TC-JDSIM-HIER-003  A story moved under an epic of another project, then taken out again: parents equal the oracle and who
                     reads each record still equals Jira
"""

import pytest
from connectors.jira_data_center.sim_world import JiraDcWorld, succeeded


async def _differences(w: JiraDcWorld) -> list[dict]:
    index = w.sim.expected()["ticket_index"]
    records = {r["externalRecordId"] for r in await w.records()}
    problems: list[dict] = []
    for tid, t in index.items():
        if tid not in records:
            continue
        want_parent = str(t["parent_id"]) if t["parent_id"] is not None and str(t["parent_id"]) in index else None
        parent = await w.graph.get_record_parent_external_id(w.connector_id, tid)
        if parent != want_parent:
            problems.append({"type": "PARENT", "key": t["key"], "is": parent, "should": want_parent})
        want_links = set(t["links"]) | set(t["mentions"])
        links = {x for x in await w.graph.get_record_link_targets(w.connector_id, tid) if x in index}
        if links != want_links:
            problems.append({"type": "LINKS", "key": t["key"], "missing": sorted(want_links - links),
                             "extra": sorted(links - want_links)})
    return problems


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimHierarchy:

    async def test_tc_jdsim_hier_001_first_sync(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        index = w.sim.expected()["ticket_index"]
        assert any(t["parent_id"] is not None for t in index.values()) and any(t["links"] for t in index.values())
        problems = await _differences(w)
        assert not problems, f"hierarchy or links differ from Jira: {problems[:10]}"

    async def test_tc_jdsim_hier_002_changed_hierarchy_and_links(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        w.sim.advance(reparent=2)
        assert succeeded(await w.sync())
        problems = await _differences(w)
        assert not problems, f"after re-parenting: {problems[:10]}"
        w.sim.advance(link_add=3, link_remove=2, issue_edit=2)
        assert succeeded(await w.sync())
        problems = await _differences(w)
        assert not problems, f"after links changed: {problems[:10]}"

    async def test_tc_jdsim_hier_003_cross_project_parent(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        index = w.sim.expected()["ticket_index"]
        epic = next(t for t in index.values() if t["type"] == "Epic")
        story = next(t for t in index.values()
                     if t["type"] in ("Story", "Task", "Bug") and t["parent_id"] is None and t["project_id"] != epic["project_id"])
        assert succeeded(await w.start(reset=False))
        w.mutate({"op": "issue", "issue": story["key"], "parent": epic["key"]})
        assert succeeded(await w.sync())
        problems = await _differences(w)
        assert not problems, f"after a story moved under an epic of another project: {problems[:10]}"
        await w.assert_matches_source("after a story moved under an epic of another project")
        w.mutate({"op": "issue", "issue": story["key"], "parent": None})
        assert succeeded(await w.sync())
        problems = await _differences(w)
        assert not problems, f"after the story left the epic: {problems[:10]}"
