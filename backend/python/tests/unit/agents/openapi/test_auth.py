import base64
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import pytest

from app.agents.mcp.models import OAuthTokens
from app.agents.mcp.oauth_client import MCPOAuthError
from app.agents.openapi import auth as auth_module
from app.agents.openapi.auth import (
    ApiToolsetAuthError,
    NoAuth,
    OAuth2Auth,
    OAuthTokenSource,
    build_auth,
)
from app.agents.openapi.executor import ApiCallError, OpenAPIHttpExecutor
from app.agents.openapi.models import (
    ApiToolsetCredentials,
    AuthScheme,
    AuthType,
    CompiledOperation,
)
from tests.unit.agents.mcp.refresh_failures import (
    BODY_SECRET,
    TOKEN_HOST,
    token_endpoint_error,
)

BASE = "https://api.acme.test/v2"


def _request(url: str = BASE + "/items?x=1") -> httpx.Request:
    return httpx.Request("GET", url)


async def _applied(scheme: AuthScheme, creds: ApiToolsetCredentials) -> httpx.Request:
    request = _request()
    await build_auth(scheme, creds, base_url=BASE, allow_private=False).apply(request)
    return request


class TestStaticSchemes:
    async def test_api_key_in_header(self) -> None:
        scheme = AuthScheme(name="k", type=AuthType.API_KEY, location="header", param_name="X-API-Key")
        assert (await _applied(scheme, ApiToolsetCredentials(api_key="s3cret"))).headers["X-API-Key"] == "s3cret"

    async def test_api_key_in_query_keeps_other_params(self) -> None:
        scheme = AuthScheme(name="k", type=AuthType.API_KEY, location="query", param_name="api_key")
        request = await _applied(scheme, ApiToolsetCredentials(api_key="s3cret"))
        assert list(request.url.params.multi_items()) == [("x", "1"), ("api_key", "s3cret")]

    async def test_api_key_in_cookie_joins_existing_cookies(self) -> None:
        scheme = AuthScheme(name="k", type=AuthType.API_KEY, location="cookie", param_name="sid")
        request = _request()
        request.headers["Cookie"] = "a=1"
        await build_auth(scheme, ApiToolsetCredentials(api_key="s3cret"), base_url=BASE, allow_private=False).apply(request)
        assert request.headers["Cookie"] == "a=1; sid=s3cret"

    async def test_bearer(self) -> None:
        request = await _applied(AuthScheme(name="b", type=AuthType.HTTP_BEARER), ApiToolsetCredentials(token="t0k"))
        assert request.headers["Authorization"] == "Bearer t0k"

    async def test_basic(self) -> None:
        request = await _applied(AuthScheme(name="b", type=AuthType.HTTP_BASIC), ApiToolsetCredentials(username="u", password="p:w"))
        assert request.headers["Authorization"] == "Basic " + base64.b64encode(b"u:p:w").decode()

    async def test_none_adds_nothing(self) -> None:
        applier = build_auth(AuthScheme(name="none", type=AuthType.NONE), ApiToolsetCredentials(), base_url=BASE, allow_private=False)
        assert isinstance(applier, NoAuth) and not applier.can_refresh
        request = _request()
        await applier.apply(request)
        assert "Authorization" not in request.headers

    @pytest.mark.parametrize(
        ("scheme", "creds", "missing"),
        [
            (AuthScheme(name="k", type=AuthType.API_KEY, location="header", param_name="K"), ApiToolsetCredentials(), "API key"),
            (AuthScheme(name="b", type=AuthType.HTTP_BEARER), ApiToolsetCredentials(), "bearer token"),
            (AuthScheme(name="b", type=AuthType.HTTP_BASIC), ApiToolsetCredentials(username="u"), "password"),
        ],
    )
    def test_missing_credentials_are_reported(self, scheme: AuthScheme, creds: ApiToolsetCredentials, missing: str) -> None:
        with pytest.raises(ApiToolsetAuthError, match=missing):
            build_auth(scheme, creds, base_url=BASE, allow_private=False)


def _tokens(access: str, *, expires_in: int | None = 3600, refresh: str | None = None, age: int = 0) -> OAuthTokens:
    return OAuthTokens(access_token=access, expires_in=expires_in, refresh_token=refresh,
                       created_at=datetime.now(timezone.utc) - timedelta(seconds=age))


class _FakeTokenEndpoint:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.error: Exception | None = None

    async def client_credentials(self, token_url: str, client_id: str, client_secret: str | None, **kw: object) -> OAuthTokens:
        self.calls.append(("client_credentials", {"url": token_url, "client_id": client_id, **kw}))
        if self.error:
            raise self.error
        return _tokens(f"cc-{len(self.calls)}")

    async def refresh(self, token_url: str, client_id: str, client_secret: str | None, refresh_token: str, **kw: object) -> OAuthTokens:
        self.calls.append(("refresh", {"url": token_url, "refresh_token": refresh_token, **kw}))
        if self.error:
            raise self.error
        return _tokens(f"rt-{len(self.calls)}", refresh="r2")


@pytest.fixture
def endpoint(monkeypatch: pytest.MonkeyPatch) -> _FakeTokenEndpoint:
    fake = _FakeTokenEndpoint()
    monkeypatch.setattr(auth_module, "request_client_credentials_token", fake.client_credentials)
    monkeypatch.setattr(auth_module, "refresh_access_token", fake.refresh)
    return fake


CLIENT_CREDENTIALS = AuthScheme(name="o", type=AuthType.OAUTH2_CLIENT_CREDENTIALS, token_url="/oauth/token")
AUTH_CODE = AuthScheme(name="o", type=AuthType.OAUTH2_AUTHORIZATION_CODE, token_url="https://auth.acme.test/token")


class TestOAuth:
    async def test_client_credentials_token_is_fetched_once_then_reused(self, endpoint: _FakeTokenEndpoint) -> None:
        saved: list[ApiToolsetCredentials] = []

        async def save(creds: ApiToolsetCredentials) -> None:
            saved.append(creds)

        applier = build_auth(CLIENT_CREDENTIALS, ApiToolsetCredentials(client_id="c", client_secret="s"),
                             base_url=BASE, allow_private=True, scopes=["tickets:read"], save_tokens=save)
        assert isinstance(applier, OAuth2Auth) and applier.can_refresh
        first, second = _request(), _request()
        await applier.apply(first)
        await applier.apply(second)

        assert first.headers["Authorization"] == second.headers["Authorization"] == "Bearer cc-1"
        assert len(endpoint.calls) == 1
        kind, call = endpoint.calls[0]
        # A relative tokenUrl resolves against the host of the base URL.
        assert call["url"] == "https://api.acme.test/oauth/token"
        assert call["scopes"] == ["tickets:read"] and call["allow_private"] is True
        assert saved[-1].oauth is not None and saved[-1].oauth.access_token == "cc-1"

    async def test_force_refresh_gets_a_new_token(self, endpoint: _FakeTokenEndpoint) -> None:
        source = OAuthTokenSource(CLIENT_CREDENTIALS, ApiToolsetCredentials(client_id="c", oauth=_tokens("old")),
                                  token_url="https://a/t", allow_private=False)
        assert await source.access_token() == "old"
        assert await source.access_token(force_refresh=True) == "cc-1"

    async def test_token_close_to_expiry_is_replaced(self, endpoint: _FakeTokenEndpoint) -> None:
        source = OAuthTokenSource(CLIENT_CREDENTIALS, ApiToolsetCredentials(client_id="c", oauth=_tokens("old", expires_in=60, age=45)),
                                  token_url="https://a/t", allow_private=False)
        assert await source.access_token() == "cc-1"

    async def test_token_without_expiry_is_reused(self, endpoint: _FakeTokenEndpoint) -> None:
        source = OAuthTokenSource(CLIENT_CREDENTIALS, ApiToolsetCredentials(client_id="c", oauth=_tokens("old", expires_in=None, age=10**6)),
                                  token_url="https://a/t", allow_private=False)
        assert await source.access_token() == "old"

    async def test_authorization_code_refreshes_with_the_refresh_token(self, endpoint: _FakeTokenEndpoint) -> None:
        creds = ApiToolsetCredentials(client_id="c", oauth=_tokens("old", expires_in=10, age=100, refresh="r1"))
        source = OAuthTokenSource(AUTH_CODE, creds, token_url="https://auth.acme.test/token", allow_private=False)
        assert await source.access_token() == "rt-1"
        assert endpoint.calls[0][1]["refresh_token"] == "r1"

    async def test_authorization_code_without_refresh_token_asks_to_sign_in(self, endpoint: _FakeTokenEndpoint) -> None:
        source = OAuthTokenSource(AUTH_CODE, ApiToolsetCredentials(client_id="c"), token_url="https://a/t", allow_private=False)
        with pytest.raises(ApiToolsetAuthError, match="Sign in again"):
            await source.access_token()

    def test_a_sign_in_without_a_refresh_token_cannot_refresh(self) -> None:
        # Otherwise a 401 (Jira refusing a token sent to the site URL) reads as "sign-in expired".
        signed_in = ApiToolsetCredentials(client_id="c", oauth=_tokens("a"))
        applier = build_auth(AUTH_CODE, signed_in, base_url=BASE, allow_private=False)
        assert not applier.can_refresh
        refreshable = signed_in.model_copy(update={"oauth": _tokens("a", refresh="r1")})
        assert build_auth(AUTH_CODE, refreshable, base_url=BASE, allow_private=False).can_refresh

    async def test_client_credentials_without_client_id(self, endpoint: _FakeTokenEndpoint) -> None:
        source = OAuthTokenSource(CLIENT_CREDENTIALS, ApiToolsetCredentials(), token_url="https://a/t", allow_private=False)
        with pytest.raises(ApiToolsetAuthError, match="client ID"):
            await source.access_token()

    async def test_token_endpoint_failure_is_reported_without_details(self, endpoint: _FakeTokenEndpoint) -> None:
        endpoint.error = MCPOAuthError("OAuth token request failed (401): invalid_client: secret abc")
        source = OAuthTokenSource(CLIENT_CREDENTIALS, ApiToolsetCredentials(client_id="c"), token_url="https://a/t", allow_private=False)
        with pytest.raises(ApiToolsetAuthError) as raised:
            await source.access_token()
        assert "abc" not in str(raised.value)


PROVIDER_DOWN = (
    "The API's OAuth server is down or busy, so PipesHub couldn't get an access token. Try again in a few minutes."
)
OUTAGES = [
    (500, {"error": "server_error"}), (502, {}), (503, {"error": "temporarily_unavailable"}), (504, {}),
    (429, {"error": "slow_down"}),
    # An overloaded endpoint can send any code: the status decides.
    (503, {"error": "invalid_grant"}),
]
REFUSALS = [
    (400, {"error": "invalid_grant"}), (401, {"error": "invalid_client"}), (403, {}), (400, {"error": "invalid_request"}),
    (200, {}),  # answered without an access token: no status on the error
]


def _with_secret(body: dict[str, Any]) -> dict[str, Any]:
    return {**body, "error_description": BODY_SECRET}


def _refreshable() -> ApiToolsetCredentials:
    return ApiToolsetCredentials(client_id="c", oauth=_tokens("old", expires_in=10, age=100, refresh="r1"))


class TestTokenEndpointOutage:
    """A provider that is down or rate-limiting hasn't revoked anything: the user is told to retry, not to sign in."""

    @pytest.mark.parametrize(("status", "body"), OUTAGES)
    async def test_a_refresh_outage_says_try_again(self, endpoint: _FakeTokenEndpoint, status: int, body: dict[str, Any]) -> None:
        endpoint.error = await token_endpoint_error(status, _with_secret(body))
        saved: list[ApiToolsetCredentials] = []

        async def save(creds: ApiToolsetCredentials) -> None:
            saved.append(creds)

        source = OAuthTokenSource(AUTH_CODE, _refreshable(), token_url="https://auth.acme.test/token", allow_private=False, save=save)
        with pytest.raises(ApiCallError) as raised:
            await source.access_token()
        assert str(raised.value) == PROVIDER_DOWN
        assert raised.value.__cause__ is endpoint.error and endpoint.error.status == status
        assert not saved

    @pytest.mark.parametrize(("status", "body"), [(503, {"error": "temporarily_unavailable"}), (429, {"error": "slow_down"})])
    async def test_a_client_credentials_outage_says_try_again(
        self, endpoint: _FakeTokenEndpoint, status: int, body: dict[str, Any],
    ) -> None:
        endpoint.error = await token_endpoint_error(status, _with_secret(body))
        source = OAuthTokenSource(CLIENT_CREDENTIALS, ApiToolsetCredentials(client_id="c", client_secret="s"),
                                  token_url="https://a/t", allow_private=False)
        with pytest.raises(ApiCallError, match="^The API's OAuth server is down or busy"):
            await source.access_token()
        assert [kind for kind, _ in endpoint.calls] == ["client_credentials"]

    @pytest.mark.parametrize(("status", "body"), REFUSALS)
    async def test_a_refusal_still_asks_to_sign_in_again(self, endpoint: _FakeTokenEndpoint, status: int, body: dict[str, Any]) -> None:
        endpoint.error = await token_endpoint_error(status, _with_secret(body))
        source = OAuthTokenSource(AUTH_CODE, _refreshable(), token_url="https://auth.acme.test/token", allow_private=False)
        with pytest.raises(ApiToolsetAuthError, match="or sign in again") as raised:
            await source.access_token()
        assert BODY_SECRET not in str(raised.value) and TOKEN_HOST not in str(raised.value)

    async def test_after_the_outage_the_same_sign_in_works(self, endpoint: _FakeTokenEndpoint) -> None:
        saved: list[ApiToolsetCredentials] = []

        async def save(creds: ApiToolsetCredentials) -> None:
            saved.append(creds)

        source = OAuthTokenSource(AUTH_CODE, _refreshable(), token_url="https://auth.acme.test/token", allow_private=False, save=save)
        endpoint.error = await token_endpoint_error(503, {"error": "temporarily_unavailable"})
        with pytest.raises(ApiCallError):
            await source.access_token()
        endpoint.error = None
        assert await source.access_token() == "rt-2"
        assert [call["refresh_token"] for _, call in endpoint.calls] == ["r1", "r1"]
        assert len(saved) == 1

    async def test_an_outage_on_the_refresh_after_a_401_says_try_again(self, endpoint: _FakeTokenEndpoint) -> None:
        creds = ApiToolsetCredentials(client_id="c", oauth=_tokens("valid", refresh="r1"))
        auth = build_auth(AUTH_CODE, creds, base_url=BASE, allow_private=False)
        executor = OpenAPIHttpExecutor(BASE, auth, allow_private=False,
                                       transport=httpx.MockTransport(lambda r: httpx.Response(401, json={})))
        endpoint.error = await token_endpoint_error(503, _with_secret({"error": "temporarily_unavailable"}))
        op = CompiledOperation(tool_name="t", method="GET", path="/items", input_schema={"type": "object"})
        with pytest.raises(ApiCallError, match="^The API's OAuth server is down or busy"):
            await executor.call(op, {})
        assert [kind for kind, _ in endpoint.calls] == ["refresh"]


class TestSharedCredentialRefresh:
    """Two requests on one stored sign-in, each with its own token source, as two chats have."""

    async def test_concurrent_requests_spend_the_refresh_token_once(self, endpoint: _FakeTokenEndpoint) -> None:
        import asyncio

        expired = ApiToolsetCredentials(client_id="app", oauth=_tokens("old", expires_in=60, refresh="r1", age=3600))
        stored = {"creds": expired}

        async def save(creds: ApiToolsetCredentials) -> None:
            stored["creds"] = creds

        async def load() -> ApiToolsetCredentials:
            return stored["creds"]

        sources = [
            OAuthTokenSource(AUTH_CODE, expired, token_url="https://auth.acme.test/token", allow_private=True,
                             save=save, load=load, lock_key="toolset-1/__shared__")
            for _ in range(2)
        ]
        tokens = await asyncio.gather(*(s.access_token() for s in sources))

        assert [kind for kind, _ in endpoint.calls] == ["refresh"]
        assert tokens[0] == tokens[1] == stored["creds"].oauth.access_token

    async def test_a_rejected_token_is_refreshed_when_the_store_has_nothing_newer(self, endpoint: _FakeTokenEndpoint) -> None:
        held = ApiToolsetCredentials(client_id="app", oauth=_tokens("rejected", refresh="r1"))

        async def load() -> ApiToolsetCredentials:
            return held

        source = OAuthTokenSource(AUTH_CODE, held, token_url="https://auth.acme.test/token", allow_private=True,
                                  load=load, lock_key="toolset-2/u1")
        assert await source.access_token(force_refresh=True) != "rejected"
        assert len(endpoint.calls) == 1

    async def test_a_rotated_refresh_token_is_used_even_when_the_access_token_came_back_the_same(
        self, endpoint: _FakeTokenEndpoint,
    ) -> None:
        held = ApiToolsetCredentials(client_id="app", oauth=_tokens("same", refresh="r1"))
        rotated = ApiToolsetCredentials(client_id="app", oauth=_tokens("same", refresh="r2"))

        async def load() -> ApiToolsetCredentials:
            return rotated

        source = OAuthTokenSource(AUTH_CODE, held, token_url="https://auth.acme.test/token", allow_private=True,
                                  load=load, lock_key="toolset-3/u1")
        # The API refused "same": refresh, with the refresh token another request rotated to.
        await source.access_token(force_refresh=True)
        assert [call["refresh_token"] for _, call in endpoint.calls] == ["r2"]
