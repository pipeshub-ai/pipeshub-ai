# pyright: ignore-file

"""
Personal Jira Cloud on the simulator, round 4: what leaves the signed-in person's view
=====================================================================================

  TC-JCOA-R4P-001  A project leaves the person's view (deleted in Jira): its issues go with it, the others stay
  TC-JCOA-R4P-002  The same while Jira does not say the project is gone (the project read fails): nothing is removed, then it is
  TC-JCOA-R4P-003  Jira does not confirm the account (``/myself`` refused, dropped, not a profile): an issue that left the view and a
                   project that left it are NOT removed (an expired token reads as an anonymous visitor to whom everything is gone)

Vendor pages: a project that is deleted answers 404 on ``GET /rest/api/3/project/{id}`` ("Returned if the project is not found
or the user does not have permission to view it", https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-projects/);
``GET /rest/api/3/myself`` answers 401 to a caller who is not signed in.
"""

import pytest
from connectors.jira.cloud_sim_oauth_world import (  # noqa: F401 - fixtures
    OAuthWorld,
    atlassian_front,
    oauth_people,
    oauth_sim,
    oauth_world,
)
from connectors.jira.cloud_sim_world import drop, fault, malformed, succeeded

PROJECT = r"/ex/jira/.*/rest/api/3/project/\d+$"
MYSELF = r"/ex/jira/.*/rest/api/3/myself$"


def personal(w: OAuthWorld) -> None:
    w.registry_name, w.team = "Jira Cloud Personal", False
    w.signed_in_as = w.sim.call("GET", "/_sim/jira/profiles")["u_role"]["email"]


def project_of(w: OAuthWorld, issue_id: str) -> str:
    return w.sim.expected()["ticket_index"][issue_id]["key"].rsplit("-", 1)[0]


async def a_project_to_lose(w: OAuthWorld) -> tuple[str, set[str]]:
    """A project the person reads (and at least one other): its key, and the ids of its issues the person reads."""
    visible = w.expected_for(w.signed_in_as)
    by_project: dict[str, set[str]] = {}
    for i in visible:
        by_project.setdefault(project_of(w, i), set()).add(i)
    assert len(by_project) >= 2, f"the person reads {len(by_project)} project(s)"
    key = max(by_project, key=lambda k: len(by_project[k]))
    return key, by_project[key]


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudPersonalRound4:

    async def test_tc_jcoa_r4p_001_project_leaves_the_view(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth())
        key, ids = await a_project_to_lose(w)
        assert ids <= await w.issue_ids()
        w.mutate({"op": "project", "project": key, "status": "deleted"})
        assert not (ids & w.expected_for(w.signed_in_as)), "Jira still shows the deleted project's issues to the person"
        assert succeeded(await w.sync())
        await w.assert_issues_match("after the project was deleted")
        assert not (ids & await w.issue_ids())

    async def test_tc_jcoa_r4p_002_project_gone_but_jira_does_not_say_so(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth())
        key, ids = await a_project_to_lose(w)
        before = await w.issue_ids()
        w.mutate({"op": "project", "project": key, "status": "deleted"})
        w.faults(fault(PROJECT, 500, max_times=500))
        await w.sync()
        assert w.clear_faults() > 0
        assert ids <= await w.issue_ids(), "issues of a project Jira did not confirm gone were removed"
        assert before - ids <= await w.issue_ids()
        assert succeeded(await w.sync())
        await w.assert_issues_match("after Jira said the project is gone")

    @pytest.mark.parametrize("how", ["refused", "dropped", "not_a_profile"])
    async def test_tc_jcoa_r4p_003_account_not_confirmed(self, oauth_world: OAuthWorld, how: str) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth())
        key, ids = await a_project_to_lose(w)
        before = await w.issue_ids()
        w.mutate({"op": "project", "project": key, "status": "deleted"})
        broken = {"refused": fault(MYSELF, 401, max_times=500), "dropped": drop(MYSELF, max_times=500),
                  "not_a_profile": malformed(MYSELF, max_times=500)}[how]
        w.faults(broken)
        await w.sync()
        assert w.clear_faults() > 0, f"{how}: the account check was never made"
        assert before <= await w.issue_ids(), f"{how}: issues were removed though Jira did not confirm the account"
        assert succeeded(await w.sync())
        await w.assert_issues_match(f"{how}: after the account was confirmed")
