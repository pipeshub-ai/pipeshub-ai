# pyright: ignore-file

"""
Jira Cloud on the simulator: paging and changes between pages
=============================================================

  TC-JCSIM-PAGE-001  Users, group members and issues at a page-size boundary (a listing of 100 and 101 issues; 50 and 51 group members)
  TC-JCSIM-PAGE-002  Short pages of the user list (the vendor's quirk), on and off
  TC-JCSIM-PAGE-003  Issue-search page tokens that go wrong (short, dropped, expired, repeated, empty): the run is not a
                     success unless it read everything, and the next clean run matches
  TC-JCSIM-PAGE-004  A change lands between two issue searches of one run: the run keeps everything it held and gives
                     nobody anything Jira does not; the next run reads the change
  TC-JCSIM-PAGE-005  A change lands between two pages of a group's members
  TC-JCSIM-PAGE-006  Issues the bulk read refuses once (retriable) are read on retry
"""

import pytest
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    JiraCloudWorld,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    page_token,
    succeeded,
)

SEARCH = r"^/rest/api/3/search/jql$"
MEMBERS = r"^/rest/api/3/group/member$"


def _trigger(method: str, path_regex: str, after_nth: int, *mutations: dict) -> list[dict]:
    return [{"match": {"method": method, "path_regex": path_regex}, "after_nth": after_nth, "mutations": list(mutations)}]


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimPaging:

    @pytest.mark.parametrize("total", [100, 101])
    async def test_tc_jcsim_page_001_issue_listing_boundary(self, jira_cloud_world: JiraCloudWorld, total: int) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        project = w.big_project()
        have = len(w.sim.expected()["ticket_index"])
        for _ in range(max(0, total - have)):
            w.sim.rest("POST", "/issue", {"fields": {"project": {"key": project}, "summary": "Filler", "issuetype": {"name": "Task"}}})
        assert succeeded(await w.sync())
        await w.assert_matches_source(f"with {total} issues")

    @pytest.mark.parametrize("short", [True, False])
    async def test_tc_jcsim_page_002_user_list(self, jira_cloud_world: JiraCloudWorld, short: bool) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start(switches={"users_search_short_pages": short}))
        await w.assert_matches_source(f"with users_search_short_pages {short}")

    @pytest.mark.parametrize("mode", ["short", "drop", "expire", "repeat", "empty"])
    async def test_tc_jcsim_page_003_search_tokens(self, jira_cloud_world: JiraCloudWorld, mode: str) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        before = await w.holdings()
        w.sim.advance(issue_edit=2, issue_create=1)
        w.faults(page_token(SEARCH, mode))
        outcome = await w.sync()
        w.clear_faults()
        if succeeded(outcome):
            problems = [p for p in await w.differences() if str(p["type"]).startswith(("LEAK", "DUPLICATE", "PARENT"))]
            assert not problems, f"a run with {mode} search pages reported success and shows what Jira does not: {problems[:10]}"
        await w.assert_holdings_kept(before, f"after a run whose search had {mode} pages", allow_gains=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the search answered again")

    async def test_tc_jcsim_page_004_change_between_two_searches(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        before = await w.holdings()
        late = w.issue_keys(w.projects()[-1])[0]
        who = w.emails()[3]
        # the run searches project by project: after the third search an issue and a grant change land
        w.sim.call("PUT", "/_sim/jira/triggers", _trigger(
            "POST", SEARCH, 3, {"op": "issue", "issue": late, "summary": "Changed mid-run"},
            {"op": "grant", "project": w.projects()[-1], "holder": "user", "parameter": who}))
        w.sim.advance(issue_edit=2)
        assert succeeded(await w.sync())
        assert w.sim.call("GET", "/_sim/jira/triggers")[0]["fired"], "the change never landed during the run"
        problems = [p for p in await w.differences() if str(p["type"]).startswith(("LEAK", "STALE", "DUPLICATE", "PARENT"))]
        assert not problems, f"a change that landed mid-run made PipesHub show what Jira does not: {problems[:10]}"
        await w.assert_holdings_kept(before, "after a run a change landed in", allow_gains=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the next run read what landed mid-run")
        record = await w.record_for(next(t for t, v in w.sim.expected()["ticket_index"].items() if v["key"] == late))
        assert "Changed mid-run" in str(record["recordName"])

    async def test_tc_jcsim_page_005_change_between_member_pages(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start())
        who, other = w.emails()[3], w.emails()[4]
        w.sim.call("PUT", "/_sim/jira/triggers", _trigger(
            "GET", MEMBERS, 1, {"op": "user_active", "user": who, "active": False},
            {"op": "group_member", "group": "jira-software-users", "user": other, "member": False}))
        w.sim.advance(issue_edit=1)
        assert succeeded(await w.sync())
        assert w.sim.call("GET", "/_sim/jira/triggers")[0]["fired"], "the change never landed between the pages"
        problems = [p for p in await w.differences() if str(p["type"]).startswith(("STALE", "DUPLICATE", "PARENT"))]
        assert not problems, f"a change between the pages broke the records: {problems[:10]}"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the next run read the groups again")

    async def test_tc_jcsim_page_006_bulk_read_retriable(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        assert succeeded(await w.start(switches={"bulkfetch_retriable_share": 0.5}))
        await w.assert_matches_source("after a first sync whose bulk reads refused half the issues once")
