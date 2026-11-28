# pyright: ignore-file

"""
Jira Cloud on the simulator: people, groups, roles and the organisation gate
===========================================================================

  TC-JCSIM-PPL-001  A person listed later (product access given after the sync), named by a user, reporter and assignee holder
  TC-JCSIM-PPL-002  A re-used e-mail: the account deleted, a new account with the same address
  TC-JCSIM-PPL-003  Many accounts gone at once (deleted, deactivated, unlicensed): held for several reads, then gone
  TC-JCSIM-PPL-004  A few accounts gone: gone at once
  TC-JCSIM-PPL-005  A group renamed (new group, same members), named by a grant and by a role actor
  TC-JCSIM-PPL-006  A group of thousands of members, paged; a page refused halfway
  TC-JCSIM-PPL-007  A role emptied; a lead replaced by an unlisted person, by a deleted account
  TC-JCSIM-PPL-008  ``anyone`` with the site public, private, on a free plan, a policy; the signed-out search not answered
  TC-JCSIM-PPL-009  A user list cut short after its first page
"""

import logging
import uuid

import pytest
from connectors.jira.cloud_sim_access import API, PERSISTENT, actual, oracle, pick, refused, rule, within_bounds
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    CREATOR_EMAIL,
    OUTSIDER_EMAIL,
    JiraCloudWorld,
    fault,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    succeeded,
)

logger = logging.getLogger("jira-cloud-sim-people")
DOMAIN = "simcorp.example.com"


def licence_groups() -> tuple[str, ...]:
    return ("jira-software-users", "jira-servicemanagement-users", "jira-product-discovery-users")


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimPeople:

    async def _newcomer(self, w: JiraCloudWorld, products: list[str]) -> tuple[str, str]:
        email = f"newcomer-{uuid.uuid4().hex[:8]}@{DOMAIN}"
        made = w.sim.rest("POST", "/user", {"emailAddress": email, "products": products})
        await w.ensure_account(email)
        return email, made["accountId"]

    async def test_tc_jcsim_ppl_001_listed_later(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        newcomer, _ = await self._newcomer(w, [])
        project, _who, _keep = pick(w)
        issue = w.issue_keys(project)[0]
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "user", "parameter": newcomer},
            {"op": "grant", "project": project, "holder": "reporter"},
            {"op": "grant", "project": project, "holder": "assignee"},
            {"op": "issue", "issue": issue, "reporter": newcomer}]))
        await w.assert_matches_source("a newcomer with no product access reads nothing")
        assert not (await actual(w))[newcomer]
        w.mutate({"op": "group_member", "group": "jira-software-users", "user": newcomer, "member": True})
        assert w.probes()[newcomer]["jira_ids"], "the newcomer reads nothing in Jira after product access"
        assert succeeded(await w.sync())
        await w.assert_matches_source("the newcomer listed later reads what Jira shows")
        # the same through a full run, with the person taken away and given back
        w.mutate({"op": "group_member", "group": "jira-software-users", "user": newcomer, "member": False})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("product access taken away")

    async def test_tc_jcsim_ppl_002_reused_email(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "user", "parameter": who},
            {"op": "grant", "project": project, "holder": "user", "parameter": keep}]))
        await w.assert_matches_source("after the first sync")
        old = w.sim.access(who)["account_id"]
        w.sim.delete_account(old)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the account was deleted")
        made = w.sim.rest("POST", "/user", {"emailAddress": who, "products": ["jira-software"]})
        assert made["accountId"] != old
        w.mutate({"op": "grant", "project": project, "holder": "user", "parameter": who})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a new account with the address of the deleted one")
        assert w.sim.access(who)["account_id"] == made["accountId"]

    @pytest.mark.parametrize("how", ["deleted", "deactivated", "unlicensed"])
    async def test_tc_jcsim_ppl_003_many_gone_at_once(self, jira_cloud_world: JiraCloudWorld, how: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, *_ = pick(w)
        crowd = [e for e in w.emails() if e != CREATOR_EMAIL][:14]
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "user", "parameter": e} for e in crowd]))
        await w.assert_matches_source("after the first sync")
        before = oracle(w)
        for e in crowd:
            if how == "deleted":
                w.sim.delete_account(w.sim.access(e)["account_id"])
            elif how == "deactivated":
                w.mutate({"op": "user_active", "user": e, "active": False})
            else:
                w.mutate(*[{"op": "group_member", "group": g, "user": e, "member": False} for g in licence_groups()])
        after = oracle(w)
        assert any(before[e] - after[e] for e in crowd), "the change took access from nobody in Jira"
        gone_at: int | None = None
        for read in range(1, 5):
            assert succeeded(await w.sync(full=read % 2 == 0))
            now = await actual(w)
            lost = any(before[e] - now[e] for e in crowd)
            if not lost and not any(now[e] - before[e] for e in crowd):
                continue
            if all(now[e] == after[e] for e in crowd):
                gone_at = read
                break
            within_bounds(before, after, now, f"{how}: after read {read}")
        assert gone_at is not None, f"{how}: after four complete reads the accounts still read what Jira no longer shows"
        logger.info("%s: gone after %d read(s)", how, gone_at)
        await w.assert_matches_source(f"{how}: after they went")

    async def test_tc_jcsim_ppl_004_few_gone(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, *_ = pick(w)
        crowd = [e for e in w.emails() if e != CREATOR_EMAIL][:3]
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "user", "parameter": e} for e in crowd]))
        await w.assert_matches_source("after the first sync")
        w.sim.delete_account(w.sim.access(crowd[0])["account_id"])
        w.mutate({"op": "user_active", "user": crowd[1], "active": False})
        w.mutate(*[{"op": "group_member", "group": g, "user": crowd[2], "member": False} for g in licence_groups()])
        assert succeeded(await w.sync())
        await w.assert_matches_source("a few accounts gone: gone at once")

    async def test_tc_jcsim_ppl_005_group_renamed(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "group_create", "group": "old-name", "members": [who, keep]},
            {"op": "grant", "project": project, "holder": "group", "parameter": "old-name"},
            {"op": "grant", "project": project, "holder": "projectRole", "parameter": "Viewers"},
            {"op": "role_actor", "project": project, "role": "Viewers", "kind": "group", "who": "old-name"}]))
        await w.assert_matches_source("after the first sync")
        w.mutate({"op": "group_create", "group": "new-name", "members": [who, keep]},
                 {"op": "grant", "project": project, "holder": "group", "parameter": "new-name"},
                 {"op": "role_actor", "project": project, "role": "Viewers", "kind": "group", "who": "new-name"},
                 {"op": "group_delete", "group": "old-name"})
        assert w.project_readers(project) >= {who, keep}
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the group was renamed (a new group, the old one deleted)")
        w.mutate({"op": "group_member", "group": "new-name", "user": who, "member": False})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a member left the renamed group")

    async def test_tc_jcsim_ppl_006_thousands_of_members(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        w.mutate({"op": "group_create", "group": "crowd", "members": [who, keep]},
                 {"op": "grant", "project": project, "holder": "group", "parameter": "crowd"})
        ids = []
        for i in range(2100):
            made = w.sim.rest("POST", "/user", {"emailAddress": f"crowd{i}@{DOMAIN}", "products": ["jira-software"]})
            w.sim.rest("POST", "/group/user", {"accountId": made["accountId"]}, groupname="crowd")
            ids.append(made["accountId"])
        assert succeeded(await w.start(reset=False))
        await w.assert_matches_source("a group of 2,102 members, paged")
        # a page refused halfway: the stored members stay, and nobody reads more than before
        before = oracle(w)
        w.mutate({"op": "group_member", "group": "crowd", "user": who, "member": False})
        after = oracle(w)
        w.faults(fault(API + r"/group/member$", 502, after_n=3, max_times=PERSISTENT))
        await w.sync(full=True)
        assert w.clear_faults()
        within_bounds(before, after, await actual(w), "a member page refused after three pages")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the pages were read again")
        for account in ids[:40]:
            w.sim.rest("DELETE", "/group/user", None, groupname="crowd", accountId=account)
        assert succeeded(await w.sync())
        await w.assert_matches_source("after 40 of the crowd left")

    async def test_tc_jcsim_ppl_007_role_emptied_and_lead_replaced(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        ghost, ghost_id = await self._newcomer(w, [])
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "projectRole", "parameter": "Developers"},
            {"op": "role_actor", "project": project, "role": "Developers", "kind": "user", "who": who},
            {"op": "grant", "project": project, "holder": "projectLead"},
            {"op": "project", "project": project, "lead": keep}]))
        await w.assert_matches_source("after the first sync")
        w.mutate({"op": "role_clear", "project": project, "role": "Developers"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the role was emptied")
        w.mutate({"op": "project", "project": project, "lead": ghost})
        assert succeeded(await w.sync())
        await w.assert_matches_source("a lead with no product access (unlisted by the connector)")
        w.mutate({"op": "group_member", "group": "jira-software-users", "user": ghost, "member": True})
        assert succeeded(await w.sync())
        await w.assert_matches_source("the lead given product access")
        w.sim.delete_account(ghost_id)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("the lead's account deleted")

    @pytest.mark.parametrize("state", ["public", "private", "free", "policy"])
    async def test_tc_jcsim_ppl_008_anyone(self, jira_cloud_world: JiraCloudWorld, state: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        assert succeeded(await w.start(reset=False, prepare=[{"op": "grant", "project": project, "holder": "anyone"}]))
        await w.assert_matches_source("anyone, the site private")
        w.sim.switches(anonymous_access=True)
        assert succeeded(await w.sync())
        await w.assert_matches_source("the site public")
        assert OUTSIDER_EMAIL in w.people
        # the signed-out search is not answered while the site turns private: an open gate that is fresh stays open
        open_now = oracle(w)
        w.sim.switches(**{"public": {}, "private": {"anonymous_access": False}, "free": {"plan": "free"},
                          "policy": {"guard_prevent_anonymous": True}}[state])
        closed = oracle(w)
        for kind in ("500", "429", "malformed"):
            w.faults(rule(kind, API + r"/search/jql$", first_n=3))
            await w.sync(full=kind == "500")
            w.clear_faults()
            # A full run forgets when the gate was last confirmed, so it cannot keep it open without an answer; and a body that
            # is not JSON reads as "not public" in any run (``_read_anonymously``). Both close the gate (losses allowed, gains
            # never) until the next run that is answered. A rate limit keeps an open gate.
            within_bounds(open_now, closed, await actual(w), f"{state}: the signed-out search failed ({kind})",
                          losses_ok=kind != "429")
            assert succeeded(await w.sync(full=True))
            await w.assert_matches_source(f"{state}: after the signed-out search answered")
            w.sim.switches(anonymous_access=True, plan="standard", guard_prevent_anonymous=False)
            assert succeeded(await w.sync(full=True))
            w.sim.switches(**{"public": {}, "private": {"anonymous_access": False}, "free": {"plan": "free"},
                              "policy": {"guard_prevent_anonymous": True}}[state])

    async def test_tc_jcsim_ppl_010_a_leveled_issue_of_a_public_project_stays_closed(self, jira_cloud_world: JiraCloudWorld) -> None:
        """The whole organisation reads a public project's issues as any visitor does; an issue under a security level of that
        project is shown to the level's holders alone, and a comment restricted to a role or group to its readers alone."""
        w = jira_cloud_world
        w.sim.reset()
        project, levels = w.leveled_project()
        leveled = w.issue_keys(project)[0]
        index = w.sim.expected()["ticket_index"]
        leveled_id = next(i for i, t in index.items() if t["key"] == leveled)
        w.mutate({"op": "security", "issue": leveled, "level": levels[0]["name"]})
        w.sim.switches(anonymous_access=True)
        assert succeeded(await w.start(reset=False, prepare=[{"op": "grant", "project": project, "holder": "anyone"}]))
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("a public project with a leveled issue, the gate open")
        visitors = w.visitor_ids()
        others = {i for i, t in index.items() if t["key"].rsplit("-", 1)[0] == project and i != leveled_id}
        assert visitors & others and leveled_id not in visitors, "the site shows visitors the leveled issue, or nothing"
        outsider = (await w.holdings()).get(OUTSIDER_EMAIL, set())
        assert visitors <= outsider, f"the gate is not open: the outsider misses {sorted(visitors - outsider)[:5]}"
        assert leveled_id not in outsider
        # the level goes: the issue becomes the visitors' too, and back
        w.mutate({"op": "security", "issue": leveled, "level": None})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("the level removed")
        assert leveled_id in (await w.holdings()).get(OUTSIDER_EMAIL, set())
        w.mutate({"op": "security", "issue": leveled, "level": levels[0]["name"]})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("the level back")

    async def test_tc_jcsim_ppl_009_user_list_cut_short(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "user", "parameter": who},
            {"op": "grant", "project": project, "holder": "user", "parameter": keep}]))
        await w.assert_matches_source("after the first sync")
        before = oracle(w)
        w.sim.switches(users_search_short_pages=True)
        w.mutate({"op": "user_active", "user": who, "active": False})
        after = oracle(w)
        await w.sync(full=True)
        within_bounds(before, after, await actual(w), "pages of the user list shorter than asked")
        w.sim.switches(users_search_short_pages=False)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the user list was whole again")
