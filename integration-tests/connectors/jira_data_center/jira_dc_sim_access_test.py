# pyright: ignore-file

"""
Jira Data Center on the simulator – who can find which issue
============================================================

Each test changes who may read something in the simulated Jira, syncs, and checks that every person reads in PipesHub
exactly what the simulator's oracle (Jira's permission rules) lets them read (sim_world.py). A person reads an issue
when the project's Browse holders (group, application role, anyone, user, project role, project lead, and per issue
the reporter or assignee) include them and, for an issue at a security level, the level's holders do too; and only an
active person with Jira product access is a user the connector links. LIGH is the project whose scheme grants nobody
but the sync account, ATLA a project open by project roles, CASC and GRAN have issue security schemes.

  TC-JDSIM-ACL-001  First sync: every permission pattern of the site equals the oracle
  TC-JDSIM-ACL-002  Each Browse holder kind granted to a project, then taken away again: group, application role (with
                    and without a key), anyone, user, project role, project lead, reporter, assignee
  TC-JDSIM-ACL-003  A group member added then removed; the group deleted while a grant names it
  TC-JDSIM-ACL-004  A project role's user and group actors added then removed; the role emptied
  TC-JDSIM-ACL-005  An issue put at each security level of its project's scheme and taken out again
  TC-JDSIM-ACL-006  Every kind of security level holder added then removed: group, user, project role, reporter,
                    assignee, project lead
  TC-JDSIM-ACL-007  A project the sync account no longer reaches; an archived project; both restored
  TC-JDSIM-ACL-008  A person deactivated, then reactivated
  TC-JDSIM-ACL-009  A person whose e-mail is hidden by Jira, and a person whose username changed (the key stays)
  TC-JDSIM-ACL-010  A Jira user with no PipesHub account, and a PipesHub user with no Jira account: neither changes
                    what anyone reads
  TC-JDSIM-ACL-012  /applicationrole refused (403) while schemes grant application roles: nobody gains access they do not
                    have in Jira; the next clean sync matches
  TC-JDSIM-ACL-011  Without the level-members route (Data Center documents none) a leveled issue: nobody reads it who Jira
                    does not show it to (the connector closes it to everyone: the tolerated DENIED:leveled-issue, #6114)
"""

import pytest
from connectors.jira_data_center.sim_world import CREATOR_EMAIL, OUTSIDER_EMAIL, JiraDcWorld, fault, succeeded

QUIET = "LIGH"      # Browse is held by nobody but the sync account
# The route that lists a level's holders is not in Data Center's documented API: it answers only with this switch.
LEVELS = {"level_members": True}
HOLDERS = [
    ("group", "readers"), ("applicationRole", ""), ("applicationRole", "jira-software"), ("anyone", ""),
    ("user", "@2"), ("projectRole", "Viewers"), ("projectLead", ""), ("reporter", ""), ("assignee", ""),
]


@pytest.mark.integration
@pytest.mark.jira_dc_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraDcSimAccess:

    async def test_tc_jdsim_acl_001_first_sync_access_matrix(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync of a site with every kind of access")

    async def test_tc_jdsim_acl_002_every_browse_holder_kind(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        names = w.usernames()
        assert succeeded(await w.start(prepare=[
            {"op": "group_create", "group": "readers", "members": names[3:6]}]))
        await w.assert_matches_source("after the first sync")
        for holder, parameter in HOLDERS:
            parameter = names[int(parameter[1:])] if parameter.startswith("@") else parameter
            grant = {"op": "grant", "project": QUIET, "holder": holder, "parameter": parameter}
            w.mutate(grant)
            assert succeeded(await w.sync())
            await w.assert_matches_source(f"after a Browse grant to {holder} {parameter!r} on {QUIET}")
            w.mutate({**grant, "present": False})
            assert succeeded(await w.sync())
            await w.assert_matches_source(f"after the Browse grant to {holder} {parameter!r} was taken away")

    async def test_tc_jdsim_acl_003_group_membership_and_deletion(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        a, b, c = w.usernames()[2:5]
        assert succeeded(await w.start(prepare=[
            {"op": "group_create", "group": "readers", "members": [a]},
            {"op": "grant", "project": QUIET, "holder": "group", "parameter": "readers"}]))
        await w.assert_matches_source("after the first sync")
        w.mutate({"op": "group_member", "group": "readers", "user": b, "member": True},
                 {"op": "group_member", "group": "readers", "user": a, "member": False})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after one member joined the group and another left")
        w.mutate({"op": "group_delete", "group": "readers"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the group was deleted while the project's scheme still names it")
        w.mutate({"op": "group_create", "group": "readers", "members": [c]})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a group of that name was created again")

    async def test_tc_jdsim_acl_004_project_role_actors(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        a, b = w.usernames()[2:4]
        assert succeeded(await w.start(prepare=[
            {"op": "group_create", "group": "readers", "members": [b]}]))
        await w.assert_matches_source("after the first sync")
        w.mutate({"op": "role_actor", "project": "GRAN", "role": "Developers", "kind": "user", "who": a},
                 {"op": "role_actor", "project": "GRAN", "role": "Viewers", "kind": "group", "who": "readers"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a user and a group joined project roles of GRAN")
        w.mutate({"op": "role_actor", "project": "GRAN", "role": "Developers", "kind": "user", "who": a,
                  "present": False},
                 {"op": "role_actor", "project": "GRAN", "role": "Viewers", "kind": "group", "who": "readers",
                  "present": False})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the user and the group left")
        for role in ("Developers", "Administrators", "Viewers", "Member"):
            w.mutate({"op": "role_clear", "project": "GRAN", "role": role})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after every project role of GRAN was emptied")

    async def test_tc_jdsim_acl_005_security_level_on_an_issue(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(switches=LEVELS))
        await w.assert_matches_source("after the first sync")
        keys = w.issue_keys("CASC")
        w.mutate({"op": "security", "issue": keys[0], "level": "Internal"},
                 {"op": "security", "issue": keys[1], "level": "Leadership"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after two issues of CASC were put at a security level")
        w.mutate({"op": "security", "issue": keys[0], "level": "Leadership"},
                 {"op": "security", "issue": keys[1], "level": None})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after one issue changed level and the other left its level")

    async def test_tc_jdsim_acl_006_security_level_holder_kinds(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        names = w.usernames()
        assert succeeded(await w.start(switches=LEVELS, prepare=[
            {"op": "group_create", "group": "readers", "members": names[3:6]}]))
        keys = w.issue_keys("GRAN")
        w.mutate({"op": "security", "issue": keys[0], "level": "Internal"},
                 {"op": "security", "issue": keys[1], "level": "Internal"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after two issues of GRAN were put at a level")
        for holder, parameter in [("group", "readers"), ("user", names[7]), ("projectRole", "Viewers"),
                                  ("reporter", ""), ("assignee", ""), ("projectLead", "")]:
            member = {"op": "level_member", "project": "GRAN", "level": "Internal", "holder": holder,
                      "parameter": parameter}
            w.mutate(member)
            assert succeeded(await w.sync())
            await w.assert_matches_source(f"after the level Internal gained the holder {holder} {parameter!r}")
            w.mutate({**member, "present": False})
            assert succeeded(await w.sync())
            await w.assert_matches_source(f"after the level Internal lost the holder {holder} {parameter!r}")

    async def test_tc_jdsim_acl_007_project_hidden_archived_restored(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        await w.assert_matches_source("after the first sync")
        # Jira keeps an issue's `updated` when who may read it changes, so only a full sync can learn that issues left.
        w.mutate({"op": "project", "project": "FALC", "sa_browse": False})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after the sync account lost Browse on FALC")
        w.mutate({"op": "project", "project": "FALC", "sa_browse": True},
                 {"op": "project", "project": "EVER", "status": "archived"})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after FALC came back and EVER was archived")
        w.mutate({"op": "project", "project": "EVER", "status": "live"})
        assert succeeded(await w.sync(full=True))
        await w.assert_matches_source("after EVER was restored")

    async def test_tc_jdsim_acl_008_user_deactivated_then_reactivated(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        a, b = w.usernames()[1:3]
        assert succeeded(await w.start(prepare=[
            {"op": "grant", "project": QUIET, "holder": "user", "parameter": a},
            {"op": "grant", "project": QUIET, "holder": "applicationRole", "parameter": ""}]))
        await w.assert_matches_source("after the first sync")
        w.mutate({"op": "user_active", "user": a, "active": False}, {"op": "user_active", "user": b, "active": False})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after two people were deactivated")
        w.mutate({"op": "user_active", "user": a, "active": True}, {"op": "user_active", "user": b, "active": True})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after both were reactivated")

    async def test_tc_jdsim_acl_009_hidden_email_and_renamed_user(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        a, b = w.usernames()[1:3]
        assert succeeded(await w.start(prepare=[
            {"op": "user_email", "user": a, "visible": False}, {"op": "user_email", "user": b, "visible": True},
            {"op": "grant", "project": QUIET, "holder": "user", "parameter": a},
            {"op": "grant", "project": QUIET, "holder": "user", "parameter": b}]))
        await w.assert_matches_source("after the first sync with one hidden and one visible e-mail")
        w.mutate({"op": "user_rename", "user": a, "name": "renamed.person"},
                 {"op": "user_email", "user": b, "visible": False})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after one username changed and the other person's e-mail was hidden")

    async def test_tc_jdsim_acl_010_people_on_one_side_only(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start(prepare=[
            {"op": "user_create", "username": "no.pipeshub.account"},
            {"op": "grant", "project": QUIET, "holder": "user", "parameter": "no.pipeshub.account"},
            {"op": "grant", "project": QUIET, "holder": "applicationRole", "parameter": ""}]))
        await w.assert_matches_source("after the first sync with a Jira user nobody has a PipesHub account for")
        w.mutate({"op": "user_active", "user": "no.pipeshub.account", "active": False})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after that Jira user was deactivated")

    async def test_tc_jdsim_acl_011_levels_without_the_members_route(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        assert succeeded(await w.start())
        keys = w.issue_keys("CASC")
        w.mutate({"op": "security", "issue": keys[0], "level": "Internal"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after an issue was put at a level the connector cannot read the holders of")
        assert w.known_gaps() >= {"DENIED:leveled-issue"}
        record = await w.record_for(next(t for t, v in w.sim.expected()["ticket_index"].items() if v["key"] == keys[0]))
        assert record is not None, "a leveled issue the connector cannot open is still a record (closed, not dropped)"

    async def test_tc_jdsim_acl_012_application_roles_refused(self, jira_dc_world: JiraDcWorld) -> None:
        w = jira_dc_world
        w.sim.reset()
        w.faults(fault(r"^/rest/api/2/applicationrole$", 403, max_times=500))
        assert await w.start(reset=False, prepare=[
            {"op": "grant", "project": QUIET, "holder": "applicationRole", "parameter": "jira-software"},
            {"op": "grant", "project": QUIET, "holder": "applicationRole", "parameter": ""}])
        w.clear_faults()
        # The connector cannot map the role to groups: whoever it then reads must be a reader in Jira too (no LEAK, no
        # stale or duplicate record); a person it leaves out is the fail-closed answer.
        bad = [p for p in await w.differences() if not p["type"].startswith("DENIED") and p["type"] != "SIM_UNMODELED"]
        assert not bad, f"with /applicationrole refused: {bad[:10]}"
        assert succeeded(await w.sync())
        await w.assert_matches_source("after /applicationrole answered again")
