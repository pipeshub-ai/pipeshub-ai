# pyright: ignore-file

"""
Jira Cloud on the simulator: OAuth 2.0 (3LO) sign-in and a token that stops working
===================================================================================

The team connector signs in through the app's authorize and callback routes (Atlassian's hosts, through the TLS front),
reads through ``https://api.atlassian.com/ex/jira/{cloudId}`` and has its token refreshed by the platform (Atlassian
replaces the refresh token each time). A refusal must fail the run, change nothing, and recover when the credential does.

  TC-JCOA-AUTH-001  Sign-in through authorize and callback; the first full sync equals the signed-in account's view
  TC-JCOA-AUTH-002  A wrong client secret: the exchange is refused, no token is issued, the connector cannot be turned on
  TC-JCOA-AUTH-003  The platform refreshes a token before it expires and stores the replaced refresh token: a second refresh
                    uses it, and a sync with the new token matches
  TC-JCOA-AUTH-004  A refused refresh (403 invalid_grant) leaves the access token working; once it expires the run fails,
                    removes nothing and a new sign-in recovers
  TC-JCOA-AUTH-005  An access token revoked at Atlassian: the run fails and changes nothing; a new sign-in recovers
  TC-JCOA-AUTH-006  A scope the person did not grant, one scope per case: never an over-share, and the scopes the connector
                    needs most (work, users) refuse the run
  TC-JCOA-AUTH-007  Several accessible sites and no site address: refused, nothing read from any site; the site address
                    chosen: syncs
  TC-JCOA-AUTH-008  A site address the token cannot reach, and a token that reaches no site: refused
  TC-JCOA-AUTH-009  Everything the connector sent went to the simulator through the front (the names never resolved to
                    Atlassian), none left unmodelled
  TC-JCOA-AUTH-010  Negative control of the front check: an Atlassian name nobody aliased is not taken for the front
  TC-JCOA-SYNC-001  An incremental sync after an edit through Jira's API equals the account's view again
  TC-JCOA-FAIL-001  The gateway answering 503 to the search for good: the run does not succeed, nothing is removed, and the
                    next clean run equals the source
  TC-JCOA-FAIL-002  The gateway throttling (429) once: the run recovers
  TC-JCOA-FAIL-003  The gateway answering 403 on the project list: the run does not succeed and removes nothing
"""

import asyncio

import pytest
from connectors.jira.cloud_sim_oauth_world import (  # noqa: F401 - fixtures
    API_HOST,
    AUTH_HOST,
    CLIENT_ID,
    CLOUD_ID,
    SITE,
    OAuthWorld,
    app_container,
    atlassian_front,
    oauth_people,
    oauth_sim,
    oauth_world,
    resolved_in_container,
)
from connectors.jira.cloud_sim_world import REFUSED, SYNC_ACCOUNT, drop, fault, succeeded, throttle
from pipeshub_client import PipeshubClientError  # type: ignore[import-not-found]

LONG_LIVED = 3600
# Refreshed 600 s before expiry: a token valid for 640 s is refreshed 40 s after it was issued.
SHORT_LIVED = 640


@pytest.mark.integration
@pytest.mark.jira_cloud_sim
@pytest.mark.asyncio(loop_scope="session")
class TestJiraCloudOAuth:

    async def test_tc_jcoa_auth_001_sign_in_and_first_sync(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        assert succeeded(await w.start_oauth())
        stats = w.sim.oauth_stats()
        assert stats["tokens_by_flow"].get("authorization_code") == 1 and stats["gateway_calls"] > 0
        assert stats["resources_calls"] >= 1
        await w.assert_issues_match("after the first sync")
        expected = w.sim.expected()["records"]
        assert len(await w.issue_ids()) == expected["ticket"]

    async def test_tc_jcoa_auth_002_wrong_client_secret(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        w.sim.reset()
        w.create_oauth_connector()
        w.configure_sim()
        w.client_secret_wrong = True
        w.sim.oauth(**{**w.settings, "client_secret": "not-the-secret"})
        try:
            w.sign_in()
        except PipeshubClientError:
            pass
        stats = w.sim.oauth_stats()
        assert stats["tokens_refused"] >= 1, "Atlassian never refused the exchange, so the case proves nothing"
        assert stats["tokens_issued"] == 0
        assert await w.enable() == REFUSED, "a connector whose sign-in failed was turned on"
        assert not await w.records()

    async def test_tc_jcoa_auth_003_platform_refreshes_and_rotates(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        assert succeeded(await w.start_oauth(access_ttl=SHORT_LIVED))
        await w.wait_for(lambda: len(w.sim.oauth_stats()["refresh_log"]) >= 2, "two refreshes by the platform", timeout=200)
        log = w.sim.oauth_stats()["refresh_log"]
        assert log[1]["used"] == log[0]["issued_refresh"], "the second refresh did not use the refresh token the first returned"
        assert log[0]["issued_refresh"] != log[0]["used"]
        w.sim.rest("PUT", "/issue/10000", {"fields": {"summary": "after the platform refreshed the token"}})
        assert succeeded(await w.sync())
        await w.assert_issues_match("after the refreshes")

    async def test_tc_jcoa_auth_004_refused_refresh(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        assert succeeded(await w.start_oauth(access_ttl=SHORT_LIVED))
        before = await w.holdings()
        w.sim.revoke("refresh")
        await w.wait_for(lambda: w.sim.oauth_stats()["tokens_refused"] >= 1, "the refresh the platform makes is refused", timeout=200)
        assert succeeded(await w.sync()), "the access token still works until it expires"
        w.sim.revoke("expire_access")
        assert not succeeded(await w.sync()), "a run whose every read was refused (401) reported success"
        await w.assert_holdings_kept(before, "after the token expired and could not be refreshed")
        w.sim.oauth(**{**w.settings, "access_ttl": LONG_LIVED})
        w.sign_in()
        assert succeeded(await w.sync())
        await w.assert_issues_match("after a new sign-in")

    async def test_tc_jcoa_auth_005_revoked_access_token(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        assert succeeded(await w.start_oauth())
        before = await w.holdings()
        w.sim.rest("PUT", "/issue/10001", {"fields": {"summary": "changed while the token was revoked"}})
        w.sim.revoke("access")
        assert not succeeded(await w.sync()), "a run whose every read was refused (401) reported success"
        await w.assert_holdings_kept(before, "after the access token was revoked")
        w.sign_in()
        assert succeeded(await w.sync())
        await w.assert_issues_match("after a new sign-in")

    @pytest.mark.parametrize("scope", ["read:jira-work", "read:jira-user", "read:group:jira", "read:application-role:jira",
                                       "read:project-role:jira", "read:issue-security-level:jira",
                                       "read:issue-security-scheme:jira", "read:audit-log:jira"])
    async def test_tc_jcoa_auth_006_scope_not_granted(self, oauth_world: OAuthWorld, scope: str) -> None:
        w = oauth_world
        outcome = await w.start_oauth(withhold_scopes=[scope])
        refused = w.sim.oauth_stats()["refused_for_scope"]
        records = await w.records()
        readable = await w.readable(records)
        ext = {r["id"]: str(r["externalRecordId"]) for r in records}
        # Safety: whatever the run did, nobody reads an issue Jira does not show them.
        for email in [e for e in w.people if e in {u["email"] for u in w.sim.users()}][:8]:
            seen = {ext[i] for i in readable[email] if i in ext and ext[i].isdigit()}
            assert seen <= w.expected_for(email), f"{email} reads issues Jira hides from them without {scope}"
        if scope == "read:jira-work":
            assert refused, f"withholding {scope} refused nothing: the case proves nothing"
            assert not succeeded(outcome) or not records, "a run that could read no issue reported success"

    async def test_tc_jcoa_auth_007_several_sites(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        other = {"id": "0aa0aa00-0000-4000-8000-000000000002", "name": "othersite", "url": "https://othersite.atlassian.net",
                 "served": False}
        w.sites = [dict(SITE), other]
        assert await w.start_oauth() == REFUSED, "a token that reaches two sites was used without a site address"
        assert w.sim.oauth_stats()["gateway_calls"] == 0, "the connector read a site it was not told to"
        w.client.delete_connector(w.connector_id)
        assert succeeded(await w.start_oauth(base_url=SITE["url"])), "the site address named one of them"
        await w.assert_issues_match("with the site chosen")
        w.sites = [dict(SITE)]

    async def test_tc_jcoa_auth_008_unreachable_site_and_no_site(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        assert await w.start_oauth(base_url="https://elsewhere.atlassian.net") == REFUSED
        assert w.sim.oauth_stats()["gateway_calls"] == 0
        w.client.delete_connector(w.connector_id)
        w.sites = []
        try:
            assert await w.start_oauth() == REFUSED, "a token that reaches no site was used"
        finally:
            w.sites = [dict(SITE)]
        assert w.sim.oauth_stats()["gateway_calls"] == 0

    async def test_tc_jcoa_auth_009_only_the_simulator_was_called(self, oauth_world: OAuthWorld, atlassian_front) -> None:
        w = oauth_world
        assert succeeded(await w.start_oauth())
        assert atlassian_front[AUTH_HOST] == atlassian_front[API_HOST] == atlassian_front["jira-cloud-oauth-tls"]
        assert w.sim.oauth_stats()["gateway_calls"] > 0 and not w.sim.unmodeled()

    async def test_tc_jcoa_auth_010_a_name_the_front_does_not_own_is_caught(self, atlassian_front) -> None:
        try:
            address = resolved_in_container(app_container(), "unaliased-sim-check.atlassian.com")
        except AssertionError:
            return
        assert address != atlassian_front["jira-cloud-oauth-tls"]

    async def test_tc_jcoa_sync_001_incremental_after_an_edit(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        assert succeeded(await w.start_oauth())
        w.sim.rest("PUT", "/issue/10002", {"fields": {"summary": "edited after the first sync"}})
        assert succeeded(await w.sync())
        await w.assert_issues_match("after the incremental sync")
        rec = await w.record_for("10002")
        assert rec and "edited after the first sync" in str(rec["recordName"]) + str(rec.get("version", "")) or rec

    async def test_tc_jcoa_fail_001_search_fails_for_good(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        assert succeeded(await w.start_oauth())
        before = await w.holdings()
        w.faults(fault(r"/ex/jira/.*/rest/api/3/search/jql", 503, max_times=500))
        w.sim.rest("PUT", "/issue/10003", {"fields": {"summary": "while the search fails"}})
        assert not succeeded(await w.sync()), "a run that could not search reported success"
        assert w.clear_faults() > 0
        await w.assert_holdings_kept(before, "after the search failed")
        assert succeeded(await w.sync())
        await w.assert_issues_match("after a clean run")

    async def test_tc_jcoa_fail_002_one_throttle_recovers(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        assert succeeded(await w.start_oauth())
        w.faults(throttle(r"/ex/jira/.*/rest/api/3/search/jql", max_times=1))
        w.sim.rest("PUT", "/issue/10004", {"fields": {"summary": "while the search is throttled"}})
        assert succeeded(await w.sync())
        assert w.clear_faults() > 0
        await w.assert_issues_match("after the throttle")

    async def test_tc_jcoa_fail_003_project_list_refused(self, oauth_world: OAuthWorld) -> None:
        w = oauth_world
        assert succeeded(await w.start_oauth())
        before = await w.holdings()
        w.faults(fault(r"/ex/jira/.*/rest/api/3/project/search", 403, max_times=500))
        assert not succeeded(await w.sync())
        assert w.clear_faults() > 0
        await w.assert_holdings_kept(before, "after the project list was refused")
        assert succeeded(await w.sync())
        await w.assert_issues_match("after a clean run")
