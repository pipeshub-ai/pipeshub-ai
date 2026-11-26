# pyright: ignore-file

"""
Linear on the simulator: signing in, and a credential that stops working
========================================================================

The connector offers a personal API key and OAuth. The key is stored as given; the OAuth token pair comes from the app's
authorize and callback routes, and the platform refreshes it (Linear replaces the refresh token each time). A credential
Linear refuses must fail the run, remove nothing, give nobody anything, and recover when the credential does.

  TC-LNSIM-AUTH-001  Authorization-code sign-in through PipesHub's authorize and callback routes: the first sync equals the
                     workspace [A]
  TC-LNSIM-AUTH-002  A wrong client secret: the exchange is refused, no token is issued, the connector cannot be turned on [A]
  TC-LNSIM-AUTH-003  An API key Linear revoked: the run fails, nothing is removed, a working key recovers [A, I]
  TC-LNSIM-AUTH-004  An unknown key: the connector cannot be turned on and stores nothing [A]
  TC-LNSIM-AUTH-005  The key of a suspended person: the connector cannot be turned on [A]
  TC-LNSIM-AUTH-006  The key of a member who is no admin, and of a guest: the sync holds what that account sees, and
                     nobody reads what it did not see [A, E]
  TC-LNSIM-AUTH-007  OAuth tokens Linear revoked: the run fails and removes nothing; a new sign-in recovers [A, I]
  TC-LNSIM-AUTH-008  The platform's own refresh of a short-lived access token (Linear replaces the refresh token): the sync
                     that follows uses the new pair and equals the workspace [A]
  TC-LNSIM-AUTH-009  Everything the connector sent went to the simulator through the TLS front: the host names never were the
                     real Linear's, aiohttp and httpx both trust the front's certificate, and every request was modelled [N]
  TC-LNSIM-AUTH-010  Negative control of the front check: a Linear name nobody aliased is not taken for the front [N, O]
  TC-LNSIM-AUTH-011  Neither the key nor the client secret is in the connector's answer to the app [N]
  TC-LNSIM-AUTH-012  An unreachable source (every connection dropped): the connector cannot be turned on, stores nothing [A]
  TC-LNSIM-AUTH-013  An OAuth connector sends its token as a Bearer token, the form Linear takes (a key sent so, or a token
                     sent bare, is refused): its first sync equals the workspace [A]
"""

import asyncio
import time

import pytest
from connectors.linear.sim_instances import API_KEY, small, workspace
from connectors.linear.sim_world import (
    REFUSED,
    LinearWorld,
    _app_container,
    authorization_code,
    resolved_in_container,
    succeeded,
)


@pytest.mark.integration
@pytest.mark.linear_sim
@pytest.mark.asyncio(loop_scope="session")
class TestLinearSimAuth:

    async def test_tc_lnsim_auth_001_authorization_code_sign_in(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(small())
        w.create_connector(auth="oauth")
        answer = authorization_code(w)
        assert "error" not in str(answer).lower(), answer
        assert w.sim.stats()["tokens_by_flow"].get("oauth") == 1, w.sim.stats()["tokens_by_flow"]
        assert succeeded(await w.enable())
        await w.assert_matches_source("after signing in through the authorization-code flow")

    async def test_tc_lnsim_auth_002_wrong_client_secret(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(small())
        w.create_connector(auth="oauth", client_secret="not-the-secret")
        try:
            answer = authorization_code(w)
        except Exception as exc:  # noqa: BLE001 - the route may answer with an HTTP error
            answer = {"error": str(exc)}
        stats = w.sim.stats()
        assert stats["tokens_refused"] >= 1, "Linear never refused the exchange, so the case proves nothing"
        assert stats["tokens_issued"] == 0, f"a token was issued for a wrong secret: {answer}"
        assert await w.enable() == REFUSED, "a connector whose sign-in failed was turned on"
        assert not await w.records()

    async def test_tc_lnsim_auth_003_revoked_key(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        before = await w.holdings()
        w.sim.call("POST", "/_sim/linear/keys/revoke", {"key": API_KEY})
        w.ops({"op": "add_issue", "team": "OPS", "title": "after the key died", "creator": "bob"})
        assert not succeeded(await w.sync()), "a run whose key was refused (401) reported success"
        await w.assert_holdings_kept(before, "after the key was revoked")
        w.sim.call("POST", "/_sim/reset")
        w.sim.call("DELETE", "/_sim/linear/behaviours")
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the key worked again")

    async def test_tc_lnsim_auth_004_unknown_key(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(small())
        w.create_connector(key="lin_api_not_a_key")
        assert await w.enable() == REFUSED, "a connector with an unknown key was turned on"
        assert not await w.records()

    async def test_tc_lnsim_auth_005_key_of_a_suspended_person(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(workspace())
        w.create_connector(key="lin_api_sam")
        assert await w.enable() == REFUSED, "the key of a suspended person was accepted"
        assert not await w.records()

    @pytest.mark.parametrize("viewer", ["bob", "gail"])
    async def test_tc_lnsim_auth_006_account_with_fewer_privileges(self, linear_world: LinearWorld, viewer: str) -> None:
        w = linear_world
        account = workspace()
        account["viewer"] = viewer
        w.load(account)
        w.create_connector(key=f"lin_api_{viewer}")
        assert succeeded(await w.enable())
        await w.assert_matches_source(f"a sync by {viewer}")
        seen = {t["key"] for t in w.sim.expected()["teams"] if t["visible_to_viewer"]}
        assert seen and len(seen) < len(account["teams"]), f"{viewer} sees every team: the case proves nothing"

    async def test_tc_lnsim_auth_007_revoked_oauth_tokens(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(small(), auth="oauth"))
        before = await w.holdings()
        w.sim.call("POST", "/_sim/linear/tokens/revoke")
        w.ops({"op": "add_issue", "team": "OPS", "title": "after the token died", "creator": "bob"})
        assert not succeeded(await w.sync()), "a run whose token was refused reported success"
        await w.assert_holdings_kept(before, "after the OAuth tokens were revoked")
        await w.sign_in()
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a new sign-in")

    async def test_tc_lnsim_auth_008_the_platform_refreshes_the_token(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(small(), behaviours={"access_token_lifetime_s": 60})
        w.create_connector(auth="oauth")
        authorization_code(w)
        deadline = time.monotonic() + 120
        while w.sim.stats()["tokens_by_flow"].get("refresh", 0) < 1:
            assert time.monotonic() < deadline, f"the platform never refreshed a 60 s token: {w.sim.stats()['tokens_by_flow']}"
            await asyncio.sleep(3)
        w.sim.call("DELETE", "/_sim/linear/behaviours")
        assert succeeded(await w.enable())
        await w.assert_matches_source("after the platform refreshed the token")

    async def test_tc_lnsim_auth_009_only_the_simulator_was_called(self, linear_world: LinearWorld, linear_front) -> None:
        w = linear_world
        assert succeeded(await w.start(workspace()))
        assert len({linear_front[h] for h in ("api.linear.app", "linear.app", "uploads.linear.app", "linear-tls")}) == 1
        assert linear_front["aiohttp"] == "200" and linear_front["httpx"] == "200", linear_front
        stats = w.sim.stats()
        assert stats["total_operations"] > 0 and stats["downloads"] > 0, stats
        assert not w.source_problems()
        assert w.sim.call("GET", "/_sim/linear/unmodeled") == []

    async def test_tc_lnsim_auth_010_a_name_the_front_does_not_own_is_caught(self, linear_front) -> None:
        """Negative control of ``linear_front``: a Linear name nobody aliased does not resolve to the front, so the same
        check that passes api.linear.app would fail it."""
        try:
            address = resolved_in_container(_app_container(), "unaliased-sim-check.linear.app")
        except AssertionError:
            return  # does not resolve at all inside the stack: nothing could be reached either
        assert address != linear_front["linear-tls"], "an unaliased Linear name resolved to the simulator's front"

    @pytest.mark.parametrize("auth", ["token", "oauth"])
    async def test_tc_lnsim_auth_011_secrets_are_not_returned(self, linear_world: LinearWorld, auth: str) -> None:
        w = linear_world
        assert succeeded(await w.start(small(), auth=auth))
        document = str(w.connector())
        assert API_KEY not in document, "the connector's document carries the API key"
        assert "lin-secret" not in document, "the connector's document carries the client secret"
        assert w.sim.stats()["tokens_issued"] >= (1 if auth == "oauth" else 0)

    async def test_tc_lnsim_auth_012_unreachable_source(self, linear_world: LinearWorld) -> None:
        w = linear_world
        w.load(small())
        w.create_connector()
        w.faults(core=({"match": {"path_regex": "^/graphql$"}, "effect": {"type": "reset"}},))
        assert await w.enable() == REFUSED, "a connector that could not reach the source was turned on"
        w.clear_faults()
        assert not await w.records()

    async def test_tc_lnsim_auth_013_each_sign_in_sends_the_header_linear_takes(self, linear_world: LinearWorld) -> None:
        w = linear_world
        assert succeeded(await w.start(small(), auth="oauth"))
        stats = w.sim.stats()
        assert stats["tokens_refused"] == 0 and stats["calls"].get("organization"), stats
        await w.assert_matches_source("an OAuth connector's first sync")
