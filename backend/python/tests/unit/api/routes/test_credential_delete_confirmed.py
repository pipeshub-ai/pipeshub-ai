"""Credential removal must not answer success when the stored credential is still there (API1-S1, C-41).

Every route that disconnects a credential (toolsets, MCP servers, API toolsets; the user's and an
agent's; DELETE .../credentials and POST .../reauthenticate) and DELETE /api-toolsets/instances/{id}
is driven through a real FastAPI app over a config store that answers deletes one way:

- ``refused``: ``delete_config`` answers False and the key is still stored (a read finds it);
- ``down``: ``delete_config`` answers False (``ConfigurationService`` swallows the store's error) and a
  confirming read raises, as an unreachable etcd/Redis does;
- ``raises``: ``delete_config`` itself raises ``ConnectionError`` (a wrapper that does not swallow, or the binder);
- ``bug``: ``delete_config`` raises ``ValueError``, a fault of ours, not of the store;
- ``ok``: a working store.

A store that kept the key or could not answer is a 503 with Retry-After; a bug of ours a 500; a working store, and a
key that was never there, the route's usual success.
"""
from __future__ import annotations

import logging
import secrets
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.agents.constants.api_toolset_constants import (
    SHARED_CREDENTIAL_OWNER,
    get_credentials_path,
    get_instance_path,
    get_oauth_client_path,
)
from app.agents.mcp import service as mcp_service
from app.agents.openapi import service as api_service
from app.agents.openapi.models import AuthType
from app.api.routes import api_toolsets as api_routes
from app.api.routes import mcp_servers as mcp_routes
from app.api.routes import toolsets as ts_routes
from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.core.org_context import OrgContext, bind
from app.edition_containers import ConnectorAppContainer
from app.utils.env_flags import CONTROL_PLANE_CREDENTIALS
from tests.support.in_memory_config import InMemoryConfigService

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

ORG, USER, AGENT, INST = "org-1", "user-1", "agent-1", "inst-1"
STORED = {"isAuthenticated": True, "auth": {"apiToken": "t"}}


class StuckConfig(InMemoryConfigService):
    """A store that answers every delete as ``mode`` says (see the module docstring)."""

    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    async def delete_config(self, key: str) -> bool:
        if self.mode == "ok":
            return await super().delete_config(key)
        if self.mode == "raises":
            raise ConnectionError("store unreachable")
        if self.mode == "bug":
            raise ValueError("a fault of ours")
        return False

    async def get_config(self, key: str, default: Any = None, use_cache: bool = True, **kw: Any) -> Any:  # noqa: ANN401
        if self.mode == "down" and kw.get("raise_on_error"):
            raise ConnectionError("store unreachable")
        return await super().get_config(key, default, use_cache)


KEPT = ["refused", "down", "raises"]


def _app(config: object, router: Any) -> FastAPI:  # noqa: ANN401
    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        request.state.user = {"userId": USER, "orgId": ORG}
        with bind(OrgContext(ORG, USER, "user", "jwt", "req-api1-s1")):
            return await call_next(request)

    app.state.config_service = config
    app.include_router(router)
    return app


async def _ok(*_: object, **__: object) -> None:
    return None


def _toolsets_app(config: object, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> FastAPI:
    async def instances(*_: object) -> list[dict[str, Any]]:
        return [{"_id": INST, "orgId": ORG}]

    monkeypatch.setattr(ts_routes, "_find_instance", _ok)
    monkeypatch.setattr(ts_routes, "_require_agent_edit_access", _ok)
    monkeypatch.setattr(ts_routes, "_load_toolset_instances", instances)
    container = ConnectorAppContainer()
    container.config_service.override(providers.Object(config))
    container.wire(modules=[ts_routes])
    request.addfinalizer(container.unwire)
    return _app(config, ts_routes.router)


def _mcp_app(
    config: object, monkeypatch: pytest.MonkeyPatch, _request: pytest.FixtureRequest, auth_mode: str = "api_token",
) -> FastAPI:
    instance = {"_id": INST, "orgId": ORG, "authMode": auth_mode}

    async def get_instance(*_: object) -> dict[str, Any]:
        return dict(instance)

    async def own_path(_instance: object, user_id: str, _config: object) -> str:
        return mcp_routes.get_mcp_credentials_path(INST, user_id)

    monkeypatch.setattr(mcp_routes, "_get_org_instance", get_instance)
    monkeypatch.setattr(mcp_routes, "_assert_admin_owns_shared_credential", _ok)
    monkeypatch.setattr(mcp_routes, "_shared_aware_credential_path", own_path)
    monkeypatch.setattr(mcp_routes, "_require_mcp_agent_edit_access", _ok)
    app = _app(config, mcp_routes.router)
    app.dependency_overrides[mcp_routes._require_mcp_enabled] = lambda: None
    return app


def _api_app(shared: bool) -> Callable[[object, pytest.MonkeyPatch, pytest.FixtureRequest], FastAPI]:
    def build(config: object, monkeypatch: pytest.MonkeyPatch, _request: pytest.FixtureRequest) -> FastAPI:
        instance = SimpleNamespace(
            id=INST, name="Acme", use_shared_credential=shared, auth_scheme=SimpleNamespace(type=AuthType.API_KEY),
        )

        async def admin(*_: object) -> SimpleNamespace:
            return SimpleNamespace(is_admin=True)

        async def enabled(*_: object, **__: object) -> bool:
            return True

        async def found(*_: object) -> SimpleNamespace:
            return instance

        monkeypatch.setattr(api_routes, "fetch_caller_role", admin)
        monkeypatch.setattr(api_routes, "is_api_toolsets_enabled", enabled)
        monkeypatch.setattr(api_routes, "_instance_or_404", found)
        app = _app(config, api_routes.router)
        app.state.graph_provider = SimpleNamespace(check_api_toolset_in_use=lambda _id: _empty())
        return app

    return build


async def _empty() -> list[str]:
    return []


# (id, builder, method, path, the credential's key, body on success, 500 detail for a bug of ours or None for the default)
ROUTES = [
    ("T1", _toolsets_app, "DELETE", f"/api/v1/toolsets/instances/{INST}/credentials", ts_routes._get_user_auth_path(INST, USER),
     {"status": "success", "message": "Credentials removed successfully."}, "Failed to remove credentials."),
    ("T2", _toolsets_app, "POST", f"/api/v1/toolsets/instances/{INST}/reauthenticate", ts_routes._get_user_auth_path(INST, USER),
     {"status": "success", "message": "Credentials cleared. Please re-authenticate."}, "Failed to clear credentials."),
    ("T3", _toolsets_app, "DELETE", f"/api/v1/toolsets/agents/{AGENT}/instances/{INST}/credentials",
     ts_routes._get_agent_auth_path(INST, AGENT),
     {"status": "success", "message": "Agent credentials removed successfully."}, "Failed to remove agent credentials."),
    ("T4", _toolsets_app, "POST", f"/api/v1/toolsets/agents/{AGENT}/instances/{INST}/reauthenticate",
     ts_routes._get_agent_auth_path(INST, AGENT),
     {"status": "success", "message": "Agent credentials cleared. Please re-authenticate."}, "Failed to clear agent credentials."),
    ("M1", _mcp_app, "DELETE", f"/api/v1/mcp-servers/instances/{INST}/credentials",
     mcp_routes.get_mcp_credentials_path(INST, USER), {"success": True}, None),
    ("M2", _mcp_app, "POST", f"/api/v1/mcp-servers/instances/{INST}/reauthenticate",
     mcp_routes.get_mcp_credentials_path(INST, USER), {"success": True}, None),
    ("M3", _mcp_app, "DELETE", f"/api/v1/mcp-servers/agents/{AGENT}/instances/{INST}/credentials",
     mcp_routes.get_mcp_credentials_path(INST, AGENT), {"success": True}, None),
    ("M4", _mcp_app, "POST", f"/api/v1/mcp-servers/agents/{AGENT}/instances/{INST}/reauthenticate",
     mcp_routes.get_mcp_credentials_path(INST, AGENT), {"success": True}, None),
    ("A1", _api_app(False), "DELETE", f"/api/v1/api-toolsets/instances/{INST}/credentials", get_credentials_path(INST, USER),
     {"success": True, "isConnected": False}, None),
    ("A2", _api_app(True), "DELETE", f"/api/v1/api-toolsets/instances/{INST}/shared-credentials",
     get_credentials_path(INST, SHARED_CREDENTIAL_OWNER), {"success": True, "isConnected": False}, None),
    ("A3", _api_app(False), "DELETE", f"/api/v1/api-toolsets/instances/{INST}", get_credentials_path(INST, USER),
     {"success": True}, None),
]
ROUTE_IDS = [r[0] for r in ROUTES]


@pytest.fixture(autouse=True)
def _switch_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(CONTROL_PLANE_CREDENTIALS, raising=False)


def _call(route: tuple[Any, ...], config: StuckConfig, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> httpx.Response:
    _, build, method, path, *_ = route
    client = TestClient(build(config, monkeypatch, request), raise_server_exceptions=False)
    return client.request(method, path)


@pytest.mark.parametrize("mode", KEPT)
@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_x1_a_credential_the_store_kept_is_a_503_never_a_success(
    route: tuple[Any, ...], mode: str, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    config = StuckConfig(mode)
    config.values[route[4]] = dict(STORED)
    resp = _call(route, config, monkeypatch, request)
    assert resp.status_code == 503, (resp.status_code, resp.text)
    assert resp.headers.get("Retry-After")
    assert "success" not in resp.text and "removed" not in resp.text.lower()
    assert config.values[route[4]] == STORED


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_x2_a_working_store_removes_the_credential_and_answers_as_before(
    route: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    config = StuckConfig("ok")
    config.values[route[4]] = dict(STORED)
    config.values[get_instance_path(INST)] = {"id": INST}
    resp = _call(route, config, monkeypatch, request)
    assert resp.status_code == 200, (resp.status_code, resp.text)
    assert resp.json() == route[5]
    assert route[4] not in config.values


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_x3_nothing_stored_is_still_a_success(
    route: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    # "refused" with no key: delete_config answers False for an absent key, the confirming read finds nothing.
    resp = _call(route, StuckConfig("refused"), monkeypatch, request)
    assert resp.status_code == 200, (resp.status_code, resp.text)
    assert resp.json() == route[5]


@pytest.mark.parametrize("route", ROUTES, ids=ROUTE_IDS)
def test_x4_a_fault_of_ours_is_a_500_not_a_503(
    route: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    config = StuckConfig("bug")
    config.values[route[4]] = dict(STORED)
    resp = _call(route, config, monkeypatch, request)
    assert resp.status_code == 500, (resp.status_code, resp.text)
    assert "Retry-After" not in resp.headers
    if route[6] is not None:
        assert resp.json()["detail"] == route[6]


class DcrKept(StuckConfig):
    """Deletes the credential; keeps the per-owner DCR client."""

    async def delete_config(self, key: str) -> bool:
        return False if key.endswith("dcr-client") else await super().delete_config(key)


@pytest.mark.parametrize("route", [r for r in ROUTES if r[0] in ("M1", "M2", "M4")], ids=["M1", "M2", "M4"])
def test_a_kept_dcr_client_is_a_503(
    route: tuple[Any, ...], monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    """The caller's own registered OAuth client goes with the credential (on DELETE only for an OAuth instance)."""
    config = DcrKept("ok")
    owner = AGENT if route[0] == "M4" else USER
    dcr = mcp_routes.get_mcp_dcr_client_path(INST, owner)
    config.values[route[4]] = dict(STORED)
    config.values[dcr] = {"clientId": "c"}
    app = _mcp_app(config, monkeypatch, request, auth_mode="oauth")
    resp = TestClient(app, raise_server_exceptions=False).request(route[2], route[3])
    assert resp.status_code == 503, (resp.status_code, resp.text)
    assert "success" not in resp.text
    assert dcr in config.values


# ---------------------------------------------------------------- X3/X1 over a real ConfigurationService


class DownKV(InMemoryKeyValueStore):
    def __init__(self) -> None:
        super().__init__(logging.getLogger("tests.api1_s1"))
        self.down = False

    async def delete_key(self, key: str) -> bool:
        if self.down:
            raise ConnectionError("kv down")
        return await super().delete_key(key)

    async def get_key(self, key: str, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        if self.down:
            raise ConnectionError("kv down")
        return await super().get_key(key)


@pytest.fixture
async def real(monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest) -> AsyncIterator[tuple[httpx.AsyncClient, DownKV]]:
    monkeypatch.setenv("SECRET_KEY", secrets.token_urlsafe(32))
    kv = DownKV()
    service = ConfigurationService(logging.getLogger("tests.api1_s1"), kv)
    app = _mcp_app(service, monkeypatch, request)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
        yield client, kv
    await service.close()


REAL_PATH = f"/api/v1/mcp-servers/instances/{INST}/reauthenticate"
REAL_KEY = mcp_routes.get_mcp_credentials_path(INST, USER)


async def test_real_service_nothing_stored_is_a_success(real: tuple[httpx.AsyncClient, DownKV]) -> None:
    client, _ = real
    resp = await client.post(REAL_PATH)
    assert resp.status_code == 200, resp.text


async def test_real_service_a_stored_credential_is_removed(real: tuple[httpx.AsyncClient, DownKV]) -> None:
    client, kv = real
    await kv.create_key(REAL_KEY, dict(STORED))
    resp = await client.post(REAL_PATH)
    assert resp.status_code == 200, resp.text
    assert await kv.get_key(REAL_KEY) is None


async def test_real_service_a_store_outage_is_a_503(real: tuple[httpx.AsyncClient, DownKV]) -> None:
    client, kv = real
    await kv.create_key(REAL_KEY, dict(STORED))
    kv.down = True
    resp = await client.post(REAL_PATH)
    kv.down = False
    assert resp.status_code == 503, resp.text
    assert resp.headers.get("Retry-After")
    assert await kv.get_key(REAL_KEY) == STORED


# ---------------------------------------------------------------- X7: PUT /mcp-servers, useAdminAuth switched off


def test_x7_switching_off_admin_auth_over_a_store_that_kept_the_shared_credential_is_a_503_and_saves_nothing(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    existing = {"_id": INST, "orgId": ORG, "name": "Acme", "authMode": "api_token", "transport": "streamable_http",
                "useAdminAuth": True, "url": "https://mcp.acme.test"}
    record = {**existing, "useAdminAuth": False}
    config = StuckConfig("refused")
    shared = mcp_routes.get_mcp_credentials_path(INST, mcp_service.SHARED_CREDENTIAL_OWNER)
    config.values[shared] = dict(STORED)
    config.values[mcp_service.instance_record_path(existing)] = dict(existing)

    async def get_instance(*_: object) -> dict[str, Any]:
        return dict(existing)

    monkeypatch.setattr(mcp_routes, "_get_org_instance", get_instance)
    monkeypatch.setattr(mcp_routes, "_require_manage_access", _ok)
    monkeypatch.setattr(mcp_routes, "forbid_inherited_mcp_mutation", lambda _i: None)
    monkeypatch.setattr(mcp_routes, "_get_mcp_registry", lambda _r: None)
    monkeypatch.setattr(mcp_routes, "_validate_instance_config", lambda *_: None)
    monkeypatch.setattr(mcp_routes, "_build_instance_record", lambda *_a, **_k: dict(record))
    monkeypatch.setattr(mcp_routes, "_assert_instance_urls_allowed", lambda _r: None)
    monkeypatch.setattr(mcp_routes, "sync_capabilities", _ok)
    app = _app(config, mcp_routes.router)
    app.dependency_overrides[mcp_routes._require_mcp_enabled] = lambda: None
    body = {"name": "Acme", "transport": "streamable_http", "authMode": "api_token", "useAdminAuth": False,
            "url": "https://mcp.acme.test"}
    resp = TestClient(app, raise_server_exceptions=False).put(f"/api/v1/mcp-servers/instances/{INST}", json=body)
    assert resp.status_code == 503, (resp.status_code, resp.text)
    assert config.values[mcp_service.instance_record_path(existing)] == existing
    assert config.values[shared] == STORED


# ---------------------------------------------------------------- X8: PUT /api-toolsets, auth scheme changed


def test_x8_a_new_auth_scheme_over_a_store_that_kept_the_old_credentials_is_a_503_and_saves_nothing(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> None:
    config = StuckConfig("refused")
    config.values[get_credentials_path(INST, USER)] = {"apiKey": "k"}
    config.values[get_instance_path(INST)] = {"id": INST, "old": True}
    app = _api_app(False)(config, monkeypatch, request)
    new = SimpleNamespace(id=INST, auth_scheme=SimpleNamespace(type=AuthType.OAUTH2_AUTHORIZATION_CODE))
    monkeypatch.setattr(api_service, "build_instance", lambda *_a, **_k: new)
    monkeypatch.setattr(api_service, "credentials_still_apply", lambda *_: False)
    body = {"name": "Acme", "baseUrl": "https://api.acme.test", "operations": [], "oauthClientId": "new-app"}
    resp = TestClient(app, raise_server_exceptions=False).put(f"/api/v1/api-toolsets/instances/{INST}", json=body)
    assert resp.status_code == 503, (resp.status_code, resp.text)
    assert config.values[get_instance_path(INST)] == {"id": INST, "old": True}
    assert get_oauth_client_path(INST) not in config.values
