# pyright: ignore-file

"""Jira Cloud signed in with OAuth 2.0 (3LO) against the connectors-simulator, through Atlassian's fixed hosts.

The connector and the platform call ``https://auth.atlassian.com`` (authorize, token, refresh) and
``https://api.atlassian.com`` (accessible resources, ``/ex/jira/{cloudId}/...``). The overlay
``docker-compose.integration.jira-cloud-oauth-sim.yml`` aliases both names to a TLS front of the simulator; this module
proves, inside the PipesHub container, that they resolve to it before anything is signed in (``atlassian_front``), and
drives the sign-in the way a browser does (``authorize``, the simulator's redirect, the app's callback route).

It builds on ``cloud_sim_world`` (the API-token suite's world) and changes only what OAuth needs: the simulator's control
API here is the ``/_sim/atlassian/oauth`` family, and the account a test signs in as is a person of the site.
"""

from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
import time
import uuid
from typing import Any
from urllib.parse import parse_qs, urlparse

import pytest
import pytest_asyncio
import requests
from connectors.jira.cloud_sim_world import (
    CREATOR_EMAIL,
    OUTSIDER_EMAIL,
    SYNC_ACCOUNT,
    JiraCloudSim,
    JiraCloudWorld,
    _active_user,
    basic,
)
from pipeshub_client import PipeshubClient, PipeshubClientError  # type: ignore[import-not-found]
from helper.clients.connectors_client import ConnectorOAuthConfigsClient  # type: ignore[import-not-found]

OAUTH_SIM_URL_ENV = "JIRA_CLOUD_OAUTH_SIM_URL"
APP_CONTAINER_ENV = "JIRA_CLOUD_OAUTH_SIM_APP_CONTAINER"
AUTH_HOST, API_HOST = "auth.atlassian.com", "api.atlassian.com"
CLIENT_ID, CLIENT_SECRET = "sim-client-id", "sim-client-secret"
_ORG_APPS: dict[tuple[str, str, str], str] = {}
CLOUD_ID = "6f3a1c52-7b1e-4c1d-9a55-0d1f2e3a4b5c"
SITE = {"id": CLOUD_ID, "name": "pipeshubsim", "url": "https://pipeshubsim.atlassian.net", "served": True}
SYNC_TIMEOUT_SEC = int(os.getenv("JIRA_CLOUD_SIM_SYNC_TIMEOUT_SEC", "600"))


class OAuthSim(JiraCloudSim):
    """The same site, the same control API, minus the controls the OAuth image does not have."""

    def reset(self) -> None:
        self.call("DELETE", "/_sim/faults")
        self.call("POST", "/_sim/reset")
        self.call("POST", "/_sim/calls/reset")

    def users(self) -> list[dict[str, Any]]:
        out, start = [], 0
        while True:
            page = self.rest("GET", "/users/search", startAt=start, maxResults=100)
            out += [{"account_id": u["accountId"], "display_name": u.get("displayName"), "email": u.get("emailAddress"),
                     "active": u.get("active", True)} for u in page if u.get("accountType") == "atlassian"]
            if len(page) < 100:
                return out
            start += 100

    def unmodeled(self) -> list[str]:
        """Requests no route matched and the simulator answered 404: an injected fault or a refused token answers before
        routing and is not an unmodelled request."""
        calls = self.call("GET", "/_sim/calls")["endpoints"]
        return [k for k, v in calls.items() if k.endswith("unmatched") and "404" in v["by_status"]]

    def oauth(self, **settings: Any) -> dict[str, Any]:
        return self.call("PUT", "/_sim/atlassian/oauth", settings)

    def oauth_stats(self) -> dict[str, Any]:
        return self.call("GET", "/_sim/atlassian/oauth/stats")

    def revoke(self, what: str, **kw: Any) -> None:
        self.call("POST", "/_sim/atlassian/oauth/revoke", {"what": what, **kw})

    def set_scopes(self, *scopes: str) -> None:
        self.call("POST", "/_sim/atlassian/oauth/scopes", {"scopes": list(scopes)})


class OAuthWorld(JiraCloudWorld):
    """A connector signed in through the authorization-code flow."""

    def __init__(self, *args: Any, **kw: Any) -> None:
        super().__init__(*args, **kw)
        self.registry_name = "Jira"
        self.team = True
        self.signed_in_as = SYNC_ACCOUNT
        self.sites: list[dict[str, Any]] = [dict(SITE)]
        self.settings: dict[str, Any] = {}

    def create_oauth_connector(self, *, base_url: str | None = None, client_secret: str | None = None) -> str:
        auth: dict[str, Any] = {"clientId": CLIENT_ID, "clientSecret": client_secret or CLIENT_SECRET}
        if not self.team:
            # A person's own OAuth app is held to public https addresses on every call it leads to (the token exchange
            # included), and here Atlassian's hosts are the simulator's front on the stack's network: a personal
            # connector signs in through an app the admin saved for the org, as it may.
            auth = {"oauthConfigId": self._org_oauth_app(auth["clientSecret"])}
        if base_url:
            auth["baseUrl"] = base_url
        created = self.client.create_connector(
            connector_type=self.registry_name, instance_name=f"jira-oauth-{uuid.uuid4().hex[:8]}",
            scope="team" if self.team else "personal", config={"auth": auth}, auth_type="OAUTH")
        assert created.connector_id, "Connector must have a valid ID"
        self.connector_id = created.connector_id
        return self.connector_id

    def _org_oauth_app(self, client_secret: str) -> str:
        """The id of an OAuth app the admin saved for ``registry_name`` with the simulator's client, made once per stack."""
        key = (self.client.base_url, self.registry_name, client_secret)
        if key not in _ORG_APPS:
            resp = ConnectorOAuthConfigsClient(self.client).create_config(self.registry_name, {
                "oauthInstanceName": f"jira-oauth-sim-{uuid.uuid4().hex[:8]}",
                "config": {"clientId": CLIENT_ID, "clientSecret": client_secret},
            })
            assert resp.status_code in (200, 201), f"saving the {self.registry_name} OAuth app answered HTTP {resp.status_code}"
            oauth = resp.json().get("oauthConfig") or {}
            _ORG_APPS[key] = str(oauth.get("_id") or oauth.get("id") or "")
            assert _ORG_APPS[key], f"saving the {self.registry_name} OAuth app returned no id"
        return _ORG_APPS[key]

    def authorization_url(self) -> str:
        return self.client._request_json("GET", f"/api/v1/connectors/{self.connector_id}/oauth/authorize")["authorizationUrl"]

    def configure_sim(self, **over: Any) -> None:
        """The app and the person at the browser. The redirect address is what the platform put in its authorize URL."""
        url = urlparse(self.authorization_url())
        assert url.hostname == AUTH_HOST and url.path == "/authorize", url
        redirect = parse_qs(url.query)["redirect_uri"][0]
        self.settings = {"lenient_authorize": True, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET, "redirect_uri": redirect,
                         "user": self.signed_in_as, "sites": self.sites, **over}
        self.sim.oauth(**self.settings)

    def sign_in(self) -> dict[str, Any]:
        """What the browser does: the platform's authorize URL, the simulator's redirect with a code, the callback route."""
        url = urlparse(self.authorization_url())
        landed = requests.get(f"{self.sim.base_url}{url.path}?{url.query}", allow_redirects=False, timeout=30,
                              headers={"Host": AUTH_HOST})
        assert landed.status_code == 302, landed.text[:300]
        back = parse_qs(urlparse(landed.headers["Location"]).query)
        return self.client._request_json("GET", "/api/v1/connectors/oauth/callback",
                                         params={"code": back["code"][0], "state": back["state"][0]})

    async def start_oauth(self, *, base_url: str | None = None, enable: bool = True, sign_in: bool = True,
                          client_secret: str | None = None, filters: dict[str, Any] | None = None,
                          **sim_settings: Any) -> str | None:
        """Reset the site, sign a new connector in as ``signed_in_as`` and (unless ``enable`` is False) turn it on."""
        self.sim.reset()
        self.scope.projects_in = self.scope.projects_out = None
        self.filters = filters
        self.create_oauth_connector(base_url=base_url, client_secret=client_secret)
        self.configure_sim(**sim_settings)
        if sign_in:
            self.sign_in()
        if filters:
            self.client.update_connector_filters_sync_config(self.connector_id, filters=filters)
        if not enable:
            return None
        return await self.enable()

    async def wait_for(self, check, what: str, timeout: float = 240, interval: float = 3) -> Any:
        deadline = time.monotonic() + timeout
        while True:
            found = check()
            if found:
                return found
            assert time.monotonic() < deadline, f"never happened within {timeout}s: {what}"
            await asyncio.sleep(interval)

    # ---- the comparison with the oracle, small: issues and files -------------------------------------

    async def issue_ids(self) -> set[str]:
        return {str(r["externalRecordId"]) for r in await self.records() if r.get("recordType") == "TICKET"}

    async def file_count(self) -> int:
        return sum(1 for r in await self.records() if r.get("recordType") == "FILE")

    def expected_for(self, email: str) -> set[str]:
        """The issue ids Jira lets ``email`` read (the simulator's own oracle, not the connector's)."""
        return {str(i) for i in self.sim.call("GET", "/_sim/jira/access", params={"email": email, "ids": "true"})["jira_ids"]}

    async def assert_issues_match(self, label: str, email: str | None = None) -> None:
        email = email or self.signed_in_as
        want = self.expected_for(email)
        deadline = time.monotonic() + 90
        while True:
            have = await self.issue_ids()
            if have == want or time.monotonic() > deadline:
                break
            await asyncio.sleep(5)
        assert have == want, (f"{label}: issues differ from Jira's view of {email}: missing {sorted(want - have)[:10]}, "
                              f"stale {sorted(have - want)[:10]}")
        assert not self.sim.unmodeled(), f"{label}: unmodelled requests {self.sim.unmodeled()}"


def app_container() -> str:
    named = os.getenv(APP_CONTAINER_ENV, "").strip()
    if named:
        return named
    out = subprocess.run(["docker", "ps", "--filter", "label=com.docker.compose.service=pipeshub-ai", "-q"],
                         capture_output=True, text=True, timeout=30, check=False).stdout.split()
    assert len(out) == 1, f"{len(out)} pipeshub-ai containers are running; set {APP_CONTAINER_ENV} to the one under test"
    return out[0]


def resolved_in_container(container: str, host: str) -> str:
    out = subprocess.run(["docker", "exec", container, "python3", "-c",
                          "import socket,sys;print(socket.gethostbyname(sys.argv[1]))", host],
                         capture_output=True, text=True, timeout=60, check=False)
    assert out.returncode == 0, f"{host} does not resolve in {container}: {out.stderr.strip()}"
    return out.stdout.strip()


@pytest.fixture(scope="session")
def atlassian_front() -> dict[str, str]:
    """Both Atlassian OAuth hosts resolve, inside the PipesHub container, to the simulator's TLS front and to nothing else.
    Fails, never skips: a stack without the front must not run the suite."""
    if shutil.which("docker") is None:
        pytest.fail("the docker CLI is needed to check where the Atlassian names resolve in the PipesHub container")
    container = app_container()
    front = {"jira-cloud-oauth-tls": resolved_in_container(container, "jira-cloud-oauth-tls")}
    for host in (AUTH_HOST, API_HOST):
        front[host] = resolved_in_container(container, host)
        assert front[host] == front["jira-cloud-oauth-tls"], (
            f"{host} resolves to {front[host]} in {container}, not to the simulator's front: a sign-in would reach Atlassian")
    return front


@pytest.fixture(scope="session")
def oauth_sim(atlassian_front: dict[str, str]) -> OAuthSim:
    url = os.getenv(OAUTH_SIM_URL_ENV, "").strip()
    if not url:
        pytest.fail(f"{OAUTH_SIM_URL_ENV} is not set: this suite needs the jira-cloud-oauth-sim overlay")
    sim = OAuthSim(url)
    try:
        sim.call("GET", "/_sim/health")
    except (requests.RequestException, RuntimeError) as exc:
        pytest.fail(f"{OAUTH_SIM_URL_ENV}={url} does not answer: {exc}")
    return sim


@pytest_asyncio.fixture(scope="session", loop_scope="session")
async def oauth_people(oauth_sim: OAuthSim, pipeshub_client: PipeshubClient, users_client: Any, graph_provider: Any) -> dict[str, str]:
    """A PipesHub account for every person of the site with a visible address, the PipesHub test user and an outsider."""
    pipeshub_client._ensure_access_token()
    oauth_sim.reset()
    emails = [u["email"] for u in oauth_sim.users() if u["email"]]
    return {e: await _active_user(users_client, graph_provider, e) for e in [*emails, CREATOR_EMAIL, OUTSIDER_EMAIL]}


@pytest_asyncio.fixture(loop_scope="session")
async def oauth_world(oauth_sim: OAuthSim, oauth_people: dict[str, str], pipeshub_client: PipeshubClient,
                      graph_provider: Any, users_client: Any):
    world = OAuthWorld(oauth_sim, pipeshub_client, graph_provider, dict(oauth_people), users_client)
    try:
        yield world
    finally:
        try:
            oauth_sim.call("DELETE", "/_sim/faults")
        except (requests.RequestException, RuntimeError):
            pass
        if world.connector_id and os.getenv("JIRA_CLOUD_SIM_KEEP_CONNECTOR") != "1":
            for action in (lambda: world.client.toggle_sync(world.connector_id, enable=False),
                           lambda: world.client.delete_connector(world.connector_id)):
                try:
                    action()
                except (PipeshubClientError, requests.RequestException):
                    pass
