# pyright: ignore-file

"""
Jira Cloud on the simulator: round 3, the access code no earlier case reached
==============================================================================

  TC-JCSIM-R3A-001  A level held by user pickers (single and multiple): the picked people read the issue, a change of the
                    pickers, a holder removed, a picked person deactivated
  TC-JCSIM-R3A-002  A level held by group pickers (single and multiple): the picked groups' members read the issue, a group's
                    members changed, a picker moved to another group
  TC-JCSIM-R3A-003  A level held by an application role, and by a reporter and an assignee who are then deactivated
  TC-JCSIM-R3A-004  A picker holder with the level members refused at the first run, then read
  TC-JCSIM-R3A-005  The level members paged without ``isLast``, without ``total``, and with answers that cannot be followed

The organisation gate cases (expiry of a day-old confirmation, a refused signed-out search) are in jira_cloud_sim_round4_access_test.py.

Vendor pages: the level holders ``userCustomField`` and ``groupCustomField`` ("Grant for a user selected in the specified custom
field", https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-permission-schemes/; "anyone listed in a user or
group picker field", https://support.atlassian.com/jira-cloud-administration/docs/grant-users-access-to-security-levels-in-a-work-item-security-scheme/).
"""

import logging

import pytest
from connectors.jira.cloud_sim_access import API, PERSISTENT, actual, oracle, pick, refused, rule, within_bounds
from connectors.jira.cloud_sim_round3_access import (
    GROUP_PICKERS,
    USER_PICKERS,
    Leveled,
    holder,
    picker,
    shape,
)
from connectors.jira.cloud_sim_world import (  # noqa: F401 - fixtures of the jira_cloud_sim suite
    JiraCloudWorld,
    jira_cloud_sim,
    jira_cloud_sim_people,
    jira_cloud_world,
    succeeded,
)

logger = logging.getLogger("jira-cloud-sim-round3-access")
MEMBERS = API + r"/issuesecurityschemes/level/member$"


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudSimRound3Access:

    async def test_tc_jcsim_r3a_001_user_pickers(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        c = Leveled(w)
        single, multi = USER_PICKERS
        a, b, d, e = c.candidates()[:4]
        await c.start([holder(c.project, c.level, "userCustomField", single), holder(c.project, c.level, "userCustomField", multi),
                       picker(c.issue, single, a), picker(c.issue, multi, [b, d])])
        for person in (a, b, d):
            c.expect(person, True, "picked")
        c.expect(e, False, "not picked")
        # the pickers change: the issue is edited, so the next incremental run reads it again
        w.mutate(picker(c.issue, single, e), picker(c.issue, multi, [b]))
        c.expect(a, False, "no longer picked")
        c.expect(e, True, "picked now")
        await c.sync_and_compare("after the pickers changed")
        # a holder leaves the level: nobody reads through that picker any more, and the issue was not edited
        w.mutate(holder(c.project, c.level, "userCustomField", multi, present=False))
        c.expect(b, False, "its picker is no longer a holder")
        await c.sync_and_compare("after the multiple picker left the level")
        # a picked person is deactivated, then given back
        w.mutate({"op": "user_active", "user": e, "active": False})
        c.expect(e, False, "deactivated")
        await c.sync_and_compare("after the picked person was deactivated")
        w.mutate({"op": "user_active", "user": e, "active": True})
        c.expect(e, True, "given back")
        await c.sync_and_compare("after the picked person was given back", full=True)
        w.mutate(picker(c.issue, single, None))
        c.expect(e, False, "picker cleared")
        await c.sync_and_compare("after the picker was cleared")

    async def test_tc_jcsim_r3a_002_group_pickers(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        c = Leveled(w)
        single, multi = GROUP_PICKERS
        a, b, d, e = c.candidates()[:4]
        prepare = [{"op": "group_create", "group": "r3-one", "members": [a]}, {"op": "group_create", "group": "r3-two", "members": [b]},
                   holder(c.project, c.level, "groupCustomField", single), holder(c.project, c.level, "groupCustomField", multi),
                   picker(c.issue, single, "r3-one"), picker(c.issue, multi, ["r3-two"])]
        await c.start(prepare)
        for person in (a, b):
            c.expect(person, True, "in a picked group")
        c.expect(d, False, "in no picked group")
        # a member joins a picked group: no issue is edited, the group's members are read again
        w.mutate({"op": "group_member", "group": "r3-two", "user": d, "member": True})
        c.expect(d, True, "joined")
        await c.sync_and_compare("after a member joined a picked group")
        # the single picker moves to the other group
        w.mutate(picker(c.issue, single, "r3-two"))
        c.expect(a, False, "its group is no longer picked")
        await c.sync_and_compare("after the single picker moved to the other group")
        w.mutate({"op": "group_member", "group": "r3-two", "user": b, "member": False},
                 holder(c.project, c.level, "groupCustomField", multi, present=False))
        c.expect(b, False, "left the group and the picker left the level")
        c.expect(d, True, "still in the group the single picker names")
        await c.sync_and_compare("after a member left and the multiple picker left the level", full=True)
        w.mutate(picker(c.issue, single, None))
        c.expect(d, False, "cleared")
        await c.sync_and_compare("after the picker was cleared")

    async def test_tc_jcsim_r3a_003_application_role_and_deactivated_reporter_assignee(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        c = Leveled(w)
        a, b, d, e = c.candidates()[:4]
        await c.start([holder(c.project, c.level, "reporter"), holder(c.project, c.level, "assignee"),
                       {"op": "issue", "issue": c.issue, "reporter": a, "assignee": b}])
        c.expect(a, True, "reporter")
        c.expect(b, True, "assignee")
        # the level also admits an application role, by the API (a bare holder is not a level holder)
        w.sim.rest("PUT", f"/issuesecurityschemes/{c.scheme}/level/{c.level_id}/member",
                   {"members": [{"type": "applicationRole", "parameter": "jira-software"}]})
        await c.sync_and_compare("after the level admitted an application role")
        w.mutate({"op": "group_member", "group": "jira-software-users", "user": d, "member": False})
        await c.sync_and_compare("after a person left the role's group", full=True)
        # the reporter and the assignee are deactivated: they read nothing in Jira, and the level grants them nothing
        w.mutate({"op": "user_active", "user": a, "active": False}, {"op": "user_active", "user": b, "active": False})
        c.expect(a, False, "deactivated reporter")
        c.expect(b, False, "deactivated assignee")
        await c.sync_and_compare("after the reporter and the assignee were deactivated")
        w.mutate({"op": "user_active", "user": a, "active": True}, {"op": "user_active", "user": b, "active": True})
        await c.sync_and_compare("after they were given back", full=True)

    async def test_tc_jcsim_r3a_004_picker_holder_members_refused_first(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        c = Leveled(w)
        single, multi = USER_PICKERS
        a, b = c.candidates()[:2]
        assert not await w.start(reset=False, enable=False, prepare=[
            holder(c.project, c.level, "userCustomField", single), holder(c.project, c.level, "userCustomField", multi),
            picker(c.issue, single, a), picker(c.issue, multi, [b])])
        await refused(w, [], rule("403", MEMBERS, None, max_times=PERSISTENT), "picker holders, level members refused at the first run",
                      full=True, before={e: set() for e in w.people}, first=True)

    @pytest.mark.parametrize("variant", ["no_islast", "no_islast_no_total", "values_not_a_list", "max_results_text", "max_results_huge"])
    async def test_tc_jcsim_r3a_005_member_pages(self, jira_cloud_world: JiraCloudWorld, variant: str) -> None:
        w = jira_cloud_world
        c = Leveled(w)
        single, multi = USER_PICKERS
        a, b, d = c.candidates()[:3]
        await c.start([holder(c.project, c.level, "userCustomField", single), holder(c.project, c.level, "userCustomField", multi),
                       picker(c.issue, single, a), picker(c.issue, multi, [b])])
        before = oracle(w)
        w.mutate(picker(c.issue, single, d), holder(c.project, c.level, "userCustomField", multi, present=False))
        after = oracle(w)
        no_islast = {"path": "$.isLast", "delete": True}
        no_total = {"path": "$.total", "delete": True}
        ops, benign = {
            "no_islast": ([no_islast], True),                 # a server that pages with ``total``: complete when reached
            "no_islast_no_total": ([no_islast, no_total], True),  # a page past the end is empty: complete
            "values_not_a_list": ([{"path": "$.values", "value": "oops"}], False),
            "max_results_text": ([no_islast, no_total, {"path": "$.maxResults", "value": "many"}], False),
            "max_results_huge": ([no_islast, no_total, {"path": "$.maxResults", "value": 200000}], False),
        }[variant]
        w.faults(shape(MEMBERS, *ops, max_times=PERSISTENT))
        outcome = await w.sync(full=True)
        log = w.sim.call("GET", "/_sim/faults/log") or []
        w.clear_faults()
        assert log and {e["effect"] for e in log} == {"shape"}, f"{variant}: the shape did not apply: {log[:2]}"
        if benign:
            assert succeeded(outcome), f"{variant}: the members were read in full, yet the run ended {outcome}"
            await w.assert_matches_source(f"{variant}: the faulted run")
        else:
            # the members could not be followed: the leveled issue stays as it was or closed, nobody gains
            within_bounds(before, after, await actual(w), f"{variant} (run {outcome})", losses_ok=True)
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source(f"{variant}: after the members could be read")

    async def test_tc_jcsim_r3a_009_project_lead_changes(self, jira_cloud_world: JiraCloudWorld) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, a, b = pick(w)
        assert succeeded(await w.start(reset=False, prepare=[{"op": "grant", "project": project, "holder": "projectLead"},
                                                              {"op": "project", "project": project, "lead": a}]))
        await w.assert_matches_source("lead a")
        for label, ops in (("lead b", [{"op": "project", "project": project, "lead": b}]),
                           ("lead b deactivated", [{"op": "user_active", "user": b, "active": False}]),
                           ("lead b back, a again", [{"op": "user_active", "user": b, "active": True},
                                                     {"op": "project", "project": project, "lead": a}])):
            w.mutate(*ops)
            assert succeeded(await w.sync(full=label.endswith("again")))
            await w.assert_matches_source(label)

    @pytest.mark.parametrize("kind", ["404", "malformed", "empty"])
    async def test_tc_jcsim_r3a_010_application_roles_unreadable(self, jira_cloud_world: JiraCloudWorld, kind: str) -> None:
        w = jira_cloud_world
        w.sim.reset()
        project, who, keep = pick(w)
        assert succeeded(await w.start(reset=False, prepare=[
            {"op": "grant", "project": project, "holder": "applicationRole", "parameter": "jira-software"}]))
        await w.assert_matches_source("after the first sync")
        licensed = next(e for e in w.emails() if w.probes()[e]["licensed"] and e != who)
        ops = [{"op": "group_member", "group": "jira-software-users", "user": licensed, "member": False}]
        await refused(w, ops, rule(kind, API + r"/applicationrole$", [], max_times=PERSISTENT), f"application roles {kind}", full=True,
                      losses_ok=True)

    @pytest.mark.parametrize("by", ["reporter", "assignee"])
    async def test_tc_jcsim_r3a_011_open_issue_named_by_a_browse_holder_and_a_level(self, jira_cloud_world: JiraCloudWorld, by: str) -> None:
        """Browse is held through the issue's ``by``: an issue under a level is read by that person only when the level admits
        them; once the level is cleared, the project's Browse holder names them again."""
        w = jira_cloud_world
        c = Leveled(w)
        a, b, d = c.candidates()[:3]
        await c.start([{"op": "grant", "project": c.project, "holder": by}, holder(c.project, c.level, "user", a),
                       {"op": "issue", "issue": c.issue, by: b}])
        await c.sync_and_compare("a level that admits another person than the one the Browse holder names", full=True)
        w.mutate({"op": "security", "issue": c.issue, "level": None})
        await c.sync_and_compare("after the level was cleared")
        w.mutate({"op": "security", "issue": c.issue, "level": c.level}, {"op": "issue", "issue": c.issue, by: d})
        await c.sync_and_compare("after the issue went back under the level with another " + by, full=True)
