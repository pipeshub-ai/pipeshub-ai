"""Unit tests for Odoo client module."""

import logging
import xmlrpc.client
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import ValidationError

from app.sources.client.odoo.odoo import (
    AuthConfig,
    OdooClient,
    OdooClientBuilder,
    OdooConfig,
    OdooConnectorConfig,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def log():
    lg = logging.getLogger("test_odoo_client")
    lg.setLevel(logging.CRITICAL)
    return lg


@pytest.fixture
def cs():
    return AsyncMock()


def _make_server_proxy_pair(*, uid=42, execute_kw_return=None):
    """Build the (common, models) ServerProxy mock pair connect()/execute_kw() see."""
    common = MagicMock()
    common.authenticate.return_value = uid
    models = MagicMock()
    models.execute_kw.return_value = [] if execute_kw_return is None else execute_kw_return
    return common, models


SERVER_PROXY = "app.sources.client.odoo.odoo.xmlrpc.client.ServerProxy"


# ---------------------------------------------------------------------------
# OdooClient — construction
# ---------------------------------------------------------------------------


class TestOdooClientInit:
    def test_stores_config_no_io(self):
        with patch(SERVER_PROXY) as proxy:
            client = OdooClient(url="https://x.odoo.com/", db="d", username="u", api_key="k")
            proxy.assert_not_called()
        assert client.url == "https://x.odoo.com"  # trailing slash stripped
        assert client.db == "d"
        assert client.username == "u"
        assert client.api_key == "k"
        assert client.timeout == 30
        assert not client.is_connected()


# ---------------------------------------------------------------------------
# OdooClient — connect()
# ---------------------------------------------------------------------------


class TestOdooClientConnection:
    @pytest.mark.asyncio
    async def test_connect_success_caches_uid(self):
        common, models = _make_server_proxy_pair(uid=7)
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="k")
        with patch(SERVER_PROXY, side_effect=[common, models]):
            result = await client.connect()
        assert result is client
        assert client.is_connected()
        common.authenticate.assert_called_once_with("d", "u", "k", {})

    @pytest.mark.asyncio
    async def test_connect_idempotent_only_authenticates_once(self):
        common, models = _make_server_proxy_pair(uid=7)
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="k")
        with patch(SERVER_PROXY, side_effect=[common, models]):
            await client.connect()
            await client.connect()
        common.authenticate.assert_called_once()

    @pytest.mark.asyncio
    async def test_connect_rejected_credentials_raises(self):
        common, models = _make_server_proxy_pair(uid=0)
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="bad")
        with patch(SERVER_PROXY, side_effect=[common, models]):
            with pytest.raises(ConnectionError, match="rejected"):
                await client.connect()
        assert not client.is_connected()

    @pytest.mark.asyncio
    async def test_connect_protocol_error_wrapped(self):
        common = MagicMock()
        common.authenticate.side_effect = xmlrpc.client.ProtocolError("url", 502, "Bad Gateway", {})
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="k")
        with patch(SERVER_PROXY, return_value=common):
            with pytest.raises(ConnectionError, match="Failed to authenticate"):
                await client.connect()

    @pytest.mark.asyncio
    async def test_connect_os_error_wrapped(self):
        common = MagicMock()
        common.authenticate.side_effect = OSError("network unreachable")
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="k")
        with patch(SERVER_PROXY, return_value=common):
            with pytest.raises(ConnectionError):
                await client.connect()


# ---------------------------------------------------------------------------
# OdooClient — execute_kw()
# ---------------------------------------------------------------------------


class TestOdooClientExecuteKw:
    @pytest.mark.asyncio
    async def test_execute_kw_after_connect(self):
        common, models = _make_server_proxy_pair(uid=42, execute_kw_return=[{"id": 1, "name": "Acme"}])
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="k")
        with patch(SERVER_PROXY, side_effect=[common, models]):
            await client.connect()
            rows = await client.execute_kw("res.partner", "search_read", [[]], {"fields": ["name"]})
        assert rows == [{"id": 1, "name": "Acme"}]
        models.execute_kw.assert_called_once_with(
            "d", 42, "k", "res.partner", "search_read", [[]], {"fields": ["name"]}
        )

    @pytest.mark.asyncio
    async def test_execute_kw_auto_connects_if_needed(self):
        common, models = _make_server_proxy_pair(uid=42, execute_kw_return=3)
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="k")
        assert not client.is_connected()
        with patch(SERVER_PROXY, side_effect=[common, models]):
            result = await client.execute_kw("res.partner", "search_count")
        assert result == 3
        assert client.is_connected()

    @pytest.mark.asyncio
    async def test_execute_kw_default_args(self):
        common, models = _make_server_proxy_pair(uid=42)
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="k")
        with patch(SERVER_PROXY, side_effect=[common, models]):
            await client.connect()
            await client.execute_kw("res.partner", "search_count")
        models.execute_kw.assert_called_once_with("d", 42, "k", "res.partner", "search_count", [], {})

    @pytest.mark.asyncio
    async def test_execute_kw_fault_wrapped(self):
        common, models = _make_server_proxy_pair(uid=42)
        models.execute_kw.side_effect = xmlrpc.client.Fault(1, "boom")
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="k")
        with patch(SERVER_PROXY, side_effect=[common, models]):
            await client.connect()
            with pytest.raises(RuntimeError, match="Odoo call failed"):
                await client.execute_kw("res.partner", "search_count")


# ---------------------------------------------------------------------------
# OdooClient — misc
# ---------------------------------------------------------------------------


class TestOdooClientMisc:
    @pytest.mark.asyncio
    async def test_close_resets_state(self):
        common, models = _make_server_proxy_pair(uid=42)
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="k")
        with patch(SERVER_PROXY, side_effect=[common, models]):
            await client.connect()
        assert client.is_connected()
        await client.close()
        assert not client.is_connected()

    def test_get_connection_info_excludes_api_key(self):
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="super-secret")
        info = client.get_connection_info()
        assert info == {"url": "https://x.odoo.com", "db": "d", "username": "u"}
        assert "super-secret" not in info.values()

    @pytest.mark.asyncio
    async def test_async_context_manager(self):
        common, models = _make_server_proxy_pair(uid=42)
        client = OdooClient(url="https://x.odoo.com", db="d", username="u", api_key="k")
        with patch(SERVER_PROXY, side_effect=[common, models]):
            async with client as c:
                assert c is client
                assert client.is_connected()
        assert not client.is_connected()


# ---------------------------------------------------------------------------
# OdooConfig / AuthConfig / OdooConnectorConfig
# ---------------------------------------------------------------------------


class TestOdooConfig:
    def test_create_client(self):
        cfg = OdooConfig(url="https://x.odoo.com", db="d", username="u", apiKey="k")
        client = cfg.create_client()
        assert isinstance(client, OdooClient)
        assert client.db == "d"
        assert client.api_key == "k"

    def test_missing_required_field_raises(self):
        with pytest.raises(ValidationError):
            OdooConfig(db="d", username="u", apiKey="k")  # missing url


class TestAuthConfig:
    def test_accepts_connector_field_names(self):
        auth = AuthConfig.model_validate(
            {"baseUrl": "https://x.odoo.com", "db": "d", "username": "u", "apiKey": "k"}
        )
        assert auth.url == "https://x.odoo.com"
        assert auth.api_key == "k"

    def test_accepts_password_alias_for_api_key(self):
        auth = AuthConfig.model_validate(
            {"url": "https://x.odoo.com", "db": "d", "username": "u", "password": "k"}
        )
        assert auth.api_key == "k"


class TestOdooConnectorConfig:
    def test_default_timeout(self):
        cfg = OdooConnectorConfig.model_validate(
            {"auth": {"baseUrl": "https://x.odoo.com", "db": "d", "username": "u", "apiKey": "k"}}
        )
        assert cfg.timeout == 30


# ---------------------------------------------------------------------------
# OdooClientBuilder
# ---------------------------------------------------------------------------


class TestOdooClientBuilder:
    def test_init_and_get_client(self):
        mock_client = MagicMock(spec=OdooClient)
        builder = OdooClientBuilder(mock_client)
        assert builder.get_client() is mock_client

    def test_build_with_config(self):
        cfg = OdooConfig(url="https://x.odoo.com", db="d", username="u", apiKey="k")
        builder = OdooClientBuilder.build_with_config(cfg)
        assert isinstance(builder, OdooClientBuilder)
        assert isinstance(builder.get_client(), OdooClient)

    @pytest.mark.asyncio
    async def test_build_from_services_success(self, log, cs):
        cs.get_config = AsyncMock(return_value={
            "auth": {"baseUrl": "https://x.odoo.com", "db": "d", "username": "u", "apiKey": "k"},
            "timeout": 20,
        })
        builder = await OdooClientBuilder.build_from_services(log, cs, "inst-1")
        assert isinstance(builder, OdooClientBuilder)
        client = builder.get_client()
        assert client.url == "https://x.odoo.com"
        assert client.timeout == 20

    @pytest.mark.asyncio
    async def test_build_from_services_no_config_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Odoo"):
            await OdooClientBuilder.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_not_dict_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value="nope")
        with pytest.raises(ValueError):
            await OdooClientBuilder.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_validation_error(self, log, cs):
        cs.get_config = AsyncMock(return_value={"auth": {"db": "d"}})  # missing url/username/apiKey
        with pytest.raises(ValueError, match="Invalid Odoo"):
            await OdooClientBuilder.build_from_services(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_build_from_services_generic_exception_reraises(self, log, cs):
        cs.get_config = AsyncMock(side_effect=RuntimeError("etcd down"))
        with pytest.raises(ValueError, match="Failed to get Odoo"):
            await OdooClientBuilder.build_from_services(log, cs, "inst-1")


class TestGetConnectorConfig:
    @pytest.mark.asyncio
    async def test_returns_dict(self, log, cs):
        cs.get_config = AsyncMock(return_value={"auth": {}})
        result = await OdooClientBuilder._get_connector_config(log, cs, "inst-1")
        assert result == {"auth": {}}

    @pytest.mark.asyncio
    async def test_empty_raises(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError, match="Failed to get Odoo"):
            await OdooClientBuilder._get_connector_config(log, cs, "inst-1")

    @pytest.mark.asyncio
    async def test_instance_id_in_message(self, log, cs):
        cs.get_config = AsyncMock(return_value=None)
        with pytest.raises(ValueError) as exc_info:
            await OdooClientBuilder._get_connector_config(log, cs, "inst-abc")
        assert "inst-abc" in str(exc_info.value)
