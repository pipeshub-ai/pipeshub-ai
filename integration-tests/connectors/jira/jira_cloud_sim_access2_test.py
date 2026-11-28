# pyright: ignore-file

"""
Jira Cloud on the simulator: every read of access, refused
==========================================================

For each read the connector makes to learn who may read what (users, hidden e-mails, groups, group members, application
roles, permission schemes, project roles, security levels and their holders), a change of access is made in Jira and one run
reads it with that read refused, in each way Jira can refuse (401, 403, 404, 429, 500, a dropped connection, a cut-short
answer, an empty one). The oracle (cloud_sim_access.py) bounds what each person may read after that run: nobody reads more
than Jira shows them before or after the change, nobody loses what Jira shows them both before and after it; the fault
must fire; the next clean run equals Jira. Changes go both ways (access taken away, then given), runs alternate between
full and incremental.

  TC-JCSIM-ACC2-001  Group read refused (group list, group members): a person leaves a group a scheme names
  TC-JCSIM-ACC2-002  Application roles refused / group members of the licence group refused: product access taken away
  TC-JCSIM-ACC2-003  Permission scheme refused (the project's scheme, its grants): a user holder removed
  TC-JCSIM-ACC2-004  Project roles refused (role list, one role): an actor removed from a role a scheme names
  TC-JCSIM-ACC2-005  Issue security refused (project's scheme, level members): a level holder removed
  TC-JCSIM-ACC2-006  User list refused: an account deactivated
  TC-JCSIM-ACC2-007  Hidden e-mail lookup refused at the first sync, then at a later one
  TC-JCSIM-ACC2-008  Every read refused at the first sync: nobody reads more than Jira shows
  TC-JCSIM-ACC2-009  The scheme of one project refused: only that project keeps what it stored
  TC-JCSIM-ACC2-011  User list refused outright (401, 403): who loses what, and the next clean run restores it
  TC-JCSIM-ACC2-010  Application roles refused while a scheme names the bare application role
"""

import logging
import os
from collections.abc import Callable
from typing import Any

import pytest
from connectors.jira.cloud_sim_access import API, KINDS, PAGE_EMPTY, PERSISTENT, actual, oracle, pick, refused, rule
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

ACTIVE_KINDS = tuple(os.getenv("JIRA_CLOUD_SIM_ACC2_KINDS", "403,500,malformed,empty").split(","))

# name -> (path regex, the answer for "empty" or None, the refusals after which access may be taken away: the read's own
# fail-closed rule, or a 200 that says nothing, which no connector can tell from the truth). Stored records are never lost.
Endpoints = dict[str, tuple]
logger = logging.getLogger("jira-cloud-sim-access2")


async def matrix(w: JiraCloudWorld, toggle: Callable[[bool], list[dict[str, Any]]], endpoints: Endpoints, label: str,
                 kinds: tuple[str, ...] = ACTIVE_KINDS) -> None:
    """Each (read, refusal): the change flips (access taken away, then given), the run alternates full / incremental."""
    i = 0
    window: list[dict[str, set[str]]] = []
    for name, (rx, empty, *closes) in endpoints.items():
        for kind in kinds:
            if kind == "empty" and empty is None:
                continue
            i += 1
            loses = kind == "empty" or kind in (closes[0] if closes else ())
            # a clean run after every third refused run (the bounds hold over every state Jira had since the last one)
            await refused(w, toggle(i % 2 == 0), rule(kind, rx, empty, max_times=PERSISTENT), f"{label}: {name} {kind}",
                          full=i % 6 < 3, clean=i % 3 == 0 or loses, window=window, losses_ok=loses)
    if window:
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source(f"{label}: after the last refusal")


async def begin(w: JiraCloudWorld, prepare: Callable[[str, str, str], list[dict[str, Any]]]) -> tuple[str, str, str]:
    w.sim.reset()
    project, who, keep = pick(w)
    assert succeeded(await w.start(prepare=prepare(project, who, keep), reset=False))
    await w.assert_matches_source("after the first sync")
    return project, who, keep


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimAccessRefused:

    async def test_tc_jcsim_acc2_001_groups(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project, who, keep = await begin(w, lambda p, a, b: [
            {"op": "group_create", "group": "rg", "members": [a, b]},
            {"op": "grant", "project": p, "holder": "group", "parameter": "rg"}])
        assert who in w.project_readers(project) and keep in w.project_readers(project)
        await matrix(w, lambda on: [{"op": "group_member", "group": "rg", "user": who, "member": on}], {
            "group list": (API + r"/group/bulk$", PAGE_EMPTY),
            "group members": (API + r"/group/member$", PAGE_EMPTY)}, "group")

    async def test_tc_jcsim_acc2_002_product_access(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        assert succeeded(await w.start(reset=False))
        who = next(e for e in w.emails() if w.probes()[e]["licensed"] and w.probes()[e]["jira_ids"] and e != CREATOR_EMAIL)
        groups = ("jira-software-users", "jira-servicemanagement-users", "jira-product-discovery-users")

        def toggle(on: bool) -> list[dict[str, Any]]:
            return [{"op": "group_member", "group": g, "user": who, "member": on} for g in groups]

        await matrix(w, toggle, {
            "application roles": (API + r"/applicationrole$", None, ("403",)),
            "licence group members": (API + r"/group/member$", PAGE_EMPTY)}, "product access")

    async def test_tc_jcsim_acc2_003_permission_scheme(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project, who, keep = await begin(w, lambda p, a, b: [
            {"op": "grant", "project": p, "holder": "user", "parameter": a},
            {"op": "grant", "project": p, "holder": "user", "parameter": b}])
        await matrix(w, lambda on: [{"op": "grant", "project": project, "holder": "user", "parameter": who, "present": on}], {
            "project's scheme": (API + r"/project/[^/]+/permissionscheme$", None, ("403",)),
            "scheme grants": (API + r"/permissionscheme/\d+/permission$", {"permissions": []}, ("403",))}, "scheme")

    async def test_tc_jcsim_acc2_004_project_roles(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project, who, keep = await begin(w, lambda p, a, b: [
            {"op": "grant", "project": p, "holder": "projectRole", "parameter": "Developers"},
            {"op": "role_actor", "project": p, "role": "Developers", "kind": "user", "who": a},
            {"op": "role_actor", "project": p, "role": "Developers", "kind": "user", "who": b}])
        await matrix(w, lambda on: [{"op": "role_actor", "project": project, "role": "Developers", "kind": "user",
                                     "who": who, "present": on}], {
            "role list": (API + r"/project/[^/]+/role$", {}),
            "one role": (API + r"/project/[^/]+/role/\d+$", {"actors": []})}, "role")

    async def test_tc_jcsim_acc2_005_security_levels(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, levels = w.leveled_project()
        level, issue = levels[0]["name"], w.issue_keys(project)[0]
        tid = next(t for t, v in w.sim.expected()["ticket_index"].items() if v["key"] == issue)
        inside = [e for e in sorted(w.readers_now()[tid]) if e != CREATOR_EMAIL and e in w.emails()]
        who, keep = inside[0], inside[1]
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "security", "issue": issue, "level": level},
            {"op": "level_member", "project": project, "level": level, "holder": "user", "parameter": who},
            {"op": "level_member", "project": project, "level": level, "holder": "user", "parameter": keep}]))
        await w.assert_matches_source("after the first sync")
        await matrix(w, lambda on: [{"op": "level_member", "project": project, "level": level, "holder": "user",
                                     "parameter": who, "present": on}], {
            "level scheme": (API + r"/project/[^/]+/issuesecuritylevelscheme$", {"levels": []}, ("403",)),
            "level members": (API + r"/issuesecurityschemes/level/member$", PAGE_EMPTY, ("403",))}, "level",
                     kinds=tuple(k for k in ACTIVE_KINDS if k in ("403", "empty")))

    async def test_tc_jcsim_acc2_006_user_list(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project, who, keep = await begin(w, lambda p, a, b: [
            {"op": "grant", "project": p, "holder": "user", "parameter": a},
            {"op": "grant", "project": p, "holder": "user", "parameter": b}])
        await matrix(w, lambda on: [{"op": "user_active", "user": who, "active": on}],
                     {"user list": (API + r"/users/search$", None)}, "users", kinds=("500", "malformed"))

    async def test_tc_jcsim_acc2_007_hidden_email_lookup(self, jira_cloud_world: JiraCloudWorld) -> None:
        """Addresses hidden at the first sync are looked up one by one: that lookup refused, the first run links nobody it
        could not name; the stored people keep their access when the lookup is refused later."""
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        hidden = [{"op": "user_email", "user": e, "visible": False} for e in (who, keep)]
        grants = [{"op": "grant", "project": project, "holder": "user", "parameter": who},
                  {"op": "grant", "project": project, "holder": "user", "parameter": keep}]
        for kind in ACTIVE_KINDS:
            if kind == "empty":
                continue
            await w.start(prepare=[*grants, *hidden], reset=True, enable=False)
            await refused(w, [], rule(kind, API + r"/user/search$", None, max_times=PERSISTENT),
                          f"hidden lookup {kind} at the first run", full=True, before={e: set() for e in w.people},
                          first=True)
            w.remove_connector()
        # later: stored people keep access when the lookup is refused, hidden again after being shown
        assert succeeded(await w.start(prepare=grants, reset=True))
        await matrix(w, lambda on: [{"op": "user_email", "user": who, "visible": on}], {
            "user lookup": (API + r"/user/search$", None)}, "hidden later", kinds=("403", "500", "malformed"))

    async def test_tc_jcsim_acc2_008_first_sync_refused(self, jira_cloud_world: JiraCloudWorld) -> None:
        """A first run whose read of who may read is refused gives nobody anything Jira does not show them."""
        w = jira_cloud_world
        eps: Endpoints = {
            "groups": (API + r"/group/bulk$", PAGE_EMPTY), "members": (API + r"/group/member$", PAGE_EMPTY),
            "application roles": (API + r"/applicationrole$", None, ("403",)), "users": (API + r"/users/search$", None),
            "schemes": (API + r"/permissionscheme/\d+/permission$", {"permissions": []}),
            "roles": (API + r"/project/[^/]+/role/\d+$", {"actors": []}),
            "levels": (API + r"/issuesecurityschemes/level/member$", PAGE_EMPTY)}
        for name, (rx, empty, *_closes) in eps.items():
            for kind in ("403",) if name != "users" else ("malformed",):
                w.sim.reset()
                await w.start(reset=False, enable=False)
                await refused(w, [], rule(kind, rx, empty, max_times=PERSISTENT), f"first run, {name} {kind}", full=True,
                              before={e: set() for e in w.people}, first=True)
                w.remove_connector()

    async def test_tc_jcsim_acc2_009_one_project_scheme_refused(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project, who, keep = await begin(w, lambda p, a, b: [
            {"op": "grant", "project": p, "holder": "user", "parameter": a},
            {"op": "grant", "project": p, "holder": "user", "parameter": b}])
        other = next(k for k in w.projects() if k != project and w.outsiders(k))
        stranger = w.outsiders(other)[0]
        i = 0
        for kind in ACTIVE_KINDS:
            if kind == "empty":
                continue
            ops = [{"op": "grant", "project": project, "holder": "user", "parameter": who, "present": i % 2 == 1},
                   {"op": "grant", "project": other, "holder": "user", "parameter": stranger, "present": i % 2 == 0}]
            await refused(w, ops, rule(kind, API + rf"/project/{project}/permissionscheme$", None, max_times=PERSISTENT),
                          f"scheme of {project} {kind}", full=i % 2 == 0, losses_ok=kind == "403")
            i += 1

    async def test_tc_jcsim_acc2_010_bare_application_role_with_roles_refused(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        project, who, keep = await begin(w, lambda p, a, b: [
            {"op": "grant", "project": p, "holder": "applicationRole", "parameter": "jira-software"}])
        licensed = next(e for e in w.emails() if w.probes()[e]["licensed"] and e != CREATOR_EMAIL)
        groups = ("jira-software-users", "jira-servicemanagement-users", "jira-product-discovery-users")
        await matrix(w, lambda on: [{"op": "group_member", "group": g, "user": licensed, "member": on} for g in groups], {
            "application roles": (API + r"/applicationrole$", None, ("403",))}, "application role holder")
        assert OUTSIDER_EMAIL in w.people and oracle(w)[OUTSIDER_EMAIL] == set()

    @pytest.mark.parametrize("status", [401, 403])
    async def test_tc_jcsim_acc2_011_user_list_refused_outright(self, jira_cloud_world: JiraCloudWorld, status: int) -> None:
        """With the user list refused outright, people are looked up by their PipesHub e-mail (find users), and the connector's
        own Jira account, which no PipesHub user has the e-mail of, is known from GET /myself: the PipesHub user who created
        the connector reads through it (``authenticatedAs``). Asserted: nobody, the creator included, loses or gains anything,
        and the next clean run equals Jira."""
        w = jira_cloud_world
        project, who, keep = await begin(w, lambda p, a, b: [
            {"op": "grant", "project": p, "holder": "user", "parameter": a}])
        before_actual = await actual(w)
        assert before_actual[CREATOR_EMAIL], "the creator reads nothing before the refusal"
        w.faults(fault(API + r"/users/search$", status, max_times=PERSISTENT))
        outcome = await w.sync(full=True)
        w.clear_faults()
        after = await actual(w)
        lost = {e: len(before_actual[e] - after[e]) for e in before_actual if before_actual[e] - after[e]}
        gained = {e: len(after[e] - before_actual[e]) for e in after if after[e] - before_actual[e]}
        assert not gained, f"the refused list ({status}, run {outcome}) gave someone access: {gained}"
        assert not lost, f"people lost access ({status}, run {outcome}): {lost}"
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the user list was read again")

    @pytest.mark.parametrize("kind", ["500", "malformed"])
    @pytest.mark.xfail(strict=True, raises=AssertionError,
                       reason="a 500 or a cut-short answer on the project's issue security scheme takes away reads that Jira "
                              "still shows (a 500: a sub-task of one person; a cut-short one: the leveled issues of everyone): "
                              "jira_cloud/connector.py _load_issue_security / _reindex_level_readers do not keep "
                              "the stored readers; no open pull request")
    async def test_tc_jcsim_acc2_012_level_scheme_unread_loses_readers(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, levels = w.leveled_project()
        level, issue = levels[0]["name"], w.issue_keys(project)[0]
        assert succeeded(await w.start(reset=False, prepare=[{"op": "security", "issue": issue, "level": level}]))
        await w.assert_matches_source("after the first sync")
        before = await actual(w)
        w.faults(rule(kind, API + r"/project/[^/]+/issuesecuritylevelscheme$", None, max_times=PERSISTENT))
        await w.sync(full=True)
        w.clear_faults()
        after = await actual(w)
        lost = {e: sorted(before[e] - after[e]) for e in before if before[e] - after[e]}
        assert not lost, f"a refused level scheme took away what Jira still shows: {lost}"
