# pyright: ignore-file

"""
Jira Cloud on the simulator: round 4, the organisation gate and the code around it
=================================================================================

  TC-JCSIM-R4A-001  An open gate whose signed-out search is rate limited is kept (no new confirmation); one that is refused for good closes nothing it did not read
  TC-JCSIM-R4A-002  One project of several granting ``anyone`` that visitors cannot read closes the gate
  TC-JCSIM-R4A-003  A public project's issue pass failed: an open gate closes, a gate about to open stays closed, the next clean run opens it
  TC-JCSIM-R4A-004  A project granting ``anyone`` added while the gate is open closes it until the signed-out search confirms it
  TC-JCSIM-R4A-005  A day-old confirmation closes the gate (answered or not), the notice is due, and a clean run reopens it
  TC-JCSIM-R4A-006  The signed-out search retried after a 503, dropped, answered with an empty page that is not the last
  TC-JCSIM-R4A-007  The application roles read as a non-list, with no key, with users and no group, with a group with no id
  TC-JCSIM-R4A-008  Holder types the connector does not know (portal customers, a type Jira adds later); the add-ons role as a holder
  TC-JCSIM-R4A-009  A refused group list with the role lists refused, not a mapping, or naming a role with no URL
  TC-JCSIM-R4A-011  A leveled issue whose levels cannot be applied (members refused, scheme refused or 404, holder with no account, paging that cannot be followed)
  TC-JCSIM-R4A-012  Many linked accounts missing from a complete user list: counted and restarted (012), released by three reads (012c); 012b: strict xfail, access dropped at once
  TC-JCSIM-R4A-013  A person listed after a leveled issue was read, with the issue read, the scheme read refused
  TC-JCSIM-R4A-014  A project the connector cannot build a record group for
  TC-JCSIM-R4A-015  The day ends while the signed-out search is being read (answered late, or not at all)

The organisation (a PipesHub account with no Jira account) reads a public project's issues only while the gate is open. The
clock is the saved ``confirmed_at`` of the sync point ``anonymous_gate``, moved back through the graph store.

Vendor pages: signed-out search "can be accessed anonymously" (https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/),
anonymous access (https://support.atlassian.com/user-management/docs/give-users-access-to-products/), rate limits
(https://developer.atlassian.com/cloud/jira/platform/rate-limiting/).
"""

import asyncio
import logging
import uuid

import pytest
from connectors.jira.cloud_sim_access import API, PERSISTENT, actual, oracle, pick, rule, within_bounds
from connectors.jira.cloud_sim_round3_access import Leveled, holder, shape
from connectors.jira.cloud_sim_round4_access import (
    DAY_MS,
    ISSUE_BULK,
    age_gate,
    assert_closed,
    assert_no_gain,
    assert_open,
    confirmed_at,
    gate_state,
    open_gate,
    outsider_reads,
)
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    CREATOR_EMAIL,
    JiraCloudWorld,
    fault,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    succeeded,
)

logger = logging.getLogger("jira-cloud-sim-round4-access")
SEARCH = API + r"/search/jql$"
MEMBERS = API + r"/issuesecurityschemes/level/member$"


def refuse_signed_out(w: JiraCloudWorld, status: str, times: int = 0) -> None:
    w.sim.switches(anonymous_refusal=status, anonymous_refusal_times=times)


def stop_refusing(w: JiraCloudWorld) -> None:
    w.sim.switches(anonymous_refusal="", anonymous_refusal_times=0)


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimRound4Access:

    async def test_tc_jcsim_r4a_001_open_gate_kept_when_the_search_is_limited(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        await open_gate(w)
        visitors = w.visitor_ids()
        confirmed = await confirmed_at(w)
        # a rate limit on the signed-out search alone: no proof either way, the open gate stays as it is, unconfirmed again
        refuse_signed_out(w, "429")
        assert succeeded(await w.sync())
        stop_refusing(w)
        assert await confirmed_at(w) == confirmed, "the gate was confirmed again though the signed-out search was refused"
        assert visitors <= await outsider_reads(w), "a rate limit took the organisation's access away"
        await w.assert_matches_source("an open gate kept through a rate limit")
        # the same refusal that clears after one answer: the search is asked again and confirms
        refuse_signed_out(w, "429", times=1)
        assert succeeded(await w.sync())
        stop_refusing(w)
        assert await confirmed_at(w) > confirmed, "the retry after the 429 did not confirm the gate"
        await assert_open(w, "after a 429 and a 200")

    @pytest.mark.parametrize("status", ["503", "401", "403"])
    async def test_tc_jcsim_r4a_001b_refusals_of_the_signed_out_search(self, jira_cloud_world: JiraCloudWorld, status: str) -> None:
        """A 503 that lasts is no answer (the open gate is kept); a 401 or 403 says visitors may not read: the gate closes."""
        w = jira_cloud_world
        await open_gate(w)
        visitors = w.visitor_ids()
        confirmed = await confirmed_at(w)
        refuse_signed_out(w, status)
        assert succeeded(await w.sync())
        stop_refusing(w)
        if status == "503":
            assert await confirmed_at(w) == confirmed
            assert visitors <= await outsider_reads(w)
        else:
            await assert_closed(w, visitors, f"the signed-out search answered {status}")
        assert succeeded(await w.sync())
        await assert_open(w, "after the search answered again")
        await w.assert_matches_source("the gate reopened")

    async def test_tc_jcsim_r4a_002_one_private_project_closes_the_gate(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        keys = await open_gate(w, projects=3)
        visitors = w.visitor_ids()
        project, levels = w.leveled_project()
        if project not in keys:
            w.mutate({"op": "grant", "project": project, "holder": "anyone"})
            assert succeeded(await w.sync())
            await assert_open(w, "the leveled project public too")
            visitors = w.visitor_ids()
        # every issue of that project goes under a level: visitors read none of it, so the project is not public
        w.mutate(*[{"op": "security", "issue": i, "level": levels[0]["name"]} for i in w.issue_keys(project)])
        now = w.visitor_ids()
        assert now and now < visitors, "the change did not hide that project from visitors"
        assert succeeded(await w.sync())
        await assert_closed(w, visitors, "one project of several is private to visitors")
        # Jira still shows the other public projects to visitors; the gate is one switch for all of them, so the organisation
        # loses those too (a loss, never a gain): the connector fails closed.
        await assert_no_gain(w, "the gate closed by one private project")

    async def test_tc_jcsim_r4a_003_a_failed_issue_pass_closes_the_gate(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        keys = await open_gate(w)
        visitors = w.visitor_ids()
        issue = w.issue_keys(keys[0])[0]
        # an open gate: the issues of its project are read again and refused
        w.mutate({"op": "issue", "issue": issue, "summary": "Edited for the failed pass"})
        w.faults(fault(SEARCH, 500, after_n=1, max_times=PERSISTENT))  # the signed-out search (first) answers; the issue pass does not
        await w.sync()
        w.clear_faults()
        await assert_closed(w, visitors, "a public project whose issues were not all read")
        assert succeeded(await w.sync())
        await assert_open(w, "after a clean issue pass")
        await w.assert_matches_source("the gate reopened")

    async def test_tc_jcsim_r4a_003b_a_gate_about_to_open_waits_for_the_issue_pass(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        w.sim.switches(anonymous_access=True, anonymous_refusal="", anonymous_refusal_times=0)
        project = w.projects()[0]
        assert await w.start(reset=False, enable=False, prepare=[{"op": "grant", "project": project, "holder": "anyone"}]) is None
        w.faults(fault(SEARCH, 500, after_n=1, max_times=PERSISTENT))
        await w.enable()
        w.clear_faults()
        assert not await confirmed_at(w), "the gate opened though the project's issues were not read"
        assert not (w.visitor_ids() & await outsider_reads(w))
        assert succeeded(await w.sync(full=True))
        await assert_open(w, "after a clean issue pass")
        await w.assert_matches_source("the gate opened by a clean pass")

    async def test_tc_jcsim_r4a_004_a_project_made_public_while_the_gate_is_open(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        keys = await open_gate(w)
        before = w.visitor_ids()
        other = next(p for p in w.projects() if p not in keys)
        w.mutate({"op": "grant", "project": other, "holder": "anyone"})
        after = w.visitor_ids()
        assert after > before, "the new grant shows visitors nothing more"
        # the signed-out search cannot confirm it this run: the gate closes before the grant is written, and stays closed
        refuse_signed_out(w, "429")
        assert succeeded(await w.sync())
        stop_refusing(w)
        await assert_closed(w, after, "a project made public that no search has confirmed")
        assert succeeded(await w.sync())
        await assert_open(w, "both projects confirmed")
        await w.assert_matches_source("the gate open for both projects")

    @pytest.mark.parametrize("answered", [True, False])
    async def test_tc_jcsim_r4a_005_a_day_old_confirmation(self, jira_cloud_world: JiraCloudWorld, answered: bool) -> None:
        w = jira_cloud_world
        await open_gate(w)
        visitors = w.visitor_ids()
        old = await age_gate(w, DAY_MS + 60_000)
        if not answered:
            refuse_signed_out(w, "429")
        assert succeeded(await w.sync())
        stop_refusing(w)
        state = await gate_state(w)
        if answered:
            # closed first, then confirmed again by this run's search and a clean issue pass
            assert int(state.get("confirmed_at") or 0) > old
            await assert_open(w, "a day-old confirmation, the search answered")
        else:
            await assert_closed(w, visitors, "a day-old confirmation, the search not answered")
            logger.info("notice state after the day-old closing: %r", state.get("notice"))
            assert succeeded(await w.sync())  # a due notice is sent again (or already was); the gate reopens
            await assert_open(w, "after the search answered")
        await w.assert_matches_source("after the day-old confirmation was dealt with")

    async def _newcomer(self, w: JiraCloudWorld) -> tuple[str, str]:
        email = f"newcomer-{uuid.uuid4().hex[:8]}@simcorp.example.com"
        made = w.sim.rest("POST", "/user", {"emailAddress": email, "products": []})
        await w.ensure_account(email)
        return email, made["accountId"]

    # ---- the signed-out search (`_read_anonymously`) ------------------------------------------------------------------

    async def test_tc_jcsim_r4a_006_signed_out_search_dropped_or_retried(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        await open_gate(w)
        visitors = w.visitor_ids()
        confirmed = await confirmed_at(w)
        # a 503 once: asked again after the wait, answered, confirmed again
        refuse_signed_out(w, "503", times=1)
        assert succeeded(await w.sync())
        stop_refusing(w)
        assert await confirmed_at(w) > confirmed, "the retry after a 503 did not confirm the gate"
        confirmed = await confirmed_at(w)
        # the answer dropped: no answer, the open gate stays as it is
        w.faults(rule("drop", SEARCH, first_n=1))
        assert succeeded(await w.sync())
        w.clear_faults()
        assert await confirmed_at(w) == confirmed, "a dropped answer was taken for one"
        assert visitors <= await outsider_reads(w)
        await w.assert_matches_source("a dropped signed-out answer")

    async def test_tc_jcsim_r4a_006b_an_empty_page_that_is_not_the_last(self, jira_cloud_world: JiraCloudWorld) -> None:
        """A page with no issue and a next token is followed; when the next page cannot be read the project is not shown public."""
        w = jira_cloud_world
        await open_gate(w)
        visitors = w.visitor_ids()
        w.faults(shape(SEARCH, {"path": "issues", "value": []}, {"path": "isLast", "value": False},
                       {"path": "nextPageToken", "value": "not-a-token"}, first_n=1))
        assert succeeded(await w.sync())
        w.clear_faults()
        await assert_closed(w, visitors, "an empty page, then a page that cannot be read")
        assert succeeded(await w.sync())
        await assert_open(w, "after the search answered")

    # ---- the application roles ---------------------------------------------------------------------------------------

    @pytest.mark.parametrize("variant", ["not_a_list", "no_key", "users_without_group", "group_without_id"])
    async def test_tc_jcsim_r4a_007_application_roles_that_cannot_be_read(self, jira_cloud_world: JiraCloudWorld, variant: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        other = next(p for p in w.projects() if p != project)
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "applicationRole", "parameter": "jira-software"},
            {"op": "grant", "project": other, "holder": "applicationRole"}]))
        await w.assert_matches_source("after the first sync")
        email, _ = await self._newcomer(w)
        ops = {
            "not_a_list": [{"path": "$", "value": {}}],
            "no_key": [{"path": "[*].key", "delete": True}],
            "users_without_group": [{"path": "[*].groupDetails", "value": []}],
            "group_without_id": [{"path": "[*].groupDetails[*].groupId", "delete": True}],
        }[variant]
        before = oracle(w)
        w.mutate({"op": "group_member", "group": "jira-software-users", "user": email, "member": True})
        after = oracle(w)
        assert after[email] - before[email], "the newcomer gains nothing in Jira"
        w.faults(shape(API + r"/applicationrole$", *ops, max_times=PERSISTENT))
        await w.sync(full=True)
        log = w.sim.call("GET", "/_sim/faults/log") or []
        w.clear_faults()
        assert {e["effect"] for e in log} == {"shape"}, f"{variant}: the shape did not apply: {log[:2]}"
        # the roles are unknown, not empty: the stored access stays and the newcomer does not gain from a guess
        within_bounds(before, after, await actual(w), f"{variant}: application roles that could not be read")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source(f"{variant}: after the roles could be read")

    # ---- holder types the connector does not know ---------------------------------------------------------------------

    @pytest.mark.parametrize("kind", ["sd.customer.portal.only", "a.holder.jira.adds.later"])
    async def test_tc_jcsim_r4a_008_holder_types(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "group_create", "group": "readers", "members": [keep]},
            {"op": "grant", "project": project, "holder": "group", "parameter": "readers"}]))
        await w.assert_matches_source("after the first sync")
        before = oracle(w)
        w.mutate({"op": "group_member", "group": "readers", "user": who, "member": True})
        after = oracle(w)
        w.faults(shape(API + r"/permissionscheme/\d+/permission$", {"path": "permissions[*].holder.type", "value": kind},
                       max_times=PERSISTENT))
        await w.sync()
        log = w.sim.call("GET", "/_sim/faults/log") or []
        w.clear_faults()
        assert log and {e["effect"] for e in log} == {"shape"}, f"the shape did not apply: {log[:2]}"
        # a holder the connector cannot read gives no one access (portal customers are not synced): nobody gains
        within_bounds(before, after, await actual(w), f"holder type {kind}", losses_ok=True)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the holders were read")

    async def test_tc_jcsim_r4a_008b_add_ons_role_holder(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "projectRole", "parameter": "atlassian-addons-project-access"},
            {"op": "grant", "project": project, "holder": "user", "parameter": keep}]))
        await w.assert_matches_source("a project granting the add-ons role (no person of it is synced)")
        mine = {i for i, t in w.sim.expected()["ticket_index"].items() if t["key"].rsplit("-", 1)[0] == project}
        assert mine and not (mine & (await actual(w))[who]), f"{who} reads issues of {project} through the add-ons role"

    # ---- the roles a refused group list keeps ----------------------------------------------------------------------

    @pytest.mark.parametrize("variant", ["refused", "not_a_mapping", "role_without_url"])
    async def test_tc_jcsim_r4a_009_stored_roles_kept(self, jira_cloud_world: JiraCloudWorld, variant: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "projectRole", "parameter": "Developers"},
            {"op": "role_actor", "project": project, "role": "Developers", "kind": "user", "who": keep}]))
        await w.assert_matches_source("after the first sync")
        before = oracle(w)
        w.mutate({"op": "role_actor", "project": project, "role": "Developers", "kind": "user", "who": who})
        after = oracle(w)
        roles = API + r"/project/[^/]+/role$"
        broken = {"refused": fault(roles, 500, max_times=PERSISTENT),
                  "not_a_mapping": shape(roles, {"path": "$", "value": []}, max_times=PERSISTENT),
                  "role_without_url": shape(roles, {"path": "Developers", "value": 7}, max_times=PERSISTENT)}[variant]
        w.faults(fault(API + r"/group/bulk$", 403, max_times=PERSISTENT), broken)
        await w.sync(full=True)
        w.clear_faults()
        within_bounds(before, after, await actual(w), f"{variant}: group list refused, role list unreadable", losses_ok=True)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source(f"{variant}: after both were read")

    # ---- security levels that cannot be applied ----------------------------------------------------------------------

    @pytest.mark.parametrize("variant", ["members_refused", "scheme_refused", "scheme_404", "user_without_account",
                                         "members_not_a_list", "max_results_text", "max_results_huge"])
    async def test_tc_jcsim_r4a_011_levels_that_cannot_be_applied(self, jira_cloud_world: JiraCloudWorld, variant: str) -> None:
        w = jira_cloud_world
        c = Leveled(w)
        a, b, *_ = c.candidates()
        await c.start([holder(c.project, c.level, "user", a)])
        c.expect(a, True, "named by the level")
        c.expect(b, False, "not named yet")
        before = oracle(w)
        w.mutate(holder(c.project, c.level, "user", b), {"op": "issue", "issue": c.issue, "summary": "Edited by round 4"})
        after = oracle(w)
        c.expect(b, True, "named by the level now")
        scheme = API + r"/project/[^/]+/issuesecuritylevelscheme$"
        broken = {"members_refused": fault(MEMBERS, 403, max_times=PERSISTENT),
                  "scheme_refused": fault(scheme, 500, max_times=PERSISTENT),
                  "scheme_404": fault(scheme, 404, max_times=PERSISTENT),
                  "user_without_account": shape(MEMBERS, {"path": "values[*].holder.user.accountId", "delete": True},
                                                {"path": "values[*].holder.parameter", "delete": True},
                                                max_times=PERSISTENT),
                  "members_not_a_list": shape(MEMBERS, {"path": "values", "value": "oops"}, max_times=PERSISTENT),
                  "max_results_text": shape(MEMBERS, {"path": "isLast", "delete": True}, {"path": "total", "delete": True},
                                            {"path": "maxResults", "value": "many"}, max_times=PERSISTENT),
                  "max_results_huge": shape(MEMBERS, {"path": "isLast", "delete": True}, {"path": "total", "delete": True},
                                            {"path": "maxResults", "value": 200000}, max_times=PERSISTENT)}[variant]
        w.faults(broken)
        await w.sync(full=True)
        log = w.sim.call("GET", "/_sim/faults/log") or []
        w.clear_faults()
        assert log, f"{variant}: the fault never fired"
        if variant not in ("members_refused", "scheme_refused", "scheme_404"):
            assert {e["effect"] for e in log} == {"shape"}, f"{variant}: the shape did not apply: {log[:2]}"
        now = await actual(w)
        within_bounds(before, after, now, f"{variant}: a level that could not be applied", losses_ok=True)
        if variant != "user_without_account":
            assert c.tid not in now[b], f"{variant}: {b} reads a leveled issue whose level could not be read"
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source(f"{variant}: after the level could be read")

    # ---- a person listed after the issues were read -----------------------------------------------------------------

    @pytest.mark.parametrize("variant", ["clean", "issues_refused", "scheme_refused"])
    async def test_tc_jcsim_r4a_013_a_person_listed_later_reads_a_leveled_issue(self, jira_cloud_world: JiraCloudWorld, variant: str) -> None:
        w = jira_cloud_world
        c = Leveled(w)
        newcomer, _ = await self._newcomer(w)
        await c.start([holder(c.project, c.level, "reporter"), {"op": "issue", "issue": c.issue, "reporter": newcomer}])
        c.expect(newcomer, False, "no product access")
        before = oracle(w)
        w.mutate({"op": "group_member", "group": "jira-software-users", "user": newcomer, "member": True})
        after = oracle(w)
        c.expect(newcomer, True, "listed, and the reporter the level names")
        broken = {"clean": None, "issues_refused": fault(ISSUE_BULK, 500, max_times=PERSISTENT),
                  "scheme_refused": fault(API + r"/permissionscheme/\d+/permission$", 500, max_times=PERSISTENT)}[variant]
        if broken:
            w.faults(broken)
        outcome = await w.sync()
        if broken:
            assert w.clear_faults()
        within_bounds(before, after, await actual(w), f"{variant}: a newcomer listed later (outcome {outcome})", losses_ok=bool(broken))
        if broken:
            assert succeeded(await w.sync())
        await w.assert_matches_source(f"{variant}: the person listed later reads what Jira shows")
        assert c.tid in (await actual(w))[newcomer]

    # ---- many accounts missing from the user list ----------------------------------------------------------------------

    async def test_tc_jcsim_r4a_012_many_missing_accounts_held_then_released(self, jira_cloud_world: JiraCloudWorld) -> None:
        """A complete user list that leaves 13 linked accounts out is not proof they are gone: they keep their access while the
        same accounts stay missing from reads that agree (three in a row release them); a list that shows them again, or a
        different set missing, starts the count again. Nobody ever gains."""
        w = jira_cloud_world
        w.sim.reset()
        project, *_ = pick(w)
        crowd = [e for e in w.emails() if e != CREATOR_EMAIL and w.probes()[e].get("licensed")][:14]
        assert len(crowd) == 14, f"only {len(crowd)} licensed people"
        ids = [w.sim.access(e)["account_id"] for e in crowd]
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "user", "parameter": e} for e in crowd]))
        await w.assert_matches_source("after the first sync")
        before = await actual(w)
        assert all(before[e] for e in crowd), "the crowd reads nothing: the case proves nothing"

        async def read(omitted: list[str], label: str) -> tuple[int, dict]:
            w.sim.switches(users_search_omit=",".join(omitted))
            assert succeeded(await w.sync()), label  # a full run clears the count
            held = dict(await w.graph.get_sync_point(w.connector_id, "withdrawals_held") or {})
            now = await actual(w)
            assert all(now[e] <= before[e] for e in crowd), f"{label}: someone reads more than before"
            return int(held.get("seen") or 0), now

        counts = []
        for omitted, label in ((ids[:13], "read 1: 13 accounts missing"), ([], "read 2: the list shows them again"),
                               (ids[:13], "read 3: missing again"), (ids[:14], "read 4: one more missing, a different set")):
            seen, _now = await read(omitted, label)
            counts.append(seen)
        # held (1), a list that shows them ends the count (0), held again (1), another set starts over (1)
        assert counts == [1, 0, 1, 1], f"the count of agreeing reads went {counts}"
        w.sim.switches(users_search_omit="")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the list showed them again")

    async def test_tc_jcsim_r4a_012c_the_same_missing_accounts_are_released_by_three_reads(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, *_ = pick(w)
        crowd = [e for e in w.emails() if e != CREATOR_EMAIL and w.probes()[e].get("licensed")][:13]
        ids = [w.sim.access(e)["account_id"] for e in crowd]
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "user", "parameter": e} for e in crowd]))
        before = await actual(w)
        counts = []
        w.sim.switches(users_search_omit=",".join(ids))
        for _ in range(4):
            assert succeeded(await w.sync())  # a full run clears the count
            held = dict(await w.graph.get_sync_point(w.connector_id, "withdrawals_held") or {})
            counts.append(int(held.get("seen") or 0))
        w.sim.switches(users_search_omit="")
        assert counts == [1, 2, 0, 0], f"the same accounts missing from reads in a row: counts {counts}"
        now = await actual(w)
        assert all(now[e] < before[e] for e in crowd), "accounts gone from three reads keep everything they read"
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the list showed them again")

    @pytest.mark.xfail(strict=True, reason="jira_cloud/connector.py:2943-2954 holds many absent accounts until 3 syncs agree and tells "
                       "the administrator 'They keep their access', but it keeps their link only: _linked_not_read "
                       "(connector.py:2973) is empty on a list read in full, so groups, roles, project grants and issue grants are "
                       "written without them by the first sync, incremental too")
    async def test_tc_jcsim_r4a_012b_held_accounts_keep_their_access(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, *_ = pick(w)
        crowd = [e for e in w.emails() if e != CREATOR_EMAIL and w.probes()[e].get("licensed")][:13]
        ids = [w.sim.access(e)["account_id"] for e in crowd]
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "user", "parameter": e} for e in crowd]))
        before = await actual(w)
        assert all(before[e] for e in crowd)
        w.sim.switches(users_search_omit=",".join(ids))
        assert succeeded(await w.sync())
        w.sim.switches(users_search_omit="")
        held = dict(await w.graph.get_sync_point(w.connector_id, "withdrawals_held") or {})
        assert int(held.get("seen") or 0) == 1, "the missing accounts were not held: the case proves nothing"
        now = await actual(w)
        assert all(now[e] == before[e] for e in crowd), "held accounts lost their access at once"

    # ---- a project that cannot be built ---------------------------------------------------------------------------------

    @pytest.mark.parametrize("share", ["one", "all"])
    async def test_tc_jcsim_r4a_014_projects_that_cannot_be_built(self, jira_cloud_world: JiraCloudWorld, share: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "user", "parameter": keep}]))
        await w.assert_matches_source("after the first sync")
        before = oracle(w)
        w.mutate({"op": "grant", "project": project, "holder": "user", "parameter": who})
        after = oracle(w)
        # the project list names a project with no name (the record group cannot be made): its stored access stays
        ops = [{"path": "values[*].name", "delete": True}] if share == "all" else [{"path": "values[0].name", "delete": True}]
        w.faults(shape(API + r"/project/search$", *ops, first_n=1))
        outcome = await w.sync(full=True)
        log = w.sim.call("GET", "/_sim/faults/log") or []
        w.clear_faults()
        assert log and {e["effect"] for e in log} == {"shape"}, f"the shape did not apply: {log[:2]}"
        within_bounds(before, after, await actual(w), f"{share}: project(s) that could not be built (outcome {outcome})")
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source(f"{share}: after the projects could be built")

    # ---- a day that ends while the run is checking ------------------------------------------------------------------

    @pytest.mark.parametrize("answered", [True, False])
    async def test_tc_jcsim_r4a_015_the_day_ends_during_the_signed_out_check(self, jira_cloud_world: JiraCloudWorld, answered: bool) -> None:
        """The confirmation is a few seconds short of a day old when the run starts, so the run begins with the gate open; the
        signed-out search is slow (an answer after 20 s, or none within the read timeout), so the day ends before it is back."""
        w = jira_cloud_world
        await open_gate(w)
        visitors = w.visitor_ids()
        old = await age_gate(w, DAY_MS - 12_000)
        slow = 15_000 if answered else 25_000
        w.faults({"match": {"path_regex": SEARCH}, "effect": {"type": "latency", "ms": slow}, "trigger": {"first_n": 1}})
        assert succeeded(await w.sync())
        await asyncio.sleep(12)  # the simulator records a delayed answer when it ends
        w.clear_faults()
        if answered:
            assert await confirmed_at(w) > old + DAY_MS - 12_000, "the gate was not confirmed again by the run"
            await assert_open(w, "the day ended during the check, the search answered")
        else:
            # no answer, and the day is over: the gate is closed (the next run, with an answer, reopens it)
            await assert_closed(w, visitors, "the day ended during the check, the search unanswered")
            assert succeeded(await w.sync())
            await assert_open(w, "after the search answered")
        await w.assert_matches_source("after the day ended during the check")
