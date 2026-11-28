# pyright: ignore-file

"""
Jira Cloud on the simulator: the mechanics of a sync (checkpoints, retries, parents, links, filters, runs that overlap)
======================================================================================================================

  TC-JCSIM-SYN2-001  A run stopped half way (the connector turned off while it runs) and resumed
  TC-JCSIM-SYN2-002  Issues that fail to build are retried by the next run, and read once Jira answers right
  TC-JCSIM-SYN2-003  A parent outside the sync, reparented, deleted, in an unselected project
  TC-JCSIM-SYN2-004  Sub-tasks and children of moved issues
  TC-JCSIM-SYN2-005  Links added, removed, to deleted issues and to issues of an unselected project
  TC-JCSIM-SYN2-006  A changelog longer than the part sent with the issue; an attachment removed long ago
  TC-JCSIM-SYN2-007  Created and modified date filters at their edges, taken in and out
  TC-JCSIM-SYN2-008  The account's time zone unknown or unreadable: the date window is widened, never narrowed
  TC-JCSIM-SYN2-009  Filter options of the project picker: paged, searched, and the search refused
  TC-JCSIM-SYN2-010  Two connectors of one site sync the same changes; a sync requested while one runs; delete mid-run
  TC-JCSIM-SYN2-011  Checkpoints: edits, a full sync after incremental ones, a project added after the first run
  TC-JCSIM-SYN2-012  Transient refusals (1, 2, 3 in a row) of each read are retried; four in a row are not
  TC-JCSIM-SYN2-013  A throttle (429 with Retry-After in seconds, without it) is waited out
  TC-JCSIM-SYN2-014  The account's profile read refused at start-up and during a run
"""

import asyncio
import time

import pytest
from connectors.jira.cloud_sim_sync import (
    end_faults,
    BULK_RX,
    MYSELF_RX,
    PERSISTENT,
    PROJECT_SEARCH_RX,
    SEARCH_RX,
    clean,
    child_under_another_projects_epic,
    gaps,
    make_issues,
    make_projects,
    run_case,
    shaped,
)
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    JiraCloudWorld,
    date_filter,
    fault,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    merge_filters,
    project_filter,
    succeeded,
    throttle,
)
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimSyncMechanics:

    async def test_tc_jcsim_syn2_001_stopped_half_way(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project = w.big_project()
        make_issues(w, project, 260, "Many")
        assert await w.start(enable=False, reset=False) is None  # created, not on: the first run is the stopped one
        w.client.toggle_sync(w.connector_id, enable=True)
        await asyncio.sleep(25)
        w.disable()
        await w._wait_idle()
        assert succeeded(await w.enable())
        await w.assert_matches_source("after a first run was stopped half way and the connector turned on again")
        w.sim.advance(issue_edit=3, issue_create=2)
        w.client.resync_connector(w.connector_id, full_sync=False, connector_name=w.connector_type)
        await asyncio.sleep(8)
        w.disable()
        await w._wait_idle()
        assert succeeded(await w.enable())
        await w.assert_matches_source("after an incremental run was stopped and the connector turned on again")

    async def test_tc_jcsim_syn2_002_issues_that_fail_to_build(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.advance(issue_edit=4)
        # every issue of the search answers with a created time no one can read: each fails to build and is retried
        bad = shaped(SEARCH_RX, {"path": "issues[*].fields.created", "value": "not a time"}, max_times=PERSISTENT)
        for round_ in range(2):  # a retried issue that fails again is kept for the next run, not dropped
            outcome, _ = await run_case(w, bad, f"issues that fail to build, round {round_}")
            assert outcome is not None
            w.sim.advance(issue_edit=2)

    async def test_tc_jcsim_syn2_002b_bulk_read_refuses_for_good(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.advance(issue_edit=4, issue_create=2)
        await run_case(w, fault(BULK_RX, 500, max_times=PERSISTENT), "bulk read refused")
        w.sim.advance(issue_edit=2)
        await run_case(w, shaped(BULK_RX, {"path": "$", "value": {"issues": []}}, max_times=PERSISTENT), "bulk read empty")

    async def test_tc_jcsim_syn2_003_parents(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        child, parent = child_under_another_projects_epic(w)
        index = w.sim.expected()["ticket_index"]
        child_project = child["key"].rsplit("-", 1)[0]
        # the parent's project is outside the sync
        assert succeeded(await w.start(filters=project_filter("in", [child_project]), reset=False))
        await w.assert_matches_source("a child whose parent is in an unselected project")
        w.sim.rest("DELETE", f"/issue/{parent['key']}", deleteSubtasks="false", ok=(400,))
        await clean(w, "after the parent in the unselected project was deleted")
        w.mutate({"op": "issue", "issue": child["key"], "parent": None})
        await clean(w, "after the child left its parent")
        w.sim.advance(reparent=3)
        await clean(w, "after seeded reparenting")

    async def test_tc_jcsim_syn2_003b_parent_deleted_everywhere(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        index = w.sim.expected()["ticket_index"]
        pairs = [t for t in index.values() if t["parent_id"] and str(t["parent_id"]) in index]
        parent = index[str(pairs[0]["parent_id"])]
        w.sim.rest("DELETE", f"/issue/{parent['key']}", deleteSubtasks="true", ok=(400,))
        w.sim.advance(delete=2)
        await clean(w, "after parents were deleted with their sub-tasks")
        await clean(w, "after a full sync", full=True)

    async def test_tc_jcsim_syn2_004_moved_issues(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.advance(move=4)
        await clean(w, "after issues (and their sub-tasks) moved to other projects")
        a = w.projects()[0]
        assert succeeded(await w.restart(project_filter("not_in", [a])))
        w.sim.advance(move=3)
        await clean(w, "after issues moved into and out of a project the filter leaves out")

    async def test_tc_jcsim_syn2_005_links(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.advance(link_add=6, link_remove=3)
        await clean(w, "after links were added and removed")
        index = w.sim.expected()["ticket_index"]
        keys = sorted(t["key"] for t in index.values())
        first, last = keys[0], keys[-1]
        types = w.sim.rest("GET", "/issueLinkType")["issueLinkTypes"]
        w.sim.rest("POST", "/issueLink", {"type": {"name": types[0]["name"]}, "inwardIssue": {"key": first},
                                          "outwardIssue": {"key": last}})
        w.sim.rest("DELETE", f"/issue/{last}", ok=(400,))
        w.sim.rest("PUT", f"/issue/{first}", {"fields": {"summary": "Linked to an issue that is gone"}})
        await clean(w, "after an issue was linked to one that was then deleted")
        proj = last.rsplit("-", 1)[0]
        assert succeeded(await w.restart(project_filter("in", [first.rsplit("-", 1)[0]])))
        w.sim.advance(link_add=4)
        w.sim.rest("PUT", f"/issue/{first}", {"fields": {"summary": "Edited with links across projects"}})
        await clean(w, "with links to issues of an unselected project")
        assert proj

    async def test_tc_jcsim_syn2_006_long_changelog_and_old_removal(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        index = w.sim.expected()["ticket_index"]
        with_files = next(t for t in index.values() if len(t["files"]) >= 1)
        key = with_files["key"]
        removed = with_files["files"][0]["id"]
        w.sim.rest("DELETE", f"/attachment/{removed}", ok=(404,))
        for i in range(115):
            w.sim.rest("PUT", f"/issue/{key}", {"fields": {"summary": f"Edit {i}"}})
        await clean(w, "after an attachment was removed and 115 edits pushed that out of the changelog sent with the issue")
        assert f"attachment_{removed}" not in {r["externalRecordId"] for r in await w.records()}
        # a description change that drops an attachment's name, and a restricted comment edit
        w.sim.rest("PUT", f"/issue/{key}", {"fields": {"description": {"type": "doc", "version": 1, "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": "A new description"}]}]}}})
        w.sim.advance(attachment_add=2, attachment_remove=2, comment_edit=2)
        await clean(w, "after descriptions, files and comments changed")

    async def test_tc_jcsim_syn2_007_date_filters_edges(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        lo, hi = gaps(w, "created_ms", 2)
        mlo, mhi = gaps(w, "updated_ms", 2)
        assert succeeded(await w.start(filters=date_filter("created", "is_between", lo, hi), reset=False))
        await w.assert_matches_source("created between")
        for name, op, start, end in (("created", "is_after", hi, None), ("created", "is_before", None, lo),
                                     ("modified", "is_between", mlo, mhi), ("modified", "is_after", mhi, None),
                                     ("modified", "is_before", None, mlo)):
            assert succeeded(await w.restart(date_filter(name, op, start, end)))
            assert succeeded(await w.sync(full=True))
            await w.assert_matches_source(f"{name} {op}")
        both = merge_filters(date_filter("created", "is_between", lo, hi), date_filter("modified", "is_after", mlo))
        assert succeeded(await w.restart(both))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("created and modified together")
        w.sim.advance(issue_edit=3, issue_create=3)
        await clean(w, "an incremental run under a date filter")
        assert succeeded(await w.restart({"sync": {"values": {}}}))
        await clean(w, "filters cleared", full=True)

    @pytest.mark.parametrize("zone", ["missing", "unknown", "refused"])
    async def test_tc_jcsim_syn2_008_time_zone_unreadable(self, jira_cloud_world: JiraCloudWorld, zone: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        at = gaps(w, "created_ms")[0]
        rule = {"missing": shaped(MYSELF_RX, {"path": "timeZone", "delete": True}, max_times=PERSISTENT),
                "unknown": shaped(MYSELF_RX, {"path": "timeZone", "value": "Mars/Olympus"}, max_times=PERSISTENT),
                "refused": fault(MYSELF_RX, 500, max_times=PERSISTENT)}[zone]
        w.faults(rule)
        outcome = await w.start(filters=date_filter("created", "is_after", at), reset=False)
        end_faults(w)
        # SAFETY: a wider window than the filter may add issues, never take one away or give anyone anything. A run that did not
        # succeed may have read nothing (with the account's profile refused, the connector does not start).
        kinds = ("LEAK", "DUPLICATE", "PARENT", *(("MISSING",) if succeeded(outcome) else ()))
        bad = [p for p in await w.differences() if str(p["type"]).startswith(kinds)]
        assert not bad, f"{zone}: {outcome}: {bad[:10]}"
        # PipesHub does not turn a connector on whose account profile was refused.
        assert succeeded(await w.sync(full=True) if succeeded(outcome) else await w.enable())
        await w.assert_matches_source(f"after the time zone could be read ({zone})")

    async def test_tc_jcsim_syn2_009_filter_options(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        extra = make_projects(w, 30)
        assert succeeded(await w.start(reset=False))
        listed = {p["key"] for p in w.sim.rest("GET", "/project/search", maxResults=100)["values"]}
        seen: set[str] = set()
        page = 1
        while True:
            got = w.client.get_filter_options(w.connector_id, "project_keys", limit=7, page=page)
            options = got.get("options") or (got.get("data") or {}).get("options") or []
            seen |= {o["id"] for o in options}
            if not (got.get("hasMore") or (got.get("data") or {}).get("hasMore")) or page > 20:
                break
            page += 1
        assert page > 1 and seen == listed, f"paged options {sorted(seen ^ listed)}"
        found = w.client.get_filter_options(w.connector_id, "project_keys", limit=50, search=extra[3])
        names = {o["id"] for o in (found.get("options") or (found.get("data") or {}).get("options") or [])}
        assert extra[3] in names and len(names) < len(listed), f"search {extra[3]} found {sorted(names)}"
        nothing = w.client.get_filter_options(w.connector_id, "project_keys", limit=50, search="no-such-project-name")
        assert not (nothing.get("options") or (nothing.get("data") or {}).get("options") or [])
        for rule in (fault(PROJECT_SEARCH_RX, 500, max_times=PERSISTENT), shaped(PROJECT_SEARCH_RX, {"path": "$", "value": []}),
                     fault(PROJECT_SEARCH_RX, 401, max_times=PERSISTENT)):
            w.faults(rule)
            with pytest.raises(PipeshubClientError):
                w.client.get_filter_options(w.connector_id, "project_keys", limit=7)
            end_faults(w)
        with pytest.raises(PipeshubClientError):
            w.client.get_filter_options(w.connector_id, "no_such_filter", limit=7)

    async def test_tc_jcsim_syn2_010_two_connectors_and_overlapping_runs(self, jira_cloud_world: JiraCloudWorld) -> None:
        first = jira_cloud_world
        assert succeeded(await first.start())
        second = JiraCloudWorld(first.sim, first.client, first.graph, first.people)
        try:
            assert succeeded(await second.start(reset=False, filters=project_filter("not_in", [first.projects()[0]])))
            first.sim.advance(issue_edit=3, issue_create=2, delete=1)
            await first.sync()
            assert succeeded(await second.sync())
            await first.assert_matches_source("first connector, a second one on the site")
            await second.assert_matches_source("second connector with a filter")
            # a request while a run runs, then the connector deleted while the run still goes
            first.client.resync_connector(first.connector_id, full_sync=True, connector_name=first.connector_type)
            with pytest.raises(PipeshubClientError, match="409"):
                first.client.resync_connector(first.connector_id, full_sync=False, connector_name=first.connector_type)
            await first._wait_idle()
            second.client.resync_connector(second.connector_id, full_sync=True, connector_name=second.connector_type)
            time.sleep(4)
            second.disable()
            await second._wait_idle()
            second.client.delete_connector(second.connector_id)
            second.connector_id = None
            await first.assert_matches_source("first connector after the second was deleted while it ran")
        finally:
            second.remove_connector()

    async def test_tc_jcsim_syn2_011_checkpoints(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        await clean(w, "unchanged")
        w.sim.advance(issue_edit=3)
        await clean(w, "edits")
        make_projects(w, 2, "Q")
        make_issues(w, "QAA", 3)
        await clean(w, "a project added after the first run")
        await clean(w, "full after incremental", full=True)
        w.sim.advance(issue_create=2)
        await clean(w, "incremental after full")

    @pytest.mark.parametrize("times", [1, 2, 3, 4])
    @pytest.mark.parametrize("endpoint", ["search", "bulk", "project", "myself"])
    async def test_tc_jcsim_syn2_012_transient_refusals(self, jira_cloud_world: JiraCloudWorld, endpoint: str, times: int) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.advance(issue_edit=3, issue_create=2)
        rx = {"search": SEARCH_RX, "bulk": BULK_RX, "project": PROJECT_SEARCH_RX, "myself": MYSELF_RX}[endpoint]
        outcome, fired = await run_case(w, fault(rx, 503, max_times=times), f"{times} refusals of {endpoint}")
        assert fired >= 1
        if times < 4 and endpoint != "bulk":
            assert succeeded(outcome), f"{times} refusal(s) of {endpoint} in a row are within the retries but the run was {outcome}"

    @pytest.mark.parametrize("kind", ["seconds", "none"])
    async def test_tc_jcsim_syn2_013_throttle(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.advance(issue_edit=3)
        rule = throttle(SEARCH_RX) if kind == "seconds" else fault(SEARCH_RX, 429, max_times=1)
        outcome, _ = await run_case(w, rule, f"throttle {kind}")
        assert outcome is not None

    async def test_tc_jcsim_syn2_014_profile_refused(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        w.sim.advance(issue_edit=2)
        # refused after the first read: the one at the start of the issue pass (the time zone) fails
        await run_case(w, fault(MYSELF_RX, 500, after_n=1, max_times=PERSISTENT), "profile refused during a run", fires=False)
