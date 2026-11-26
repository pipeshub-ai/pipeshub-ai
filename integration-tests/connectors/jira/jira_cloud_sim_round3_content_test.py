# pyright: ignore-file

"""
Jira Cloud on the simulator, round 3: live fields of a ticket the simulator did not serve, and the stubs of ancestors
===================================================================================================================

Live fields (``jira/enrichment``): the simulator holds components, versions, environment and time tracking of an issue, and
Jira Software's Sprint (schema ``com.pyxis.greenhopper.jira:gh-sprint``, the one Jira Software's documentation names), Epic Link
and Epic Name; a test sets them through the simulator's ``issue_field`` mutation and reads the ticket as an agent does. The
expected line is derived from what the simulator answers for the issue (Jira's own shape), never from the test's input.

  TC-JCSIM-R3C-001  Components, fix and affects versions, environment and time tracking read as Jira holds them; an issue with none has no line
  TC-JCSIM-R3C-002  Sprint, Epic Name and Epic Link lines; an Epic Link naming an issue the reader cannot read adds no line and no key
  TC-JCSIM-R3C-003  The search that resolves an Epic Link key failing leaves the other live lines and no Epic Link line

Ancestor stubs (``_sweep_placeholder_records``): a child whose parent the sync did not take (the parent was created before the
``created`` filter) has a stub for its parent. A parent in a project that is not synced never has one: the child hangs off its
project (``_extract_issue_data``, ``_parent_outside_synced_projects``), which test FLT-008 shows.

  TC-JCSIM-R3C-004  The stub is named from Jira ("[KEY] summary"), stays in the child's project, is not indexed, and is read in bulk
  TC-JCSIM-R3C-006  A sub-task in the filter under a story and an epic that are not: both get a stub, named from Jira, one level at a time
  TC-JCSIM-R3C-005  Jira failing the bulk read of the ancestors (500, a cut answer) leaves the stub as it was and loses no record; the next clean run names it
"""

import asyncio
import time

import pytest
from connectors.jira.cloud_sim_content import SEARCH_URL, ids_by_external, new_issue, wait_for
from connectors.jira.cloud_sim_round3_content import (
    BULKFETCH_URL,
    EPIC_LINK,
    EPIC_NAME,
    SPRINT,
    a_readable_issue,
    child_of_older_parent,
    graph_rows,
    jira_fields,
    line,
    read_ticket,
    set_field,
    stub_of,
)
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    JiraCloudWorld,
    date_filter,
    fault,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    malformed,
    merge_filters,
    project_filter,
    succeeded,
)


def _adf_text(adf) -> str:
    return " ".join(i["text"] for b in adf["content"] for i in b["content"])


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimRound3Content:

    async def test_tc_jcsim_r3c_001_system_fields(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        by_ext = ids_by_external(await w.creator_records())
        tid, key = a_readable_issue(w, by_ext)
        other_tid, other_key = a_readable_issue(w, by_ext, skip={key})
        for field, value in (("components", ["Backend", "UI"]), ("fixVersions", ["1.2"]), ("versions", ["1.1", "1.0"]),
                             ("environment", "linux, python 3.12"),
                             ("timetracking", {"originalEstimate": "1d 2h", "remainingEstimate": "90m", "timeSpent": "30m"})):
            set_field(w, key, field, value)
        held = jira_fields(w, key, "components", "fixVersions", "versions", "environment", "timetracking")
        text = await read_ticket(w, by_ext[tid], need="* Time Tracking:")
        tt = held["timetracking"]
        want = {"Components": ", ".join(c["name"] for c in held["components"]),
                "Fix Versions": ", ".join(v["name"] for v in held["fixVersions"]),
                "Affects Versions": ", ".join(v["name"] for v in held["versions"]),
                "Environment": _adf_text(held["environment"]),
                "Time Tracking": f"original: {tt['originalEstimate']}; remaining: {tt['remainingEstimate']}; spent: {tt['timeSpent']}"}
        assert want["Components"] == "Backend, UI" and want["Affects Versions"] == "1.1, 1.0"  # the oracle holds what was written
        problems = [f"{label}: {line(text, label)!r} != {value!r}" for label, value in want.items()
                    if line(text, label) != f"* {label}: {value}"]
        assert not problems, (problems, text[:1800])
        empty = await read_ticket(w, by_ext[other_tid])
        assert not [lb for lb in want if line(empty, lb)], f"{other_key} holds none of them, yet its read has a line: {empty[:1500]}"

    async def test_tc_jcsim_r3c_002_software_fields_and_epic_link(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project = w.big_project(8)
        assert succeeded(await w.start(filters=project_filter("in", [project])))
        by_ext = ids_by_external(await w.creator_records())
        tid, key = a_readable_issue(w, by_ext)
        link_tid, link_key = a_readable_issue(w, by_ext, skip={key})
        out_key = next(k for p in w.projects() if p != project for k in w.issue_keys(p))  # an issue of a project that is not synced
        sprints = [{"id": 6, "name": "Sprint 6", "state": "closed", "boardId": 2, "startDate": "2026-01-05T09:00:00.000Z",
                    "endDate": "2026-01-19T09:00:00.000Z", "completeDate": "2026-01-19T10:00:00.000Z"},
                   {"id": 7, "name": "Sprint 7", "state": "active", "boardId": 2}]
        set_field(w, key, SPRINT, sprints)
        set_field(w, key, EPIC_NAME, "Checkout revamp")
        set_field(w, key, EPIC_LINK, link_key)
        set_field(w, link_key, EPIC_LINK, out_key)
        held = jira_fields(w, key, SPRINT, EPIC_NAME, EPIC_LINK)
        assert len(held[SPRINT]) == 2 and held[EPIC_LINK] == link_key and held[EPIC_NAME] == "Checkout revamp"
        text = await read_ticket(w, by_ext[tid], need="* Sprint:")
        assert line(text, "Sprint") == "* Sprint: Sprint 6; Sprint 7", line(text, "Sprint")
        assert line(text, "Epic Name") == "* Epic Name: Checkout revamp", text[:1500]
        assert line(text, "Epic Link") == f"* Epic Link: {link_key}", (key, text[:1500])
        # An Epic Link to an issue of a project that is not synced: the reader cannot read it, so it is not named.
        other = await read_ticket(w, by_ext[link_tid])
        assert out_key not in other and line(other, "Epic Link") is None, (out_key, project, other[:1500])

    async def test_tc_jcsim_r3c_003_epic_link_key_lookup_fails(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        by_ext = ids_by_external(await w.creator_records())
        tid, key = a_readable_issue(w, by_ext)
        _, link_key = a_readable_issue(w, by_ext, skip={key})
        set_field(w, key, EPIC_NAME, "Lookup epic")
        set_field(w, key, EPIC_LINK, link_key)
        assert line(await read_ticket(w, by_ext[tid], need="* Epic Link:"), "Epic Link") == f"* Epic Link: {link_key}"
        for kind, rule in (("500", fault(SEARCH_URL, 500, after_n=1)), ("cut", malformed(SEARCH_URL, after_n=1))):
            w.faults(rule)  # the read of the issue is the first search; the lookup of the key is the second
            text = await read_ticket(w, by_ext[tid], need="* Epic Name:")
            w.clear_faults()
            assert line(text, "Epic Name") == "* Epic Name: Lookup epic", (kind, text[:1200])
            assert line(text, "Epic Link") is None and link_key not in text, (kind, text[:1200])

    async def test_tc_jcsim_r3c_004_ancestor_stub_is_named_from_jira(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        child, parent, _gap = child_of_older_parent(w)
        at = (child["created_ms"] + parent["created_ms"]) // 2
        assert succeeded(await w.start(filters=date_filter("created", "is_after", at), reset=False))
        await w.assert_matches_source("with a child in the filter and its parent created before it")
        index = w.sim.expected()["ticket_index"]
        parent_id = next(i for i, t in index.items() if t["key"] == parent["key"])
        child_id = next(i for i, t in index.items() if t["key"] == child["key"])
        want = f"[{parent['key']}] {jira_fields(w, parent['key'], 'summary')['summary']}"

        async def named():
            s = await stub_of(w, parent_id)
            return s if s and s.get("recordName") == want else None

        stub = await wait_for(named, f"the stub of {parent['key']} named {want!r}", timeout=90, interval=3)
        assert str(stub.get("externalGroupId")) == str(child["project_id"]), stub
        assert str(stub.get("indexingStatus")).upper() in ("AUTO_INDEX_OFF", "NOT_STARTED", "NONE", "AUTO_INDEX_OFF "), stub
        assert not any(str(r["externalRecordId"]) == parent_id for r in await w.records()), "a stub is not a synced issue"
        assert any(str(r.get("externalRecordId")) == child_id and not r.get("isPlaceholder") for r in await graph_rows(w))
        assert any("bulkfetch" in p for p in w.sim.paths_called()), w.sim.paths_called()

    @pytest.mark.parametrize("kind", ["500", "cut"])
    async def test_tc_jcsim_r3c_005_bulk_read_of_ancestors_fails(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        child, parent, _gap = child_of_older_parent(w)
        at = (child["created_ms"] + parent["created_ms"]) // 2
        assert await w.start(filters=date_filter("created", "is_after", at), reset=False, enable=False) is None
        w.faults(fault(BULKFETCH_URL, 500) if kind == "500" else malformed(BULKFETCH_URL))
        await w.enable()
        w.clear_faults()  # asserts that the fault fired
        index = w.sim.expected()["ticket_index"]
        parent_id = next(i for i, t in index.items() if t["key"] == parent["key"])
        child_id = next(i for i, t in index.items() if t["key"] == child["key"])
        rows = await graph_rows(w)
        assert any(str(r.get("externalRecordId")) == child_id and not r.get("isDeleted") for r in rows), "the child was lost"
        stub = await stub_of(w, parent_id)
        assert stub is not None and stub.get("recordName") != f"[{parent['key']}] {jira_fields(w, parent['key'], 'summary')['summary']}", \
            "the stub was named though Jira did not answer"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a clean run that follows the failed bulk read")
        want = f"[{parent['key']}] {jira_fields(w, parent['key'], 'summary')['summary']}"

        async def named():
            s = await stub_of(w, parent_id)
            return s if s and s.get("recordName") == want else None

        await wait_for(named, f"the stub of {parent['key']} named after the clean run", timeout=90, interval=3)

    async def test_tc_jcsim_r3c_006_chain_of_ancestor_stubs(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project = w.big_project(4)
        epic = new_issue(w, project, "Round 3 epic", issuetype="Epic")
        story = new_issue(w, project, "Round 3 story", issuetype="Story")
        w.mutate({"op": "issue", "issue": story["key"], "parent": epic["key"]})
        # A `created` filter has the resolution of a minute: the next minute boundary is the filter, and the sub-task is
        # created after it.
        cut = (int(time.time() * 1000) // 60_000 + 1) * 60_000
        await asyncio.sleep(max(0.0, cut / 1000 + 3 - time.time()))
        sub = new_issue(w, project, "Round 3 sub-task", issuetype="Sub-task", parent={"key": story["key"]})
        assert succeeded(await w.start(filters=merge_filters(date_filter("created", "is_after", cut), project_filter("in", [project])), reset=False))
        await w.assert_matches_source("with a sub-task in the filter under a story and an epic created before it")
        by_ext = ids_by_external(await w.records())
        assert str(sub["id"]) in by_ext, "the sub-task is in the filter"
        assert str(epic["id"]) not in by_ext and str(story["id"]) not in by_ext, "the story and the epic are not in the filter"
        for issue in (story, epic):
            want = f"[{issue['key']}] {jira_fields(w, issue['key'], 'summary')['summary']}"

            async def named(issue=issue, want=want):
                s = await stub_of(w, issue["id"])
                return s if s and s.get("recordName") == want else None

            stub = await wait_for(named, f"the stub of {issue['key']} named {want!r}", timeout=90, interval=3)
            assert str(stub.get("externalGroupId")) == str(next(
                t["project_id"] for t in w.sim.expected()["ticket_index"].values() if t["key"] == issue["key"])), stub
