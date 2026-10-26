"""API Toolsets routes, through FastAPI with an in-memory config store.

The admin check and the Labs flag are the only things replaced; everything from the
request body to the stored record runs for real. The test call goes to the Acme
reference server on loopback, which the SSRF guard would refuse, so that one test
swaps the guard for a plain transport.
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import threading
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.agents.mcp.models import OAuthTokens
from app.agents.openapi import executor as executor_module
from app.agents.openapi import service as service_module
from app.api.routes import api_toolsets as routes
from app.utils.desktop_state import DESKTOP_STATE_PREFIX
from tests.support.in_memory_config import InMemoryConfigService

if TYPE_CHECKING:
    from collections.abc import Iterator

ACME_DIR = Path(__file__).resolve().parents[6] / "integration-tests" / "openapi_fixture"
ACME_SPEC = (ACME_DIR / "openapi.yaml").read_text()
ORG, ADMIN, MEMBER = "org-1", "admin-1", "member-1"
PREFIX = "/api/v1/api-toolsets"

AUTH_CODE_SPEC = """
openapi: 3.0.3
info: {title: Wiki, version: "2"}
paths:
  /pages:
    get: {operationId: list_pages, summary: List pages}
components:
  securitySchemes:
    sso:
      type: oauth2
      flows:
        authorizationCode:
          authorizationUrl: https://login.acme.test/authorize
          tokenUrl: https://login.acme.test/token
          scopes: {pages.read: Read pages}
"""


class Harness:
    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.config = InMemoryConfigService()
        self.enabled = True
        self.admins = {ADMIN}
        # Names of the agents the graph reports for any toolset, or an exception it raises.
        self.used_by: list[str] | Exception = []
        app = FastAPI()
        app.state.config_service = self.config

        async def check_api_toolset_in_use(_instance_id: str) -> list[str]:
            if isinstance(self.used_by, Exception):
                raise self.used_by
            return self.used_by

        app.state.graph_provider = SimpleNamespace(check_api_toolset_in_use=check_api_toolset_in_use)

        @app.middleware("http")
        async def authenticate(request: Request, call_next: Any) -> Any:  # noqa: ANN401
            request.state.user = {"userId": request.headers.get("x-test-user", MEMBER), "orgId": request.headers.get("x-test-org", ORG)}
            return await call_next(request)

        async def caller_role(request: Request, _config: Any) -> SimpleNamespace:  # noqa: ANN401
            return SimpleNamespace(is_admin=request.state.user["userId"] in self.admins)

        async def enabled(_config: Any) -> bool:  # noqa: ANN401
            return self.enabled

        monkeypatch.setattr(routes, "fetch_caller_role", caller_role)
        monkeypatch.setattr(routes, "is_api_toolsets_enabled", enabled)
        app.include_router(routes.router)
        self.client = TestClient(app)

    def call(self, method: str, path: str, *, user: str = MEMBER, org: str = ORG, **kwargs: Any) -> httpx.Response:  # noqa: ANN401
        return self.client.request(method, PREFIX + path, headers={"x-test-user": user, "x-test-org": org}, **kwargs)

    def create(self, **overrides: Any) -> dict[str, Any]:  # noqa: ANN401
        body = {
            "name": "Acme Helpdesk", "spec": ACME_SPEC, "baseUrl": "https://helpdesk.acme.test",
            "authSchemeName": "apiKeyAuth", "operations": ["list_tickets", "get_ticket", "update_ticket"], **overrides,
        }
        resp = self.call("POST", "/instances", user=ADMIN, json=body)
        assert resp.status_code == 200, resp.text
        return resp.json()


@pytest.fixture
def api(monkeypatch: pytest.MonkeyPatch) -> Harness:
    return Harness(monkeypatch)


def _stored_secrets(config: InMemoryConfigService, instance_id: str) -> list[str]:
    """Every stored value under the instance's record, flattened to text."""
    return [str(v) for k, v in config.values.items() if k.endswith(f"/instances/{instance_id}")]


class TestAccess:
    def test_everything_is_off_without_the_labs_flag(self, api: Harness) -> None:
        api.enabled = False
        resp = api.call("GET", "/instances", user=ADMIN)
        assert resp.status_code == 403 and "disabled" in resp.json()["detail"]

    @pytest.mark.parametrize(("method", "path"), [("POST", "/validate"), ("POST", "/instances")])
    def test_members_cannot_add_toolsets(self, api: Harness, method: str, path: str) -> None:
        resp = api.call(method, path, json={"spec": ACME_SPEC, "name": "x", "baseUrl": "https://a.test", "operations": ["a"]})
        assert resp.status_code == 403

    def test_members_cannot_change_or_delete(self, api: Harness) -> None:
        created = api.create()
        assert api.call("PUT", f"/instances/{created['id']}", json={"name": "x", "baseUrl": "https://a.test", "operations": ["get_ticket"]}).status_code == 403
        assert api.call("DELETE", f"/instances/{created['id']}").status_code == 403

    def test_another_org_sees_nothing(self, api: Harness) -> None:
        created = api.create()
        assert api.call("GET", "/instances", org="org-2").json()["instances"] == []
        assert api.call("GET", f"/instances/{created['id']}", org="org-2").status_code == 404
        assert api.call("DELETE", f"/instances/{created['id']}", user=ADMIN, org="org-2").status_code == 404


class TestValidate:
    def test_preview_lists_operations_schemes_and_servers(self, api: Harness) -> None:
        resp = api.call("POST", "/validate", user=ADMIN, json={"spec": ACME_SPEC})
        assert resp.status_code == 200
        body = resp.json()
        assert [op["toolName"] for op in body["operations"]][:2] == ["list_tickets", "create_ticket"]
        assert {s["name"] for s in body["authSchemes"]} == {"apiKeyAuth", "bearerAuth", "basicAuth", "oauth2ClientCredentials", "oauth2SignIn"}
        assert body["servers"] == [{"url": "http://localhost:8098", "description": "Any Acme Helpdesk deployment."}]

    def test_bad_spec_says_why(self, api: Harness) -> None:
        resp = api.call("POST", "/validate", user=ADMIN, json={"spec": "swagger: '2.0'"})
        assert resp.status_code == 400 and "Swagger 2.0" in resp.json()["detail"]

    def test_a_spec_is_compiled_off_the_event_loop(self, api: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
        # A big YAML spec takes seconds to parse; on the loop, the service would stop answering meanwhile.
        where: list[str] = []
        real = service_module.compile_spec

        def noting_where(raw: bytes) -> Any:  # noqa: ANN401
            try:
                asyncio.get_running_loop()
                where.append("event loop")
            except RuntimeError:
                where.append("worker thread")
            return real(raw)

        monkeypatch.setattr(routes, "compile_spec", noting_where)
        monkeypatch.setattr(service_module, "compile_spec", noting_where)
        assert api.call("POST", "/validate", user=ADMIN, json={"spec": ACME_SPEC}).status_code == 200
        api.create()
        assert where == ["worker thread", "worker thread"]


class TestInstances:
    def test_create_stores_only_the_selected_operations(self, api: Harness) -> None:
        created = api.create()
        stored = api.config.values[f"/services/api-toolsets/instances/{created['id']}"]
        assert [op["toolName"] for op in stored["operations"]] == ["list_tickets", "get_ticket", "update_ticket"]
        assert stored["orgId"] == ORG and stored["createdBy"] == ADMIN
        assert stored["authScheme"]["type"] == "api_key"
        assert "spec" not in stored

    def test_connection_details_are_on_the_admin_route_only(self, api: Harness) -> None:
        created = api.create()
        member_view = api.call("GET", "/instances").json()["instances"][0]
        assert "baseUrl" not in member_view and member_view["isConnected"] is False
        assert "baseUrl" not in api.call("GET", f"/instances/{created['id']}").json()
        assert api.call("GET", f"/instances/{created['id']}/config").status_code == 403
        config = api.call("GET", f"/instances/{created['id']}/config", user=ADMIN).json()
        assert config["baseUrl"] == "https://helpdesk.acme.test" and config["authScheme"]["name"] == "apiKeyAuth"
        assert member_view["operations"][0] == {"toolName": "list_tickets", "method": "GET", "path": "/tickets",
                                                "summary": "List helpdesk tickets, optionally filtered.", "kind": "read",
                                                "requiresAuth": True}

    def test_get_one_includes_input_schemas(self, api: Harness) -> None:
        created = api.create()
        op = api.call("GET", f"/instances/{created['id']}").json()["operations"][2]
        assert op["toolName"] == "update_ticket" and op["inputSchema"]["required"] == ["ticketId", "body"]

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"operations": ["nope"]}, "no operation nope"),
            ({"operations": []}, "at least one operation"),
            ({"authSchemeName": "sso"}, "no security scheme named sso"),
            ({"authSchemeName": None}, "Choose how PipesHub authenticates"),
            ({"baseUrl": "ftp://files.acme.test"}, "http(s) URL"),
            ({"baseUrl": "https://user:pw@acme.test"}, "http(s) URL"),
            ({"baseUrl": "http://169.254.169.254"}, "not allowed"),
            ({"baseUrl": "http://localhost:8098"}, "not allowed"),
            ({"timeoutSeconds": 0}, "between 1 and"),
            ({"name": "  "}, "name"),
            ({"spec": None}, "Upload the API's OpenAPI spec"),
            ({"authSchemeName": "oauth2ClientCredentials", "scopes": ["admin"]}, "no scope admin"),
        ],
    )
    def test_invalid_toolsets_are_refused_with_the_reason(self, api: Harness, overrides: dict[str, Any], message: str) -> None:
        body = {"name": "Acme", "spec": ACME_SPEC, "baseUrl": "https://helpdesk.acme.test",
                "authSchemeName": "apiKeyAuth", "operations": ["get_ticket"], **overrides}
        resp = api.call("POST", "/instances", user=ADMIN, json=body)
        assert resp.status_code == 400 and message in resp.json()["detail"]

    def test_client_credentials_are_always_shared(self, api: Harness) -> None:
        created = api.create(authSchemeName="oauth2ClientCredentials", scopes=["tickets:read"], useSharedCredential=False)
        assert created["useSharedCredential"] is True
        assert created["scopes"] == ["tickets:read"]

    def test_update_without_a_spec_reselects_from_the_stored_operations(self, api: Harness) -> None:
        created = api.create()
        resp = api.call("PUT", f"/instances/{created['id']}", user=ADMIN,
                        json={"name": "Helpdesk", "baseUrl": "https://helpdesk.acme.test/v1", "operations": ["get_ticket"]})
        assert resp.status_code == 200, resp.text
        assert [op["toolName"] for op in resp.json()["operations"]] == ["get_ticket"]
        assert resp.json()["createdAt"] == created["createdAt"]
        refused = api.call("PUT", f"/instances/{created['id']}", user=ADMIN,
                           json={"name": "H", "baseUrl": "https://a.test", "operations": ["create_ticket"]})
        assert refused.status_code == 400

    def test_changing_the_auth_scheme_drops_old_credentials(self, api: Harness) -> None:
        created = api.create()
        api.call("PUT", f"/instances/{created['id']}/credentials", json={"apiKey": "k"})
        api.call("PUT", f"/instances/{created['id']}", user=ADMIN, json={
            "name": "Acme", "spec": ACME_SPEC, "baseUrl": "https://helpdesk.acme.test",
            "authSchemeName": "bearerAuth", "operations": ["get_ticket"]})
        assert api.call("GET", "/instances").json()["instances"][0]["isConnected"] is False

    @pytest.mark.parametrize(
        ("base_url", "still_connected"),
        [
            ("https://helpdesk.acme.test/v2", True),
            ("https://helpdesk.acme.test:443", True),
            ("https://other.acme.test", False),
            ("http://helpdesk.acme.test", False),
            ("https://helpdesk.acme.test:8443", False),
        ],
    )
    def test_moving_the_api_to_another_host_drops_old_credentials(
        self, api: Harness, base_url: str, still_connected: bool,
    ) -> None:
        created = api.create()
        api.call("PUT", f"/instances/{created['id']}/credentials", json={"apiKey": "k"})
        api.call("PUT", f"/instances/{created['id']}", user=ADMIN, json={
            "name": "Acme", "baseUrl": base_url, "operations": ["get_ticket"]})
        assert api.call("GET", "/instances").json()["instances"][0]["isConnected"] is still_connected

    def test_a_toolset_an_agent_uses_is_not_deleted(self, api: Harness) -> None:
        created = api.create()
        api.used_by = ["Support bot", "Triage", "Escalations", "Night shift"]
        resp = api.call("DELETE", f"/instances/{created['id']}", user=ADMIN)
        assert resp.status_code == 409
        assert resp.json()["detail"] == (
            "Acme Helpdesk is used by 'Support bot', 'Triage', 'Escalations' and 1 more. Remove it from those agents first."
        )
        assert api.call("GET", f"/instances/{created['id']}").status_code == 200

    def test_nothing_is_deleted_when_usage_cannot_be_read(self, api: Harness) -> None:
        created = api.create()
        api.used_by = RuntimeError("graph down")
        assert api.call("DELETE", f"/instances/{created['id']}", user=ADMIN).status_code == 503
        assert api.call("GET", f"/instances/{created['id']}").status_code == 200

    def test_delete_removes_the_record_and_every_credential(self, api: Harness) -> None:
        created = api.create()
        api.call("PUT", f"/instances/{created['id']}/credentials", json={"apiKey": "k"})
        assert api.call("DELETE", f"/instances/{created['id']}", user=ADMIN).status_code == 200
        assert not [k for k in api.config.values if created["id"] in k]


class TestCredentials:
    def test_each_member_connects_their_own(self, api: Harness) -> None:
        created = api.create()
        resp = api.call("PUT", f"/instances/{created['id']}/credentials", json={"apiKey": " s3cret ", "token": "ignored"})
        assert resp.status_code == 200
        assert api.config.values[f"/services/api-toolsets/credentials/{created['id']}/{MEMBER}"] == {"apiKey": "s3cret"}
        assert api.call("GET", "/instances").json()["instances"][0]["isConnected"] is True
        assert api.call("GET", "/instances", user="member-2").json()["instances"][0]["isConnected"] is False
        assert all("s3cret" not in text for text in _stored_secrets(api.config, created["id"]))

    def test_missing_field_is_named(self, api: Harness) -> None:
        created = api.create(authSchemeName="basicAuth")
        resp = api.call("PUT", f"/instances/{created['id']}/credentials", json={"username": "u"})
        assert resp.status_code == 400 and "password" in resp.json()["detail"]

    def test_shared_credentials_are_for_admins(self, api: Harness) -> None:
        created = api.create(useSharedCredential=True)
        own = api.call("PUT", f"/instances/{created['id']}/credentials", json={"apiKey": "k"})
        assert own.status_code == 400 and "administrator manages" in own.json()["detail"]
        assert api.call("PUT", f"/instances/{created['id']}/shared-credentials", json={"apiKey": "k"}).status_code == 403
        assert api.call("PUT", f"/instances/{created['id']}/shared-credentials", user=ADMIN, json={"apiKey": "k"}).status_code == 200
        assert api.call("GET", "/instances").json()["instances"][0]["isConnected"] is True
        assert api.call("DELETE", f"/instances/{created['id']}/shared-credentials").status_code == 403
        assert api.call("DELETE", f"/instances/{created['id']}/shared-credentials", user=ADMIN).json()["isConnected"] is False

    def test_shared_routes_refuse_a_per_member_toolset(self, api: Harness) -> None:
        created = api.create()
        resp = api.call("PUT", f"/instances/{created['id']}/shared-credentials", user=ADMIN, json={"apiKey": "k"})
        assert resp.status_code == 400 and "each member connects their own" in resp.json()["detail"]

    @pytest.mark.parametrize(
        ("method", "path"),
        [("GET", "/config"), ("PUT", ""), ("DELETE", ""), ("PUT", "/shared-credentials"),
         ("DELETE", "/shared-credentials"), ("GET", "/shared-credentials/oauth/authorize")],
    )
    def test_admin_routes_refuse_members_before_looking_anything_up(self, api: Harness, method: str, path: str) -> None:
        resp = api.call(method, f"/instances/no-such-id{path}", json={"name": "x", "baseUrl": "https://a.test", "operations": ["a"]})
        assert resp.status_code == 403 and "administrators" in resp.json()["detail"]

    def test_disconnect(self, api: Harness) -> None:
        created = api.create()
        api.call("PUT", f"/instances/{created['id']}/credentials", json={"apiKey": "k"})
        resp = api.call("DELETE", f"/instances/{created['id']}/credentials")
        assert resp.json() == {"success": True, "isConnected": False}

    def test_no_auth_toolset_is_always_connected(self, api: Harness) -> None:
        api.create(authSchemeName="none")
        assert api.call("GET", "/instances").json()["instances"][0]["isConnected"] is True


class TestOAuthSignIn:
    def _create(self, api: Harness) -> dict[str, Any]:
        return api.create(spec=AUTH_CODE_SPEC, authSchemeName="sso", operations=["list_pages"], scopes=["pages.read"],
                          oauthClientId="wiki-app", oauthClientSecret="app-secret")

    def test_client_id_is_required(self, api: Harness) -> None:
        resp = api.call("POST", "/instances", user=ADMIN, json={
            "name": "Wiki", "spec": AUTH_CODE_SPEC, "baseUrl": "https://wiki.acme.test", "authSchemeName": "sso", "operations": ["list_pages"]})
        assert resp.status_code == 400 and "client ID" in resp.json()["detail"]

    def test_admin_view_never_shows_the_app_secret(self, api: Harness) -> None:
        created = self._create(api)
        view = api.call("GET", f"/instances/{created['id']}/config", user=ADMIN).json()
        assert view["oauthClientId"] == "wiki-app" and view["hasOauthClientSecret"] is True
        assert "app-secret" not in str(view)

    def test_entering_credentials_is_refused(self, api: Harness) -> None:
        created = self._create(api)
        resp = api.call("PUT", f"/instances/{created['id']}/credentials", json={"token": "t"})
        assert resp.status_code == 400 and "Sign in" in resp.json()["detail"]

    def test_sign_in_round_trip(self, api: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
        created = self._create(api)
        exchanged: list[dict[str, Any]] = []

        async def exchange(token_url: str, client_id: str, client_secret: str | None, code: str, redirect_uri: str, **kw: Any) -> OAuthTokens:  # noqa: ANN401
            exchanged.append({"token_url": token_url, "client_id": client_id, "secret": client_secret, "code": code,
                              "redirect_uri": redirect_uri, **kw})
            return OAuthTokens(access_token="at", refresh_token="rt", expires_in=3600)

        monkeypatch.setattr(service_module, "exchange_code_for_token", exchange)
        url = api.call("GET", f"/instances/{created['id']}/oauth/authorize").json()["authorizationUrl"]
        parts = urlsplit(url)
        params = {k: v[0] for k, v in parse_qs(parts.query).items()}
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == "https://login.acme.test/authorize"
        assert params["client_id"] == "wiki-app" and params["scope"] == "pages.read"
        assert params["code_challenge_method"] == "S256" and params["redirect_uri"].endswith("/api-toolsets/oauth/callback/")

        state = params["state"]
        stolen = api.call("GET", "/oauth/callback", user="member-2", params={"code": "c", "state": state})
        assert stolen.status_code == 400
        # The failed attempt didn't use up the real sign-in.
        done = api.call("GET", "/oauth/callback", params={"code": "the-code", "state": state})
        assert done.status_code == 200 and done.json()["instanceId"] == created["id"]
        assert exchanged[0]["code"] == "the-code" and exchanged[0]["secret"] == "app-secret"
        assert exchanged[0]["code_verifier"]
        stored = api.config.values[f"/services/api-toolsets/credentials/{created['id']}/{MEMBER}"]
        assert stored["oauth"]["accessToken"] == "at" and "clientSecret" not in stored
        assert api.call("GET", "/instances").json()["instances"][0]["isConnected"] is True

        replay = api.call("GET", "/oauth/callback", params={"code": "the-code", "state": state})
        assert replay.status_code == 400 and "already used" in replay.json()["detail"]


    def test_a_sign_in_from_the_desktop_app_is_marked_and_completes(self, api: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
        created = self._create(api)

        async def exchange(*_a: Any, **_kw: Any) -> OAuthTokens:  # noqa: ANN401
            return OAuthTokens(access_token="at", refresh_token="rt", expires_in=3600)

        monkeypatch.setattr(service_module, "exchange_code_for_token", exchange)
        url = api.call("GET", f"/instances/{created['id']}/oauth/authorize", params={"desktop": "true"}).json()["authorizationUrl"]
        state = parse_qs(urlsplit(url).query)["state"][0]
        # The callback page hands a marked state to the desktop app, which completes it here.
        assert state.startswith(DESKTOP_STATE_PREFIX)
        done = api.call("GET", "/oauth/callback", params={"code": "the-code", "state": state})
        assert done.status_code == 200 and done.json()["instanceId"] == created["id"]


def _load_acme_server() -> Any:  # noqa: ANN401
    spec = importlib.util.spec_from_file_location("acme_server_for_route_tests", ACME_DIR / "server.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def acme_url(monkeypatch: pytest.MonkeyPatch) -> Iterator[str]:
    server = _load_acme_server().serve("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(executor_module, "GuardedTransport", lambda url, allow_private: httpx.AsyncHTTPTransport())
    monkeypatch.setattr(service_module, "check_mcp_url_at_save", lambda url, allow_private: None)
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


class TestTestCall:
    def test_needs_credentials_first(self, api: Harness) -> None:
        created = api.create()
        resp = api.call("POST", f"/instances/{created['id']}/test", json={})
        assert resp.status_code == 400 and "not connected" in resp.json()["detail"]

    def test_runs_the_first_argument_free_read_against_the_api(self, api: Harness, acme_url: str) -> None:
        created = api.create(baseUrl=acme_url)
        api.call("PUT", f"/instances/{created['id']}/credentials", json={"apiKey": "acme-demo-key"})
        resp = api.call("POST", f"/instances/{created['id']}/test", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True and "ACME-7311" in resp.json()["body"]

    def test_no_auth_toolset_tests_with_an_operation_that_needs_no_credentials(self, api: Harness, acme_url: str) -> None:
        created = api.create(baseUrl=acme_url, authSchemeName="none", operations=["list_tickets", "list_announcements"])
        resp = api.call("POST", f"/instances/{created['id']}/test", json={})
        assert resp.status_code == 200, resp.text
        assert resp.json()["ok"] is True and "AN-41" in resp.json()["body"]

    def test_named_operation_and_wrong_key(self, api: Harness, acme_url: str) -> None:
        created = api.create(baseUrl=acme_url)
        api.call("PUT", f"/instances/{created['id']}/credentials", json={"apiKey": "wrong"})
        resp = api.call("POST", f"/instances/{created['id']}/test", json={"toolName": "get_ticket", "arguments": {"ticketId": "ACME-7312"}})
        assert resp.json()["ok"] is False and resp.json()["status"] == 401

    def test_only_reads_can_be_test_calls(self, api: Harness, acme_url: str) -> None:
        created = api.create(baseUrl=acme_url, useSharedCredential=True)
        api.call("PUT", f"/instances/{created['id']}/shared-credentials", user=ADMIN, json={"apiKey": "acme-demo-key"})
        resp = api.call("POST", f"/instances/{created['id']}/test",
                        json={"toolName": "update_ticket", "arguments": {"ticketId": "ACME-7311", "body": {"status": "closed"}}})
        assert resp.status_code == 400 and "changes data" in resp.json()["detail"]

    def test_unreachable_api_is_a_502(self, api: Harness, acme_url: str) -> None:
        created = api.create(baseUrl="http://127.0.0.1:9", authSchemeName="none", operations=["list_announcements"])
        resp = api.call("POST", f"/instances/{created['id']}/test", json={})
        assert resp.status_code == 502 and "Could not reach the API" in resp.json()["detail"]
