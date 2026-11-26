# pyright: ignore-file

"""
Zoom on the simulator: signing in, and a token that stops working
=================================================================

The connector signs in with OAuth (the authorization-code flow of the app's own routes) and the platform refreshes
its token. A token or a refresh that Zoom refuses must fail the run, change nothing, and recover when the credential
does.

  TC-ZMSIM-AUTH-001  The authorization-code sign-in through PipesHub's authorize and callback routes: the connector
                     ends authenticated and its first sync equals the account [A]
  TC-ZMSIM-AUTH-002  A wrong client secret: the code exchange is refused, the connector is not authenticated and
                     cannot be turned on [A]
  TC-ZMSIM-AUTH-003  An access token Zoom revoked: the run fails and changes nothing; a new token recovers it [A]
  TC-ZMSIM-AUTH-004  A token that lives 60 seconds: the platform refreshes it at once (Zoom replaces the refresh token
                     each time), and the sync still works; with rotation off the same [A]
  TC-ZMSIM-AUTH-005  Zoom refuses the refresh (401 invalid_request, as Zoom answers a replaced, revoked or removed-app
                     refresh token): the platform counts the first refusal as a dead token; nothing it holds is lost
                     (the third refusal's deactivation is the platform's, unit-tested) [A]
  TC-ZMSIM-AUTH-006  A token for a user Zoom deactivated cannot be turned on: the connection test refuses it [A]
  TC-ZMSIM-AUTH-007  Everything the connector sent went to the simulator through the TLS front: the host names were
                     never the real Zoom's (zoom_front) and every call is counted [N]
  TC-ZMSIM-AUTH-008  Negative control of the front check: a Zoom name nobody aliased is not taken for the front [N]
"""

import asyncio
import os
import subprocess
from urllib.parse import parse_qs, urlparse

import pytest
import requests

from connectors.zoom.sim_instances import small
from connectors.zoom.sim_world import CALLED, REFUSED, ZoomWorld, succeeded
from helper.graph_provider_utils import async_poll_until


def authorize_through_the_app(w: ZoomWorld, *, client_secret: str | None = None) -> dict:
    """What a person's browser does: the app's authorize route gives Zoom's URL, the (simulated) Zoom sends the
    browser back with a code, and the app's callback route exchanges it. Returns the callback's answer."""
    started = w.client._request_json("GET", f"/api/v1/connectors/{w.connector_id}/oauth/authorize")
    url = urlparse(started["authorizationUrl"])
    assert url.hostname == "zoom.us" and url.path == "/oauth/authorize", started["authorizationUrl"]
    # The browser reaches the simulator on its published port; the app's token request goes through the front.
    landed = requests.get(f"{w.sim.base_url}{url.path}?{url.query}", allow_redirects=False, timeout=30)
    assert landed.status_code in (302, 303, 307), landed.text[:200]
    back = parse_qs(urlparse(landed.headers["Location"]).query)
    return w.client._request_json("GET", "/api/v1/connectors/oauth/callback",
                                  params={"code": back["code"][0], "state": back["state"][0]})


def _container() -> str:
    from connectors.zoom.sim_world import _app_container  # noqa: PLC0415
    return _app_container()


async def restart_app(timeout: int = 300) -> None:
    """Restart the PipesHub container and wait, polling against a deadline, until it answers."""
    await asyncio.to_thread(subprocess.run, ["docker", "restart", _container()], check=True, capture_output=True,
                            timeout=180)
    health = os.getenv("PIPESHUB_BASE_URL", "http://localhost:3000").rstrip("/") + "/api/v1/health"

    async def answers():
        try:
            return True if (await asyncio.to_thread(requests.get, health, timeout=5)).ok else None
        except requests.RequestException:
            return None

    await async_poll_until(answers, timeout=timeout, interval=3, description="the PipesHub container after a restart")


def strike_logged(connector_id: str) -> bool:
    """Did the platform's token refresh count Zoom's refusal of this connector's refresh token (a strike toward
    deactivating it)? Read from the container's log: the service writes no other record of it."""
    out = subprocess.run(["docker", "logs", "--since", "10m", _container()], capture_output=True, text=True,
                         timeout=120, check=False)
    return any("credential_refresh.strike" in line and connector_id in line and "counted=true" in line
               and "oauth_error=invalid_request" in line for line in (out.stdout + out.stderr).splitlines())


@pytest.mark.integration
@pytest.mark.zoom_sim
@pytest.mark.asyncio(loop_scope="session")
class TestZoomSimAuth:

    async def test_tc_zmsim_auth_001_authorization_code_sign_in(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        w.load(small())
        w.create_connector()
        answer = authorize_through_the_app(w)
        assert "oauth_error" not in str(answer), answer
        assert w.sim.stats()["tokens_by_flow"].get("authorization_code") == 1
        assert succeeded(await w.enable())
        await w.assert_matches_source("after signing in through the authorization-code flow")

    async def test_tc_zmsim_auth_002_wrong_client_secret(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        w.load(small())
        w.create_connector(client_secret="not-the-secret")
        try:
            answer = authorize_through_the_app(w)
        except Exception as exc:  # noqa: BLE001 - the route may answer with an HTTP error
            answer = {"error": str(exc)}
        assert w.sim.stats()["tokens_refused"] >= 1, "Zoom never refused the exchange, so the case proves nothing"
        assert w.sim.stats()["tokens_issued"] == 0, f"a token was issued for a wrong secret: {answer}"
        assert await w.enable() == REFUSED, "a connector whose sign-in failed was turned on"
        assert not await w.records()

    async def test_tc_zmsim_auth_003_revoked_access_token(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        before = await w.holdings()
        w.ops({"op": "new_meeting_ended", "meeting": "review",
               "instance": {"key": "r9", "days_ago": 1, "participants": [{"user": "cara"}]}},
              {"op": "revoke_tokens"})
        assert not succeeded(await w.sync()), "a run whose every read was refused (401) reported success"
        await w.assert_holdings_kept(before, "after the access token was revoked")
        await w.sign_in()
        assert succeeded(await w.sync())
        await w.assert_matches_source("after a new token")

    @pytest.mark.parametrize("rotation", [True, False])
    async def test_tc_zmsim_auth_004_token_refreshed_by_the_platform(self, zoom_world: ZoomWorld, rotation: bool) -> None:
        w = zoom_world
        w.load(small(), {"token_lifetime_s": 60, "refresh_token_rotation": rotation})
        w.create_connector()
        authorize_through_the_app(w)

        async def refreshed():
            return True if w.sim.stats()["tokens_by_flow"].get("refresh_token", 0) else None

        await async_poll_until(refreshed, timeout=180, interval=3, description="the platform's refresh of a 60 s token")
        w.behaviours({"token_lifetime_s": 3599})  # a refresh of a token this short is scheduled at once, again
        assert succeeded(await w.enable())
        w.ops({"op": "new_meeting_ended", "meeting": "review",
               "instance": {"key": "r9", "days_ago": 1, "participants": [{"user": "cara"}]}})
        assert succeeded(await w.sync()), "the sync after the platform refreshed the token failed"
        await w.assert_matches_source("after the token was refreshed")

    async def test_tc_zmsim_auth_005_refresh_refused(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        w.load(small(), {"token_lifetime_s": 60})
        w.create_connector()
        authorize_through_the_app(w)
        assert succeeded(await w.enable())
        await w.assert_matches_source("before Zoom refuses the refresh")
        before = await w.holdings()
        w.sim.call("POST", "/_sim/zoom/refresh/revoke")
        refused = w.sim.stats()["tokens_refused"]
        # The platform scans every connector's credentials when it starts and every five minutes after.
        await restart_app()

        async def counted():
            return True if await asyncio.to_thread(strike_logged, w.connector_id) else None

        await async_poll_until(counted, timeout=240, interval=5,
                               description="the platform counting Zoom's refusal (401 invalid_request) as a dead refresh token")
        assert w.sim.stats()["tokens_refused"] > refused, "Zoom never refused a refresh, so the case proves nothing"
        await w.assert_holdings_kept(before, "after the first refusal")
        assert w._connector().get("isActive") is not False, "one refusal took the connector's sign-in away"
        # The third refusal in a row, two five-minute scans on, ends the connector unauthenticated: the platform's
        # strike counting is unit-tested (test_token_refresh_service.py and the others of that family) and waiting
        # for it here costs ten minutes.

    async def test_tc_zmsim_auth_006_token_of_a_deactivated_user(self, zoom_world: ZoomWorld) -> None:
        w = zoom_world
        w.load(small())
        w.create_connector()
        await w.sign_in()
        w.ops({"op": "user_deactivated", "user": "ann"})
        assert await w.enable() == REFUSED, "a token of a deactivated user was accepted"
        assert not await w.records()

    async def test_tc_zmsim_auth_007_only_the_simulator_was_called(self, zoom_world: ZoomWorld, zoom_front) -> None:
        w = zoom_world
        assert succeeded(await w.start(small()))
        assert zoom_front["api.zoom.us"] == zoom_front["zoom.us"] == zoom_front["zoom-tls"]
        stats = w.sim.stats()
        assert stats["total_calls"] > 0
        assert not w.source_problems()
        called = {e for e in w.sim.calls() if e.startswith(("GET /v2", "GET /rec"))}
        assert called <= CALLED, f"calls outside the fault matrix's list: {sorted(called - CALLED)}"

    async def test_tc_zmsim_auth_008_a_name_the_front_does_not_own_is_caught(self, zoom_front) -> None:
        """Negative control of ``zoom_front``: a Zoom name nobody aliased does not resolve to the front, so the same
        check that passes api.zoom.us and zoom.us would fail it."""
        from connectors.zoom.sim_world import resolved_in_container  # noqa: PLC0415
        try:
            address = resolved_in_container(_container(), "accounts.zoom.us")
        except AssertionError:
            return  # does not resolve at all inside the stack: nothing could be reached either
        assert address != zoom_front["zoom-tls"], "an unaliased Zoom name resolved to the simulator's front"
