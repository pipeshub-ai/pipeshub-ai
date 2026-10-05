"""Which Salesforce host sign-in, token exchange and refresh go to.

A sandbox signs in on test.salesforce.com and a My Domain org may sign in on its own
host. Production stays on login.salesforce.com, which is also what every connection saved
before the Login URL setting existed keeps using. The token request carries the client
secret from the server, so only hosts on salesforce.com are accepted.
"""

import json
import logging
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.config.constants.http_status_code import HttpStatusCode
from app.connectors.api.router import (
    _build_oauth_flow_config,
    _create_or_update_oauth_config,
    _validate_admin_oauth_config_before_creation,
    create_oauth_config,
    update_oauth_config,
)
from app.connectors.core.base.token_service.oauth_service import OAuthToken
from app.connectors.core.base.token_service.token_refresh_service import (
    TokenRefreshService,
)
from app.connectors.sources.salesforce.common.auth_fields import (
    SALESFORCE_LOGIN_URL_DESCRIPTION,
    SALESFORCE_LOGIN_URL_PLACEHOLDER,
)
from app.connectors.sources.salesforce.connector import SalesforceConnector
from app.utils.oauth_config import (
    SALESFORCE_LOGIN_URL_ERROR,
    get_oauth_config,
    normalize_salesforce_login_url,
)

_ROUTER = "app.connectors.api.router"
PROD_AUTHORIZE = "https://login.salesforce.com/services/oauth2/authorize"
PROD_TOKEN = "https://login.salesforce.com/services/oauth2/token"
SANDBOX = "https://test.salesforce.com"
MY_DOMAIN = "https://acme.my.salesforce.com"
SANDBOX_MY_DOMAIN = "https://acme--uat.sandbox.my.salesforce.com"

REFUSED_LOGIN_URLS = [
    "https://evil.example",
    "http://test.salesforce.com",
    "test.salesforce.com",
    "https://salesforce.com",
    "https://evilsalesforce.com",
    "https://test.salesforce.com.evil.example",
    "https://evil.example#.salesforce.com",
    "https://evil.example?.salesforce.com",
    "https://evil.example\\.salesforce.com",
    "https://user@test.salesforce.com",
    "https://evil.example@test.salesforce.com",
    "https://test.salesforce.com:8443",
    "https://test.salesforce.com/services/oauth2/token",
    "https://test.salesforce.com.",
    "https://-bad.my.salesforce.com",
    "https://169.254.169.254",
    "https://localhost",
    "javascript:alert(1)",
]


def _flow(login_url: object = None, *, with_key: bool = True) -> dict[str, Any]:
    flow: dict[str, Any] = {
        "clientId": "client",
        "clientSecret": "secret",
        "redirectUri": "https://pipeshub.example/connectors/oauth/callback/Salesforce",
        "authorizeUrl": PROD_AUTHORIZE,
        "tokenUrl": PROD_TOKEN,
        "scopes": ["api", "refresh_token", "offline_access"],
        "instance_url": MY_DOMAIN,
    }
    if with_key:
        flow["loginUrl"] = login_url
    return flow


def _shared_app(login_url: str | None) -> dict[str, Any]:
    config: dict[str, Any] = {"clientId": "client", "clientSecret": "secret", "instance_url": MY_DOMAIN}
    if login_url is not None:
        config["loginUrl"] = login_url
    return {
        "_id": "app-1",
        "orgId": "org-1",
        "oauthInstanceName": "Salesforce app",
        "authorizeUrl": PROD_AUTHORIZE,
        "tokenUrl": PROD_TOKEN,
        "redirectUri": "https://pipeshub.example/connectors/oauth/callback/Salesforce",
        "scopes": {"team_sync": ["api", "refresh_token", "offline_access"]},
        "config": config,
    }


class TestProductionStaysTheDefault:
    def test_a_config_saved_before_the_setting_existed_signs_in_on_login_salesforce_com(self) -> None:
        oauth = get_oauth_config(_flow(with_key=False))
        assert oauth.authorize_url == PROD_AUTHORIZE
        assert oauth.token_url == PROD_TOKEN

    @pytest.mark.parametrize("blank", [None, "", "   "])
    def test_a_blank_login_url_means_production(self, blank: str | None) -> None:
        oauth = get_oauth_config(_flow(blank))
        assert oauth.authorize_url == PROD_AUTHORIZE
        assert oauth.token_url == PROD_TOKEN

    def test_login_salesforce_com_itself_is_accepted(self) -> None:
        oauth = get_oauth_config(_flow("https://login.salesforce.com/"))
        assert oauth.authorize_url == PROD_AUTHORIZE
        assert oauth.token_url == PROD_TOKEN


class TestSandboxAndMyDomain:
    def test_a_sandbox_signs_in_and_exchanges_the_code_on_test_salesforce_com(self) -> None:
        oauth = get_oauth_config(_flow(SANDBOX))
        assert oauth.authorize_url == "https://test.salesforce.com/services/oauth2/authorize"
        assert oauth.token_url == "https://test.salesforce.com/services/oauth2/token"

    @pytest.mark.parametrize("login_url", [MY_DOMAIN, SANDBOX_MY_DOMAIN])
    def test_a_my_domain_login_url_is_used_for_both_endpoints(self, login_url: str) -> None:
        oauth = get_oauth_config(_flow(login_url))
        assert oauth.authorize_url == f"{login_url}/services/oauth2/authorize"
        assert oauth.token_url == f"{login_url}/services/oauth2/token"

    def test_case_spaces_a_trailing_slash_and_port_443_are_tidied(self) -> None:
        assert normalize_salesforce_login_url("  HTTPS://Test.Salesforce.com:443/ ") == SANDBOX

    def test_a_saved_http_endpoint_moves_to_https(self) -> None:
        """The token request carries the client secret, so it must never go over http."""
        flow = {
            **_flow(SANDBOX),
            "authorizeUrl": "http://login.salesforce.com/services/oauth2/authorize",
            "tokenUrl": "http://login.salesforce.com/services/oauth2/token",
        }
        oauth = get_oauth_config(flow)
        assert oauth.authorize_url == "https://test.salesforce.com/services/oauth2/authorize"
        assert oauth.token_url == "https://test.salesforce.com/services/oauth2/token"


class TestOnlySalesforceHostsAreAccepted:
    @pytest.mark.parametrize("login_url", REFUSED_LOGIN_URLS)
    def test_a_host_off_salesforce_com_is_refused_before_any_request(self, login_url: str) -> None:
        with pytest.raises(ValueError, match="salesforce.com"):
            get_oauth_config(_flow(login_url))

    def test_a_non_string_value_is_refused(self) -> None:
        with pytest.raises(ValueError):
            normalize_salesforce_login_url(["https://test.salesforce.com"])

    def test_other_connectors_do_not_read_the_setting(self) -> None:
        flow = {
            "clientId": "c",
            "clientSecret": "s",
            "authorizeUrl": "https://gitlab.com/oauth/authorize",
            "tokenUrl": "https://gitlab.com/oauth/token",
            "loginUrl": "https://evil.example",
        }
        oauth = get_oauth_config(flow)
        assert oauth.token_url == "https://gitlab.com/oauth/token"


class TestSignInRoute:
    """The authorize and callback routes both build their URLs this way."""

    async def _urls(self, login_url: str | None) -> tuple[str, str]:
        with patch(
            f"{_ROUTER}.resolve_shared_oauth_config_for_flow",
            AsyncMock(return_value=_shared_app(login_url)),
        ):
            flow = await _build_oauth_flow_config(
                {"oauthConfigId": "app-1", "connectorScope": "team", "loginUrl": "https://evil.example"},
                "SALESFORCE",
                "org-1",
                AsyncMock(),
                logging.getLogger("test"),
                registry_type="Salesforce",
            )
        oauth = get_oauth_config(flow)
        return oauth.authorize_url, oauth.token_url

    async def test_the_shared_apps_sandbox_setting_is_used_and_the_connectors_own_value_is_not(self) -> None:
        authorize, token = await self._urls(SANDBOX)
        assert authorize == "https://test.salesforce.com/services/oauth2/authorize"
        assert token == "https://test.salesforce.com/services/oauth2/token"

    async def test_an_app_without_the_setting_stays_on_production(self) -> None:
        assert await self._urls(None) == (PROD_AUTHORIZE, PROD_TOKEN)


class TestRefreshGoesToTheHostThatIssuedTheToken:
    async def _refresh_token_url(self, auth: dict[str, Any], shared: dict[str, Any] | None) -> str:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            return_value={"auth": auth, "credentials": {"refresh_token": "rt"}}
        )
        service = TokenRefreshService(config_service, MagicMock(), None)
        service._fetch_shared_oauth_config = AsyncMock(return_value=shared)
        service._persist_refreshed_credentials = AsyncMock()
        provider = MagicMock()
        provider.refresh_access_token = AsyncMock(return_value=OAuthToken(access_token="at", refresh_token="rt2"))
        provider.close = AsyncMock()
        with patch(
            "app.connectors.core.base.token_service.oauth_service.OAuthProvider",
            return_value=provider,
        ) as provider_cls:
            await service._perform_token_refresh("conn-1", "SALESFORCE", "rt")
        return provider_cls.call_args.kwargs["config"].token_url

    async def test_a_sandbox_refreshes_on_test_salesforce_com(self) -> None:
        url = await self._refresh_token_url(
            {"oauthConfigId": "app-1", "connectorScope": "team"}, _shared_app(SANDBOX)
        )
        assert url == "https://test.salesforce.com/services/oauth2/token"

    async def test_an_app_without_the_setting_refreshes_on_production(self) -> None:
        url = await self._refresh_token_url(
            {"oauthConfigId": "app-1", "connectorScope": "team"}, _shared_app(None)
        )
        assert url == PROD_TOKEN

    async def test_a_connector_with_its_own_credentials_refreshes_on_its_login_host(self) -> None:
        auth = {
            "clientId": "client",
            "clientSecret": "secret",
            "authorizeUrl": PROD_AUTHORIZE,
            "tokenUrl": PROD_TOKEN,
            "loginUrl": SANDBOX_MY_DOMAIN,
        }
        url = await self._refresh_token_url(auth, None)
        assert url == f"{SANDBOX_MY_DOMAIN}/services/oauth2/token"


def _admin_request(body: dict[str, Any]) -> MagicMock:
    request = MagicMock()
    request.json = AsyncMock(return_value=body)
    request.app.container.logger = MagicMock(return_value=logging.getLogger("test"))
    return request


_ADMIN = {"user_id": "u1", "org_id": "org-1", "is_admin": True}


class TestSavingRefusesANonSalesforceHost:
    async def test_creating_an_oauth_app(self) -> None:
        config_service = AsyncMock()
        body = {
            "oauthInstanceName": "Sandbox app",
            "config": {"clientId": "c", "clientSecret": "s", "instance_url": MY_DOMAIN, "loginUrl": "https://evil.example"},
        }
        with patch(f"{_ROUTER}._get_user_context", return_value=_ADMIN), \
             patch(f"{_ROUTER}._validate_admin_only"):
            with pytest.raises(HTTPException) as refused:
                await create_oauth_config("Salesforce", _admin_request(body), config_service=config_service)
        assert refused.value.status_code == HttpStatusCode.BAD_REQUEST.value
        assert refused.value.detail == SALESFORCE_LOGIN_URL_ERROR
        config_service.set_config.assert_not_called()

    async def test_editing_an_oauth_app(self) -> None:
        existing = _shared_app(None)
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[existing])
        body = {"config": {"loginUrl": "http://test.salesforce.com"}}
        with patch(f"{_ROUTER}._get_user_context", return_value=_ADMIN), \
             patch(f"{_ROUTER}._validate_admin_only"):
            with pytest.raises(HTTPException) as refused:
                await update_oauth_config("SALESFORCE", "app-1", _admin_request(body), config_service=config_service)
        assert refused.value.status_code == HttpStatusCode.BAD_REQUEST.value
        config_service.set_config.assert_not_called()

    async def test_creating_a_connector_is_refused_before_the_instance_exists(self) -> None:
        with pytest.raises(HTTPException) as refused:
            await _validate_admin_oauth_config_before_creation(
                connector_type="SALESFORCE",
                config={"auth": {"clientId": "c", "clientSecret": "s", "loginUrl": "https://evil.example"}},
                oauth_config_id=None,
                instance_name="Salesforce",
                org_id="org-1",
                config_service=AsyncMock(),
                logger=logging.getLogger("test"),
            )
        assert refused.value.status_code == HttpStatusCode.BAD_REQUEST.value

    async def test_saving_connector_credentials(self) -> None:
        config_service = AsyncMock()
        with pytest.raises(HTTPException) as refused:
            await _create_or_update_oauth_config(
                connector_type="SALESFORCE",
                auth_config={"clientId": "c", "clientSecret": "s", "loginUrl": "https://evil.example"},
                instance_name="Salesforce",
                user_id="u1",
                org_id="org-1",
                is_admin=True,
                config_service=config_service,
                base_url="",
            )
        assert refused.value.status_code == HttpStatusCode.BAD_REQUEST.value
        config_service.set_config.assert_not_called()

    async def test_a_sandbox_login_url_is_saved(self) -> None:
        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value=[])
        config_service.set_config = AsyncMock(return_value=True)
        body = {
            "oauthInstanceName": "Sandbox app",
            "config": {"clientId": "c", "clientSecret": "s", "instance_url": SANDBOX_MY_DOMAIN, "loginUrl": SANDBOX},
        }
        with patch(f"{_ROUTER}._get_user_context", return_value=_ADMIN), \
             patch(f"{_ROUTER}._validate_admin_only"), \
             patch(f"{_ROUTER}._update_oauth_infrastructure_fields", new_callable=AsyncMock):
            result = await create_oauth_config("Salesforce", _admin_request(body), config_service=config_service)
        assert result["success"] is True
        saved = config_service.set_config.call_args.args[1]
        assert saved[-1]["config"]["loginUrl"] == SANDBOX


class TestLoginUrlFormField:
    def _field(self) -> dict[str, Any]:
        fields = SalesforceConnector._connector_metadata["config"]["auth"]["schemas"]["OAUTH"]["fields"]
        return next(f for f in fields if f["name"] == "loginUrl")

    def test_it_is_optional_so_existing_connections_need_no_change(self) -> None:
        assert self._field()["required"] is False

    def test_it_is_a_url_field_that_suggests_production(self) -> None:
        field = self._field()
        assert field["fieldType"] == "URL"
        assert field["placeholder"] == SALESFORCE_LOGIN_URL_PLACEHOLDER
        assert field["description"] == SALESFORCE_LOGIN_URL_DESCRIPTION


_BACKEND_ROOT = Path(__file__).resolve().parents[4]
_STARTUP_ORDER_SCRIPT = """
import asyncio, json
from unittest.mock import AsyncMock
import app.connectors.sources.salesforce.connector  # connectors_main registers connectors first
import app.agents.actions.salesforce.salesforce  # then auto_discover_toolsets() loads this
from app.connectors.api.router import _create_or_update_oauth_config
from app.connectors.core.registry.oauth_config_registry import get_oauth_config_registry

fields = [f.name for f in get_oauth_config_registry().get_config("Salesforce").auth_fields]
service = AsyncMock()
service.get_config = AsyncMock(return_value=[])
service.set_config = AsyncMock(return_value=True)
asyncio.run(_create_or_update_oauth_config(
    connector_type="Salesforce",
    auth_config={"clientId": "c", "clientSecret": "s", "instance_url": "%(instance)s", "loginUrl": "%(login)s"},
    instance_name="Salesforce", user_id="u1", org_id="org-1", is_admin=True,
    config_service=service, base_url="https://pipeshub.example",
))
print(json.dumps({"fields": fields, "saved": service.set_config.call_args.args[1][-1]["config"].get("loginUrl")}))
"""


class TestTheToolsetDoesNotDropTheField:
    """The agent toolset registers an OAuth config with the same name after the connector does."""

    @pytest.mark.timeout(180)
    def test_after_startup_the_registry_lists_it_and_a_create_saves_it(self) -> None:
        script = _STARTUP_ORDER_SCRIPT % {"instance": SANDBOX_MY_DOMAIN, "login": SANDBOX}
        run = subprocess.run(
            [sys.executable, "-c", script],
            cwd=_BACKEND_ROOT,
            capture_output=True,
            text=True,
            timeout=170,
            check=False,
        )
        assert run.returncode == 0, run.stderr[-2000:]
        result = json.loads(run.stdout.strip().splitlines()[-1])
        assert "loginUrl" in result["fields"]
        assert result["saved"] == SANDBOX


class TestAgentToolset:
    def _registry_oauth(self) -> MagicMock:
        oauth = MagicMock()
        oauth.redirect_uri = "toolsets/oauth/callback/salesforce"
        oauth.authorize_url = PROD_AUTHORIZE
        oauth.token_url = PROD_TOKEN
        oauth.additional_params = {}
        oauth.token_access_type = None
        oauth.scope_parameter_name = "scope"
        oauth.token_response_path = None
        oauth.scopes.get_scopes_for_type = MagicMock(return_value=["api"])
        return oauth

    async def test_sign_in_uses_the_apps_sandbox_host(self) -> None:
        from app.api.routes import toolsets

        app_config = {"type": "OAUTH", "clientId": "c", "clientSecret": "s", "loginUrl": SANDBOX}
        with patch.object(toolsets, "_get_oauth_config_from_registry", return_value=self._registry_oauth()):
            flow = await toolsets._build_oauth_config(app_config, "salesforce", MagicMock(), "https://pipeshub.example")
        oauth = get_oauth_config(flow)
        assert oauth.authorize_url == "https://test.salesforce.com/services/oauth2/authorize"
        assert oauth.token_url == "https://test.salesforce.com/services/oauth2/token"

    async def test_refresh_uses_the_apps_sandbox_host(self) -> None:
        from app.api.routes import (
            toolsets,  # noqa: F401  (imported after the router to avoid an import cycle)
        )
        from app.connectors.core.base.token_service.toolset_token_refresh_service import (
            ToolsetTokenRefreshService,
        )

        config_service = AsyncMock()
        config_service.get_config = AsyncMock(return_value={"oauthConfigId": "app-1", "orgId": "org-1", "auth": {}})
        service = ToolsetTokenRefreshService(config_service)
        service._get_toolset_oauth_config_from_registry = MagicMock(return_value=None)
        service._enrich_from_toolset_registry = MagicMock()
        creds = {"clientId": "c", "clientSecret": "s", "authorizeUrl": PROD_AUTHORIZE, "tokenUrl": PROD_TOKEN, "loginUrl": SANDBOX}
        with patch(
            "app.api.routes.toolsets.get_oauth_credentials_for_toolset",
            new_callable=AsyncMock,
            return_value=creds,
        ):
            flow = await service._build_complete_oauth_config("/services/toolsets/inst/user", "salesforce", {"type": "OAUTH"})
        assert get_oauth_config(flow).token_url == "https://test.salesforce.com/services/oauth2/token"

    async def test_saving_the_toolsets_oauth_app_refuses_a_non_salesforce_host(self) -> None:
        from app.api.routes import toolsets

        config_service = AsyncMock()
        with pytest.raises(toolsets.InvalidAuthConfigError) as refused:
            await toolsets._create_or_update_toolset_oauth_config(
                toolset_type="salesforce",
                auth_config={"type": "OAUTH", "clientId": "c", "clientSecret": "s", "loginUrl": "https://evil.example"},
                instance_name="Salesforce",
                user_id="u1",
                org_id="org-1",
                config_service=config_service,
                registry=MagicMock(),
                base_url="https://pipeshub.example",
            )
        assert refused.value.status_code == HttpStatusCode.BAD_REQUEST.value
        config_service.set_config.assert_not_called()
