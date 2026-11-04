"""ServiceNow signs in four ways: OAuth authorization code (a linked OAuth app), basic auth, a REST
API key, and the client-credentials grant (AuthType.CUSTOM, so the platform's token refresh and
authorize route leave it alone). Every way checks the instance URL and runs the same connection
test; a 401 stops a basic or API-key sync at once and makes client credentials mint one new token.
"""

import json
import logging
import time
from unittest.mock import AsyncMock, MagicMock

import pytest
from servicenow_fakes import status_error

from app.connectors.core.base.connector.connector_service import (
    ConnectorInitError,
)
from app.connectors.core.registry.auth_builder import AuthType
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.services.notification.types import NotificationType
from app.sources.client.http.exception.exception import BadRequestError
from app.sources.client.servicenow.servicenow import (
    ServiceNowRESTClientViaAPIKey,
    ServiceNowRESTClientViaOAuthAuthorizationCode,
    ServiceNowRESTClientViaOAuthClientCredentials,
    ServiceNowRESTClientViaUsernamePassword,
)
from app.sources.external.servicenow.models import ServiceNowAuthError

URL = "https://acme.service-now.com"
SECRETS = {"password": "SECRET-PW-1", "apiKey": "SECRET-KEY-1", "clientSecret": "SECRET-CS-1"}
AUTH = {
    AuthType.BASIC_AUTH: {"instanceUrl": URL, "username": "integration", "password": SECRETS["password"]},
    AuthType.API_TOKEN: {"instanceUrl": URL, "apiKey": SECRETS["apiKey"]},
    AuthType.CUSTOM: {"instanceUrl": URL, "clientId": "cid", "clientSecret": SECRETS["clientSecret"]},
}
CLIENT = {
    AuthType.BASIC_AUTH: ServiceNowRESTClientViaUsernamePassword,
    AuthType.API_TOKEN: ServiceNowRESTClientViaAPIKey,
    AuthType.CUSTOM: ServiceNowRESTClientViaOAuthClientCredentials,
}


@pytest.fixture()
def minted(monkeypatch) -> list[str]:
    """fetch_token answers like ServiceNow: a token valid for 1,800 s. Lists the instance URLs asked."""
    asked: list[str] = []

    async def fetch(self) -> str:
        asked.append(self.instance_url)
        self.access_token, self.expires_at = "cc-token", time.time() + 1800
        self.headers["Authorization"] = "Bearer cc-token"
        return "cc-token"

    monkeypatch.setattr(ServiceNowRESTClientViaOAuthClientCredentials, "fetch_token", fetch)
    return asked


def _saved(sc, auth_type: str, **changes: str) -> None:
    sc.config_service.get_config = AsyncMock(return_value={"auth": {"authType": auth_type, **AUTH[auth_type], **changes}})
    sc._connect = AsyncMock(return_value=True)  # the connection test has its own tests (test_notices_and_access_gating.py)
    sc._fetch_oauth_config_by_id = AsyncMock()


def _notices(sc) -> list[dict]:
    sent: list[dict] = []

    async def publish(**kwargs) -> bool:
        sent.append(kwargs)
        return True

    sc._notification_service = MagicMock(publish_notification=AsyncMock(side_effect=publish))
    sc.connector_instance_name = "IT Knowledge"
    return sent


# --- registry -------------------------------------------------------------------------------------


def test_registry_offers_oauth_first_then_basic_api_key_and_client_credentials() -> None:
    meta = ServiceNowConnector._connector_metadata
    assert meta["supportedAuthTypes"] == ["OAUTH", "BASIC_AUTH", "API_TOKEN", "CUSTOM"]
    schemas = meta["config"]["auth"]["schemas"]
    fields = {t: [(f["name"], f.get("isSecret", f.get("is_secret"))) for f in schemas[t]["fields"]] for t in schemas}
    assert [n for n, _ in fields["BASIC_AUTH"]] == ["instanceUrl", "username", "password"]
    assert [n for n, _ in fields["API_TOKEN"]] == ["instanceUrl", "apiKey"]
    assert [n for n, _ in fields["CUSTOM"]] == ["instanceUrl", "clientId", "clientSecret"]
    secret = {n for t in ("BASIC_AUTH", "API_TOKEN", "CUSTOM") for n, s in fields[t] if s}
    assert secret == {"password", "apiKey", "clientSecret"}
    # Only OAuth has an OAuth app, so only OAuth goes to the refresh service and the authorize route
    assert set(meta["config"]["auth"].get("oauthConfigs", {})) <= {"OAUTH"}


# --- init builds the client for the saved type ----------------------------------------------------


@pytest.mark.asyncio
async def test_basic_auth_init_builds_basic_client(servicenow_connector) -> None:
    sc = servicenow_connector
    _saved(sc, AuthType.BASIC_AUTH)
    assert await sc.init() is True
    client = sc.servicenow_client
    assert isinstance(client, ServiceNowRESTClientViaUsernamePassword)
    assert client.get_base_url() == f"{URL}/api/now"
    assert client.headers["Authorization"].startswith("Basic ")
    assert client.resilience is sc.resilience
    sc._fetch_oauth_config_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_api_key_init(servicenow_connector) -> None:
    sc = servicenow_connector
    _saved(sc, AuthType.API_TOKEN)
    assert await sc.init() is True
    client = sc.servicenow_client
    assert isinstance(client, ServiceNowRESTClientViaAPIKey)
    assert client.headers["x-sn-apikey"] == SECRETS["apiKey"]
    assert client.get_base_url() == f"{URL}/api/now"
    assert client.resilience is sc.resilience


@pytest.mark.asyncio
async def test_client_credentials_init_mints_a_token_and_writes_nothing(servicenow_connector, minted) -> None:
    sc = servicenow_connector
    _saved(sc, AuthType.CUSTOM)
    assert await sc.init() is True
    client = sc.servicenow_client
    assert isinstance(client, ServiceNowRESTClientViaOAuthClientCredentials)
    assert client.headers["Authorization"] == "Bearer cc-token"
    assert client.resilience is sc.resilience
    assert minted == [URL]
    sc.config_service.set_config.assert_not_called()  # the token is never stored


@pytest.mark.asyncio
async def test_client_credentials_refused_names_the_vendor_reason(servicenow_connector, monkeypatch) -> None:
    sc = servicenow_connector
    _saved(sc, AuthType.CUSTOM)
    monkeypatch.setattr(ServiceNowRESTClientViaOAuthClientCredentials, "fetch_token", AsyncMock(side_effect=BadRequestError(
        'Token request failed with status 401: {"error_description":"access_denied","error":"server_error"}')))
    close = AsyncMock()
    monkeypatch.setattr(ServiceNowRESTClientViaOAuthClientCredentials, "close", close)
    with pytest.raises(ConnectorInitError) as error:
        await sc.init()
    assert "access_denied" in str(error.value) and "OAuth application user" in str(error.value)
    assert SECRETS["clientSecret"] not in str(error.value)
    close.assert_awaited_once()
    sc._connect.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_credentials_answer_that_is_not_json_names_the_host(servicenow_connector, monkeypatch) -> None:
    # A hibernating instance answers the token request with an HTML page.
    sc = servicenow_connector
    _saved(sc, AuthType.CUSTOM)
    monkeypatch.setattr(ServiceNowRESTClientViaOAuthClientCredentials, "fetch_token", AsyncMock(
        side_effect=json.JSONDecodeError("Expecting value", "<html>", 0)))
    close = AsyncMock()
    monkeypatch.setattr(ServiceNowRESTClientViaOAuthClientCredentials, "close", close)
    with pytest.raises(ConnectorInitError, match=r"Could not get a token from ServiceNow at acme\.service-now\.com"):
        await sc.init()
    close.assert_awaited_once()
    sc._connect.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", list(AUTH))
async def test_every_type_checks_the_instance_url(servicenow_connector, auth_type, minted) -> None:
    sc = servicenow_connector
    _saved(sc, auth_type, instanceUrl="http://acme.service-now.com")
    with pytest.raises(ConnectorInitError, match="must use https"):
        await sc.init()
    assert sc.servicenow_client is None
    assert minted == []  # no secret was sent to the plain-http address


@pytest.mark.asyncio
@pytest.mark.parametrize(("auth_type", "field"), [
    (AuthType.BASIC_AUTH, "password"), (AuthType.API_TOKEN, "apiKey"), (AuthType.CUSTOM, "clientSecret")])
async def test_a_missing_secret_is_named(servicenow_connector, auth_type, field) -> None:
    sc = servicenow_connector
    _saved(sc, auth_type, **{field: ""})
    with pytest.raises(ConnectorInitError, match="Authenticate tab"):
        await sc.init()


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", list(AUTH))
async def test_init_logs_no_secret(servicenow_connector, auth_type, minted, caplog) -> None:
    sc = servicenow_connector
    sc.logger = logging.getLogger("servicenow-batch-h")
    caplog.set_level(logging.DEBUG)
    _saved(sc, auth_type)
    await sc.init()
    assert not any(secret in caplog.text for secret in SECRETS.values())


@pytest.mark.asyncio
async def test_oauth_init_still_reads_the_linked_app_with_auth_config(servicenow_connector) -> None:
    # A subclass may override _fetch_oauth_config_by_id; it is called with auth_config=.
    sc = servicenow_connector
    sc._connect = AsyncMock(return_value=True)
    sc._fetch_oauth_config_by_id = AsyncMock(return_value={
        "config": {"instanceUrl": URL, "clientId": "id", "clientSecret": "secret"}, "redirectUri": "https://p/cb"})
    assert await sc.init() is True
    assert sc.auth_type == AuthType.OAUTH
    assert isinstance(sc.servicenow_client, ServiceNowRESTClientViaOAuthAuthorizationCode)
    assert sc._fetch_oauth_config_by_id.await_args.kwargs["auth_config"] == {"oauthConfigId": "oauth-sn-1"}


# --- 401 during a sync --------------------------------------------------------------------------


def _client(sc, auth_type: str) -> MagicMock:
    sc.auth_type = auth_type
    sc.servicenow_client = MagicMock(spec=CLIENT[auth_type], expires_at=None, access_token="t1")
    return sc.servicenow_client


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", [AuthType.BASIC_AUTH, AuthType.API_TOKEN])
async def test_basic_and_api_key_401_stops_without_a_retry(servicenow_connector, auth_type) -> None:
    sc = servicenow_connector
    _client(sc, auth_type)
    call = AsyncMock(side_effect=status_error(401, "User is not authenticated"))
    with pytest.raises(ServiceNowAuthError):
        await sc._with_token_retry(call)
    call.assert_awaited_once()  # a retry would only spend the instance's failed-login budget
    sc.config_service.get_config.assert_not_awaited()


@pytest.mark.asyncio
async def test_client_credentials_401_remints_once(servicenow_connector) -> None:
    sc = servicenow_connector
    client = _client(sc, AuthType.CUSTOM)
    client.fetch_token = AsyncMock(return_value="t2")
    call = AsyncMock(side_effect=[status_error(401), "rows"])
    assert await sc._with_token_retry(call) == "rows"
    client.fetch_token.assert_awaited_once()

    client.fetch_token.reset_mock()
    call = AsyncMock(side_effect=[status_error(401), status_error(401)])
    with pytest.raises(ServiceNowAuthError):
        await sc._with_token_retry(call)
    client.fetch_token.assert_awaited_once()


@pytest.mark.asyncio
async def test_client_credentials_reminted_near_expiry(servicenow_connector) -> None:
    sc = servicenow_connector
    client = _client(sc, AuthType.CUSTOM)
    client.fetch_token = AsyncMock(return_value="t2")
    client.expires_at = time.time() + 600
    await sc._get_fresh_datasource()
    client.fetch_token.assert_not_awaited()
    client.expires_at = time.time() + 30
    await sc._get_fresh_datasource()
    client.fetch_token.assert_awaited_once()
    sc.config_service.get_config.assert_not_awaited()  # no token in config to read


@pytest.mark.asyncio
@pytest.mark.parametrize("auth_type", [AuthType.BASIC_AUTH, AuthType.API_TOKEN, AuthType.CUSTOM])
async def test_401_stops_and_notifies_with_the_credentials_fix(servicenow_connector, auth_type) -> None:
    sc = servicenow_connector
    sent = _notices(sc)
    sc.auth_type = auth_type
    sc._run_sync = AsyncMock(side_effect=ServiceNowAuthError("User is not authenticated"))
    with pytest.raises(ServiceNowAuthError):
        await sc.run_sync()
    assert len(sent) == 1 and sent[0]["type"] == NotificationType.CONNECTOR_AUTH_ERROR
    assert "Check the credentials on the connector's Authenticate tab." in sent[0]["message"]
    assert "Authorize tab" not in sent[0]["message"] and "token refresh" not in sent[0]["message"]
    assert sc.last_action_items[0].title == "Check the ServiceNow credentials"


@pytest.mark.asyncio
async def test_connection_test_401_names_the_credentials_fix(servicenow_connector) -> None:
    sc = servicenow_connector
    sc.auth_type, sc.instance_url = AuthType.BASIC_AUTH, URL
    sc._with_token_retry = AsyncMock(side_effect=ServiceNowAuthError("User is not authenticated"))
    with pytest.raises(ConnectorInitError) as error:
        await sc.test_connection_and_access()
    assert str(error.value) == (
        "ServiceNow at acme.service-now.com refused the connector's sign-in (HTTP 401: User is not authenticated). "
        "Check the credentials on the connector's Authenticate tab."
    )


def test_auth_config_problems_checks_the_url_of_every_type() -> None:
    for auth_type, auth in AUTH.items():
        assert ServiceNowConnector.auth_config_problems({"authType": auth_type, **auth}) == []
        assert ServiceNowConnector.auth_config_problems(
            {"authType": auth_type, **auth, "instanceUrl": "https://acme.service-now.com/path"})
