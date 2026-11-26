# pyright: ignore-file

"""
Zendesk on the simulator: signing in, and a token that stops working
====================================================================

The connector signs in with OAuth (the authorization-code flow of the app's own routes) and the platform refreshes its
token (Zendesk replaces the refresh token each time). A token or a refresh that Zendesk refuses must fail the run, change
nothing, and recover when the credential does.

  TC-ZDSIM-AUTH-001  The authorization-code sign-in through PipesHub's authorize and callback routes: the connector ends
                     authenticated and its first sync equals the account [A]
  TC-ZDSIM-AUTH-002  A wrong client secret: the code exchange is refused, nothing is issued, the connector cannot be
                     turned on [A]
  TC-ZDSIM-AUTH-003  An access token Zendesk revoked while its refresh token still works: PipesHub refreshes it (and
                     stores the replaced refresh token), and the sync matches; with rotation off the same [A]
  TC-ZDSIM-AUTH-004  Access and refresh tokens both revoked: the run fails and changes nothing; a new sign-in recovers [A]
  TC-ZDSIM-AUTH-005  A token of an agent (not an admin): the run stops and stores nothing [A]
  TC-ZDSIM-AUTH-006  A token without the read scope cannot be turned on [A]
  TC-ZDSIM-AUTH-007  Everything the connector sent went to the simulator through the TLS front: the host names were
                     never the real Zendesk's (zendesk_front), every call is counted, none left the matrix, and no
                     bearer token reached the attachment CDN [N]
  TC-ZDSIM-AUTH-008  Negative control of the front check: a Zendesk name nobody aliased is not taken for the front [N, O]
  TC-ZDSIM-AUTH-009  A subdomain that is not a Zendesk subdomain is refused before any request leaves [N]
  TC-ZDSIM-AUTH-010  A token Zendesk deactivated (the connected admin lost the role): the run fails and changes nothing [A]
  TC-ZDSIM-AUTH-011  The secret the sign-in used is never in the connector's answer to the app [N]
"""

import pytest
from connectors.zendesk.sim_instances import OAUTH, desk, small
from connectors.zendesk.sim_world import (
    CALLED,
    REFUSED,
    ZendeskWorld,
    _app_container,
    authorization_code,
    resolved_in_container,
    succeeded,
)
from pipeshub_client import PipeshubClientError


@pytest.mark.integration
@pytest.mark.zendesk_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZendeskSimAuth:

    async def test_tc_zdsim_auth_001_authorization_code_sign_in(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        w.load(small())
        w.create_connector()
        answer = authorization_code(w)
        assert "error" not in str(answer).lower(), answer
        assert w.sim.stats()["tokens_by_flow"].get("authorization_code") == 1
        assert succeeded(await w.enable())
        await w.assert_matches_source("after signing in through the authorization-code flow")

    async def test_tc_zdsim_auth_002_wrong_client_secret(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        w.load(small())
        w.create_connector(client_secret="not-the-secret")
        try:
            answer = authorization_code(w)
        except Exception as exc:  # noqa: BLE001 - the route may answer with an HTTP error
            answer = {"error": str(exc)}
        stats = w.sim.stats()
        assert stats["tokens_refused"] >= 1, "Zendesk never refused the exchange, so the case proves nothing"
        assert stats["tokens_issued"] == 0, f"a token was issued for a wrong secret: {answer}"
        assert await w.enable() == REFUSED, "a connector whose sign-in failed was turned on"
        assert not await w.records()

    @pytest.mark.parametrize("rotation", [True, False])
    async def test_tc_zdsim_auth_003_revoked_access_token_is_refreshed(self, zendesk_world: ZendeskWorld,
                                                                       rotation: bool) -> None:
        w = zendesk_world
        assert succeeded(await w.start(small(), behaviours={"refresh_rotation": rotation}))
        w.sim.call("POST", "/_sim/zendesk/tokens/revoke")
        w.ops({"op": "add_ticket", "key": "t_new", "subject": "After the token died", "requester": "alice",
               "group": "support"})
        assert succeeded(await w.sync()), "a run whose access token died did not recover through the refresh token"
        assert w.sim.stats()["tokens_by_flow"].get("refresh_token", 0) >= 1, "the platform never refreshed the token"
        await w.assert_matches_source("after the platform refreshed the token")
        w.sim.call("POST", "/_sim/zendesk/tokens/revoke")
        w.ops({"op": "update_ticket", "ticket": "t_new", "subject": "After the second revocation"})
        assert succeeded(await w.sync()), "the refresh token the platform stored after the first refresh did not work"
        await w.assert_matches_source("after a second refresh")

    async def test_tc_zdsim_auth_004_every_token_revoked(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(small()))
        before = await w.holdings()
        w.sim.call("POST", "/_sim/zendesk/tokens/revoke")
        w.sim.call("POST", "/_sim/zendesk/refresh/revoke")
        w.ops({"op": "add_ticket", "key": "t_new", "subject": "After the tokens died", "requester": "alice",
               "group": "support"})
        assert not succeeded(await w.sync()), "a run whose every read was refused (401) reported success"
        await w.assert_holdings_kept(before, "after every token was revoked")
        w.sim.call("PUT", "/_sim/zendesk/scenario", {**small(), "oauth": {**OAUTH, "access_token": "zd-new-access",
                                                                           "refresh_token": "zd-new-refresh"}})
        w.ops({"op": "add_ticket", "key": "t_new", "subject": "After the tokens died", "requester": "alice",
               "group": "support"})
        w.scenario = {**small(), "oauth": {**OAUTH, "access_token": "zd-new-access", "refresh_token": "zd-new-refresh"}}
        await w.sign_in()
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a new sign-in")

    async def test_tc_zdsim_auth_005_token_of_an_agent(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        account = desk()
        account["oauth"] = {**OAUTH, "user": "agent_groups"}
        outcome = await w.start(account)
        assert outcome == REFUSED or not succeeded(outcome), "a token of an agent synced"
        assert not await w.records(), "a token of an agent stored records"

    async def test_tc_zdsim_auth_006_token_without_the_read_scope(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        account = small()
        account["oauth"] = {**OAUTH, "scope": "write"}
        outcome = await w.start(account)
        assert outcome == REFUSED or not succeeded(outcome), "a token without the read scope synced"
        assert not await w.records()

    async def test_tc_zdsim_auth_007_only_the_simulator_was_called(self, zendesk_world: ZendeskWorld,
                                                                   zendesk_front) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        assert zendesk_front["pipeshubsim.zendesk.com"] == zendesk_front["p1.zdusercontent.com"] == \
            zendesk_front["zendesk-tls"]
        stats = w.sim.stats()
        assert stats["total_calls"] > 0 and stats["downloads"] > 0
        assert not w.source_problems()
        assert stats["wrong_hosts"] == [] and stats["cdn_auth_leaks"] == []
        assert not CALLED or set(w.sim.calls()) <= CALLED

    async def test_tc_zdsim_auth_008_a_name_the_front_does_not_own_is_caught(self, zendesk_front) -> None:
        """Negative control of ``zendesk_front``: a Zendesk name nobody aliased does not resolve to the front, so the
        same check that passes pipeshubsim.zendesk.com would fail it."""
        try:
            address = resolved_in_container(_app_container(), "unaliased-sim-check.zendesk.com")
        except AssertionError:
            return  # does not resolve at all inside the stack: nothing could be reached either
        assert address != zendesk_front["zendesk-tls"], "an unaliased Zendesk name resolved to the simulator's front"

    @pytest.mark.parametrize("subdomain", ["pipeshubsim.evil.example", "pipeshubsim.zendesk.com.evil.example", "-bad", "a b"])
    async def test_tc_zdsim_auth_009_not_a_zendesk_subdomain(self, zendesk_world: ZendeskWorld, subdomain: str) -> None:
        w = zendesk_world
        w.load(small())
        try:
            w.create_connector(subdomain=subdomain)
        except PipeshubClientError:
            return  # refused when it is saved
        await w.sign_in()
        assert await w.enable() == REFUSED, f"a connector for {subdomain!r} was turned on"
        assert w.sim.stats()["total_calls"] == 0, "the connector sent a request for a subdomain that is not Zendesk's"

    async def test_tc_zdsim_auth_010_admin_loses_the_role(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(desk()))
        before = await w.holdings()
        w.ops({"op": "update_user", "user": "admin", "role": "agent"},
              {"op": "update_ticket", "ticket": "t_billing", "priority": "high"})
        assert not succeeded(await w.sync()), "a run whose connected account lost the admin role reported success"
        await w.assert_holdings_kept(before, "after the connected account lost the admin role")
        w.ops({"op": "update_user", "user": "admin", "role": "admin"})
        assert succeeded(await w.sync())
        await w.assert_matches_source("after the role came back")

    async def test_tc_zdsim_auth_011_the_secret_is_not_returned(self, zendesk_world: ZendeskWorld) -> None:
        w = zendesk_world
        assert succeeded(await w.start(small()))
        assert OAUTH["client_secret"] not in str(w.connector()), "the connector's document carries the client secret"
