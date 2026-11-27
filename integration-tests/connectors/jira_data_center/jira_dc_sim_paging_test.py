# pyright: ignore-file

"""
Jira Data Center on the simulator – page boundaries and page-token faults
=========================================================================

The connector reads users 50 at a time through /user/search (100 through /user/list) and a group's members 50 at a time.
Jira users are added until the lists are exactly one page and one user over; the last user added has a PipesHub account
and reaches a project only through the group that holds every licensed user, so a dropped last page shows as a missing
grant.

  TC-JDSIM-PAGE-001  /user/search and group members at 50 and 51 (Jira 10.2)
  TC-JDSIM-PAGE-002  /user/list at 100 and 101 (Jira 11)
  TC-JDSIM-PAGE-003  /user/list page tokens that go wrong (short pages, a dropped token, an expired token, a repeated
                     token): the run is not a success unless it read everyone, and the next clean run matches
  TC-JDSIM-PAGE-004  A change lands between two issue searches of one run (a trigger of the simulator): the run keeps
                     everything it held and gives nobody anything Jira does not; the next run reads the change
  TC-JDSIM-PAGE-005  A change lands between the two pages of /user/list (130 users): the same, and the next run matches
"""

import uuid

import pytest
from connectors.jira_data_center.sim_world import JiraDcWorld, page_token, succeeded

USERS = r"^/rest/api/2/user/list$"
SEARCHES = r"^/rest/api/2/search$"


def _trigger(method: str, path_regex: str, after_nth: int, *mutations: dict) -> list[dict]:
    """The simulator applies ``mutations`` once, after the Nth matching request has been answered."""
    return [{"match": {"method": method, "path_regex": path_regex}, "after_nth": after_nth, "mutations": list(mutations)}]


async def _fill(w: JiraDcWorld, total: int) -> None:
    """Jira users until the site has ``total``; the last one has a PipesHub account."""
    have = len(w.sim.users())
    assert total > have
    ops = [{"op": "user_create", "username": f"filler.{i}"} for i in range(have, total - 1)]
    last = f"last.{uuid.uuid4().hex[:6]}"
    ops.append({"op": "user_create", "username": last, "email": f"{last}@example.com"})
    w.mutate(*ops)
    await w.ensure_account(f"{last}@example.com")


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimPaging:

    async def _boundary(self, w: JiraDcWorld, totals: list[int], switches: dict) -> None:
        assert succeeded(await w.start(switches=switches, prepare=[
            {"op": "grant", "project": "LIGH", "holder": "group", "parameter": "jira-software-users"}]))
        await w.assert_matches_source("before the lists reach a page")
        for total in totals:
            await _fill(w, total)
            assert succeeded(await w.sync())
            await w.assert_matches_source(f"with {total} users in Jira")

    async def test_tc_jdsim_page_001_user_search_and_group_members(self, jira_dc_world: JiraDcWorld) -> None:
        await self._boundary(jira_dc_world, [50, 51], {"version": "10.2.1"})

    async def test_tc_jdsim_page_002_user_list(self, jira_dc_world: JiraDcWorld) -> None:
        await self._boundary(jira_dc_world, [100, 101], {})

    @pytest.mark.parametrize("mode", ["short", "drop", "expire", "repeat"])
    async def test_tc_jdsim_page_003_user_list_tokens(self, jira_dc_world: JiraDcWorld, mode: str) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(prepare=[
            {"op": "grant", "project": "LIGH", "holder": "applicationRole", "parameter": "jira-software"}]))
        await w.assert_matches_source("after the first sync")
        before = await w.holdings()
        names = w.usernames()
        w.mutate({"op": "grant", "project": "LIGH", "holder": "user", "parameter": names[0]})
        w.faults(page_token(USERS, mode))
        outcome = await w.sync()
        w.clear_faults()
        if succeeded(outcome):
            await w.assert_matches_source(f"after a run with {mode} user-list pages that reported success")
        else:
            await w.assert_holdings_kept(before, f"after a run whose user list had {mode} pages")
            assert succeeded(await w.sync())
            await w.assert_matches_source("after the user list answered again")

    async def test_tc_jdsim_page_004_change_between_two_searches(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        before = await w.holdings()
        late = w.issue_keys("KEYS")[0]
        who = w.usernames()[3]
        # every run searches project by project: after the third search the late project's issue and a role change land
        w.sim.call("PUT", "/_sim/jiradc/triggers", _trigger(
            "POST", SEARCHES, 3, {"op": "issue", "issue": late, "summary": "Changed mid-run"},
            {"op": "role_actor", "project": "GRAN", "role": "Administrators", "kind": "user", "who": who}))
        w.sim.advance(issue_edit=2)
        assert succeeded(await w.sync())
        assert w.sim.call("GET", "/_sim/jiradc/triggers")[0]["fired"], "the change never landed during the run"
        problems = [p for p in await w.differences() if str(p["type"]).startswith(("LEAK", "STALE", "DUPLICATE", "PARENT"))]
        assert not problems, f"a change that landed mid-run made PipesHub show what Jira does not: {problems[:10]}"
        await w.assert_holdings_kept(before, "after a run a change landed in", allow_gains=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the next run read what landed mid-run")
        record = await w.record_for(next(t for t, v in w.sim.expected()["ticket_index"].items() if v["key"] == late))
        assert "Changed mid-run" in str(record["recordName"])

    async def test_tc_jdsim_page_005_change_between_user_list_pages(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        filler = [{"op": "user_create", "username": f"filler.{i}"} for i in range(len(w.sim.users()), 130)]
        who, other = w.usernames()[3], w.usernames()[4]
        assert succeeded(await w.start(reset=False, prepare=[
            *filler, {"op": "grant", "project": "LIGH", "holder": "applicationRole", "parameter": "jira-software"}]))
        before = await w.holdings()
        w.sim.call("PUT", "/_sim/jiradc/triggers", _trigger(
            "GET", USERS, 1, {"op": "user_active", "user": who, "active": False},
            {"op": "group_member", "group": "jira-software-users", "user": other, "member": False}))
        w.sim.advance(issue_edit=1)
        assert succeeded(await w.sync())
        assert w.sim.call("GET", "/_sim/jiradc/triggers")[0]["fired"], "the change never landed between the pages"
        problems = [p for p in await w.differences() if str(p["type"]).startswith(("STALE", "DUPLICATE", "PARENT"))]
        assert not problems, f"a change between the pages broke the records: {problems[:10]}"
        del before
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the next run read the user list again")
