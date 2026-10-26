"""The shared-store lock that keeps two processes from rotating the same refresh token."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp import token_refresh
from app.agents.mcp.models import OAuthTokens
from app.agents.mcp.token_refresh import MCPTokenRefreshError, refresh_credential_record

CRED_PATH = "/services/mcp/credentials/inst-1/user-1"
LOCK_PATH = "/services/mcp/refresh-locks/inst-1/user-1"


class _SharedStore:
    """Config store with the one property the lock relies on: atomic create-if-absent."""

    def __init__(self, access_token: str = "old") -> None:
        self.data: dict[str, Any] = {
            CRED_PATH: {
                "isAuthenticated": True,
                "oauthTokens": {
                    "accessToken": access_token, "refreshToken": "refresh-1",
                    "tokenUrl": "https://auth.example.com/token", "expiresIn": 3600,
                },
            },
            "/services/mcp/dcr-clients/inst-1": {"clientId": "client"},
        }
        self.lock_ttls: list[int | None] = []

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False) -> Any:  # noqa: ANN401
        return self.data.get(key, default)

    async def set_config(self, key: str, value: Any) -> bool:  # noqa: ANN401
        self.data[key] = value
        return True

    async def create_config_if_absent(self, key: str, value: Any, *, ttl_seconds: int | None = None) -> bool:  # noqa: ANN401
        if key in self.data:
            return False
        self.data[key] = value
        self.lock_ttls.append(ttl_seconds)
        return True

    async def delete_config(self, key: str) -> bool:
        self.data.pop(key, None)
        return True


def _new_tokens(access_token: str) -> OAuthTokens:
    return OAuthTokens(access_token=access_token, refresh_token="refresh-2", expires_in=3600,
                       token_url="https://auth.example.com/token")


class TestClusterLock:
    async def test_lock_is_taken_with_a_ttl_and_released(self) -> None:
        store = _SharedStore()
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=_new_tokens("new"))):
            await refresh_credential_record("inst-1", "user-1", store)

        assert store.lock_ttls == [token_refresh.CLUSTER_LOCK_TTL_SECONDS]
        assert LOCK_PATH not in store.data

    async def test_a_second_process_waiting_on_the_lock_reuses_the_first_ones_tokens(self) -> None:
        """Two processes both saw token `old` rejected. Only one may call the endpoint;
        the other must pick up the rotated tokens instead of rotating them again."""
        store = _SharedStore()
        endpoint = AsyncMock(return_value=_new_tokens("new"))
        # Process B already holds the lock when process A arrives.
        store.data[LOCK_PATH] = {"owner": "process-b"}

        async def _process_b_finishes() -> None:
            await asyncio.sleep(0.05)
            store.data[CRED_PATH]["oauthTokens"]["accessToken"] = "rotated-by-b"
            del store.data[LOCK_PATH]

        with patch.object(oauth_client_module, "refresh_access_token", new=endpoint), \
             patch.object(token_refresh, "_CLUSTER_LOCK_POLL_SECONDS", 0.01):
            result, _ = await asyncio.gather(
                refresh_credential_record("inst-1", "user-1", store, stale_access_token="old"),
                _process_b_finishes(),
            )

        endpoint.assert_not_awaited()
        assert result.access_token == "rotated-by-b"

    async def test_gives_up_when_the_lock_is_never_released(self) -> None:
        store = _SharedStore()
        store.data[LOCK_PATH] = {"owner": "stuck"}
        endpoint = AsyncMock(return_value=_new_tokens("new"))

        with patch.object(oauth_client_module, "refresh_access_token", new=endpoint), \
             patch.object(token_refresh, "CLUSTER_LOCK_WAIT_SECONDS", 0.05), \
             patch.object(token_refresh, "_CLUSTER_LOCK_POLL_SECONDS", 0.01), \
             pytest.raises(MCPTokenRefreshError, match="Another process"):
            await refresh_credential_record("inst-1", "user-1", store)

        endpoint.assert_not_awaited()
        assert store.data[LOCK_PATH] == {"owner": "stuck"}

    async def test_a_lock_that_changed_hands_is_not_deleted(self) -> None:
        store = _SharedStore()

        async def _slow_refresh(**_kwargs: Any) -> OAuthTokens:  # noqa: ANN401
            # Our lock "expired" mid-refresh and someone else took it.
            store.data[LOCK_PATH] = {"owner": "someone-else"}
            return _new_tokens("new")

        with patch.object(oauth_client_module, "refresh_access_token", new=_slow_refresh):
            await refresh_credential_record("inst-1", "user-1", store)

        assert store.data[LOCK_PATH] == {"owner": "someone-else"}

    async def test_a_store_failure_is_not_mistaken_for_a_free_lock(self) -> None:
        store = _SharedStore()
        store.create_config_if_absent = AsyncMock(side_effect=ConnectionError("store down"))  # type: ignore[method-assign]
        endpoint = AsyncMock(return_value=_new_tokens("new"))

        with patch.object(oauth_client_module, "refresh_access_token", new=endpoint), \
             pytest.raises(ConnectionError):
            await refresh_credential_record("inst-1", "user-1", store)

        endpoint.assert_not_awaited()


class TestStaleAccessToken:
    async def test_a_matching_stale_token_is_refreshed(self) -> None:
        store = _SharedStore(access_token="old")
        endpoint = AsyncMock(return_value=_new_tokens("new"))

        with patch.object(oauth_client_module, "refresh_access_token", new=endpoint):
            result = await refresh_credential_record("inst-1", "user-1", store, stale_access_token="old")

        endpoint.assert_awaited_once()
        assert result.access_token == "new"
        assert store.data[CRED_PATH]["oauthTokens"]["accessToken"] == "new"

    async def test_without_a_stale_token_it_always_refreshes(self) -> None:
        store = _SharedStore(access_token="current")
        endpoint = AsyncMock(return_value=_new_tokens("new"))

        with patch.object(oauth_client_module, "refresh_access_token", new=endpoint):
            await refresh_credential_record("inst-1", "user-1", store)

        endpoint.assert_awaited_once()
