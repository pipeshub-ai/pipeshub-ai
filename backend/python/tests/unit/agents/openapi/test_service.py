from datetime import datetime, timedelta, timezone

import httpx
import pytest

from app.agents.mcp.models import OAuthTokens
from app.agents.openapi import auth as auth_module
from app.agents.openapi import service
from app.agents.openapi.auth import ApiToolsetAuthError
from app.agents.openapi.models import (
    ApiToolsetCredentials,
    ApiToolsetInstance,
    AuthScheme,
    AuthType,
    CompiledOperation,
    OAuthClient,
)
from app.agents.openapi.store import ApiToolsetStore
from app.services.featureflag.platform_settings import is_api_toolsets_enabled
from tests.support.in_memory_config import InMemoryConfigService

PAGES = CompiledOperation(tool_name="list_pages", method="GET", path="/pages", input_schema={"type": "object"}, kind="read")


def _instance(scheme: AuthScheme, **extra: object) -> ApiToolsetInstance:
    return ApiToolsetInstance(
        id="i1", org_id="o1", created_by="a", name="Wiki", spec_title="Wiki", openapi_version="3.1.0", spec_hash="h",
        base_url="https://wiki.acme.test", auth_scheme=scheme, timeout_seconds=10, operations=[PAGES],
        created_at=1, updated_at=1, **extra,
    )


@pytest.fixture
def store() -> ApiToolsetStore:
    return ApiToolsetStore(InMemoryConfigService())


class TestBuildExecutor:
    async def test_without_credentials_it_says_to_connect(self, store: ApiToolsetStore) -> None:
        instance = _instance(AuthScheme(name="k", type=AuthType.API_KEY, location="header", param_name="K"))
        with pytest.raises(ApiToolsetAuthError, match="not connected yet"):
            await service.build_executor(store, instance, "u1")

    async def test_no_auth_needs_no_credentials(self, store: ApiToolsetStore) -> None:
        executor = await service.build_executor(
            store, _instance(AuthScheme(name="none", type=AuthType.NONE)), "u1",
            transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"ok": True})),
        )
        assert (await executor.call(PAGES, {})).body == {"ok": True}

    async def test_refreshed_sign_in_tokens_are_saved_without_the_app_secret(
        self, store: ApiToolsetStore, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scheme = AuthScheme(name="sso", type=AuthType.OAUTH2_AUTHORIZATION_CODE, token_url="https://login.acme.test/token",
                            authorization_url="https://login.acme.test/authorize")
        instance = _instance(scheme)
        await store.save_oauth_client("i1", OAuthClient(client_id="app", client_secret="app-secret"))
        expired = OAuthTokens(access_token="old", refresh_token="r1", expires_in=60,
                              created_at=datetime.now(timezone.utc) - timedelta(hours=1))
        await store.save_credentials("i1", "u1", ApiToolsetCredentials(oauth=expired))
        refreshed_with: dict[str, object] = {}

        async def refresh(token_url: str, client_id: str, client_secret: str | None, refresh_token: str, **kw: object) -> OAuthTokens:
            refreshed_with.update(client_id=client_id, client_secret=client_secret, refresh_token=refresh_token)
            return OAuthTokens(access_token="new", refresh_token="r2", expires_in=3600)

        monkeypatch.setattr(auth_module, "refresh_access_token", refresh)
        seen: list[str] = []

        def api(request: httpx.Request) -> httpx.Response:
            seen.append(request.headers["Authorization"])
            return httpx.Response(200, json=[])

        executor = await service.build_executor(store, instance, "u1", transport=httpx.MockTransport(api))
        await executor.call(PAGES, {})

        assert seen == ["Bearer new"]
        assert refreshed_with == {"client_id": "app", "client_secret": "app-secret", "refresh_token": "r1"}
        saved = await store.get_credentials("i1", "u1")
        assert saved is not None and saved.oauth is not None and saved.oauth.access_token == "new"
        assert saved.client_secret is None and saved.client_id is None


class TestPrivateNetworks:
    def _build(self, base_url: str) -> ApiToolsetInstance:
        data = service.InstanceInput(name="Wiki", base_url=base_url, operations=["list_pages"], auth_scheme_name="none",
                                     spec=b"openapi: 3.1.0\ninfo: {title: W}\npaths: {/pages: {get: {operationId: list_pages}}}\n")
        return service.build_instance(data, instance_id="i1", org_id="o1", user_id="u1")

    def test_private_addresses_are_allowed_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("API_TOOLSETS_ALLOW_PRIVATE_NETWORK_URLS", raising=False)
        assert self._build("http://10.0.4.7:8080").base_url == "http://10.0.4.7:8080"

    def test_the_deployment_can_turn_them_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("API_TOOLSETS_ALLOW_PRIVATE_NETWORK_URLS", "false")
        with pytest.raises(service.ApiToolsetError, match="private address"):
            self._build("http://10.0.4.7:8080")


class _Settings:
    def __init__(self, flags: dict[str, bool] | None) -> None:
        self._value = {"featureFlags": flags} if flags is not None else None

    async def get_config(self, key: str, default: object = None, use_cache: bool = True, **_: object) -> object:
        return self._value if self._value is not None else default


@pytest.mark.parametrize(("flags", "enabled"), [(None, False), ({}, False), ({"ENABLE_API_TOOLSETS": True}, True)])
async def test_the_labs_flag_is_off_by_default(flags: dict[str, bool] | None, enabled: bool) -> None:
    assert await is_api_toolsets_enabled(_Settings(flags)) is enabled  # type: ignore[arg-type]


class TestCredentialsStillApply:
    def _oauth(self, base_url: str, token_url: str) -> ApiToolsetInstance:
        scheme = AuthScheme(name="cc", type=AuthType.OAUTH2_CLIENT_CREDENTIALS, token_url=token_url)
        return _instance(scheme).model_copy(update={"base_url": base_url})

    @pytest.mark.parametrize(
        ("token_url", "applies"),
        [("/oauth/token", True), ("https://wiki.acme.test/oauth/v2/token", True), ("https://login.evil.test/token", False)],
    )
    def test_a_token_url_on_another_host_drops_them(self, token_url: str, applies: bool) -> None:
        old = self._oauth("https://wiki.acme.test", "/oauth/token")
        assert service.credentials_still_apply(old, self._oauth("https://wiki.acme.test", token_url)) is applies

    def test_another_scheme_drops_them(self) -> None:
        old = _instance(AuthScheme(name="k", type=AuthType.API_KEY, location="header", param_name="K"))
        new = _instance(AuthScheme(name="b", type=AuthType.HTTP_BEARER))
        assert service.credentials_still_apply(old, new) is False
