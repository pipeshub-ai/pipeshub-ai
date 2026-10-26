"""Unit tests for app.agents.mcp.token_refresh."""
import asyncio
import copy
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.constants.mcp_server_constants import (
    get_mcp_credentials_path,
    get_mcp_dcr_client_path,
    get_mcp_oauth_client_config_path,
    get_mcp_shared_dcr_client_path,
)
from app.agents.mcp import oauth_client as oauth_client_module
from app.agents.mcp.models import OAuthTokens
from app.agents.mcp.token_refresh import (
    MCPTokenRefreshError,
    refresh_credential_record,
    resolve_client_credentials,
    retire_rejected_dcr_client,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    from tests.unit.api.routes.mcp_route_fakes import FakeConfigService


@pytest.fixture(autouse=True)
def _no_cluster_lock() -> "Iterator[None]":
    """The shared-store refresh lock has its own tests; here the config service is a bare mock."""
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _held(*_args: object, **_kwargs: object) -> "AsyncIterator[None]":
        yield

    with patch("app.agents.mcp.token_refresh._cluster_refresh_lock", _held):
        yield


CRED_PATH = "/services/mcp/credentials/inst-1/user-1"


def _oauth_record(refresh_token="refresh-1", token_url="https://example.com/token") -> dict:
    return {
        "isAuthenticated": True,
        "oauthTokens": {
            "accessToken": "access-1",
            "tokenType": "Bearer",
            "refreshToken": refresh_token,
            "expiresIn": 3600,
            "tokenUrl": token_url,
        },
    }


class TestAClientDocumentRefreshesItself:
    URL = "https://pipeshub.example.com/mcp-servers/oauth/client-metadata.json"

    async def test_the_tokens_own_client_id_and_no_secret(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value=None)
        assert await resolve_client_credentials("inst-1", "user-1", cfg, issued_by=self.URL) == (self.URL, None)

    async def test_any_other_unknown_client_is_still_unknown(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value=None)
        assert await resolve_client_credentials("inst-1", "user-1", cfg, issued_by="dcr-gone") == (None, None)

    async def test_a_refresh_sends_only_the_client_id(self) -> None:
        record = _oauth_record()
        record["oauthTokens"].update({"clientId": self.URL, "tokenEndpointAuthMethod": "none"})
        cfg = MagicMock()
        cfg.get_config = AsyncMock(side_effect=lambda path, default=None, use_cache=False: copy.deepcopy(record) if path == CRED_PATH else None)
        cfg.set_config = AsyncMock(return_value=True)
        new_tokens = OAuthTokens(access_token="a2", token_endpoint_auth_method="none")

        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=new_tokens)) as refresh:
            await refresh_credential_record("inst-1", "user-1", cfg)

        kwargs = refresh.await_args.kwargs
        assert (kwargs["client_id"], kwargs["client_secret"], kwargs["auth_method"]) == (self.URL, None, "none")


class TestARefreshAuthenticatesAsTheSignInDid:
    @pytest.mark.asyncio
    async def test_two_refreshes_in_a_row_keep_client_secret_basic(self) -> None:
        store = {CRED_PATH: _oauth_record()}
        store[CRED_PATH]["oauthTokens"].update({"clientId": "dcr-cid", "tokenEndpointAuthMethod": "client_secret_basic"})

        async def _get_config(path: str, default: object = None, use_cache: bool = False) -> object:
            if path == CRED_PATH:
                return copy.deepcopy(store[CRED_PATH])
            if path.endswith("/dcr-client"):
                return {"clientId": "dcr-cid", "clientSecret": "dcr-secret"}
            return None

        async def _set_config(path: str, value: dict, **_kwargs: object) -> bool:
            store[path] = copy.deepcopy(value)
            return True

        cfg = MagicMock()
        cfg.get_config = AsyncMock(side_effect=_get_config)
        cfg.set_config = AsyncMock(side_effect=_set_config)

        def _answer(body: dict) -> MagicMock:
            resp = MagicMock()
            resp.status_code = 200
            resp.headers = {"content-type": "application/json"}
            resp.text = str(body)
            resp.json.return_value = body
            return resp

        inner = MagicMock()
        inner.post = AsyncMock(side_effect=[_answer({"access_token": "a2", "refresh_token": "r2"}), _answer({"access_token": "a3"})])
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=inner)
        client.__aexit__ = AsyncMock(return_value=False)
        with patch("app.agents.mcp.oauth_client.httpx.AsyncClient", return_value=client):
            await refresh_credential_record("inst-1", "user-1", cfg)
            await refresh_credential_record("inst-1", "user-1", cfg)

        assert inner.post.await_count == 2
        for call in inner.post.await_args_list:
            assert call.kwargs["headers"]["Authorization"].startswith("Basic ")
            assert "client_secret" not in call.kwargs["data"]
        assert store[CRED_PATH]["oauthTokens"]["accessToken"] == "a3"
        assert store[CRED_PATH]["oauthTokens"]["tokenEndpointAuthMethod"] == "client_secret_basic"


class TestResolveClientCredentials:
    @pytest.mark.asyncio
    async def test_prefers_dcr_client(self) -> None:
        cfg = MagicMock()

        # `path.endswith(...)`, not `in` — the legacy per-owner path
        # (".../dcr-client") is a substring of the shared path (".../dcr-clients/{inst}"),
        # so a plain "dcr-client" in path check can't tell the two apart.
        async def _get_config(path, default=None):
            if path.endswith("/dcr-client"):
                return {"clientId": "dcr-cid", "clientSecret": "dcr-secret"}
            return {"clientId": "shared-cid", "clientSecret": "shared-secret"}

        cfg.get_config = AsyncMock(side_effect=_get_config)
        client_id, client_secret = await resolve_client_credentials("inst-1", "user-1", cfg)
        assert (client_id, client_secret) == ("dcr-cid", "dcr-secret")

    @pytest.mark.asyncio
    async def test_falls_back_to_shared_client(self) -> None:
        cfg = MagicMock()

        # Legacy per-owner DCR client absent, shared per-instance DCR client present, static
        # oauth-client distinct from both — this only proves the shared-DCR branch fired (and
        # not that we skipped straight to the static-client fallback) because all three
        # return distinguishable values.
        async def _get_config(path, default=None):
            if path.endswith("/dcr-client"):
                return None
            if path.endswith("/dcr-clients/inst-1"):
                return {"clientId": "shared-cid", "clientSecret": "shared-secret"}
            return {"clientId": "static-cid", "clientSecret": "static-secret"}

        cfg.get_config = AsyncMock(side_effect=_get_config)
        client_id, _ = await resolve_client_credentials("inst-1", "user-1", cfg)
        assert client_id == "shared-cid"

    @pytest.mark.asyncio
    async def test_falls_back_to_static_oauth_client_config(self) -> None:
        cfg = MagicMock()

        async def _get_config(path, default=None):
            if path.endswith("/dcr-client"):
                return None
            if path.endswith("/dcr-clients/inst-1"):
                return None
            return {"clientId": "static-cid", "clientSecret": "static-secret"}

        cfg.get_config = AsyncMock(side_effect=_get_config)
        client_id, client_secret = await resolve_client_credentials("inst-1", "user-1", cfg)
        assert (client_id, client_secret) == ("static-cid", "static-secret")

    @pytest.mark.asyncio
    async def test_no_client_returns_none_none(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value=None)
        result = await resolve_client_credentials("inst-1", "user-1", cfg)
        assert result == (None, None)


class TestRefreshCredentialRecord:
    @pytest.mark.asyncio
    async def test_missing_record_raises(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value=None)
        with pytest.raises(MCPTokenRefreshError, match="No MCP credential record"):
            await refresh_credential_record("inst-1", "user-1", cfg)

    @pytest.mark.asyncio
    async def test_missing_refresh_token_raises(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value={"oauthTokens": {"accessToken": "tok"}})
        with pytest.raises(MCPTokenRefreshError, match="No refresh token"):
            await refresh_credential_record("inst-1", "user-1", cfg)

    @pytest.mark.asyncio
    async def test_missing_token_url_raises(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value={"oauthTokens": {"refreshToken": "r1"}})
        with pytest.raises(MCPTokenRefreshError, match="tokenUrl"):
            await refresh_credential_record("inst-1", "user-1", cfg)

    @pytest.mark.asyncio
    async def test_non_dict_oauth_tokens_raises(self) -> None:
        cfg = MagicMock()
        cfg.get_config = AsyncMock(return_value={"oauthTokens": "not-a-dict"})
        with pytest.raises(MCPTokenRefreshError, match="Invalid OAuth token record"):
            await refresh_credential_record("inst-1", "user-1", cfg)

    @pytest.mark.asyncio
    async def test_missing_client_raises(self) -> None:
        cfg = MagicMock()
        record = _oauth_record()

        async def _get_config(path, default=None, use_cache=False):
            if path == CRED_PATH:
                return record
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config)
        with pytest.raises(MCPTokenRefreshError, match="OAuth client"):
            await refresh_credential_record("inst-1", "user-1", cfg)

    @pytest.mark.asyncio
    async def test_successful_refresh_persists_new_tokens(self) -> None:
        cfg = MagicMock()
        record = _oauth_record()

        async def _get_config(path, default=None, use_cache=False):
            if path == CRED_PATH:
                return record
            if path.endswith("/dcr-client"):
                return {"clientId": "dcr-cid", "clientSecret": "dcr-secret"}
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config)
        cfg.set_config = AsyncMock(return_value=True)

        new_tokens = OAuthTokens(
            access_token="new-access", refresh_token="new-refresh", expires_in=3600,
            token_url="https://example.com/token",
        )
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=new_tokens)) as mock_refresh:
            result = await refresh_credential_record("inst-1", "user-1", cfg)

        assert result.access_token == "new-access"
        mock_refresh.assert_awaited_once()
        cfg.set_config.assert_awaited_once()
        persisted_path, persisted_record = cfg.set_config.await_args.args
        assert persisted_path == CRED_PATH
        assert persisted_record["oauthTokens"]["accessToken"] == "new-access"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "instance_url,expected", [("https://example.com/mcp", True), ("https://mcp.example.org/mcp", False)],
    )
    async def test_a_private_token_url_is_allowed_only_on_a_host_the_instance_names(
        self, monkeypatch: pytest.MonkeyPatch, instance_url: str, expected: bool,
    ) -> None:
        monkeypatch.delenv("MCP_ALLOW_PRIVATE_NETWORK_URLS", raising=False)
        cfg = MagicMock()
        record = _oauth_record()  # token URL https://example.com/token
        instance = {"_id": "inst-1", "orgId": "org-1", "url": instance_url}

        async def _get_config(path: str, default: object = None, use_cache: bool = False) -> object:
            if path == CRED_PATH:
                return record
            if path == "/services/mcp/instances/inst-1":
                return instance
            if path.endswith("/dcr-client"):
                return {"clientId": "dcr-cid"}
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config)
        cfg.set_config = AsyncMock(return_value=True)
        new_tokens = OAuthTokens(access_token="new-access", token_url="https://example.com/token")

        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=new_tokens)) as refresh:
            await refresh_credential_record("inst-1", "user-1", cfg)

        assert refresh.await_args.kwargs["allow_private"] is expected

    @pytest.mark.asyncio
    async def test_personal_instance_refreshes_public_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Even where admins' servers may reach the private network.
        monkeypatch.setenv("MCP_ALLOW_PRIVATE_NETWORK_URLS", "true")
        cfg = MagicMock()
        record = _oauth_record()
        personal = {"_id": "inst-1", "orgId": "org-1", "createdBy": "user-1", "scope": "personal"}

        async def _get_config(path: str, default: object = None, use_cache: bool = False) -> object:
            if path == CRED_PATH:
                return record
            if path.startswith("/services/mcp/user-instances/"):
                return personal
            if path.endswith("/dcr-client"):
                return {"clientId": "dcr-cid"}
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config)
        cfg.set_config = AsyncMock(return_value=True)
        new_tokens = OAuthTokens(access_token="new-access", token_url="https://example.com/token")

        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=new_tokens)) as refresh:
            await refresh_credential_record("inst-1", "user-1", cfg)

        assert refresh.await_args.kwargs["allow_private"] is False

    @pytest.mark.asyncio
    async def test_missing_instance_refreshes_public_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_ALLOW_PRIVATE_NETWORK_URLS", raising=False)
        cfg = MagicMock()
        record = _oauth_record()

        async def _get_config(path: str, default: object = None, use_cache: bool = False) -> object:
            if path == CRED_PATH:
                return record
            if path.endswith("/dcr-client"):
                return {"clientId": "dcr-cid"}
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config)
        cfg.set_config = AsyncMock(return_value=True)
        new_tokens = OAuthTokens(access_token="new-access", token_url="https://example.com/token")

        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=new_tokens)) as refresh:
            await refresh_credential_record("inst-1", "user-1", cfg)

        assert refresh.await_args.kwargs["allow_private"] is False

    @pytest.mark.asyncio
    async def test_permanent_rejection_propagates(self) -> None:
        cfg = MagicMock()
        record = _oauth_record()

        async def _get_config(path, default=None, use_cache=False):
            if path == CRED_PATH:
                return record
            if path.endswith("/dcr-client"):
                return {"clientId": "dcr-cid", "clientSecret": "dcr-secret"}
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config)
        cfg.set_config = AsyncMock(return_value=True)

        with patch.object(
            oauth_client_module,
            "refresh_access_token",
            new=AsyncMock(side_effect=oauth_client_module.MCPRefreshTokenInvalidError("invalid_grant")),
        ):
            with pytest.raises(oauth_client_module.MCPRefreshTokenInvalidError):
                await refresh_credential_record("inst-1", "user-1", cfg)


class TestTheRecordChangedDuringTheRefresh:
    """The provider call takes time, and Disconnect, Reconnect, a credentials reset or a new
    sign-in can land meanwhile; none of them take the refresh lock."""

    NEW = OAuthTokens(access_token="access-2", refresh_token="refresh-2", token_url="https://example.com/token")

    def _store(self) -> "FakeConfigService":
        from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

        return FakeConfigService({
            CRED_PATH: _oauth_record(),
            "/services/mcp/oauth-clients/inst-1": {"clientId": "cid", "clientSecret": "secret"},
        })

    @pytest.mark.asyncio
    async def test_a_credential_removed_meanwhile_is_not_written_back(self) -> None:
        store = self._store()

        async def _refresh(**_kwargs: object) -> OAuthTokens:
            await store.delete_config(CRED_PATH)
            return self.NEW

        with patch.object(oauth_client_module, "refresh_access_token", new=_refresh), \
             pytest.raises(MCPTokenRefreshError, match="removed"):
            await refresh_credential_record("inst-1", "user-1", store)

        assert CRED_PATH not in store.data

    @pytest.mark.asyncio
    async def test_a_newer_sign_in_meanwhile_is_kept(self) -> None:
        store = self._store()
        newer = _oauth_record(refresh_token="from-the-new-sign-in")

        async def _refresh(**_kwargs: object) -> OAuthTokens:
            await store.set_config(CRED_PATH, newer)
            return self.NEW

        with patch.object(oauth_client_module, "refresh_access_token", new=_refresh):
            tokens = await refresh_credential_record("inst-1", "user-1", store)

        assert tokens.refresh_token == "from-the-new-sign-in"
        assert store.data[CRED_PATH]["oauthTokens"]["refreshToken"] == "from-the-new-sign-in"

    @pytest.mark.asyncio
    async def test_an_unchanged_record_gets_the_new_tokens(self) -> None:
        store = self._store()

        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=self.NEW)):
            await refresh_credential_record("inst-1", "user-1", store)

        assert store.data[CRED_PATH]["oauthTokens"]["refreshToken"] == "refresh-2"

    @pytest.mark.asyncio
    async def test_a_failed_save_is_retried_once(self) -> None:
        store = self._store()
        real_set = store.set_config
        attempts: list[str] = []

        async def _flaky_set(key: str, value: object) -> bool:
            attempts.append(key)
            if len(attempts) == 1:
                return False
            return await real_set(key, value)

        store.set_config = _flaky_set  # type: ignore[method-assign]
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=self.NEW)):
            tokens = await refresh_credential_record("inst-1", "user-1", store)

        assert tokens.access_token == "access-2"
        assert attempts == [CRED_PATH, CRED_PATH]
        assert store.data[CRED_PATH]["oauthTokens"]["refreshToken"] == "refresh-2"

    @pytest.mark.asyncio
    async def test_a_save_that_keeps_failing_still_returns_working_tokens(self, caplog: pytest.LogCaptureFixture) -> None:
        store = self._store()
        store.set_config = AsyncMock(side_effect=RuntimeError("store down"))  # type: ignore[method-assign]

        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=self.NEW)):
            tokens = await refresh_credential_record("inst-1", "user-1", store)

        assert tokens.access_token == "access-2"
        assert store.set_config.await_count == 2
        assert "could not be saved" in caplog.text


class TestRefreshKeepsTheResource:
    @pytest.mark.asyncio
    async def test_the_stored_resource_is_sent_on_refresh(self) -> None:
        record = _oauth_record()
        record["oauthTokens"]["resource"] = "https://mcp.example.com/mcp"
        cfg = MagicMock()

        async def _get_config(path: str, default: object = None, use_cache: bool = False) -> object:
            if path == CRED_PATH:
                return record
            if path.endswith("/dcr-client"):
                return {"clientId": "dcr-cid", "clientSecret": "dcr-secret"}
            return None

        cfg.get_config = AsyncMock(side_effect=_get_config)
        cfg.set_config = AsyncMock(return_value=True)
        new_tokens = OAuthTokens(access_token="new", refresh_token="nr", expires_in=3600, resource="https://mcp.example.com/mcp")

        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=new_tokens)) as refresh:
            await refresh_credential_record("inst-1", "user-1", cfg)

        assert refresh.await_args.kwargs["resource"] == "https://mcp.example.com/mcp"
        assert cfg.set_config.await_args.args[1]["oauthTokens"]["resource"] == "https://mcp.example.com/mcp"


LEGACY_PATH = get_mcp_dcr_client_path("inst-1", "user-1")
SHARED_PATH = get_mcp_shared_dcr_client_path("inst-1")
STATIC_PATH = get_mcp_oauth_client_config_path("inst-1")
STATIC = {"clientId": "static-cid", "clientSecret": "static-secret"}
LEGACY = {"clientId": "legacy-cid", "clientSecret": "legacy-secret"}
RETIRED = {"clientId": "retired-cid", "clientSecret": "retired-secret"}
SHARED = {"clientId": "shared-cid", "clientSecret": "shared-secret"}
SHARED_WITH_RETIRED = {**SHARED, "previousClient": RETIRED}


def _store(**data: object) -> "FakeConfigService":
    from tests.unit.api.routes.mcp_route_fakes import FakeConfigService

    return FakeConfigService(data)


class TestRefreshUsesTheClientThatIssuedTheTokens:
    """AUTH-11: a refresh with any client but the issuing one fails, whatever is configured since."""

    @pytest.mark.asyncio
    async def test_the_static_app_that_issued_them_is_used_although_dcr_clients_exist(self) -> None:
        cfg = _store(**{LEGACY_PATH: LEGACY, SHARED_PATH: SHARED, STATIC_PATH: STATIC})
        assert await resolve_client_credentials("inst-1", "user-1", cfg, issued_by="static-cid") == ("static-cid", "static-secret")

    @pytest.mark.asyncio
    async def test_the_shared_client_that_issued_them_is_used_over_a_legacy_one(self) -> None:
        cfg = _store(**{LEGACY_PATH: LEGACY, SHARED_PATH: SHARED})
        assert await resolve_client_credentials("inst-1", "user-1", cfg, issued_by="shared-cid") == ("shared-cid", "shared-secret")

    @pytest.mark.asyncio
    async def test_a_retired_shared_client_still_refreshes_what_it_issued(self) -> None:
        cfg = _store(**{SHARED_PATH: SHARED_WITH_RETIRED})
        assert await resolve_client_credentials("inst-1", "user-1", cfg, issued_by="retired-cid") == ("retired-cid", "retired-secret")

    @pytest.mark.asyncio
    async def test_a_client_that_is_gone_resolves_to_nothing_rather_than_another(self) -> None:
        cfg = _store(**{SHARED_PATH: SHARED, STATIC_PATH: STATIC})
        assert await resolve_client_credentials("inst-1", "user-1", cfg, issued_by="deleted-cid") == (None, None)

    @pytest.mark.asyncio
    async def test_unrecorded_tokens_use_the_retired_shared_client_before_the_current_one(self) -> None:
        # Tokens from before the issuer was recorded were issued by the client in place then.
        cfg = _store(**{SHARED_PATH: SHARED_WITH_RETIRED, STATIC_PATH: STATIC})
        assert await resolve_client_credentials("inst-1", "user-1", cfg) == ("retired-cid", "retired-secret")

    @pytest.mark.asyncio
    async def test_unrecorded_tokens_still_prefer_a_legacy_client(self) -> None:
        cfg = _store(**{LEGACY_PATH: LEGACY, SHARED_PATH: SHARED_WITH_RETIRED, STATIC_PATH: STATIC})
        assert await resolve_client_credentials("inst-1", "user-1", cfg) == ("legacy-cid", "legacy-secret")

    @pytest.mark.asyncio
    async def test_a_static_app_inherited_from_the_parent_org_is_found(self) -> None:
        cfg = _store()
        parent = _store(**{STATIC_PATH: STATIC})
        with patch("app.edition_config.resolve_instance_owner_config_service", new=AsyncMock(return_value=parent)):
            assert await resolve_client_credentials("inst-1", "user-1", cfg, issued_by="static-cid") == ("static-cid", "static-secret")
            assert await resolve_client_credentials("inst-1", "user-1", cfg) == ("static-cid", "static-secret")


class TestARefreshKeepsTheIssuingClientAndDropsARejectedOne:
    @staticmethod
    def _cred(client_id: "str | None" = None) -> dict:
        record = _oauth_record()
        if client_id:
            record["oauthTokens"]["clientId"] = client_id
        return record

    @staticmethod
    def _refreshed() -> OAuthTokens:
        return OAuthTokens(access_token="access-2", refresh_token="refresh-2", token_url="https://example.com/token")

    @pytest.mark.asyncio
    async def test_the_refresh_uses_and_records_the_issuing_client(self) -> None:
        cfg = _store(**{CRED_PATH: self._cred("static-cid"), SHARED_PATH: SHARED, STATIC_PATH: STATIC})
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=self._refreshed())) as refresh:
            tokens = await refresh_credential_record("inst-1", "user-1", cfg)

        assert refresh.await_args.kwargs["client_id"] == "static-cid"
        assert refresh.await_args.kwargs["client_secret"] == "static-secret"
        assert tokens.client_id == "static-cid"
        assert cfg.data[CRED_PATH]["oauthTokens"]["clientId"] == "static-cid"

    @pytest.mark.asyncio
    async def test_unrecorded_tokens_learn_their_client_at_the_first_refresh(self) -> None:
        cfg = _store(**{CRED_PATH: self._cred(), SHARED_PATH: SHARED})
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=self._refreshed())):
            await refresh_credential_record("inst-1", "user-1", cfg)

        assert cfg.data[CRED_PATH]["oauthTokens"]["clientId"] == "shared-cid"

    @pytest.mark.asyncio
    async def test_tokens_whose_client_is_gone_are_not_refreshed_with_another(self) -> None:
        cfg = _store(**{CRED_PATH: self._cred("deleted-cid"), SHARED_PATH: SHARED})
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock()) as refresh:
            with pytest.raises(MCPTokenRefreshError, match="No OAuth client"):
                await refresh_credential_record("inst-1", "user-1", cfg)
        refresh.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_rejected_shared_client_is_marked_so_the_next_sign_in_registers_anew(self) -> None:
        cfg = _store(**{CRED_PATH: self._cred("shared-cid"), SHARED_PATH: SHARED})
        rejected = oauth_client_module.MCPRefreshTokenInvalidError("rejected", error_code="invalid_client")
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(side_effect=rejected)):
            with pytest.raises(oauth_client_module.MCPRefreshTokenInvalidError):
                await refresh_credential_record("inst-1", "user-1", cfg)

        # Marked, not deleted: one rejection mustn't sign out everyone else it issued tokens to.
        assert cfg.data[SHARED_PATH]["clientId"] == "shared-cid"
        assert cfg.data[SHARED_PATH]["rejectedAt"] > 0

    @pytest.mark.asyncio
    async def test_other_users_of_a_rejected_shared_client_still_refresh_with_it(self) -> None:
        other_cred = get_mcp_credentials_path("inst-1", "user-2")
        cfg = _store(**{other_cred: self._cred("shared-cid"), SHARED_PATH: {**SHARED, "rejectedAt": 1}})
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(return_value=self._refreshed())) as refresh:
            await refresh_credential_record("inst-1", "user-2", cfg)

        assert refresh.await_args.kwargs["client_id"] == "shared-cid"

    @pytest.mark.asyncio
    async def test_a_rejected_retired_client_goes_and_the_current_one_stays(self) -> None:
        cfg = _store(**{CRED_PATH: self._cred("retired-cid"), SHARED_PATH: SHARED_WITH_RETIRED})
        rejected = oauth_client_module.MCPRefreshTokenInvalidError("rejected", error_code="invalid_client")
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(side_effect=rejected)):
            with pytest.raises(oauth_client_module.MCPRefreshTokenInvalidError):
                await refresh_credential_record("inst-1", "user-1", cfg)

        assert cfg.data[SHARED_PATH] == SHARED

    @pytest.mark.asyncio
    async def test_a_rejected_static_app_is_left_for_an_admin(self) -> None:
        cfg = _store(**{CRED_PATH: self._cred("static-cid"), STATIC_PATH: STATIC})
        rejected = oauth_client_module.MCPRefreshTokenInvalidError("rejected", error_code="invalid_client")
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(side_effect=rejected)):
            with pytest.raises(oauth_client_module.MCPRefreshTokenInvalidError):
                await refresh_credential_record("inst-1", "user-1", cfg)

        assert cfg.data[STATIC_PATH] == STATIC
        assert cfg.deletes == []

    @pytest.mark.asyncio
    async def test_a_rejected_grant_leaves_the_client_alone(self) -> None:
        cfg = _store(**{CRED_PATH: self._cred("shared-cid"), SHARED_PATH: SHARED})
        rejected = oauth_client_module.MCPRefreshTokenInvalidError("rejected", error_code="invalid_grant")
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(side_effect=rejected)):
            with pytest.raises(oauth_client_module.MCPRefreshTokenInvalidError):
                await refresh_credential_record("inst-1", "user-1", cfg)

        assert cfg.data[SHARED_PATH] == SHARED


class TestRetireRejectedDcrClient:
    @pytest.mark.asyncio
    async def test_an_owners_legacy_client_is_removed_by_its_id(self) -> None:
        cfg = _store(**{LEGACY_PATH: LEGACY, SHARED_PATH: SHARED})
        await retire_rejected_dcr_client(cfg, "inst-1", "user-1", "legacy-cid")
        assert LEGACY_PATH not in cfg.data
        assert cfg.data[SHARED_PATH] == SHARED

    @pytest.mark.asyncio
    async def test_the_shared_client_is_marked_once(self) -> None:
        cfg = _store(**{SHARED_PATH: {**SHARED, "rejectedAt": 7}})
        await retire_rejected_dcr_client(cfg, "inst-1", "user-1", "shared-cid")
        assert cfg.data[SHARED_PATH]["rejectedAt"] == 7
        assert cfg.writes == []

    @pytest.mark.asyncio
    async def test_another_clients_record_is_untouched(self) -> None:
        cfg = _store(**{SHARED_PATH: SHARED})
        await retire_rejected_dcr_client(cfg, "inst-1", "user-1", "someone-else")
        assert cfg.data[SHARED_PATH] == SHARED
        assert cfg.deletes == []
        assert cfg.writes == []

    @pytest.mark.asyncio
    async def test_no_client_id_does_nothing(self) -> None:
        cfg = _store(**{SHARED_PATH: SHARED})
        await retire_rejected_dcr_client(cfg, "inst-1", "user-1", None)
        assert cfg.data[SHARED_PATH] == SHARED


class TestARefreshOutlivesItsCaller:
    """A provider that rotates refresh tokens has spent the old one once it answers: a caller
    that gives up (Stop, a listing's deadline) mustn't lose the new one."""

    async def test_cancelling_the_caller_still_saves_the_rotated_tokens(self) -> None:
        cfg = _store(**{CRED_PATH: _oauth_record(), SHARED_PATH: SHARED})
        answered = asyncio.Event()
        release = asyncio.Event()

        async def slow_refresh(**_kwargs: object) -> OAuthTokens:
            answered.set()
            await release.wait()
            return OAuthTokens(access_token="access-2", refresh_token="refresh-2", token_url="https://example.com/token")

        with patch.object(oauth_client_module, "refresh_access_token", new=slow_refresh):
            caller = asyncio.ensure_future(refresh_credential_record("inst-1", "user-1", cfg))
            await answered.wait()
            caller.cancel()
            with pytest.raises(asyncio.CancelledError):
                await caller
            release.set()
            for _ in range(100):
                if cfg.data[CRED_PATH]["oauthTokens"]["refreshToken"] == "refresh-2":
                    break
                await asyncio.sleep(0.01)

        assert cfg.data[CRED_PATH]["oauthTokens"]["refreshToken"] == "refresh-2"

    async def test_a_caller_that_stays_still_gets_the_failure(self) -> None:
        cfg = _store(**{CRED_PATH: _oauth_record(), SHARED_PATH: SHARED})
        with patch.object(oauth_client_module, "refresh_access_token", new=AsyncMock(side_effect=oauth_client_module.MCPOAuthError("down"))):
            with pytest.raises(oauth_client_module.MCPOAuthError, match="down"):
                await refresh_credential_record("inst-1", "user-1", cfg)
