"""Tests for app.sources.client.zoom.zoom — Zoom client models and builders."""

import base64
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.sources.client.zoom.zoom import (
    ZoomAuthConfig,
    ZoomAuthType,
    ZoomClient,
    ZoomConnectorConfig,
    ZoomCredentialsConfig,
    ZoomOAuthConfig,
    ZoomRESTClientViaOAuth,
    ZoomRESTClientViaServerToServer,
    ZoomRESTClientViaToken,
    ZoomResponse,
    ZoomServerToServerConfig,
    ZoomSharedOAuthConfigEntry,
    ZoomSharedOAuthWrapper,
    ZoomTokenConfig,
)


# ---------------------------------------------------------------------------
# ZoomAuthType
# ---------------------------------------------------------------------------

class TestZoomAuthType:
    def test_values(self):
        assert ZoomAuthType.SERVER_TO_SERVER == "SERVER_TO_SERVER"
        assert ZoomAuthType.OAUTH == "OAUTH"
        assert ZoomAuthType.TOKEN == "TOKEN"


# ---------------------------------------------------------------------------
# ZoomResponse
# ---------------------------------------------------------------------------

class TestZoomResponse:
    def test_success_dict(self):
        r = ZoomResponse(success=True, data={"meetings": []})
        assert r.success is True
        assert r.data == {"meetings": []}

    def test_error_response(self):
        r = ZoomResponse(success=False, error="unauthorized")
        assert r.success is False
        assert r.error == "unauthorized"

    def test_to_dict_json_data(self):
        r = ZoomResponse(success=True, data={"key": "val"})
        d = r.to_dict()
        assert d["success"] is True
        assert d["data"] == {"key": "val"}

    def test_to_dict_binary_data(self):
        binary = b"hello"
        r = ZoomResponse(success=True, data=binary)
        d = r.to_dict()
        assert d["data"] == base64.b64encode(binary).decode("utf-8")

    def test_to_json_string_data(self):
        r = ZoomResponse(success=True, data={"key": "val"})
        j = r.to_json()
        parsed = json.loads(j)
        assert parsed["success"] is True

    def test_to_json_binary_data(self):
        binary = b"binary content"
        r = ZoomResponse(success=True, data=binary)
        j = r.to_json()
        parsed = json.loads(j)
        assert parsed["data"] == base64.b64encode(binary).decode("utf-8")

    def test_list_data(self):
        r = ZoomResponse(success=True, data=[{"id": 1}])
        assert r.data == [{"id": 1}]

    def test_none_data(self):
        r = ZoomResponse(success=True)
        assert r.data is None

    def test_with_message(self):
        r = ZoomResponse(success=True, message="All good")
        assert r.message == "All good"

    def test_exclude_none(self):
        r = ZoomResponse(success=True)
        d = r.to_dict()
        assert "error" not in d
        assert "data" not in d


# ---------------------------------------------------------------------------
# Config models
# ---------------------------------------------------------------------------

class TestZoomServerToServerConfig:
    def test_valid(self):
        cfg = ZoomServerToServerConfig(
            account_id="acc1", client_id="cid1", client_secret="cs1"
        )
        assert cfg.account_id == "acc1"
        assert cfg.base_url == "https://api.zoom.us/v2"

    def test_create_client(self):
        cfg = ZoomServerToServerConfig(
            account_id="acc1", client_id="cid1", client_secret="cs1"
        )
        client = cfg.create_client()
        assert isinstance(client, ZoomRESTClientViaServerToServer)

    def test_custom_base_url(self):
        cfg = ZoomServerToServerConfig(
            account_id="acc1", client_id="cid1", client_secret="cs1",
            base_url="https://custom.zoom.us"
        )
        assert cfg.base_url == "https://custom.zoom.us"


class TestZoomOAuthConfig:
    def test_valid(self):
        cfg = ZoomOAuthConfig(access_token="tok1")
        assert cfg.access_token == "tok1"

    def test_create_client(self):
        cfg = ZoomOAuthConfig(access_token="tok1", client_id="cid", client_secret="cs")
        client = cfg.create_client()
        assert isinstance(client, ZoomRESTClientViaOAuth)

    def test_optional_fields(self):
        cfg = ZoomOAuthConfig(access_token="tok1")
        assert cfg.client_id is None
        assert cfg.redirect_uri is None


class TestZoomTokenConfig:
    def test_valid(self):
        cfg = ZoomTokenConfig(token="my_token")
        assert cfg.token == "my_token"

    def test_create_client(self):
        cfg = ZoomTokenConfig(token="my_token")
        client = cfg.create_client()
        assert isinstance(client, ZoomRESTClientViaToken)


# ---------------------------------------------------------------------------
# REST client classes
# ---------------------------------------------------------------------------

class TestZoomRESTClientViaServerToServer:
    def test_init(self):
        client = ZoomRESTClientViaServerToServer("acc", "cid", "cs")
        assert client.base_url == "https://api.zoom.us/v2"
        assert client.account_id == "acc"
        assert client._authenticated is False

    def test_get_base_url(self):
        client = ZoomRESTClientViaServerToServer("acc", "cid", "cs")
        assert client.get_base_url() == "https://api.zoom.us/v2"

    def test_custom_base_url(self):
        client = ZoomRESTClientViaServerToServer("acc", "cid", "cs", "https://custom.api")
        assert client.get_base_url() == "https://custom.api"


class TestZoomRESTClientViaOAuth:
    def test_init(self):
        client = ZoomRESTClientViaOAuth("tok1")
        assert client.access_token == "tok1"
        assert client.base_url == "https://api.zoom.us/v2"

    def test_get_base_url(self):
        client = ZoomRESTClientViaOAuth("tok1", base_url="https://custom.api")
        assert client.get_base_url() == "https://custom.api"

    def test_optional_credentials(self):
        client = ZoomRESTClientViaOAuth(
            "tok1", client_id="cid", client_secret="cs", redirect_uri="http://redir"
        )
        assert client.client_id == "cid"
        assert client.redirect_uri == "http://redir"


class TestZoomRESTClientViaToken:
    def test_init(self):
        client = ZoomRESTClientViaToken("my_token")
        assert client.base_url == "https://api.zoom.us/v2"

    def test_get_base_url(self):
        client = ZoomRESTClientViaToken("tok", "https://custom.api")
        assert client.get_base_url() == "https://custom.api"


# ---------------------------------------------------------------------------
# Connector config models
# ---------------------------------------------------------------------------

class TestZoomAuthConfig:
    def test_defaults(self):
        cfg = ZoomAuthConfig()
        assert cfg.authType == ZoomAuthType.SERVER_TO_SERVER
        assert cfg.accountId is None
        assert cfg.clientId is None
        assert cfg.oauthConfigId is None

    def test_all_fields(self):
        cfg = ZoomAuthConfig(
            authType=ZoomAuthType.OAUTH,
            accountId="acc1",
            clientId="cid1",
            clientSecret="cs1",
            redirectUri="http://redir",
            token="tok",
            oauthConfigId="oid1",
        )
        assert cfg.authType == ZoomAuthType.OAUTH
        assert cfg.accountId == "acc1"


class TestZoomCredentialsConfig:
    def test_defaults(self):
        cfg = ZoomCredentialsConfig()
        assert cfg.access_token is None
        assert cfg.refresh_token is None


class TestZoomConnectorConfig:
    def test_defaults(self):
        cfg = ZoomConnectorConfig()
        assert cfg.auth.authType == ZoomAuthType.SERVER_TO_SERVER
        assert cfg.credentials.access_token is None

    def test_with_values(self):
        cfg = ZoomConnectorConfig(
            auth=ZoomAuthConfig(authType=ZoomAuthType.TOKEN, token="tok"),
            credentials=ZoomCredentialsConfig(access_token="at"),
        )
        assert cfg.auth.token == "tok"
        assert cfg.credentials.access_token == "at"


class TestZoomSharedOAuthConfigEntry:
    def test_resolved_methods_camel(self):
        entry = ZoomSharedOAuthConfigEntry(
            accountId="acc", clientId="cid", clientSecret="cs", redirectUri="redir"
        )
        assert entry.resolved_account_id() == "acc"
        assert entry.resolved_client_id() == "cid"
        assert entry.resolved_client_secret() == "cs"
        assert entry.resolved_redirect_uri() == "redir"

    def test_resolved_methods_snake(self):
        entry = ZoomSharedOAuthConfigEntry(
            account_id="acc2", client_id="cid2", client_secret="cs2", redirect_uri="redir2"
        )
        assert entry.resolved_account_id() == "acc2"
        assert entry.resolved_client_id() == "cid2"
        assert entry.resolved_client_secret() == "cs2"
        assert entry.resolved_redirect_uri() == "redir2"

    def test_resolved_methods_fallback(self):
        entry = ZoomSharedOAuthConfigEntry()
        assert entry.resolved_account_id("fb") == "fb"
        assert entry.resolved_client_id("fb") == "fb"
        assert entry.resolved_client_secret("fb") == "fb"
        assert entry.resolved_redirect_uri("fb") == "fb"

    def test_camel_takes_priority(self):
        entry = ZoomSharedOAuthConfigEntry(
            accountId="camel", account_id="snake"
        )
        assert entry.resolved_account_id() == "camel"


class TestZoomSharedOAuthWrapper:
    def test_defaults(self):
        wrapper = ZoomSharedOAuthWrapper()
        assert wrapper.entry_id is None
        assert wrapper.config is not None

    def test_with_config(self):
        wrapper = ZoomSharedOAuthWrapper(
            config=ZoomSharedOAuthConfigEntry(clientId="cid1")
        )
        assert wrapper.config.resolved_client_id() == "cid1"


# ---------------------------------------------------------------------------
# ZoomClient
# ---------------------------------------------------------------------------

class TestZoomClient:
    def test_get_client(self):
        mock_rest = MagicMock(spec=ZoomRESTClientViaToken)
        client = ZoomClient(mock_rest)
        assert client.get_client() is mock_rest

    def test_get_base_url(self):
        mock_rest = MagicMock()
        mock_rest.get_base_url.return_value = "https://api.zoom.us/v2"
        client = ZoomClient(mock_rest)
        assert client.get_base_url() == "https://api.zoom.us/v2"

    def test_build_with_s2s_config(self):
        cfg = ZoomServerToServerConfig(
            account_id="acc", client_id="cid", client_secret="cs"
        )
        client = ZoomClient.build_with_config(cfg)
        assert isinstance(client.get_client(), ZoomRESTClientViaServerToServer)

    def test_build_with_oauth_config(self):
        cfg = ZoomOAuthConfig(access_token="tok")
        client = ZoomClient.build_with_config(cfg)
        assert isinstance(client.get_client(), ZoomRESTClientViaOAuth)

    def test_build_with_token_config(self):
        cfg = ZoomTokenConfig(token="tok")
        client = ZoomClient.build_with_config(cfg)
        assert isinstance(client.get_client(), ZoomRESTClientViaToken)


class TestZoomRESTClientViaServerToServerAuth:
    @pytest.mark.asyncio
    async def test_ensure_authenticated_skips_if_already(self):
        client = ZoomRESTClientViaServerToServer("acc", "cid", "cs")
        client._authenticated = True
        await client.ensure_authenticated()
        # No request made, just returns

    @pytest.mark.asyncio
    async def test_ensure_authenticated_success(self):
        client = ZoomRESTClientViaServerToServer("acc", "cid", "cs")
        mock_response = MagicMock()
        mock_response.json.return_value = {"access_token": "new_token"}
        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            await client.ensure_authenticated()
        assert client._authenticated is True
        assert "Bearer new_token" in client.headers["Authorization"]

    @pytest.mark.asyncio
    async def test_ensure_authenticated_no_token_raises(self):
        client = ZoomRESTClientViaServerToServer("acc", "cid", "cs")
        mock_response = MagicMock()
        mock_response.json.return_value = {"error": "invalid_credentials"}
        with patch.object(client, "execute", new_callable=AsyncMock, return_value=mock_response):
            with pytest.raises(ValueError, match="Failed to obtain access token"):
                await client.ensure_authenticated()
