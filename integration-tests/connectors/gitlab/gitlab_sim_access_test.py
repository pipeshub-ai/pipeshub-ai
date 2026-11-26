# pyright: ignore-file

"""
GitLab on the simulator – who reads what, and how that follows changes at the source
====================================================================================

After every change of who may read, the connector syncs and every person's PipesHub access equals the oracle's: GitLab's
own rules for visibility, membership and role, blocked, deactivated and awaiting members, Guest, Planner and Reporter
levels, confidential issues (the author and assignees read them too), internal notes, group-inherited and shared access.
The oracle moves with each change, so a change that altered nothing fails the test instead of passing unseen.
Live twins: TC-GL-PERM-001/002, TC-GL-CONF-001. Harness scenarios: access_changes, removed_member_keeps_an_assigned_
confidential_issue, several_projects_and_visibility.

  TC-GLSIM-ACL-001  roles: a developer demoted to guest loses the code and merge requests; a guest promoted to
                    reporter gains them; a reporter blocked, unblocked and deactivated; a planner removed and added back
  TC-GLSIM-ACL-002  confidential issues: made confidential, the assignee unassigned, made public again, a new
                    confidential issue by a guest
  TC-GLSIM-ACL-003  groups: a new group member reaches the project, then is removed; the project shared with a group
                    at Reporter, then the share ended; a member removed from the project
  TC-GLSIM-ACL-004  a removed member who is the assignee of a confidential issue reads nothing, after the removal,
                    after an edit of the issue, and after a full sync
  TC-GLSIM-ACL-005  the connector stays right for a project in a subgroup (access inherited from both groups)
  TC-GLSIM-ACL-006  a public and an internal project, and the sync of each: all but the visibility grants equal the
                    oracle (KNOWN_GAPS)
  TC-GLSIM-ACL-007  strict gap: a public or internal project is read by people who are not members (GitLab), not by
                    PipesHub
  TC-GLSIM-ACL-008  strict gap: an instance administrator or auditor who is a member of nothing reads everything on
                    GitLab
  TC-GLSIM-ACL-009  a member awaiting a seat and a blocked member read nothing; the members' levels reach the five
                    record groups as the oracle lists them
  TC-GLSIM-ACL-010  the access check is the product's, for a person who is not on GitLab and for an external user
"""

import pytest

from connectors.gitlab.sim_instances import (
    base_instance,
    email_of,
    with_instance_admins,
)
from connectors.gitlab.sim_world import GitLabWorld, succeeded


def member(user: str, level: str | None, **where: str) -> dict:
    return {"op": "set_member", **(where or {"project": "widgets"}), "user": user, "level": level}


async def step(w: GitLabWorld, label: str, *ops: dict) -> None:
    """One change at GitLab, then a sync, then the comparison for everybody. The change must move GitLab's readers."""
    before = {u: set(r["readers"]) for u, r in _readers(w.sim.expected()).items()}
    w.ops(*ops)
    after = {u: set(r["readers"]) for u, r in _readers(w.sim.expected()).items()}
    assert before != after, f"{label}: the change moved nobody's access on GitLab, so it proves nothing"
    assert succeeded(await w.sync()), f"{label}: the sync did not succeed"
    await w.assert_matches_source(label)


def _readers(expected: dict) -> dict:
    return {r["id"]: r for r in expected["records"]}


@pytest.mark.integration
@pytest.mark.gitlab_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGitLabSimAccess:

    async def test_tc_glsim_acl_001_roles(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        await w.assert_matches_source("roles at the start")
        await step(w, "developer demoted to guest", member("dev", "guest"))
        await step(w, "guest promoted to reporter", member("gus", "reporter"))
        await step(w, "reporter blocked", {"op": "set_user", "user": "rita", "state": "blocked"})
        await step(w, "reporter unblocked", {"op": "set_user", "user": "rita", "state": "active"})
        await step(w, "planner deactivated", {"op": "set_user", "user": "pat", "state": "deactivated"})
        await step(w, "planner active again", {"op": "set_user", "user": "pat", "state": "active"})
        await step(w, "planner removed from the project", member("pat", None))
        await step(w, "planner added back", member("pat", "planner"))

    async def test_tc_glsim_acl_002_confidential_issues(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        await step(w, "an issue made confidential", {"op": "set_issue", "issue": "open", "confidential": True})
        await step(w, "a guest unassigned from a confidential issue", {"op": "set_issue", "issue": "conf", "assignees": []})
        await step(w, "the confidential issue made public", {"op": "set_issue", "issue": "conf", "confidential": False})
        await step(w, "a guest's own confidential issue",
                   {"op": "add_issue", "project": "widgets", "title": "Mine", "author": "gus", "confidential": True})

    async def test_tc_glsim_acl_003_groups_and_shares(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        await step(w, "a new group member reaches the project through the group", member("olga", "developer", group="acme"))
        await step(w, "the group member removed", member("olga", None, group="acme"))
        await step(w, "the project shared with a group at Reporter",
                   {"op": "share", "project": "widgets", "with_group": "platform", "cap": "reporter"})
        await step(w, "the share ended", {"op": "share", "project": "widgets", "with_group": "platform", "cap": None})
        await step(w, "a member removed from the project", member("rita", None))

    async def test_tc_glsim_acl_004_removed_assignee_keeps_nothing(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance()))
        w.ops(member("gus", None))
        stages = (("after the removal", None, False), ("after the issue is edited",
                  {"op": "set_issue", "issue": "conf", "title": "Confidential v2"}, False), ("after a full sync", None, True))
        for label, op, full in stages:
            if op:
                w.ops(op)
            assert succeeded(await w.sync(full=full)), label
            await w.assert_matches_source(f"removed assignee {label}")
        gus = (await w.holdings())[email_of("gus")]
        assert not gus, f"a removed member still reads {sorted(gus)}"

    async def test_tc_glsim_acl_005_project_in_a_subgroup(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), project="acme/platform/api"))
        await w.assert_matches_source("a project in a subgroup")
        held = await w.holdings()
        assert held[email_of("olga")], "a guest of the subgroup reads nothing of its project's issues"
        await step(w, "a member of the parent group", member("maya", None, group="acme"))

    async def test_tc_glsim_acl_006_public_and_internal_projects(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), project="acme/oss"))
        tolerated = await w.assert_matches_source("a public project")
        other = await w.second("acme/intranet")
        tolerated += await other.assert_matches_source("an internal project")
        assert tolerated, "GitLab's visibility rules differ from the oracle's here, so the tolerance has no use left"

    @pytest.mark.xfail(strict=True, reason="project visibility is not granted: the project record groups get members only "
                       "(backend/python/app/connectors/sources/gitlab/projects.py:455-475); no open PR")
    async def test_tc_glsim_acl_007_visibility_is_granted(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(base_instance(), project="acme/oss"))
        await w.assert_matches_source("a public project, no tolerance", tolerate=frozenset(), settle=False)

    @pytest.mark.xfail(strict=True, reason="an instance administrator or auditor who is not a member reads nothing: the "
                       "connector grants members only (projects.py:455-475); no open PR")
    async def test_tc_glsim_acl_008_instance_administrators_read_everything(self, gitlab_world: GitLabWorld) -> None:
        w = gitlab_world
        assert succeeded(await w.start(with_instance_admins(base_instance())))
        await w.assert_matches_source("administrator and auditor", tolerate=frozenset(), settle=False)

    async def test_tc_glsim_acl_009_awaiting_and_blocked_members(self, gitlab_synced) -> None:
        _, snap, _ = gitlab_synced
        project = next(p for p in snap.expected["projects"] if p["path_with_namespace"] == "acme/widgets")
        assert "nina" in project["members"] and "blocked" in project["inactive_members"], project
        for who in ("nina", "blocked"):
            assert not snap.readable[email_of(who)], f"{who} reads {len(snap.readable[email_of(who)])} record(s)"
        for key, readers in project["groups"].items():
            for username in readers:
                seen = snap.readable.get(email_of(username), set())
                assert seen, f"{username} is a reader of group {key} and reads nothing"

    async def test_tc_glsim_acl_010_strangers_and_external_users(self, gitlab_synced) -> None:
        _, snap, _ = gitlab_synced
        assert not snap.readable["gl-nobody@example.com"], "a person with no GitLab account reads records"
        assert not snap.readable[email_of("ext")], "an external user who is a member of nothing reads records"
