"""Personal connectors reach public addresses only; team connectors follow the deployment setting.

A personal connector can be created by any user, so neither its connection check nor a saved
instance (at init and sync) may dial a private-network, loopback, link-local or metadata address.
A team connector is created by an admin and keeps the deployment rule
(PIPESHUB_BLOCK_PRIVATE_ADDRESSES).
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

import app.sources.client.postgres.postgres as pg_client
from app.connectors.core.address_policy import public_only_for_scope
from app.connectors.core.base.connector.connector_service import ConnectorInitError
from app.connectors.core.registry.connector_builder import ConnectorScope
from app.connectors.sources.network_share.errors import NetworkShareAuthError
from app.connectors.sources.postgres.connector import PostgreSQLConnector
from app.connectors.sources.smb.connector import SmbConnector
from app.sources.client.cifs.cifs import CifsClient
from app.sources.client.smb.smb import SmbClient, _vet_every_session, dial_address
from app.utils.execute_query import _connector_is_public_only, _execute_postgres_query
from app.utils.url_fetcher import (
    PRIVATE_ADDRESS_SWITCH_ENV,
    HostCheckError,
    public_address_refusal,
    vetted_addresses,
)

_LOGGER = logging.getLogger("test.address.policy")
_PG = {"host": "db.example.com", "port": "5432", "database": "shop", "username": "reader", "password": "pw"}

# What a member could aim a personal connector at: a Redis port on the LAN, this machine, the
# cloud metadata address, a name for this machine, and a public-looking name that resolves inward.
_INWARD_HOSTS = [
    ("10.0.0.7", None),
    ("127.0.0.1", None),
    ("169.254.169.254", None),
    ("localhost", None),
    ("db.attacker.example", "10.0.0.7"),
    ("db.attacker.example", "169.254.169.254"),
    ("db.attacker.example", "127.0.0.1"),
]


@pytest.fixture(autouse=True)
def _deployment_allows_private(monkeypatch) -> None:
    monkeypatch.delenv(PRIVATE_ADDRESS_SWITCH_ENV, raising=False)


def _resolve(monkeypatch, answers: dict[str, str]) -> MagicMock:
    def getaddrinfo(host, *_args, **_kwargs) -> None:
        if host not in answers:
            raise socket.gaierror(socket.EAI_NONAME, "Name or service not known")
        ip = answers[host]
        family = socket.AF_INET6 if ":" in ip else socket.AF_INET
        return [(family, socket.SOCK_STREAM, 6, "", (ip, 0))]

    lookup = MagicMock(side_effect=getaddrinfo)
    monkeypatch.setattr(socket, "getaddrinfo", lookup)
    return lookup


@pytest.fixture
def no_socket_connect(monkeypatch) -> None:
    """Any attempt to open a TCP connection fails the test."""
    connect = MagicMock(side_effect=AssertionError("a socket connect was attempted"))
    monkeypatch.setattr(socket.socket, "connect", connect)
    monkeypatch.setattr(socket.socket, "connect_ex", connect)
    return connect


@pytest.fixture
def driver_connect(monkeypatch) -> None:
    connect = AsyncMock(side_effect=ConnectionRefusedError())
    monkeypatch.setattr(pg_client, "asyncpg", asyncpg)
    monkeypatch.setattr(asyncpg, "connect", connect)
    return connect


class TestTheScopeRule:
    @pytest.mark.parametrize(
        ("scope", "public_only"),
        [
            ("personal", True),
            (ConnectorScope.PERSONAL, True),
            ("team", False),
            (ConnectorScope.TEAM, False),
            (None, True),
            ("", True),
            ("something-else", True),
        ],
    )
    def test_only_team_follows_the_deployment_setting(self, scope, public_only) -> None:
        assert public_only_for_scope(scope) is public_only


class TestVettedAddresses:
    @pytest.mark.parametrize(("host", "resolves_to"), _INWARD_HOSTS)
    async def test_public_only_refuses_inward_addresses_with_one_message(self, monkeypatch, host, resolves_to) -> None:
        lookup = _resolve(monkeypatch, {host: resolves_to} if resolves_to else {})
        with pytest.raises(HostCheckError) as refused:
            await vetted_addresses(host, public_only=True)
        assert str(refused.value) == public_address_refusal(host)
        # Nothing about what the name resolves to.
        if resolves_to:
            assert resolves_to not in str(refused.value)
        assert lookup.call_count <= 1

    async def test_an_unknown_name_is_refused_with_the_same_message(self, monkeypatch) -> None:
        _resolve(monkeypatch, {})
        with pytest.raises(HostCheckError) as refused:
            await vetted_addresses("nowhere.example", public_only=True)
        assert str(refused.value) == public_address_refusal("nowhere.example")

    async def test_a_unix_socket_path_is_refused(self) -> None:
        with pytest.raises(HostCheckError, match="public"):
            await vetted_addresses("/var/run/postgresql", public_only=True)

    async def test_public_addresses_pass(self, monkeypatch) -> None:
        _resolve(monkeypatch, {"db.example.com": "93.184.216.34"})
        assert await vetted_addresses("db.example.com", public_only=True) == [ipaddress.ip_address("93.184.216.34")]
        assert await vetted_addresses("93.184.216.34", public_only=True) == [ipaddress.ip_address("93.184.216.34")]

    async def test_one_inward_answer_among_public_ones_is_enough_to_refuse(self, monkeypatch) -> None:
        monkeypatch.setattr(
            socket,
            "getaddrinfo",
            MagicMock(
                return_value=[
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("93.184.216.34", 0)),
                    (socket.AF_INET, socket.SOCK_STREAM, 6, "", ("10.0.0.7", 0)),
                ]
            ),
        )
        with pytest.raises(HostCheckError):
            await vetted_addresses("rebind.example", public_only=True)

    async def test_the_default_still_follows_the_deployment_setting(self, monkeypatch) -> None:
        assert await vetted_addresses("10.0.0.7") == [ipaddress.ip_address("10.0.0.7")]
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
        with pytest.raises(HostCheckError, match="private or internal address"):
            await vetted_addresses("10.0.0.7")

    async def test_the_default_still_never_reaches_metadata(self) -> None:
        with pytest.raises(HostCheckError, match="cloud metadata"):
            await vetted_addresses("169.254.169.254")


class TestPostgresCheck:
    @pytest.mark.parametrize(("host", "resolves_to"), _INWARD_HOSTS)
    async def test_a_members_check_is_refused_before_anything_is_dialled(
        self, monkeypatch, driver_connect, no_socket_connect, host, resolves_to
    ) -> None:
        _resolve(monkeypatch, {host: resolves_to} if resolves_to else {})
        result = await PostgreSQLConnector.check_connection(
            {**_PG, "host": host, "port": "6379"}, _LOGGER, public_only=True
        )
        assert result.success is False
        assert result.message == public_address_refusal(host)
        driver_connect.assert_not_called()
        no_socket_connect.assert_not_called()

    async def test_the_check_is_public_only_when_the_caller_says_nothing(self, driver_connect) -> None:
        result = await PostgreSQLConnector.check_connection({**_PG, "host": "10.0.0.7"}, _LOGGER)
        assert result.message == public_address_refusal("10.0.0.7")
        driver_connect.assert_not_called()

    async def test_the_ssl_retry_is_public_only_too(self, monkeypatch) -> None:
        seen: list[bool] = []

        async def fake_try(kwargs, *, public_only=False) -> None:
            seen.append(public_only)
            return None, asyncpg.InvalidAuthorizationSpecificationError("no encryption")

        monkeypatch.setattr(PostgreSQLConnector, "_try_connection", staticmethod(fake_try))
        await PostgreSQLConnector.check_connection({**_PG, "sslmode": "prefer"}, _LOGGER, public_only=True)
        assert seen == [True, True]

    async def test_an_admins_check_follows_the_deployment_setting(self, monkeypatch, driver_connect) -> None:
        result = await PostgreSQLConnector.check_connection({**_PG, "host": "10.0.0.7"}, _LOGGER, public_only=False)
        driver_connect.assert_awaited()
        assert "refused the connection" in result.message

        driver_connect.reset_mock()
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
        result = await PostgreSQLConnector.check_connection({**_PG, "host": "10.0.0.7"}, _LOGGER, public_only=False)
        assert "private or internal address" in result.message
        assert "Personal connectors" not in result.message
        driver_connect.assert_not_called()


class TestSavedPostgresConnector:
    @staticmethod
    def _connector(host: str, *, scope: str, saved_scope: str | None = None) -> PostgreSQLConnector:
        saved = {"auth": {**_PG, "host": host}}
        if saved_scope:
            saved["scope"] = saved_scope
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value=saved)
        return PostgreSQLConnector(
            logger=_LOGGER,
            data_entities_processor=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=config_service,
            connector_id="pg-1",
            scope=scope,
        )

    @pytest.mark.parametrize(("host", "resolves_to"), _INWARD_HOSTS)
    async def test_a_personal_connector_to_an_inward_address_fails_at_init(
        self, monkeypatch, driver_connect, no_socket_connect, host, resolves_to
    ) -> None:
        _resolve(monkeypatch, {host: resolves_to} if resolves_to else {})
        connector = self._connector(host, scope="personal")
        with pytest.raises(ConnectorInitError) as refused:
            await connector.init()
        assert str(refused.value) == public_address_refusal(host)
        driver_connect.assert_not_called()
        no_socket_connect.assert_not_called()
        assert connector.data_source is None

    async def test_a_team_connector_to_a_private_address_connects(self, driver_connect) -> None:
        connector = self._connector("10.0.0.7", scope="team")
        assert await connector.init() is False  # the driver mock refuses; the dial was attempted
        driver_connect.assert_awaited()

    async def test_a_team_connector_follows_the_deployment_switch(self, monkeypatch, driver_connect) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
        with pytest.raises(ConnectorInitError, match="private or internal address"):
            await self._connector("10.0.0.7", scope="team").init()
        driver_connect.assert_not_called()

    @pytest.mark.parametrize(("scope", "saved_scope"), [("team", "personal"), ("personal", "team"), ("personal", None)])
    async def test_either_record_naming_it_personal_is_enough(self, driver_connect, scope, saved_scope) -> None:
        with pytest.raises(ConnectorInitError, match="public"):
            await self._connector("10.0.0.7", scope=scope, saved_scope=saved_scope).init()
        driver_connect.assert_not_called()


class TestPostgresClient:
    async def test_the_pool_dials_through_the_clients_rule(self, monkeypatch) -> None:
        fake = MagicMock()
        fake.create_pool = AsyncMock(return_value=MagicMock())
        monkeypatch.setattr(pg_client, "asyncpg", fake)
        client = pg_client.PostgreSQLClient(host="h", database="d", user="u", password="p", public_only=True)
        await client.connect()
        assert fake.create_pool.await_args.kwargs["connect"].keywords == {"public_only": True}

    async def test_the_builder_passes_the_rule_on(self, monkeypatch) -> None:
        monkeypatch.setattr(pg_client, "asyncpg", MagicMock())
        config_service = MagicMock()
        config_service.get_config = AsyncMock(return_value={"auth": {**_PG}})
        builder = await pg_client.PostgreSQLClientBuilder.build_from_services(
            _LOGGER, config_service, "pg-1", public_only=True
        )
        assert builder.get_client().public_only is True

    async def test_a_public_only_dial_never_reaches_the_driver(self, monkeypatch, driver_connect) -> None:
        _resolve(monkeypatch, {"db.attacker.example": "10.0.0.7"})
        with pytest.raises(HostCheckError):
            await pg_client.connect_to_checked_address(host="db.attacker.example", port=5432, public_only=True)
        driver_connect.assert_not_called()


def _fake_smbclient() -> None:
    return MagicMock()


class TestSmb:
    @pytest.mark.parametrize(("host", "resolves_to"), _INWARD_HOSTS)
    def test_dial_address_refuses_inward_addresses_for_a_personal_connector(self, monkeypatch, host, resolves_to) -> None:
        _resolve(monkeypatch, {host: resolves_to} if resolves_to else {})
        with pytest.raises(NetworkShareAuthError) as refused:
            dial_address(host, public_only=True)
        assert str(refused.value) == public_address_refusal(host)

    def test_dial_address_still_allows_private_addresses_by_default(self, monkeypatch) -> None:
        _resolve(monkeypatch, {"files.example": "10.0.0.7"})
        assert dial_address("files.example") == "10.0.0.7"

    def test_dial_address_allows_public_addresses_for_a_personal_connector(self, monkeypatch) -> None:
        _resolve(monkeypatch, {"files.example": "93.184.216.34"})
        assert dial_address("files.example", public_only=True) == "93.184.216.34"

    async def test_a_members_check_never_opens_a_session(self, monkeypatch, no_socket_connect) -> None:
        _resolve(monkeypatch, {"files.example": "10.0.0.7"})
        fake = _fake_smbclient()
        auth = {"server": "files.example", "username": "u", "password": "p", "share": "s", "port": "445"}
        with patch.object(SmbClient, "_smbclient", return_value=fake):
            result = await SmbConnector.check_connection(auth, _LOGGER, public_only=True)
        assert result.success is False
        assert public_address_refusal("files.example") in result.message
        fake.register_session.assert_not_called()
        no_socket_connect.assert_not_called()

    async def test_an_admins_check_follows_the_deployment_setting(self, monkeypatch) -> None:
        _resolve(monkeypatch, {"files.example": "10.0.0.7"})
        fake = _fake_smbclient()
        auth = {"server": "files.example", "username": "u", "password": "p", "share": "s", "port": "445"}
        with patch.object(SmbClient, "_smbclient", return_value=fake):
            await SmbConnector.check_connection(auth, _LOGGER, public_only=False)
        fake.register_session.assert_called_once()
        assert fake.register_session.call_args.args[0] == "10.0.0.7"

    @staticmethod
    def _connector(scope: str) -> SmbConnector:
        config_service = MagicMock()
        config_service.get_config = AsyncMock(
            return_value={
                "auth": {"server": "files.example", "username": "u", "password": "p", "share": "s", "port": "445"}
            }
        )
        connector = SmbConnector(
            logger=_LOGGER,
            data_entities_processor=MagicMock(),
            data_store_provider=MagicMock(),
            config_service=config_service,
            connector_id="smb-1",
            scope=scope,
            created_by="user-1",
        )
        connector._auth_error = AsyncMock()
        connector._load_creator_email = AsyncMock()
        return connector

    async def test_a_saved_personal_connector_to_a_private_address_fails_at_init(self, monkeypatch, no_socket_connect) -> None:
        _resolve(monkeypatch, {"files.example": "10.0.0.7"})
        fake = _fake_smbclient()
        connector = self._connector("personal")
        with patch.object(SmbClient, "_smbclient", return_value=fake):
            assert await connector.init() is False
        fake.register_session.assert_not_called()
        no_socket_connect.assert_not_called()
        assert connector._auth_error.await_args.args[1] == public_address_refusal("files.example")

    async def test_a_saved_team_connector_to_a_private_address_connects(self, monkeypatch) -> None:
        _resolve(monkeypatch, {"files.example": "10.0.0.7"})
        fake = _fake_smbclient()
        connector = self._connector("team")
        with (
            patch.object(SmbClient, "_smbclient", return_value=fake),
            patch("app.connectors.sources.smb.connector.load_connector_filters", AsyncMock(return_value=(None, None))),
        ):
            assert await connector.init() is True
        assert fake.register_session.call_args.args[0] == "10.0.0.7"

    async def test_a_saved_team_connector_follows_the_deployment_switch(self, monkeypatch) -> None:
        monkeypatch.setenv(PRIVATE_ADDRESS_SWITCH_ENV, "true")
        _resolve(monkeypatch, {"files.example": "10.0.0.7"})
        connector = self._connector("team")
        with patch.object(SmbClient, "_smbclient", return_value=_fake_smbclient()):
            assert await connector.init() is False
        assert "private or internal" in connector._auth_error.await_args.args[1]

    def test_a_dfs_referral_follows_the_rule_of_the_client_that_followed_it(self, monkeypatch) -> None:
        original = MagicMock()
        pool = SimpleNamespace(register_session=original)
        _vet_every_session(pool)
        _resolve(monkeypatch, {"dfs-target.example": "10.0.0.7"})
        personal = SmbClient(server="files.example", username="u", password="p", public_only=True)
        team = SmbClient(server="files.example", username="u", password="p")

        with personal._io_lock, pytest.raises(NetworkShareAuthError, match="public"):
            pool.register_session("dfs-target.example", username="u")
        original.assert_not_called()

        with team._io_lock:
            pool.register_session("dfs-target.example", username="u")
        original.assert_called_once_with("10.0.0.7", username="u")

    def test_the_rule_does_not_leak_to_other_threads_or_past_the_lock(self, monkeypatch) -> None:
        original = MagicMock()
        pool = SimpleNamespace(register_session=original)
        _vet_every_session(pool)
        _resolve(monkeypatch, {"dfs-target.example": "10.0.0.7"})
        personal = SmbClient(server="files.example", username="u", password="p", public_only=True)

        with personal._io_lock:
            other: list[object] = []
            thread = threading.Thread(
                target=lambda: other.append(pool.register_session("dfs-target.example", username="u"))
            )
            thread.start()
            thread.join()
        assert original.call_count == 1  # the other thread holds no client's lock
        pool.register_session("dfs-target.example", username="u")
        assert original.call_count == 2


class TestCifs:
    @staticmethod
    def _client(*, public_only: bool) -> tuple[CifsClient, MagicMock]:
        client = CifsClient(
            server="files.example", username="u", password="p", remote_name="FILES", public_only=public_only
        )
        conn = MagicMock()
        conn.connect.return_value = True
        conn.isUsingSMB2 = False
        client._new_connection = lambda: conn  # type: ignore[method-assign]
        return client, conn

    def test_a_personal_connector_never_connects_to_a_private_address(self, monkeypatch) -> None:
        _resolve(monkeypatch, {"files.example": "10.0.0.7"})
        client, conn = self._client(public_only=True)
        with pytest.raises(NetworkShareAuthError, match="public"):
            client.connect()
        conn.connect.assert_not_called()

    def test_a_team_connector_connects(self, monkeypatch) -> None:
        _resolve(monkeypatch, {"files.example": "10.0.0.7"})
        client, conn = self._client(public_only=False)
        client.connect()
        conn.connect.assert_called_once_with("10.0.0.7", 445)


class TestExecuteQuery:
    @pytest.mark.parametrize(
        ("document", "public_only"),
        [({"scope": "personal"}, True), ({"scope": "team"}, False), ({}, True), (None, True)],
    )
    async def test_the_tool_reads_the_scope_of_the_connector(self, document, public_only) -> None:
        graph = MagicMock()
        graph.get_document = AsyncMock(return_value=document)
        assert await _connector_is_public_only(graph, "pg-1") is public_only

    async def test_no_graph_provider_means_public_only(self) -> None:
        assert await _connector_is_public_only(None, "pg-1") is True

    @pytest.mark.parametrize(("passed", "expected"), [(None, True), (False, False), (True, True)])
    async def test_the_query_client_is_built_with_the_rule(self, passed, expected) -> None:
        client = MagicMock()
        client.connect = AsyncMock()
        client.close = AsyncMock()
        client.execute_query_raw = AsyncMock(return_value=(["a"], [(1,)]))
        client.get_connection_info.return_value = {}
        builder = MagicMock()
        builder.get_client.return_value = client
        kwargs = {} if passed is None else {"public_only": passed}
        with patch(
            "app.sources.client.postgres.postgres.PostgreSQLClientBuilder.build_from_services",
            new_callable=AsyncMock,
            return_value=builder,
        ) as build:
            await _execute_postgres_query("SELECT 1", MagicMock(), "pg-1", **kwargs)
        assert build.await_args.kwargs["public_only"] is expected

