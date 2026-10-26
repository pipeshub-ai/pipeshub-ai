"""Unit tests for the Drupal Wiki client."""

import logging
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.sources.client.drupal_wiki.drupal_wiki import (
    MAX_REASON_CHARS,
    DrupalWikiClient,
    DrupalWikiRESTClientViaToken,
    DrupalWikiTokenConfig,
    check_wiki_address,
    describe_exception,
    describe_response,
    normalize_base_url,
    normalize_personal_access_token,
    problem_reason,
    text_reason,
)
from app.sources.client.http.http_request import HTTPRequest
from app.sources.client.resilience import ResiliencePolicy
from app.sources.external.drupal_wiki.drupal_wiki import DrupalWikiDataSource

BASE_URL = "https://wiki.example.com"


@pytest.fixture
def logger() -> logging.Logger:
    return logging.getLogger("test_drupal_wiki_client")


def _config_service(config: dict | None) -> AsyncMock:
    service = AsyncMock()
    service.get_config = AsyncMock(return_value=config)
    return service


class TestNormalizePersonalAccessToken:
    @pytest.mark.parametrize("raw", ["", "   ", None, "Bearer "])
    def test_empty_token_rejected(self, raw) -> None:
        with pytest.raises(ValueError, match="token is required"):
            normalize_personal_access_token(raw)


class TestNormalizeBaseUrl:
    @pytest.mark.parametrize(
        "raw",
        [
            BASE_URL,
            f"{BASE_URL}/",
            f"{BASE_URL}/api",
            f"{BASE_URL}/api/rest",
            f"{BASE_URL}/api/rest/",
            f"{BASE_URL}/api/spec",
        ],
    )
    def test_reduces_to_wiki_origin(self, raw) -> None:
        assert normalize_base_url(raw) == BASE_URL

    def test_keeps_context_path(self) -> None:
        assert normalize_base_url("https://intranet.example.com/wiki/api/rest") == "https://intranet.example.com/wiki"

    @pytest.mark.parametrize("raw", ["", "wiki.example.com", "ftp://wiki.example.com"])
    def test_invalid_url_rejected(self, raw) -> None:
        with pytest.raises(ValueError):
            normalize_base_url(raw)


class TestDrupalWikiRESTClientViaToken:
    def test_headers(self) -> None:
        client = DrupalWikiRESTClientViaToken(f"{BASE_URL}/api/rest", "secret")
        assert client.headers["Authorization"] == "Bearer secret"
        assert client.headers["X-API-Version"] == "1"
        assert client.headers["Accept"] == "application/json"
        # Content-Type is per request: JSON for bodies, multipart for uploads.
        assert "Content-Type" not in client.headers
        assert client.get_base_url() == BASE_URL
        assert client.get_token() == "secret"

    @pytest.mark.asyncio
    async def test_never_sends_back_the_session_cookie(self) -> None:
        # The wiki rejects a request carrying its SESSION cookie and the token with 400.
        sent: list[tuple[str, str | None]] = []

        async def wiki(_transport: httpx.AsyncHTTPTransport, request: httpx.Request) -> httpx.Response:
            sent.append((request.url.path, request.headers.get("cookie")))
            return httpx.Response(200, headers={"Set-Cookie": "SESSION=abc; Path=/; Secure; HttpOnly"}, json={})

        policy = ResiliencePolicy(rate_limit=10, max_retries=3)
        data_source = DrupalWikiDataSource(
            DrupalWikiClient.build_with_config(DrupalWikiTokenConfig(base_url=BASE_URL, token="pat:t"), policy)
        )
        public = [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0))]
        with patch.object(httpx.AsyncHTTPTransport, "handle_async_request", wiki), patch(
            "socket.getaddrinfo", return_value=public
        ), patch("urllib.request.getproxies", return_value={}):
            await data_source.list_users(size=1)
            await data_source.list_spaces(size=1)
            await data_source.list_spaces(page=1, size=1)

        assert [path for path, _ in sent] == ["/api/rest/scope/api/user"] + ["/api/rest/scope/api/space"] * 2
        assert [cookie for _, cookie in sent] == [None, None, None]

class TestDrupalWikiClient:
    def test_build_with_config(self) -> None:
        client = DrupalWikiClient.build_with_config(DrupalWikiTokenConfig(base_url=BASE_URL, token="pat:t"))
        assert isinstance(client.get_client(), DrupalWikiRESTClientViaToken)
        assert client.get_base_url() == BASE_URL

    def test_config_to_dict(self) -> None:
        assert DrupalWikiTokenConfig(base_url=BASE_URL, token="t").to_dict() == {
            "base_url": BASE_URL,
            "token": "t",
            "timeout": 30.0,
        }

    @pytest.mark.asyncio
    async def test_build_from_services_api_token(self, logger) -> None:
        service = _config_service({"auth": {"authType": "API_TOKEN", "baseUrl": f"{BASE_URL}/", "apiToken": "tok"}})
        client = await DrupalWikiClient.build_from_services(logger, service, "conn-1")
        service.get_config.assert_awaited_once_with("/services/connectors/conn-1/config")
        assert client.get_base_url() == BASE_URL
        assert client.get_client().headers["Authorization"] == "Bearer tok"

    @pytest.mark.asyncio
    async def test_auth_type_defaults_to_api_token(self, logger) -> None:
        service = _config_service({"auth": {"baseUrl": BASE_URL, "apiToken": "tok"}})
        client = await DrupalWikiClient.build_from_services(logger, service, "conn-1")
        assert client.get_client().get_token() == "tok"

    @pytest.mark.asyncio
    async def test_resilience_is_passed_to_http_client(self, logger) -> None:
        policy = object()
        service = _config_service({"auth": {"baseUrl": BASE_URL, "apiToken": "tok"}})
        client = await DrupalWikiClient.build_from_services(logger, service, "conn-1", resilience=policy)
        assert client.get_client().resilience is policy

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("config", "message"),
        [
            ({"auth": {"authType": "OAUTH", "baseUrl": BASE_URL, "apiToken": "t"}}, "Unsupported Drupal Wiki auth type"),
            ({"auth": {"authType": "BASIC_AUTH", "baseUrl": BASE_URL}}, "Unsupported Drupal Wiki auth type"),
            ({"auth": {"apiToken": "t"}}, "Base URL is required"),
            ({"auth": {"baseUrl": BASE_URL}}, "Personal access token is required"),
            ({"auth": {}}, "Auth configuration not found"),
            ({"auth": None}, "Auth configuration not found"),
        ],
    )
    async def test_build_from_services_rejects_bad_config(self, logger, config, message) -> None:
        with pytest.raises(ValueError, match=message):
            await DrupalWikiClient.build_from_services(logger, _config_service(config), "conn-1")

    @pytest.mark.asyncio
    async def test_missing_config_raises(self, logger) -> None:
        with pytest.raises(ValueError, match="Failed to get Drupal Wiki connector configuration"):
            await DrupalWikiClient.build_from_services(logger, _config_service(None), "conn-1")

    @pytest.mark.asyncio
    async def test_config_service_error_raises_value_error(self, logger) -> None:
        service = AsyncMock()
        service.get_config = AsyncMock(side_effect=RuntimeError("kv down"))
        with pytest.raises(ValueError, match="Failed to get Drupal Wiki connector configuration"):
            await DrupalWikiClient.build_from_services(logger, service, "conn-1")


def _toolset_config(**overrides: object) -> dict:
    config = {
        "isAuthenticated": True,
        "authType": "API_TOKEN",
        "instanceId": "inst-1",
        "orgId": "org-1",
        "auth": {"apiToken": "user-token"},
    }
    config.update(overrides)
    return config


class TestBuildFromToolset:
    @pytest.mark.asyncio
    async def test_uses_instance_base_url_and_user_token(self, logger: logging.Logger) -> None:
        instance = {"_id": "inst-1", "auth": {"baseUrl": f"{BASE_URL}/api/rest"}}
        lookup = AsyncMock(return_value=instance)
        service = AsyncMock()
        with patch("app.edition_config.get_toolset_by_id", lookup):
            client = await DrupalWikiClient.build_from_toolset(_toolset_config(), logger, service)

        lookup.assert_awaited_once_with("inst-1", service, org_id="org-1")
        assert client.get_base_url() == BASE_URL
        assert client.get_client().headers["Authorization"] == "Bearer user-token"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("config", "instance", "message"),
        [
            (_toolset_config(authType="OAUTH"), {"auth": {"baseUrl": BASE_URL}}, "Unsupported Drupal Wiki auth type"),
            (_toolset_config(instanceId=None), {"auth": {"baseUrl": BASE_URL}}, "instanceId is required"),
            (_toolset_config(), None, "instance 'inst-1' not found"),
            (_toolset_config(), {"auth": {}}, "Admin must configure the Drupal Wiki URL"),
            (_toolset_config(auth={}), {"auth": {"baseUrl": BASE_URL}}, "Personal access token is required"),
        ],
    )
    async def test_rejects_bad_config(
        self, logger: logging.Logger, config: dict, instance: dict | None, message: str
    ) -> None:
        with patch("app.edition_config.get_toolset_by_id", AsyncMock(return_value=instance)):
            with pytest.raises(ValueError, match=message):
                await DrupalWikiClient.build_from_toolset(config, logger, AsyncMock())

    @pytest.mark.asyncio
    async def test_requires_authenticated_toolset(self, logger: logging.Logger) -> None:
        with pytest.raises(ValueError, match="not authenticated"):
            await DrupalWikiClient.build_from_toolset(_toolset_config(isAuthenticated=False), logger, AsyncMock())

    @pytest.mark.asyncio
    async def test_requires_config_service(self, logger: logging.Logger) -> None:
        with pytest.raises(ValueError, match="config_service is required"):
            await DrupalWikiClient.build_from_toolset(_toolset_config(), logger, None)


class TestCheckWikiAddress:
    """Literal addresses only, so no test here depends on DNS."""

    @pytest.mark.asyncio
    async def test_cloud_metadata_is_always_refused(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        with pytest.raises(ValueError, match="metadata"):
            await check_wiki_address("http://169.254.169.254")

    @pytest.mark.asyncio
    async def test_a_private_address_is_allowed_by_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Most Drupal Wikis run on-premise on a private network.
        monkeypatch.delenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", raising=False)
        await check_wiki_address("http://10.0.0.5/wiki")

    @pytest.mark.asyncio
    async def test_a_private_address_is_refused_when_the_deployment_blocks_them(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PIPESHUB_BLOCK_PRIVATE_ADDRESSES", "true")
        with pytest.raises(ValueError, match="private or internal"):
            await check_wiki_address("http://10.0.0.5")


class TestRedirectsAreVetted:
    """A redirect out of the wiki is held to the same rule as the wiki's own address."""

    @staticmethod
    async def _client(handler) -> DrupalWikiRESTClientViaToken:
        rest = DrupalWikiRESTClientViaToken("https://wiki.example.com", "tok")
        client = await rest._ensure_client()
        client._transport = httpx.MockTransport(handler)
        return rest

    @pytest.mark.asyncio
    async def test_a_redirect_to_cloud_metadata_is_refused_before_it_is_sent(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url.host))
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/latest/meta-data"})

        rest = await self._client(handler)
        with pytest.raises(ValueError, match="metadata"):
            await rest.execute(HTTPRequest(method="GET", url="https://wiki.example.com/api/rest/scope/api/user"))
        assert seen == ["wiki.example.com"]

    @pytest.mark.asyncio
    async def test_a_download_redirect_to_cloud_metadata_is_refused(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url.host))
            return httpx.Response(302, headers={"Location": "http://169.254.169.254/"})

        rest = await self._client(handler)
        source = DrupalWikiDataSource(DrupalWikiClient(rest))
        with pytest.raises(ValueError, match="metadata"):
            async for _ in source.download_attachment(7):
                pass
        assert seen == ["wiki.example.com"]

    @pytest.mark.asyncio
    async def test_a_redirect_within_the_wiki_is_followed_without_a_lookup(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/old":
                return httpx.Response(301, headers={"Location": "https://wiki.example.com/new"})
            return httpx.Response(200, json={"ok": True})

        rest = await self._client(handler)
        with patch(
            "app.sources.client.drupal_wiki.drupal_wiki.vetted_addresses", new=AsyncMock()
        ) as vetted:
            response = await rest.execute(HTTPRequest(method="GET", url="https://wiki.example.com/old"))
        assert response.status == 200
        vetted.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_check_is_added_once(self) -> None:
        rest = DrupalWikiRESTClientViaToken("https://wiki.example.com", "tok")
        client = await rest._ensure_client()
        _ = await rest._ensure_client()
        assert client.event_hooks["request"].count(rest._vet_redirect) == 1


def _reply(status: int, body: object = None, text: object = None) -> MagicMock:
    resp = MagicMock()
    resp.status = status
    if isinstance(body, Exception):
        resp.json.side_effect = body
    else:
        resp.json.return_value = body
    resp.text.return_value = text
    return resp


class TestProblemReason:
    def test_problem_response_gives_code_title_and_detail(self) -> None:
        body = {"status": 403, "title": "Forbidden", "detail": "Missing right space_read",
                "errorCode": "ACCESS_DENIED"}
        assert problem_reason(body) == "ACCESS_DENIED: Forbidden: Missing right space_read"

    def test_detail_that_repeats_the_title_is_not_doubled(self) -> None:
        body = {"title": "Invalid token", "detail": "Invalid token", "errorCode": None}
        assert problem_reason(body) == "Invalid token"

    def test_graphql_errors_give_the_code_and_first_message(self) -> None:
        body = {"errors": [{"message": "Operation not allowed",
                            "extensions": {"classification": "QueryNotAllowed"}}]}
        assert problem_reason(body) == "QueryNotAllowed: Operation not allowed"

    @pytest.mark.parametrize("body", [None, [], "text", {}, {"unrelated": 1}])
    def test_anything_else_gives_nothing(self, body: object) -> None:
        assert problem_reason(body) == ""


class TestTextReason:
    def test_an_html_page_is_named_by_its_title(self) -> None:
        page = "<html><head><title>Sign in - Corp SSO</title></head><body>...</body></html>"
        assert text_reason(page) == "Sign in - Corp SSO"

    def test_plain_text_is_bounded(self) -> None:
        assert len(text_reason("x" * 5000)) <= MAX_REASON_CHARS + 3

    def test_secrets_in_a_url_are_redacted(self) -> None:
        reason = text_reason("Redirected to https://sso.example.com/cb?access_token=abc123secret")
        assert "abc123secret" not in reason

    def test_scripts_are_left_out(self) -> None:
        assert text_reason("<script>var t='x'</script>Bad gateway") == "Bad gateway"


class TestDescribeResponse:
    def test_vendor_reason_and_status(self) -> None:
        resp = _reply(401, {"status": 401, "title": "Unauthorized",
                           "detail": "Invalid or missing personal access token"})
        assert describe_response(resp) == (
            "Unauthorized: Invalid or missing personal access token (HTTP 401)"
        )

    def test_an_html_error_page_falls_back_to_its_title(self) -> None:
        resp = _reply(502, ValueError("not json"), "<title>502 Bad Gateway</title>")
        assert describe_response(resp) == "502 Bad Gateway (HTTP 502)"

    def test_an_empty_reply_gives_the_status_alone(self) -> None:
        assert describe_response(_reply(500, None, "")) == "HTTP 500"

    def test_a_reply_that_cannot_be_read_still_gives_the_status(self) -> None:
        resp = _reply(503, RuntimeError("stream closed"))
        resp.text.side_effect = RuntimeError("stream closed")
        assert describe_response(resp) == "HTTP 503"


class TestDescribeException:
    def test_an_empty_message_names_the_type(self) -> None:
        assert describe_exception(httpx.ReadTimeout("")) == "ReadTimeout"

    def test_a_message_is_kept_with_its_type(self) -> None:
        assert describe_exception(httpx.ConnectError("All connection attempts failed")) == (
            "ConnectError: All connection attempts failed"
        )

    def test_a_status_error_carries_the_vendors_reason_not_the_request(self) -> None:
        request = httpx.Request(
            "GET", "https://wiki.example.com/api/rest/scope/api/attachment/9/download",
            headers={"Authorization": "Bearer pat:secret"},
        )
        response = httpx.Response(404, json={"title": "Not Found", "detail": "Attachment 9 not found"},
                                  request=request)
        error = httpx.HTTPStatusError("404", request=request, response=response)

        described = describe_exception(error)

        assert described == "Not Found: Attachment 9 not found (HTTP 404)"
        assert "pat:secret" not in described
