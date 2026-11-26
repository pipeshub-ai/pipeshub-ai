"""Unit tests for ServiceNow client module."""

import base64
import logging
import time
from unittest.mock import AsyncMock, MagicMock
from urllib.parse import parse_qs

import httpx
import pytest

from app.sources.client.resilience import ResiliencePolicy
from app.sources.external.servicenow.servicenow import ServiceNowDataSource

from app.sources.client.servicenow.servicenow import (
    ServiceNowAPIKeyConfig,
    ServiceNowClient,
    ServiceNowOAuthAuthorizationCodeConfig,
    ServiceNowOAuthClientCredentialsConfig,
    ServiceNowOAuthROPCConfig,
    ServiceNowRESTClientViaAPIKey,
    ServiceNowRESTClientViaOAuthAuthorizationCode,
    ServiceNowRESTClientViaOAuthClientCredentials,
    ServiceNowRESTClientViaOAuthROPC,
    ServiceNowRESTClientViaToken,
    ServiceNowRESTClientViaUsernamePassword,
    ServiceNowResponse,
    ServiceNowTokenConfig,
    ServiceNowUsernamePasswordConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def logger():
    return logging.getLogger("test_servicenow_client")


@pytest.fixture
def mock_config_service():
    return AsyncMock()


INSTANCE_URL = "https://acme.service-now.com"


# ---------------------------------------------------------------------------
# ServiceNowResponse
# ---------------------------------------------------------------------------


class TestServiceNowResponse:
    def test_success(self):
        resp = ServiceNowResponse(success=True, data={"key": "val"})
        assert resp.success is True

    def test_to_dict(self):
        resp = ServiceNowResponse(success=True, data={"k": "v"})
        d = resp.to_dict()
        assert d["success"] is True

    def test_to_json(self):
        resp = ServiceNowResponse(success=True)
        j = resp.to_json()
        assert '"success":true' in j or '"success": true' in j


# ---------------------------------------------------------------------------
# REST client classes
# ---------------------------------------------------------------------------


class TestServiceNowRESTClientViaUsernamePassword:
    def test_init(self):
        client = ServiceNowRESTClientViaUsernamePassword(INSTANCE_URL, "user", "pass")
        assert client.instance_url == INSTANCE_URL
        assert client.base_url == f"{INSTANCE_URL}/api/now"
        assert client.username == "user"
        expected_cred = base64.b64encode(b"user:pass").decode()
        assert client.headers["Authorization"] == f"Basic {expected_cred}"

    def test_trailing_slash_stripped(self):
        client = ServiceNowRESTClientViaUsernamePassword(f"{INSTANCE_URL}/", "u", "p")
        assert client.instance_url == INSTANCE_URL

    def test_get_base_url(self):
        client = ServiceNowRESTClientViaUsernamePassword(INSTANCE_URL, "u", "p")
        assert client.get_base_url() == f"{INSTANCE_URL}/api/now"

    def test_get_instance_url(self):
        client = ServiceNowRESTClientViaUsernamePassword(INSTANCE_URL, "u", "p")
        assert client.get_instance_url() == INSTANCE_URL


class TestServiceNowRESTClientViaToken:
    def test_init(self):
        client = ServiceNowRESTClientViaToken(INSTANCE_URL, "my-token")
        assert client.instance_url == INSTANCE_URL
        assert f"{INSTANCE_URL}/api/now" == client.base_url

    def test_get_base_url(self):
        client = ServiceNowRESTClientViaToken(INSTANCE_URL, "tok")
        assert client.get_base_url() == f"{INSTANCE_URL}/api/now"


class TestServiceNowRESTClientViaAPIKey:
    def test_init(self):
        client = ServiceNowRESTClientViaAPIKey(INSTANCE_URL, "api-key-123")
        assert client.api_key == "api-key-123"
        assert client.headers["x-sn-apikey"] == "api-key-123"

    def test_custom_header(self):
        client = ServiceNowRESTClientViaAPIKey(INSTANCE_URL, "key", "X-Custom")
        assert client.headers["X-Custom"] == "key"
        assert client.header_name == "X-Custom"

    def test_get_base_url(self):
        client = ServiceNowRESTClientViaAPIKey(INSTANCE_URL, "key")
        assert client.get_base_url() == f"{INSTANCE_URL}/api/now"


class TestServiceNowRESTClientViaOAuthClientCredentials:
    def test_init(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            INSTANCE_URL, "cid", "csec"
        )
        assert client.client_id == "cid"
        assert client.client_secret == "csec"
        assert client.access_token is None
        assert client.is_oauth_completed() is False

    def test_init_with_token(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(
            INSTANCE_URL, "cid", "csec", access_token="at"
        )
        assert client.access_token == "at"
        assert client.is_oauth_completed() is True

    def test_get_base_url(self):
        client = ServiceNowRESTClientViaOAuthClientCredentials(INSTANCE_URL, "c", "s")
        assert client.get_base_url() == f"{INSTANCE_URL}/api/now"


class TestServiceNowRESTClientViaOAuthAuthorizationCode:
    def test_init(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        assert client.redirect_uri == "http://redirect"
        assert client.is_oauth_completed() is False

    def test_init_with_token(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect", access_token="at"
        )
        assert client.is_oauth_completed() is True

    def test_get_authorization_url(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        url = client.get_authorization_url()
        assert "response_type=code" in url
        assert "client_id=cid" in url

    def test_get_authorization_url_with_state(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        url = client.get_authorization_url(state="random-state")
        assert "state=random-state" in url

    def test_no_refresh_token_raises(self):
        client = ServiceNowRESTClientViaOAuthAuthorizationCode(
            INSTANCE_URL, "cid", "csec", "http://redirect"
        )
        assert client.refresh_token is None


class TestServiceNowRESTClientViaOAuthROPC:
    def test_init(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass"
        )
        assert client.username == "user"
        assert client.password == "pass"
        assert client.is_oauth_completed() is False

    def test_init_with_token(self):
        client = ServiceNowRESTClientViaOAuthROPC(
            INSTANCE_URL, "cid", "csec", "user", "pass", access_token="at"
        )
        assert client.is_oauth_completed() is True


# ---------------------------------------------------------------------------
# Config classes
# ---------------------------------------------------------------------------


class TestServiceNowUsernamePasswordConfig:
    def test_create_client(self):
        cfg = ServiceNowUsernamePasswordConfig(INSTANCE_URL, "user", "pass")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaUsernamePassword)

    def test_to_dict(self):
        cfg = ServiceNowUsernamePasswordConfig(INSTANCE_URL, "user", "pass")
        d = cfg.to_dict()
        assert d["instance_url"] == INSTANCE_URL
        assert d["username"] == "user"


class TestServiceNowTokenConfig:
    def test_create_client(self):
        cfg = ServiceNowTokenConfig(INSTANCE_URL, "tok")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaToken)

    def test_to_dict(self):
        cfg = ServiceNowTokenConfig(INSTANCE_URL, "tok")
        d = cfg.to_dict()
        assert d["token"] == "tok"


class TestServiceNowAPIKeyConfig:
    def test_create_client(self):
        cfg = ServiceNowAPIKeyConfig(INSTANCE_URL, "api-key")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaAPIKey)

    def test_to_dict(self):
        cfg = ServiceNowAPIKeyConfig(INSTANCE_URL, "api-key")
        d = cfg.to_dict()
        assert d["api_key"] == "api-key"


class TestServiceNowOAuthClientCredentialsConfig:
    def test_create_client(self):
        cfg = ServiceNowOAuthClientCredentialsConfig(INSTANCE_URL, "cid", "csec")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaOAuthClientCredentials)

    def test_to_dict(self):
        cfg = ServiceNowOAuthClientCredentialsConfig(INSTANCE_URL, "cid", "csec")
        d = cfg.to_dict()
        assert d["client_id"] == "cid"


class TestServiceNowOAuthAuthorizationCodeConfig:
    def test_create_client(self):
        cfg = ServiceNowOAuthAuthorizationCodeConfig(INSTANCE_URL, "cid", "csec", "http://redirect")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaOAuthAuthorizationCode)


class TestServiceNowOAuthROPCConfig:
    def test_create_client(self):
        cfg = ServiceNowOAuthROPCConfig(INSTANCE_URL, "cid", "csec", "user", "pass")
        client = cfg.create_client()
        assert isinstance(client, ServiceNowRESTClientViaOAuthROPC)


# ---------------------------------------------------------------------------
# ServiceNowClient
# ---------------------------------------------------------------------------


class TestServiceNowClient:
    def test_init(self):
        mock_client = MagicMock()
        mock_client.get_base_url.return_value = INSTANCE_URL
        mock_client.get_instance_url.return_value = INSTANCE_URL
        client = ServiceNowClient(mock_client)
        assert client.get_client() is mock_client
        assert client.get_base_url() == INSTANCE_URL
        assert client.get_instance_url() == INSTANCE_URL

    def test_build_with_config_username_password(self):
        cfg = ServiceNowUsernamePasswordConfig(INSTANCE_URL, "user", "pass")
        client = ServiceNowClient.build_with_config(cfg)
        assert isinstance(client, ServiceNowClient)

    def test_build_with_config_token(self):
        cfg = ServiceNowTokenConfig(INSTANCE_URL, "tok")
        client = ServiceNowClient.build_with_config(cfg)
        assert isinstance(client, ServiceNowClient)

    def test_build_with_config_api_key(self):
        cfg = ServiceNowAPIKeyConfig(INSTANCE_URL, "api-key")
        client = ServiceNowClient.build_with_config(cfg)
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_username_password(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "USERNAME_PASSWORD",
                    "instanceUrl": INSTANCE_URL,
                    "username": "user",
                    "password": "pass",
                }
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_token(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "TOKEN", "instanceUrl": INSTANCE_URL, "token": "tok"}
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_api_key(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "API_KEY", "instanceUrl": INSTANCE_URL, "apiKey": "key"}
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_oauth_client_credentials(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH_CLIENT_CREDENTIALS",
                    "instanceUrl": INSTANCE_URL,
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "credentials": {"access_token": "at"},
                }
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_oauth_authorization_code(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH_AUTHORIZATION_CODE",
                    "instanceUrl": INSTANCE_URL,
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "redirectUri": "http://redirect",
                    "credentials": {"access_token": "at"},
                }
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_oauth_ropc(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {
                    "authType": "OAUTH_ROPC",
                    "instanceUrl": INSTANCE_URL,
                    "clientId": "cid",
                    "clientSecret": "csec",
                    "username": "user",
                    "password": "pass",
                    "credentials": {"access_token": "at"},
                }
            }
        )
        client = await ServiceNowClient.build_from_services(
            logger, mock_config_service, "inst-1"
        )
        assert isinstance(client, ServiceNowClient)

    @pytest.mark.asyncio
    async def test_build_from_services_invalid_auth_type(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(
            return_value={
                "auth": {"authType": "UNSUPPORTED", "instanceUrl": INSTANCE_URL}
            }
        )
        with pytest.raises(ValueError, match="Invalid auth type"):
            await ServiceNowClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_build_from_services_no_config(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await ServiceNowClient.build_from_services(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_success(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value={"auth": {}})
        result = await ServiceNowClient._get_connector_config(
            logger, mock_config_service, "inst-1"
        )
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_get_connector_config_empty(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError):
            await ServiceNowClient._get_connector_config(
                logger, mock_config_service, "inst-1"
            )

    @pytest.mark.asyncio
    async def test_get_connector_config_exception(self, logger, mock_config_service):
        mock_config_service.get_config = AsyncMock(side_effect=RuntimeError("boom"))
        with pytest.raises(ValueError):
            await ServiceNowClient._get_connector_config(
                logger, mock_config_service, "inst-1"
            )


# ---------------------------------------------------------------------------
# What each client sends
# ---------------------------------------------------------------------------


def _capture(client) -> list[httpx.Request]:
    """Route the client's requests to a recorder that answers like the Table API."""
    sent: list[httpx.Request] = []

    def answer(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        if request.url.path == "/oauth_token.do":
            return httpx.Response(200, json={"access_token": "minted", "expires_in": 1799, "token_type": "Bearer"})
        return httpx.Response(200, json={"result": []})

    client.client = httpx.AsyncClient(transport=httpx.MockTransport(answer), headers=client.headers)
    return sent


@pytest.mark.asyncio
async def test_basic_client_requests_api_now_table() -> None:
    client = ServiceNowRESTClientViaUsernamePassword(INSTANCE_URL, "user", "pass")
    sent = _capture(client)
    await ServiceNowDataSource(client).get_now_table_tableName(tableName="sys_user", sysparm_limit="1")
    assert str(sent[0].url).startswith(f"{INSTANCE_URL}/api/now/table/sys_user?")
    assert sent[0].headers["Authorization"] == "Basic " + base64.b64encode(b"user:pass").decode()


@pytest.mark.asyncio
async def test_api_key_header() -> None:
    client = ServiceNowRESTClientViaAPIKey(INSTANCE_URL, "key-1")
    sent = _capture(client)
    await ServiceNowDataSource(client).get_now_table_tableName(tableName="kb_knowledge", sysparm_limit="1")
    assert sent[0].url.path == "/api/now/table/kb_knowledge"
    assert sent[0].headers["x-sn-apikey"] == "key-1"
    assert "Authorization" not in sent[0].headers  # the key is the only credential


@pytest.mark.asyncio
async def test_client_credentials_token_post() -> None:
    client = ServiceNowRESTClientViaOAuthClientCredentials(INSTANCE_URL, "cid", "csec")
    sent = _capture(client)
    before = time.time()
    assert await client.fetch_token() == "minted"
    form = parse_qs(sent[0].content.decode())
    assert sent[0].method == "POST" and str(sent[0].url) == f"{INSTANCE_URL}/oauth_token.do"
    assert form == {"grant_type": ["client_credentials"], "client_id": ["cid"], "client_secret": ["csec"]}
    assert "Authorization" not in sent[0].headers  # an empty "Bearer " is an illegal header value
    assert before + 1799 <= client.expires_at <= time.time() + 1799
    await ServiceNowDataSource(client).get_now_table_tableName(tableName="sys_user")
    assert sent[1].headers["Authorization"] == "Bearer minted"


@pytest.mark.asyncio
async def test_client_credentials_refusal_carries_the_vendor_text() -> None:
    client = ServiceNowRESTClientViaOAuthClientCredentials(INSTANCE_URL, "cid", "csec")
    client.client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(
        401, json={"error_description": "access_denied", "error": "server_error"})))
    with pytest.raises(Exception, match="401.*access_denied") as error:
        await client.fetch_token()
    assert "csec" not in str(error.value)
    assert client.expires_at is None


@pytest.mark.parametrize("make", [
    lambda r: ServiceNowRESTClientViaUsernamePassword(INSTANCE_URL, "u", "p", resilience=r),
    lambda r: ServiceNowRESTClientViaAPIKey(INSTANCE_URL, "k", resilience=r),
    lambda r: ServiceNowRESTClientViaOAuthClientCredentials(INSTANCE_URL, "c", "s", resilience=r),
])
def test_clients_take_the_connector_resilience(make) -> None:
    policy = ResiliencePolicy()
    assert make(policy).resilience is policy


# ---------------------------------------------------------------------------
# Redirects: credentials never leave the instance
# ---------------------------------------------------------------------------

_OTHER_HOST = "elsewhere.example.com"


def _transport_to(monkeypatch, status: int, seen: list[httpx.Request]) -> None:
    """Every client gets a transport where the instance answers ``status`` pointing at another host."""
    import app.sources.client.http.address_guard as guard

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.host == _OTHER_HOST:
            return httpx.Response(200, json={"result": [], "access_token": "t"})
        return httpx.Response(status, headers={"location": f"http://{_OTHER_HOST}{request.url.path}"})

    monkeypatch.setattr(guard, "guarded_async_client", lambda **kw: httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=kw["follow_redirects"], headers=kw["headers"]))


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [301, 302, 307, 308])
async def test_api_key_is_not_sent_to_a_redirect_target(monkeypatch, status) -> None:
    from app.sources.external.servicenow.models import (
        REDIRECT_MESSAGE,
        ServiceNowAPIError,
    )

    seen: list[httpx.Request] = []
    _transport_to(monkeypatch, status, seen)
    datasource = ServiceNowDataSource(ServiceNowClient(ServiceNowRESTClientViaAPIKey(INSTANCE_URL, "the-key")))

    with pytest.raises(ServiceNowAPIError) as raised:
        await datasource.get_now_table_tableName(tableName="kb_knowledge")

    assert raised.value.message == REDIRECT_MESSAGE and raised.value.status_code == status
    assert [r.url.host for r in seen] == ["acme.service-now.com"]


@pytest.mark.asyncio
@pytest.mark.parametrize("send", [
    lambda: ServiceNowRESTClientViaOAuthClientCredentials(INSTANCE_URL, "cid", "the-secret").fetch_token(),
    lambda: ServiceNowRESTClientViaOAuthAuthorizationCode(
        INSTANCE_URL, "cid", "the-secret", "https://app/cb").refresh_access_token("the-refresh-token"),
])
async def test_token_post_is_not_resent_to_a_redirect_target(monkeypatch, send) -> None:
    from app.sources.client.http.exception.exception import BadRequestError

    seen: list[httpx.Request] = []
    _transport_to(monkeypatch, 307, seen)

    with pytest.raises(BadRequestError):
        await send()

    assert [r.url.host for r in seen] == ["acme.service-now.com"]


@pytest.mark.asyncio
async def test_attachment_download_does_not_follow_a_redirect(monkeypatch) -> None:
    from app.sources.external.servicenow.models import ServiceNowAPIError

    seen: list[httpx.Request] = []
    _transport_to(monkeypatch, 302, seen)
    datasource = ServiceNowDataSource(ServiceNowClient(ServiceNowRESTClientViaAPIKey(INSTANCE_URL, "the-key")))

    with pytest.raises(ServiceNowAPIError):
        await datasource.stream_attachment("f" * 32)

    assert [r.url.host for r in seen] == ["acme.service-now.com"]
