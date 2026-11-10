"""MariaDBClient: read-only bounded queries, TLS with a CA bundle, vetted hosts."""

import asyncio
import ipaddress
import os
import stat
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.sources.client.mariadb import mariadb as client_module
from app.sources.client.mariadb.mariadb import (
    MariaDBClient,
    NoResultSetError,
    QueryTimeoutError,
)
from app.utils.url_fetcher import HostCheckError

# Keywords the binary driver accepts (checked against mariadb.connect in 2.0.0rc2-c).
DRIVER_KEYWORDS = {"host", "port", "user", "password", "database", "unix_socket", "connect_timeout",
                   "read_timeout", "write_timeout", "ssl", "ssl_ca", "ssl_verify_cert"}
CA = "-----BEGIN CERTIFICATE-----\nMIIB\n-----END CERTIFICATE-----"


class DriverError(Exception):
    def __init__(self, errno: int) -> None:
        super().__init__(f"error {errno}")
        self.errno = errno


class FakeCursor:
    def __init__(self, conn: "FakeConn") -> None:
        self.conn = conn
        self.description = None

    def execute(self, sql, params=None) -> None:
        self.conn.sent.append(sql)
        self.conn.params.append(params)
        if sql in self.conn.fail:
            raise self.conn.fail[sql]
        if self.conn.hang and sql == self.conn.query:
            time.sleep(self.conn.hang)
        self.description = [("A",), ("B",)] if sql == self.conn.query and self.conn.returns_rows else None

    def fetchall(self) -> list:
        return list(self.conn.rows)

    def fetchmany(self, n) -> list:
        return list(self.conn.rows)[:n]


class FakeConn:
    def __init__(self, query: str, *, returns_rows=True, hang=0.0, fail=None, rows=((1, 2),)) -> None:
        self.query = query
        self.returns_rows = returns_rows
        self.hang = hang
        self.fail = fail or {}
        self.rows = rows
        self.sent: list[str] = []
        self.rolled_back = False
        self.closed = False
        self.kwargs: dict = {}
        self.params: list = []

    def cursor(self, **kwargs) -> "FakeCursor":
        return FakeCursor(self)

    def rollback(self) -> None:
        self.rolled_back = True

    def close(self) -> None:
        self.closed = True


def run_against(conn: FakeConn, **client_kwargs) -> FakeConn:
    """A client whose driver hands out ``conn``, with the host vetted to a public address."""
    driver = MagicMock()

    def connect(**kwargs) -> FakeConn:
        conn.kwargs = kwargs
        return conn

    driver.connect = MagicMock(side_effect=connect)
    with patch.object(client_module, "mariadb", driver):
        client = MariaDBClient(host="db.example.com", user="u", password="p", database="shop", **client_kwargs)
    return client, driver


class TestReadOnlyQuery:
    async def test_read_only_session_and_transaction_with_a_server_limit_then_rollback(self) -> None:
        conn = FakeConn("SELECT a, b FROM t")
        client, driver = run_against(conn)
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver):
            columns, rows, truncated = await client.execute_read_only_query("SELECT a, b FROM t", 30)
        assert (columns, rows, truncated) == (["A", "B"], [(1, 2)], False)
        assert conn.sent == [
            "SET SESSION TRANSACTION READ ONLY",
            "SET SESSION max_statement_time = 30",
            "START TRANSACTION READ ONLY",
            "SELECT a, b FROM t",
        ]
        assert conn.rolled_back and conn.closed
        # A connection of its own, bounded by the driver's socket timeouts too.
        assert conn.kwargs["read_timeout"] == conn.kwargs["write_timeout"] == 35
        # The binary driver raises TypeError on any other keyword ("charset" broke every connection).
        assert set(conn.kwargs) <= DRIVER_KEYWORDS

    async def test_rows_are_capped(self) -> None:
        conn = FakeConn("SELECT a FROM t", rows=[(i,) for i in range(5)])
        client, driver = run_against(conn)
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver):
            _, rows, truncated = await client.execute_read_only_query("SELECT a FROM t", 30, max_rows=3)
        assert rows == [(0,), (1,), (2,)] and truncated

    async def test_a_statement_without_rows_is_refused_and_rolled_back(self) -> None:
        conn = FakeConn("DO 1", returns_rows=False)
        client, driver = run_against(conn)
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver), pytest.raises(NoResultSetError):
            await client.execute_read_only_query("DO 1", 30)
        assert conn.rolled_back and conn.closed

    async def test_a_server_error_is_rolled_back_and_kept(self) -> None:
        conn = FakeConn("SELECT 1", fail={"SELECT 1": DriverError(1969)})
        client, driver = run_against(conn)
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver):
            with pytest.raises(DriverError) as error:
                await client.execute_read_only_query("SELECT 1", 30)
        assert error.value.errno == 1969 and conn.rolled_back and conn.closed

    async def test_a_mysql_server_gets_its_own_time_limit(self) -> None:
        conn = FakeConn("SELECT 1", fail={"SET SESSION max_statement_time = 30": DriverError(1193)})
        client, driver = run_against(conn)
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver):
            await client.execute_read_only_query("SELECT 1", 30)
        assert "SET SESSION MAX_EXECUTION_TIME = 30000" in conn.sent

    async def test_a_stalled_answer_is_bounded_without_blocking_the_loop(self, monkeypatch) -> None:
        monkeypatch.setattr(client_module, "READ_TIMEOUT_MARGIN_S", 0.1)
        conn = FakeConn("SELECT 1", hang=0.5)
        client, driver = run_against(conn, timeout=0)
        ticks = 0

        async def ticker() -> None:
            nonlocal ticks
            while True:
                await asyncio.sleep(0.005)
                ticks += 1

        task = asyncio.create_task(ticker())
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver):
            with pytest.raises(QueryTimeoutError):
                await client.execute_read_only_query("SELECT 1", 0.01)
        task.cancel()
        assert ticks > 0  # the loop kept running while the driver was blocked


class TestBindsAndSlots:
    async def test_values_are_bound_never_written_into_the_statement(self) -> None:
        conn = FakeConn("SELECT a FROM t WHERE a IN (?, ?)")
        client, driver = run_against(conn)
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver):
            await client.execute_read_only_query("SELECT a FROM t WHERE a IN (?, ?)", 30, params=("x'); DROP", "7"))
        assert conn.params[-1] == ("x'); DROP", "7") and "DROP" not in " ".join(conn.sent)

    async def test_a_slot_is_held_until_a_timed_out_statements_thread_ends(self, monkeypatch) -> None:
        # The connection runs on after the wait gives up; so does the slot.
        monkeypatch.setattr(client_module, "READ_TIMEOUT_MARGIN_S", 0.05)
        conn = FakeConn("SELECT 1", hang=0.4)
        client, driver = run_against(conn, timeout=0)
        slots = asyncio.Semaphore(1)
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver):
            with pytest.raises(QueryTimeoutError):
                await client.execute_read_only_query("SELECT 1", 0.01, slots=slots)
            assert slots.locked()
            await asyncio.sleep(0.6)
        assert not slots.locked()

    async def test_a_failed_statement_frees_its_slot(self) -> None:
        conn = FakeConn("SELECT 1", fail={"SELECT 1": DriverError(1064)})
        client, driver = run_against(conn)
        slots = asyncio.Semaphore(1)
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver), pytest.raises(DriverError):
            await client.execute_read_only_query("SELECT 1", 30, slots=slots)
        await asyncio.sleep(0)
        assert not slots.locked()


def bare_client(**kwargs) -> MariaDBClient:
    with patch.object(client_module, "mariadb", MagicMock()):
        return MariaDBClient(host="db.example.com", user="u", password="p", database="shop", **kwargs)


def vetted(*addresses: str) -> object:
    return patch.object(
        client_module, "vetted_addresses",
        AsyncMock(return_value=[ipaddress.ip_address(a) for a in addresses]),
    )


class TestConnect:
    async def test_without_tls_the_vetted_address_is_dialled(self) -> None:
        with vetted("2001:db8::1", "203.0.113.7"):
            kwargs = await bare_client()._connect_kwargs()
        # IPv4 preferred; no keyword the binary driver refuses.
        assert kwargs["host"] == "203.0.113.7" and "ssl" not in kwargs and "charset" not in kwargs

    async def test_a_unix_socket_path_is_passed_as_a_socket(self) -> None:
        with patch.object(client_module, "mariadb", MagicMock()):
            client = MariaDBClient(host="/run/mysqld/mysqld.sock", user="u", password="p")
        with patch.object(client_module, "vetted_addresses", AsyncMock(return_value=[])):
            kwargs = await client._connect_kwargs()
        assert kwargs["unix_socket"] == "/run/mysqld/mysqld.sock" and "host" not in kwargs

    async def test_a_ca_bundle_turns_on_verified_tls_to_the_name_from_a_private_file(self) -> None:
        client = bare_client(ssl_ca_pem=CA)
        with vetted("203.0.113.7"):
            kwargs = await client._connect_kwargs()
        assert kwargs["host"] == "db.example.com"
        assert kwargs["ssl"] is True and kwargs["ssl_verify_cert"] is True
        assert set(kwargs) <= DRIVER_KEYWORDS
        path = kwargs["ssl_ca"]
        assert open(path).read().startswith("-----BEGIN CERTIFICATE-----")
        assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
        await client.close()
        assert not os.path.exists(path)

    async def test_a_wrong_password_surfaces_instead_of_hanging_in_the_pool(self) -> None:
        driver = MagicMock()
        driver.connect = MagicMock(side_effect=DriverError(1045))
        driver.create_async_pool = AsyncMock()
        with patch.object(client_module, "mariadb", driver):
            client = MariaDBClient(host="db.example.com", user="u", password="bad")
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver):
            with pytest.raises(ConnectionError) as error:
                await client.connect()
        assert error.value.__cause__.errno == 1045
        driver.create_async_pool.assert_not_awaited()

    async def test_a_refused_host_is_never_dialled(self) -> None:
        driver = MagicMock()
        driver.create_async_pool = AsyncMock()
        with patch.object(client_module, "mariadb", driver):
            client = MariaDBClient(host="169.254.169.254", user="u", password="p")
        with patch.object(client_module, "vetted_addresses", AsyncMock(side_effect=HostCheckError("refused"))), \
             patch.object(client_module, "mariadb", driver):
            with pytest.raises(ConnectionError) as refused:
                await client.connect()
        assert isinstance(refused.value.__cause__, HostCheckError)
        driver.create_async_pool.assert_not_awaited()

    async def test_a_failed_connect_removes_the_ca_file(self) -> None:
        driver = MagicMock()
        driver.connect = MagicMock(side_effect=OSError("refused"))
        driver.create_async_pool = AsyncMock()
        with patch.object(client_module, "mariadb", driver):
            client = MariaDBClient(host="db.example.com", user="u", password="p", ssl_ca_pem=CA)
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver):
            with pytest.raises(ConnectionError):
                await client.connect()
        assert client._ca_file is None


class TestCaFromConfig:
    async def test_the_agent_builder_passes_the_forms_ca(self) -> None:
        from app.sources.client.mariadb.mariadb import MariaDBClientBuilder

        config = {"auth": {"host": "db.example.com", "port": 3306, "database": "shop", "username": "u",
                           "password": "p", "sslCa": CA}}
        with patch.object(client_module, "mariadb", MagicMock()), patch.object(
            MariaDBClientBuilder, "_get_connector_config", AsyncMock(return_value=config),
        ):
            builder = await MariaDBClientBuilder.build_from_services(MagicMock(), MagicMock(), "c1")
        assert builder.get_client().ssl_ca_pem == CA

    async def test_the_connector_passes_the_forms_ca(self) -> None:
        from app.connectors.sources.mariadb import connector as connector_module

        created = {}

        class FakeConfig:
            def __init__(self, **kwargs) -> None:
                created.update(kwargs)

            def create_client(self) -> object:
                client = MagicMock()
                client.connect = AsyncMock()
                return client

        connector = connector_module.MariaDBConnector.__new__(connector_module.MariaDBConnector)
        connector.logger = MagicMock()
        connector.config_service = MagicMock()
        connector.config_service.get_config = AsyncMock(return_value={"auth": {
            "host": "db.example.com", "port": "3306", "database": "shop", "username": "u", "password": "p",
            "sslCa": CA,
        }})
        connector.connector_id = "c1"
        connector.scope = None
        connector.created_by = None
        with patch.object(connector_module, "MariaDBConfig", FakeConfig):
            assert await connector.init() is True
        assert created["ssl_ca_pem"] == CA


class TestPool:
    async def test_pooled_reads_have_read_and_write_timeouts(self) -> None:
        driver = MagicMock()
        driver.connect = MagicMock()
        driver.create_async_pool = AsyncMock()
        with patch.object(client_module, "mariadb", driver):
            client = MariaDBClient(host="db.example.com", user="u", password="p")
        with vetted("203.0.113.7"), patch.object(client_module, "mariadb", driver):
            await client.connect()
        kwargs = driver.create_async_pool.await_args.kwargs
        assert kwargs["read_timeout"] == kwargs["write_timeout"] == client_module.POOL_READ_TIMEOUT_S
