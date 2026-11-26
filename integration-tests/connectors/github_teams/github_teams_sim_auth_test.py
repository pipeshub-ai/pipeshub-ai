# pyright: ignore-file

"""
GitHub Teams on the simulator: signing in, a credential that stops working, and where the requests went
=======================================================================================================

The connector accepts a personal access token and an OAuth app. PipesHub stores the token as the sign-in would (the
live suite does the same), or goes through its own authorize and callback routes against the simulator's github.com.

  TC-GHSIM-AUTH-001  A personal access token: connector authenticated, first sync equals the oracle [A]
  TC-GHSIM-AUTH-002  An OAuth access token stored as the sign-in stores it: the same sync result [A]
  TC-GHSIM-AUTH-003  The authorization-code sign-in through the app's authorize and callback routes; the first sync equals
                     the oracle [A]
  TC-GHSIM-AUTH-004  A wrong client secret: no token is issued and the connector cannot be turned on [A]
  TC-GHSIM-AUTH-005  A token GitHub revoked: the run fails, nothing is lost and nobody gains; a new token recovers [A, I]
  TC-GHSIM-AUTH-006  A token GitHub does not know: the connector cannot be turned on and stores nothing [A]
  TC-GHSIM-AUTH-007  A token without the repo scope cannot read a private repository: nothing stored, nobody reads [A]
  TC-GHSIM-AUTH-008  The connected account is not in the organisation and holds no grant: nothing stored [A]
  TC-GHSIM-AUTH-009  A GitHub account with only a public-repository scope syncs a public repository and no private one [A]
  TC-GHSIM-NET-001   The three hosts resolve to the front inside the app container; every call was counted, the call
                     list names only those hosts, and the simulator logged nothing it does not model [N, O]
  TC-GHSIM-NET-002   A name of the shared certificate that nobody aliased (uploads.github.com) is not the front (negative
                     control of the check) [N, O]
  TC-GHSIM-NET-003   The token is never in the connector's answer to the app [N]
  TC-GHSIM-NET-004   Links to other hosts, plain http and look-alike names are never fetched, and no token leaves the API
                     host [N]
"""

import re

import pytest
from connectors.github_teams.sim_instances import ADMIN_TOKEN, NOT_ALIASED, OAUTH_APP, acme
from connectors.github_teams.sim_world import (
    API_HOST,
    HOSTS,
    REFUSED,
    GithubWorld,
    _app_container,
    authorization_code,
    call_problems,
    resolved_in_container,
    succeeded,
)

ONE = [{"op": "add_issue", "repo": "widgets", "title": "Issue", "key": "issue", "comments": [{"user": "alice", "body": "hi"}]},
       {"op": "add_pull", "repo": "widgets", "title": "PR", "changes": {"src/pr.py": "pr = 1\n"}}]


@pytest.mark.integration
@pytest.mark.github_teams_sim
@pytest.mark.asyncio(loop_scope="session")
class TestGithubSimAuth:

    async def test_tc_ghsim_auth_001_personal_access_token(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE, auth="pat"))
        assert w.connector().get("isAuthenticated") is not False
        await w.assert_matches_source("PAT sign-in")

    async def test_tc_ghsim_auth_002_oauth_access_token(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE, auth="oauth"))
        await w.assert_matches_source("OAuth access token")

    async def test_tc_ghsim_auth_003_authorization_code_sign_in(self, github_world: GithubWorld) -> None:
        w = github_world
        await w.start(acme(), ops=ONE, auth="oauth-app", enable=False)
        answer = authorization_code(w)
        assert "error" not in str(answer).lower(), answer
        assert w.sim.call("GET", "/_sim/github/stats")["oauth_tokens_issued"] == 1
        assert succeeded(await w.enable())
        await w.assert_matches_source("after signing in through the authorization-code flow")

    async def test_tc_ghsim_auth_004_wrong_client_secret(self, github_world: GithubWorld) -> None:
        w = github_world
        w.load(acme())
        w.ops(*ONE)
        w.create_connector(auth="oauth-app", client_secret="not-the-secret", filters={"sync": {"values": {"repo_ids": {
            "operator": "in", "type": "select", "value": ["acme/widgets"]}}}})
        try:
            answer = authorization_code(w)
        except Exception as exc:  # noqa: BLE001 - the route may answer with an HTTP error
            answer = {"error": str(exc)}
        assert w.sim.call("GET", "/_sim/github/stats")["oauth_tokens_issued"] == 0, f"a token was issued for a wrong secret: {answer}"
        assert await w.enable() == REFUSED, "a connector whose sign-in failed was turned on"
        assert not await w.records()

    async def test_tc_ghsim_auth_005_revoked_token_then_recovery(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE, auth="oauth"))
        before = await w.holdings()
        w.ops({"op": "revoke_token", "value": ADMIN_TOKEN}, {"op": "add_issue", "repo": "widgets", "title": "After the revocation"})
        assert not succeeded(await w.sync()), "a run whose every read was refused (401) reported success"
        await w.assert_holdings_kept(before, "after the token was revoked")
        w.ops({"op": "add_token", "user": "admin", "value": "ghp_sim_admin_2", "scopes": ["repo", "read:org", "user:email"]})
        w.sign_in("ghp_sim_admin_2")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a new token was stored")
        assert "After the revocation" in {r["recordName"] for r in await w.records()}

    async def test_tc_ghsim_auth_006_unknown_token(self, github_world: GithubWorld) -> None:
        w = github_world
        outcome = await w.start(acme(), ops=ONE, auth="pat", token="ghp_not_a_token")
        assert outcome == REFUSED or not succeeded(outcome), "a token GitHub does not know synced"
        assert not await w.records()

    async def test_tc_ghsim_auth_007_token_without_the_repo_scope(self, github_world: GithubWorld) -> None:
        w = github_world
        spec = acme()
        spec["tokens"].append({"user": "admin", "value": "ghp_sim_admin_noscope", "scopes": ["read:org", "user:email"]})
        await w.start(spec, ops=ONE, auth="pat", token="ghp_sim_admin_noscope")
        # GitHub answers 404 for a repository the token cannot see, as for one that is gone: the connector stores nothing
        # (the run's outcome is recorded in the guide)
        assert not await w.records(), "records of a repository the token cannot read"
        holdings = await w.holdings()
        assert not any(holdings[p] for p in w.people), "somebody reads records of a repository the token cannot read"

    async def test_tc_ghsim_auth_008_account_without_access(self, github_world: GithubWorld) -> None:
        w = github_world
        spec = acme()
        spec["tokens"].append({"user": "dave", "value": "ghp_sim_dave", "scopes": ["repo", "read:org", "user:email"]})
        await w.start(spec, ops=ONE, auth="pat", token="ghp_sim_dave")
        assert not await w.records(), "records of a repository the account cannot read"
        holdings = await w.holdings()
        assert not any(holdings[p] for p in w.people), "somebody reads records of a repository the account cannot read"

    async def test_tc_ghsim_auth_009_public_scope_reads_public_repository_only(self, github_world: GithubWorld) -> None:
        w = github_world
        spec = acme(repos=2)
        spec["repos"][1]["visibility"] = "public"
        spec["tokens"].append({"user": "admin", "value": "ghp_sim_admin_public", "scopes": ["public_repo", "read:org", "user:email"]})
        assert succeeded(await w.start(spec, repos=["acme/gadgets"], ops=[{"op": "add_issue", "repo": "gadgets", "title": "Public issue"}],
                                       auth="pat", token="ghp_sim_admin_public"))
        await w.assert_matches_source("public repository with a token that has no repo scope")

    async def test_tc_ghsim_net_001_only_the_front_was_called(self, github_world: GithubWorld, github_front) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE))
        assert len({github_front[h] for h in (*HOSTS, "github-tls")}) == 1
        calls = w.sim.calls()
        assert calls and {c["host"] for c in calls} <= set(HOSTS), {c["host"] for c in calls}
        assert API_HOST in {c["host"] for c in calls}
        assert not w.source_problems(), w.source_problems()

    async def test_tc_ghsim_net_002_an_unaliased_name_is_not_the_front(self, github_front) -> None:
        try:
            address = resolved_in_container(_app_container(), NOT_ALIASED)
        except AssertionError:
            return  # does not resolve at all inside the stack: nothing could be reached either
        assert address != github_front["github-tls"], f"{NOT_ALIASED} resolved to the simulator's front"

    async def test_tc_ghsim_net_003_the_token_is_not_returned(self, github_world: GithubWorld) -> None:
        w = github_world
        assert succeeded(await w.start(acme(), ops=ONE, auth="pat"))
        doc = str(w.connector())
        assert ADMIN_TOKEN not in doc and OAUTH_APP["client_secret"] not in doc, "the connector's document carries a secret"

    async def test_tc_ghsim_net_004_other_hosts_are_never_fetched(self, github_world: GithubWorld) -> None:
        w = github_world
        body = ("![a](https://evil.example/user-attachments/assets/x) "
                "![b](https://github.com.evil.example/user-attachments/assets/x) "
                "[c](http://169.254.169.254/latest/meta-data) "
                "![d](http://github.com/user-attachments/assets/plain-http)")
        assert succeeded(await w.start(acme(), ops=[{"op": "add_issue", "repo": "widgets", "title": "Links", "body": body}]))
        await w.stream(f"{w.repo_id()}/issues/1")
        web = [c for c in w.sim.calls() if c["host"] != API_HOST]
        assert not web, f"a link to another host or over plain http was fetched: {web}"
        assert not call_problems(w.sim.calls())
        assert all(re.fullmatch(r"[a-z.]+", c["host"]) for c in w.sim.calls())
