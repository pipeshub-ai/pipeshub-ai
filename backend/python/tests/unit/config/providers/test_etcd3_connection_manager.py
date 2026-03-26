"""Tests for app.config.providers.etcd.etcd3_connection_manager."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.providers.etcd.etcd3_connection_manager import (
    ConnectionConfig,
    ConnectionState,
    Etcd3ConnectionManager,
)


class TestConnectionConfig:
    def test_defaults(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        assert cfg.hosts == ["localhost"]
        assert cfg.port == 2379
        assert cfg.timeout == 5.0
        assert cfg.ca_cert is None
        assert cfg.cert_key is None
        assert cfg.cert_cert is None

    def test_custom_values(self):
        cfg = ConnectionConfig(
            hosts=["host1", "host2"],
            port=2380,
            timeout=10.0,
            ca_cert="/path/ca",
            cert_key="/path/key",
            cert_cert="/path/cert",
        )
        assert cfg.hosts == ["host1", "host2"]
        assert cfg.port == 2380
        assert cfg.timeout == 10.0
        assert cfg.ca_cert == "/path/ca"


class TestConnectionState:
    def test_states(self):
        assert ConnectionState.DISCONNECTED == "disconnected"
        assert ConnectionState.CONNECTING == "connecting"
        assert ConnectionState.CONNECTED == "connected"
        assert ConnectionState.FAILED == "failed"


class TestEtcd3ConnectionManagerInit:
    def test_initial_state(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        assert mgr.state == ConnectionState.DISCONNECTED
        assert mgr.client is None
        assert mgr.config is cfg

    def test_with_ssl(self):
        cfg = ConnectionConfig(
            hosts=["localhost"], ca_cert="/ca", cert_key="/key", cert_cert="/cert"
        )
        mgr = Etcd3ConnectionManager(cfg)
        assert mgr.config.ca_cert == "/ca"


class TestConnect:
    @pytest.mark.asyncio
    async def test_successful_connect(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        mock_client = MagicMock()
        with patch.object(mgr, "_create_client", return_value=mock_client):
            await mgr.connect()
        assert mgr.state == ConnectionState.CONNECTED
        assert mgr.client is mock_client

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        with patch.object(mgr, "_create_client", side_effect=Exception("connection refused")):
            with pytest.raises(ConnectionError, match="Failed to connect"):
                await mgr.connect()
        assert mgr.state == ConnectionState.FAILED

    @pytest.mark.asyncio
    async def test_skip_if_already_connecting(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        mgr.state = ConnectionState.CONNECTING
        await mgr.connect()
        assert mgr.state == ConnectionState.CONNECTING
        assert mgr.client is None


class TestCreateClient:
    def test_creates_client_without_ssl(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        mock_client = MagicMock()
        mock_client.status.return_value = MagicMock()
        with patch("app.config.providers.etcd.etcd3_connection_manager.etcd3.client", return_value=mock_client):
            result = mgr._create_client()
        assert result is mock_client

    def test_creates_client_with_ssl(self):
        cfg = ConnectionConfig(
            hosts=["localhost"], ca_cert="/ca", cert_key="/key", cert_cert="/cert"
        )
        mgr = Etcd3ConnectionManager(cfg)
        mock_client = MagicMock()
        mock_client.status.return_value = MagicMock()
        with patch("app.config.providers.etcd.etcd3_connection_manager.etcd3.client", return_value=mock_client):
            result = mgr._create_client()
        assert result is mock_client

    def test_create_client_failure(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        with patch(
            "app.config.providers.etcd.etcd3_connection_manager.etcd3.client",
            side_effect=Exception("etcd down"),
        ):
            with pytest.raises(Exception, match="etcd down"):
                mgr._create_client()


class TestReconnect:
    @pytest.mark.asyncio
    async def test_reconnect_success(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        old_client = MagicMock()
        mgr.client = old_client
        mgr.state = ConnectionState.CONNECTED

        new_client = MagicMock()
        with patch.object(mgr, "_create_client", return_value=new_client):
            await mgr.reconnect()

        old_client.close.assert_called_once()
        assert mgr.state == ConnectionState.CONNECTED
        assert mgr.client is new_client

    @pytest.mark.asyncio
    async def test_reconnect_close_error_handled(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        old_client = MagicMock()
        old_client.close.side_effect = Exception("close error")
        mgr.client = old_client
        mgr.state = ConnectionState.CONNECTED

        new_client = MagicMock()
        with patch.object(mgr, "_create_client", return_value=new_client):
            await mgr.reconnect()

        assert mgr.state == ConnectionState.CONNECTED

    @pytest.mark.asyncio
    async def test_reconnect_without_existing_client(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        new_client = MagicMock()
        with patch.object(mgr, "_create_client", return_value=new_client):
            await mgr.reconnect()
        assert mgr.state == ConnectionState.CONNECTED


class TestGetClient:
    @pytest.mark.asyncio
    async def test_returns_connected_client(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        mock_client = MagicMock()
        mgr.client = mock_client
        mgr.state = ConnectionState.CONNECTED
        result = await mgr.get_client()
        assert result is mock_client

    @pytest.mark.asyncio
    async def test_connects_if_disconnected(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        mock_client = MagicMock()
        with patch.object(mgr, "_create_client", return_value=mock_client):
            result = await mgr.get_client()
        assert result is mock_client
        assert mgr.state == ConnectionState.CONNECTED

    @pytest.mark.asyncio
    async def test_raises_if_no_client(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        with patch.object(mgr, "connect", new_callable=AsyncMock):
            mgr.client = None
            with pytest.raises(ConnectionError, match="No ETCD client"):
                await mgr.get_client()


class TestClose:
    @pytest.mark.asyncio
    async def test_close_with_client(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        mock_client = MagicMock()
        mgr.client = mock_client
        mgr.state = ConnectionState.CONNECTED
        await mgr.close()
        mock_client.close.assert_called_once()
        assert mgr.client is None
        assert mgr.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_close_without_client(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        await mgr.close()
        assert mgr.client is None
        assert mgr.state == ConnectionState.DISCONNECTED

    @pytest.mark.asyncio
    async def test_close_error_handled(self):
        cfg = ConnectionConfig(hosts=["localhost"])
        mgr = Etcd3ConnectionManager(cfg)
        mock_client = MagicMock()
        mock_client.close.side_effect = Exception("close error")
        mgr.client = mock_client
        mgr.state = ConnectionState.CONNECTED
        await mgr.close()
        assert mgr.client is None
        assert mgr.state == ConnectionState.DISCONNECTED
