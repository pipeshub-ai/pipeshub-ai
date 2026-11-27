"""OAuthProvider when a read of its saved record fails: it must not save back just its own field over the record.

The real ConfigurationService runs on an in-memory key-value store that copies like Redis or etcd and fails chosen reads of
one key, counted from 1. Only the provider's token endpoint is faked.
"""
from __future__ import annotations

import copy
import logging
import secrets
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.config.configuration_service import ConfigurationService
from app.config.providers.in_memory_store import InMemoryKeyValueStore
from app.connectors.core.base.token_service import oauth_service
from app.connectors.core.base.token_service.token_refresh_service import (
    CredentialSaveError,
    TokenRefreshService,
)
from app.connectors.core.base.user_access import (
    UserConnection,
    connection_path,
    get_connection,
)
from app.connectors.sources.notion import per_user

if TYPE_CHECKING:
    from collections.abc import Coroutine

LOG = logging.getLogger("app.test.oauth_config_read_outage")
CONNECTOR_PATH = "/services/connectors/c1/config"
TOOLSET_PATH = "/services/toolsets/i1/u1"
SAVED = {
    "auth": {"clientId": "cid", "clientSecret": "cs", "authType": "OAUTH"},
    "credentials": {"access_token": "at-old", "refresh_token": "rt-old"},
    "filters": {"sync": {"values": {"folders": ["legal"]}}},
    "sync": {"selectedStrategy": "SCHEDULED"},
    "oauth": {"state": "old-state", "used_codes": ["old-code"]},
}
TOOLSET_SAVED = {"isAuthenticated": True, "authType": "OAUTH", "oauthConfigId": "oc1",
                 "credentials": {"access_token": "at-old", "refresh_token": "rt-old"}}
NEW_TOKEN = {"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600}


class FlakyKV(InMemoryKeyValueStore):
    """Copies on read and write. ``fail_reads[key]`` holds the reads of ``key`` (counted from 1) that raise."""

    def __init__(self) -> None:
        super().__init__(LOG)
        self.reads: dict[str, int] = {}
        self.fail_reads: dict[str, set[int]] = {}

    async def get_key(self, key: str, *, raise_on_error: bool = False) -> Any:  # noqa: ANN401
        self.reads[key] = self.reads.get(key, 0) + 1
        if self.reads[key] in self.fail_reads.get(key, set()):
            raise ConnectionError("kv down")
        return copy.deepcopy(await super().get_key(key))

    async def create_key(self, key: str, value: Any, overwrite: bool = True, ttl: int | None = None) -> bool:  # noqa: ANN401
        return await super().create_key(key, copy.deepcopy(value), overwrite, ttl)

    def saved(self, key: str) -> Any:  # noqa: ANN401
        item = self.store.get(key)
        return copy.deepcopy(item.value) if item else None


@pytest.fixture
async def kv(monkeypatch: pytest.MonkeyPatch) -> FlakyKV:
    monkeypatch.setenv("SECRET_KEY", secrets.token_urlsafe(32))
    return FlakyKV()


@pytest.fixture
async def config(kv: FlakyKV) -> ConfigurationService:
    service = ConfigurationService(LOG, kv)
    yield service
    await service.close()


async def _refusal(call: Coroutine[Any, Any, Any]) -> Exception | None:
    """What ``call`` raised, or None. Tests check the store first, so a failure on main names the write."""
    try:
        await call
    except Exception as e:
        return e
    return None


def _provider(config: ConfigurationService, path: str) -> oauth_service.OAuthProvider:
    oauth = oauth_service.OAuthConfig(client_id="cid", client_secret="cs", redirect_uri="https://app.example/cb",
                                      authorize_url="https://idp.example/authorize", token_url="https://idp.example/token")
    provider = oauth_service.OAuthProvider(oauth, config, path)
    provider._make_token_request = AsyncMock(return_value=dict(NEW_TOKEN))  # type: ignore[method-assign]
    return provider


# ---- start_authorization

@pytest.mark.parametrize(("path", "saved"), [(CONNECTOR_PATH, SAVED), (TOOLSET_PATH, TOOLSET_SAVED)],
                         ids=["connector-config", "toolset-auth-record"])
async def test_start_refuses_and_writes_nothing_when_the_read_fails(
    kv: FlakyKV, config: ConfigurationService, path: str, saved: dict[str, Any],
) -> None:
    await kv.create_key(path, saved)
    kv.fail_reads[path] = {1}
    refusal = await _refusal(_provider(config, path).start_authorization())
    assert kv.saved(path) == saved
    assert isinstance(refusal, oauth_service.OAuthStoreUnavailableError)


async def test_start_with_nothing_saved_starts_fresh(kv: FlakyKV, config: ConfigurationService) -> None:
    url = await _provider(config, CONNECTOR_PATH).start_authorization()
    saved = kv.saved(CONNECTOR_PATH)
    assert list(saved) == ["oauth"]
    assert f"state={saved['oauth']['state']}" in url


async def test_start_keeps_the_saved_settings_and_replaces_the_session(kv: FlakyKV, config: ConfigurationService) -> None:
    await kv.create_key(CONNECTOR_PATH, SAVED)
    await _provider(config, CONNECTOR_PATH).start_authorization()
    saved = kv.saved(CONNECTOR_PATH)
    assert {k: v for k, v in saved.items() if k != "oauth"} == {k: v for k, v in SAVED.items() if k != "oauth"}
    assert saved["oauth"]["state"] != "old-state"
    assert saved["oauth"]["used_codes"] == []


# ---- refresh_access_token

async def test_refresh_returns_the_token_and_writes_nothing_when_the_read_fails(
    kv: FlakyKV, config: ConfigurationService,
) -> None:
    await kv.create_key(CONNECTOR_PATH, SAVED)
    kv.fail_reads[CONNECTOR_PATH] = {1}
    token = await _provider(config, CONNECTOR_PATH).refresh_access_token("rt-old")
    assert (token.access_token, token.refresh_token) == ("at-new", "rt-new")
    assert kv.saved(CONNECTOR_PATH) == SAVED

    # The connector refresh service then saves the token into the whole record.
    service = TokenRefreshService.__new__(TokenRefreshService)
    service.configuration_service, service.logger = config, LOG
    await service._persist_refreshed_credentials("c1", CONNECTOR_PATH, copy.deepcopy(SAVED), token)
    saved = kv.saved(CONNECTOR_PATH)
    assert saved["credentials"]["access_token"] == "at-new"
    assert {k: v for k, v in saved.items() if k != "credentials"} == {k: v for k, v in SAVED.items() if k != "credentials"}


async def test_refresh_service_reports_a_save_that_never_lands(kv: FlakyKV, config: ConfigurationService) -> None:
    await kv.create_key(CONNECTOR_PATH, SAVED)
    kv.fail_reads[CONNECTOR_PATH] = set(range(1, 10))
    token = await _provider(config, CONNECTOR_PATH).refresh_access_token("rt-old")
    service = TokenRefreshService.__new__(TokenRefreshService)
    service.configuration_service, service.logger = config, LOG
    kv.create_key = AsyncMock(side_effect=ConnectionError("kv down"))  # type: ignore[method-assign]
    with patch("app.connectors.core.base.token_service.token_refresh_service.asyncio.sleep", new=AsyncMock()), \
         pytest.raises(CredentialSaveError):
        await service._persist_refreshed_credentials("c1", CONNECTOR_PATH, copy.deepcopy(SAVED), token)
    assert kv.saved(CONNECTOR_PATH) == SAVED


@pytest.mark.parametrize("saved", [SAVED, None], ids=["saved", "nothing-saved"])
async def test_refresh_saves_the_token_into_the_record(
    kv: FlakyKV, config: ConfigurationService, saved: dict[str, Any] | None,
) -> None:
    if saved is not None:
        await kv.create_key(CONNECTOR_PATH, saved)
    await _provider(config, CONNECTOR_PATH).refresh_access_token("rt-old")
    stored = kv.saved(CONNECTOR_PATH)
    assert stored["credentials"]["access_token"] == "at-new"
    assert {k: v for k, v in stored.items() if k != "credentials"} == {
        k: v for k, v in (saved or {}).items() if k != "credentials"}


async def test_notion_refresh_keeps_the_connection_when_the_providers_read_fails(
    kv: FlakyKV, config: ConfigurationService,
) -> None:
    path = connection_path("c1", "u1")
    connection = UserConnection(user_id="u1", email="u@example.com", token="at-old", source_user_id="n1",
                                source_email="u@example.com", connected_at=5, refresh_token="rt-old", method="oauth")
    await kv.create_key(path, connection.to_dict())
    kv.fail_reads[path] = {2}  # 1: refresh_connection's own read; 2: the provider's
    oauth = oauth_service.OAuthConfig(client_id="cid", client_secret="cs", redirect_uri="https://app.example/cb",
                                      authorize_url="https://idp.example/authorize", token_url="https://idp.example/token")
    with patch.object(per_user, "user_oauth_config", new=AsyncMock(return_value=oauth)), \
         patch.object(oauth_service.OAuthProvider, "_make_token_request", new=AsyncMock(return_value=dict(NEW_TOKEN))):
        refreshed = await per_user.refresh_connection(
            connection, connector_id="c1", config_service=config, org_id="o1", logger=LOG)
    assert refreshed is True
    stored = await get_connection(config, "c1", "u1")
    assert stored is not None
    assert (stored.token, stored.refresh_token, stored.connected_at) == ("at-new", "rt-new", 5)


# ---- revoke_token

async def test_revoke_refuses_and_writes_nothing_when_the_read_fails(kv: FlakyKV, config: ConfigurationService) -> None:
    await kv.create_key(CONNECTOR_PATH, SAVED)
    kv.fail_reads[CONNECTOR_PATH] = {1}
    refusal = await _refusal(_provider(config, CONNECTOR_PATH).revoke_token())
    assert kv.saved(CONNECTOR_PATH) == SAVED
    assert isinstance(refusal, oauth_service.OAuthStoreUnavailableError)
