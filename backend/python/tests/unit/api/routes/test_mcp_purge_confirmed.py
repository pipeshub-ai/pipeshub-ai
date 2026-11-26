"""PUT /mcp-servers/instances/{id} must not save a new connection target while the old target's sign-in
tokens are still stored.

Driven through a real FastAPI app over a config store that fails part of the purge:

- ``refused``: ``delete_config`` answers False and the key is still stored (what ``ConfigurationService``
  answers after swallowing a store error);
- ``list_fails``: listing the instance's credentials raises (an etcd range that timed out), reads and
  writes work;
- ``delete_raises``: ``delete_config`` raises.

The PUT must answer 503 with Retry-After and keep the old target, so the client never pairs the new URL
with a token stored for the old one. A catalog server whose template lets the admin type the URL (Okta,
``url_editable``) must purge on a URL change too.
"""
from __future__ import annotations

from typing import Any

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from app.agents.mcp import lifecycle as mcp_lifecycle
from app.agents.mcp import service as mcp_service
from app.agents.mcp.discovery import build_auth_env_and_headers
from app.agents.mcp.registry import get_mcp_registry
from app.api.routes import mcp_servers as mcp_routes
from app.config.configuration_service import ConfigKeyNotDeletedError
from app.core.org_context import OrgContext, bind
from app.utils.env_flags import CONTROL_PLANE_CREDENTIALS
from tests.support.in_memory_config import InMemoryConfigService

ORG, ADMIN, USER, INST = "org-1", "admin-1", "user-1", "inst-1"
OLD_URL, NEW_URL = "https://mcp.old.example.com/mcp", "https://mcp.new.example.com/mcp"
INSTANCE_PATH = f"/services/mcp/instances/{INST}"
USER_CRED = f"/services/mcp/credentials/{INST}/{USER}"
USER_DCR = f"/services/mcp/credentials/{INST}/{USER}/dcr-client"
SHARED_DCR = f"/services/mcp/dcr-clients/{INST}"
OLD_TOKEN = "old-target-access-token"
STATE = "/services/mcp/oauth-states/s1"
OTHER_STATE = "/services/mcp/oauth-states/s2"


class FlakyConfig(InMemoryConfigService):
    def __init__(self, mode: str) -> None:
        super().__init__()
        self.mode = mode

    async def delete_config(self, key: str) -> bool:
        if self.mode == "refused" and key.startswith(("/services/mcp/credentials/", "/services/mcp/dcr-clients/")):
            return False
        if self.mode == "delete_raises":
            raise ConnectionError("store unreachable")
        return await super().delete_config(key)

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        if self.mode == "list_fails" and directory.startswith("/services/mcp/credentials/"):
            raise ConnectionError("store unreachable")
        return await super().list_keys_in_directory(directory)


def _instance(**fields: Any) -> dict[str, Any]:  # noqa: ANN401
    record: dict[str, Any] = {
        "_id": INST, "orgId": ORG, "createdBy": ADMIN, "name": "Search", "typeId": None, "isCustom": True,
        "transport": "streamable_http", "authMode": "oauth", "useAdminAuth": False, "url": OLD_URL,
        "authorizationUrl": "https://auth.old.example.com/authorize", "tokenUrl": "https://auth.old.example.com/token",
        "scopes": [], "createdAt": 1, "updatedAt": 1,
    }
    record.update(fields)
    return record


def _client(config: InMemoryConfigService, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv(CONTROL_PLANE_CREDENTIALS, raising=False)

    async def get_instance(instance_id: str, store: Any, *_: Any) -> dict[str, Any] | None:  # noqa: ANN401
        return await store.get_config(f"/services/mcp/instances/{instance_id}")

    async def yes(*_: object, **__: object) -> bool:
        return True

    async def nothing(*_: object, **__: object) -> None:
        return None

    monkeypatch.setattr(mcp_routes, "_get_org_instance", get_instance)
    monkeypatch.setattr(mcp_routes, "_check_user_is_admin", yes)
    monkeypatch.setattr(mcp_routes, "sync_capabilities", nothing)
    monkeypatch.setattr(mcp_routes, "_assert_instance_urls_allowed", lambda _record: None)  # no DNS in a unit test
    monkeypatch.setattr(mcp_lifecycle, "_refresh_service", lambda: None)

    app = FastAPI()

    @app.middleware("http")
    async def authenticate(request: Request, call_next: Any) -> Any:  # noqa: ANN401
        request.state.user = {"userId": ADMIN, "orgId": ORG}
        with bind(OrgContext(ORG, ADMIN, "user", "jwt", "req-api1-s4")):
            return await call_next(request)

    app.state.config_service = config
    registry = get_mcp_registry()
    registry.auto_discover_templates()
    app.state.mcp_registry = registry
    app.include_router(mcp_routes.router)
    app.dependency_overrides[mcp_routes._require_mcp_enabled] = lambda: None
    return TestClient(app, raise_server_exceptions=False)


async def _what_the_client_sends(config: InMemoryConfigService) -> tuple[str | None, dict[str, str]]:
    """The URL the MCP client connects to for USER and the headers it sends there (the chat path:
    `resolve_effective_user_auth` -> `credentials_to_discovery_dict` -> `build_auth_env_and_headers`)."""
    instance = await config.get_config(INSTANCE_PATH)
    auth = await mcp_service.resolve_effective_user_auth(instance, USER, config)
    server = mcp_service.instance_config_from_dict(instance)
    credentials = mcp_service.credentials_to_discovery_dict(instance["authMode"], auth or {})
    _, headers = build_auth_env_and_headers(server, credentials)
    return server.url, headers


def _seed(config: InMemoryConfigService, instance: dict[str, Any]) -> None:
    config.values[INSTANCE_PATH] = instance
    config.values[USER_CRED] = {
        "isAuthenticated": True, "userId": USER, "orgId": ORG,
        "oauthTokens": {"accessToken": OLD_TOKEN, "refreshToken": "old-refresh", "tokenUrl": instance.get("tokenUrl")},
    }
    config.values[USER_DCR] = {"clientId": "old-legacy-client", "clientSecret": "old-legacy-secret"}
    config.values[SHARED_DCR] = {"clientId": "old-shared-client", "clientSecret": "old-shared-secret"}


def _put_body(**fields: Any) -> dict[str, Any]:  # noqa: ANN401
    body: dict[str, Any] = {
        "name": "Search", "transport": "streamable_http", "authMode": "oauth", "useAdminAuth": False,
        "url": NEW_URL, "authorizationUrl": "https://auth.old.example.com/authorize",
        "tokenUrl": "https://auth.old.example.com/token",
    }
    body.update(fields)
    return body


@pytest.mark.parametrize("mode", ["refused", "list_fails", "delete_raises"])
async def test_a_url_change_is_refused_while_the_old_tokens_cannot_be_removed(
    mode: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = FlakyConfig(mode)
    _seed(config, _instance())
    client = _client(config, monkeypatch)

    resp = client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body())

    url, headers = await _what_the_client_sends(config)
    stored = {k for k in (USER_CRED, USER_DCR, SHARED_DCR) if k in config.values}
    evidence = (resp.status_code, resp.json().get("credentialsReset"), url, headers.get("Authorization"), sorted(stored))
    # Never: the new URL saved while the old token is still what the client sends.
    assert not (url == NEW_URL and headers.get("Authorization") == f"Bearer {OLD_TOKEN}"), evidence
    assert resp.status_code == 503, evidence
    assert resp.headers.get("Retry-After"), evidence
    assert url == OLD_URL, evidence


async def test_a_url_change_with_a_healthy_store_removes_every_old_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    config = FlakyConfig("healthy")
    _seed(config, _instance())
    client = _client(config, monkeypatch)

    resp = client.put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body())

    assert resp.status_code == 200, resp.text
    assert resp.json()["credentialsReset"] is True
    assert {USER_CRED, USER_DCR, SHARED_DCR}.isdisjoint(config.values)
    url, headers = await _what_the_client_sends(config)
    assert (url, headers.get("Authorization")) == (NEW_URL, None)


async def test_a_url_change_on_a_url_editable_template_purges_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    """Okta's template lets the admin type the org's URL; a new one is a new target."""
    config = FlakyConfig("healthy")
    okta = _instance(typeId="okta", isCustom=False, url="https://acme.okta.com/mcp", authorizationUrl=None, tokenUrl=None)
    _seed(config, okta)
    client = _client(config, monkeypatch)

    resp = client.put(
        f"/api/v1/mcp-servers/instances/{INST}",
        json=_put_body(typeId="okta", url="https://other.okta.com/mcp", authorizationUrl=None, tokenUrl=None),
    )

    url, headers = await _what_the_client_sends(config)
    evidence = (resp.status_code, resp.json().get("credentialsReset", resp.json().get("detail")), url, headers.get("Authorization"))
    assert resp.status_code == 200, evidence
    assert not (url == "https://other.okta.com/mcp" and headers.get("Authorization") == f"Bearer {OLD_TOKEN}"), evidence
    assert resp.json()["credentialsReset"] is True, evidence
    assert USER_CRED not in config.values, evidence


def test_an_auth_mode_change_on_a_url_editable_template_is_a_new_target() -> None:
    okta = _instance(typeId="okta", isCustom=False, url="https://acme.okta.com/mcp", authMode="oauth")
    assert mcp_routes._connection_target_changed(okta, {**okta, "authMode": "api_token"})
    assert not mcp_routes._connection_target_changed(okta, {**okta, "name": "Okta (renamed)"})


class Picky(FlakyConfig):
    """Refuses to delete only the keys in ``refuse``; ``states_unreadable``: an oauth-state read fails
    (the real service answers the default unless ``raise_on_error``)."""

    def __init__(self, refuse: frozenset[str] = frozenset(), states_unreadable: bool = False) -> None:
        super().__init__("healthy")
        self.refuse, self.states_unreadable = set(refuse), states_unreadable

    async def delete_config(self, key: str) -> bool:
        if key in self.refuse:
            return False
        return await super().delete_config(key)

    async def get_config(self, key: str, default: Any = None, use_cache: bool = True, **kw: Any) -> Any:  # noqa: ANN401
        if self.states_unreadable and key.startswith("/services/mcp/oauth-states/"):
            if kw.get("raise_on_error"):
                raise ConnectionError("store unreachable")
            return default
        return await super().get_config(key, default, use_cache, **kw)


class StatesUnlistable(Picky):
    async def list_keys_in_directory(self, directory: str) -> list[str]:
        if directory.startswith("/services/mcp/oauth-states/"):
            raise ConnectionError("store unreachable")
        return await super().list_keys_in_directory(directory)


def _put(config: InMemoryConfigService, monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
    return _client(config, monkeypatch).put(f"/api/v1/mcp-servers/instances/{INST}", json=_put_body())


async def test_an_absent_shared_dcr_client_is_not_a_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Picky()
    _seed(config, _instance())
    del config.values[SHARED_DCR]
    resp = _put(config, monkeypatch)
    assert resp.status_code == 200, resp.text
    assert config.values[INSTANCE_PATH]["url"] == NEW_URL


async def test_the_purge_tries_every_key_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_lifecycle, "_refresh_service", lambda: None)
    config = Picky(refuse=frozenset({USER_CRED}))
    _seed(config, _instance())
    config.values[STATE] = {"instanceId": INST}
    with pytest.raises(ConfigKeyNotDeletedError):
        await mcp_lifecycle.purge_instance_credentials(config, INST)
    assert USER_CRED in config.values
    assert {USER_DCR, SHARED_DCR, STATE}.isdisjoint(config.values)


async def test_a_failed_credentials_listing_propagates(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_lifecycle, "_refresh_service", lambda: None)
    config = FlakyConfig("list_fails")
    _seed(config, _instance())
    config.values[STATE] = {"instanceId": INST}
    with pytest.raises(ConnectionError):
        await mcp_lifecycle.purge_instance_credentials(config, INST)
    assert {SHARED_DCR, STATE}.isdisjoint(config.values)  # what could be found is still removed


async def test_deleting_an_instance_stays_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_lifecycle, "_refresh_service", lambda: None)
    config = Picky(refuse=frozenset({USER_CRED}))
    _seed(config, _instance())
    config.values["/services/mcp/logos/inst-1"] = {"dataUrl": "x"}
    await mcp_lifecycle.delete_instance_data(config, _instance())
    assert INSTANCE_PATH not in config.values
    assert "/services/mcp/logos/inst-1" not in config.values
    assert {USER_DCR, SHARED_DCR}.isdisjoint(config.values)


async def test_deleting_an_instance_survives_a_failed_credentials_listing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mcp_lifecycle, "_refresh_service", lambda: None)
    config = FlakyConfig("list_fails")
    _seed(config, _instance())
    config.values["/services/mcp/logos/inst-1"] = {"dataUrl": "x"}
    await mcp_lifecycle.delete_instance_data(config, _instance())
    assert INSTANCE_PATH not in config.values
    assert "/services/mcp/logos/inst-1" not in config.values


async def test_a_bug_in_the_purge_is_a_500_and_the_record_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(*_: object) -> None:
        raise ValueError("bug")

    config = Picky()
    _seed(config, _instance())
    monkeypatch.setattr(mcp_routes.mcp_lifecycle, "purge_instance_credentials", boom)
    resp = _put(config, monkeypatch)
    assert resp.status_code == 500, resp.text
    assert "Retry-After" not in resp.headers
    assert config.values[INSTANCE_PATH]["url"] == OLD_URL


async def test_an_unreadable_sign_in_state_of_another_server_does_not_block_the_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Picky(states_unreadable=True)
    _seed(config, _instance())
    config.values[OTHER_STATE] = b"not a state"
    resp = _put(config, monkeypatch)
    assert resp.status_code == 200, resp.text
    assert config.values[INSTANCE_PATH]["url"] == NEW_URL
    assert OTHER_STATE not in config.values  # unusable by any callback, so removed with the rest


async def test_an_unreadable_sign_in_state_of_this_server_is_removed(monkeypatch: pytest.MonkeyPatch) -> None:
    config = Picky(states_unreadable=True)
    _seed(config, _instance())
    config.values[STATE] = {"instanceId": INST}
    resp = _put(config, monkeypatch)
    assert resp.status_code == 200, resp.text
    assert STATE not in config.values


async def test_sign_in_states_on_a_store_that_is_down_block_the_change(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reads fail and the delete is not confirmed: the confirming read fails too, as on a store that is down."""
    config = Picky(refuse=frozenset({OTHER_STATE}), states_unreadable=True)
    _seed(config, _instance())
    config.values[OTHER_STATE] = {"instanceId": "inst-2"}
    resp = _put(config, monkeypatch)
    assert resp.status_code == 503, resp.text
    assert resp.headers.get("Retry-After")
    assert config.values[INSTANCE_PATH]["url"] == OLD_URL


@pytest.mark.parametrize("kept", [SHARED_DCR, STATE], ids=["dcr-clients", "oauth-states"])
async def test_a_kept_shared_dcr_client_or_sign_in_state_blocks_the_change(
    kept: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = Picky(refuse=frozenset({kept}))
    _seed(config, _instance())
    config.values[STATE] = {"instanceId": INST}
    resp = _put(config, monkeypatch)
    assert resp.status_code == 503, resp.text
    assert resp.headers.get("Retry-After")
    assert config.values[INSTANCE_PATH]["url"] == OLD_URL


async def test_an_unlistable_sign_in_states_prefix_blocks_the_change(monkeypatch: pytest.MonkeyPatch) -> None:
    config = StatesUnlistable()
    _seed(config, _instance())
    resp = _put(config, monkeypatch)
    assert resp.status_code == 503, resp.text
    assert config.values[INSTANCE_PATH]["url"] == OLD_URL


async def test_an_unlistable_sign_in_states_prefix_still_removes_the_credentials_found(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mcp_lifecycle, "_refresh_service", lambda: None)
    config = StatesUnlistable()
    _seed(config, _instance())
    with pytest.raises(ConnectionError):
        await mcp_lifecycle.purge_instance_credentials(config, INST)
    assert {USER_CRED, USER_DCR, SHARED_DCR}.isdisjoint(config.values)


class KeptThenBroken(Picky):
    """Keeps the user's token (an outage-like failure) and raises a bug on the shared DCR client, later in the purge."""

    async def delete_config(self, key: str) -> bool:
        if key == SHARED_DCR:
            raise ValueError("bug")
        return await super().delete_config(key)


async def test_a_bug_among_store_failures_is_a_500_not_a_503(monkeypatch: pytest.MonkeyPatch) -> None:
    config = KeptThenBroken(refuse=frozenset({USER_CRED}))
    _seed(config, _instance())
    resp = _put(config, monkeypatch)
    assert resp.status_code == 500, resp.text
    assert "Retry-After" not in resp.headers
    assert config.values[INSTANCE_PATH]["url"] == OLD_URL
