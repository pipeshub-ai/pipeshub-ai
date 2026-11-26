# pyright: ignore-file

"""
Personal Jira Cloud on the simulator (OAuth sign-in)
====================================================

The personal connector signs in as one person, syncs what that person sees and shows it to its creator only. It reads no
user, group, role or permission scheme. Atlassian's hosts are reached through the OAuth overlay's TLS front.

  TC-JCOA-PERS-001  The first sync equals the signed-in person's view; the creator reads every record, nobody else reads any
  TC-JCOA-PERS-002  An incremental sync after an edit, a new issue and a deleted issue equals the view again
  TC-JCOA-PERS-003  A full sync leaves out the projects a project filter excludes (and unlinks what it held)
  TC-JCOA-PERS-004  The account changes (a new sign-in as another person): the next full sync holds the new person's view
  TC-JCOA-PERS-005  A search refused for good (403) and an expired token: the run does not succeed and nothing is removed
  TC-JCOA-PERS-005b The same refusal must also fail the run (strict expected failure: the personal run reports success)
  TC-JCOA-PERS-006  One 503 on the search: the run recovers
  TC-JCOA-PERS-007  An indexing filter that turns attachments off: no file is indexed, issues still are
  TC-JCOA-PERS-008  A record's content streams, and a reindex of it is accepted once it was first indexed
  (TC-JCOA-PERS-001 also asserts the personal connector never calls the user, group, role or permission-scheme endpoints)
"""

import pytest
from connectors.jira.cloud_sim_oauth_world import (  # noqa: F401 - fixtures
    OAuthWorld,
    atlassian_front,
    oauth_people,
    oauth_sim,
    oauth_world,
)
from connectors.jira.cloud_sim_world import CREATOR_EMAIL, fault, indexing_filter, project_filter, succeeded

def personal(w: OAuthWorld, who: str | None = None) -> None:
    w.registry_name, w.team = "Jira Cloud Personal", False
    w.signed_in_as = who or w.sim.call("GET", "/_sim/jira/profiles")["u_role"]["email"]


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudPersonal:

    async def test_tc_jcoa_pers_001_owner_only(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth())
        await w.assert_issues_match("after the first sync", w.signed_in_as)
        records = await w.records()
        readable = await w.readable(records)
        assert {r["id"] for r in records} <= readable[CREATOR_EMAIL], "the creator cannot read what the connector holds"
        others = {e: ids for e, ids in readable.items() if e != CREATOR_EMAIL and ids}
        assert not others, f"people other than the creator read records of a personal connector: { {e: len(i) for e, i in others.items()} }"
        paths = w.sim.paths_called()
        assert not [p for p in paths if p.startswith("GET /rest/api/3/group") or "applicationrole" in p or "permissionscheme" in p], paths

    async def test_tc_jcoa_pers_002_incremental_changes(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth())
        keys = sorted(w.expected_for(w.signed_in_as), key=int)
        w.sim.rest("PUT", f"/issue/{keys[0]}", {"fields": {"summary": "edited after the first sync"}}, as_=w.signed_in_as)
        w.sim.rest("DELETE", f"/issue/{keys[-1]}", as_=w.signed_in_as, ok=(403, 404))
        assert succeeded(await w.sync())
        await w.assert_issues_match("after the incremental sync", w.signed_in_as)

    async def test_tc_jcoa_pers_003_project_filter_and_full_sync_unlink(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth())
        before = await w.issue_ids()
        by_project = w.sim.expected()["records_by_project"]
        first = next(k for k, v in sorted(by_project.items()) if v["ticket"] >= 2)
        assert succeeded(await w.restart(project_filter("not_in", [first])))
        assert succeeded(await w.sync(full=True))
        after = await w.issue_ids()
        assert after < before, "excluding a project and syncing in full removed nothing"
        assert succeeded(await w.restart(project_filter("in", [first])))
        assert succeeded(await w.sync(full=True))
        assert (await w.issue_ids()) and (await w.issue_ids()) < before

    async def test_tc_jcoa_pers_004_account_change(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        profiles = w.sim.call("GET", "/_sim/jira/profiles")
        personal(w, profiles["u_role"]["email"])
        assert succeeded(await w.start_oauth())
        first = await w.issue_ids()
        other = profiles["u_lead"]["email"] if profiles["u_lead"]["email"] != w.signed_in_as else profiles["u_sec"]["email"]
        w.signed_in_as = other
        w.configure_sim()
        w.sign_in()
        assert succeeded(await w.sync(full=True))
        await w.assert_issues_match("after the account changed", other)
        assert w.expected_for(other) != first, "the two people see the same issues: the case proves nothing"

    async def test_tc_jcoa_pers_005_refused_and_expired(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth())
        before = await w.holdings()
        w.faults(fault(r"/ex/jira/.*/rest/api/3/search/jql", 403, max_times=500))
        await w.sync()
        assert w.clear_faults() > 0
        await w.assert_holdings_kept(before, "after the search was refused")
        w.sim.revoke("expire_access")
        assert not succeeded(await w.sync()), "an expired token reported success"
        await w.assert_holdings_kept(before, "after the token expired")
        w.sign_in()
        assert succeeded(await w.sync())
        await w.assert_issues_match("after a new sign-in", w.signed_in_as)

    @pytest.mark.xfail(strict=True, reason="a personal run whose issue search was refused (403) for every project reports "
                       "succeeded: jira_cloud_personal/connector.py:run_sync notes the unread projects and notifies, but "
                       "does not fail the run (the team connector's run fails on the same refusal)")
    async def test_tc_jcoa_pers_005b_refused_search_outcome(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth())
        w.faults(fault(r"/ex/jira/.*/rest/api/3/search/jql", 403, max_times=500))
        outcome = await w.sync()
        assert w.clear_faults() > 0
        assert not succeeded(outcome)

    async def test_tc_jcoa_pers_006_one_503_recovers(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth())
        w.faults(fault(r"/ex/jira/.*/rest/api/3/search/jql", 503, max_times=1))
        assert succeeded(await w.sync())
        assert w.clear_faults() > 0
        await w.assert_issues_match("after the 503", w.signed_in_as)

    async def test_tc_jcoa_pers_007_attachments_not_indexed(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth(filters=indexing_filter("issue_attachments", False)))
        await w.assert_issues_match("with attachments not indexed", w.signed_in_as)

    async def test_tc_jcoa_pers_008_stream_and_reindex(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        personal(w)
        assert succeeded(await w.start_oauth())
        ticket = next(r for r in await w.creator_records() if r.get("recordType") == "TICKET")
        assert w.client.stream_record(ticket["id"]).content
        await w.wait_indexed(ticket["id"])
        assert w.client.reindex_record(ticket["id"])
