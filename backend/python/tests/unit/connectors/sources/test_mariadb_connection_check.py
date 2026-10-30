"""MariaDBConnector.check_connection: the setup form's check, classified on the driver's real errors."""

import asyncio
import logging
import socket
import ssl
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.mariadb.connection_check import (
    CHECK_STATEMENT,
    check_connection,
    config_from_auth,
    describe_connection_error,
)
from app.connectors.sources.mariadb.connector import MariaDBConnector
from app.sources.client.mariadb.mariadb import QueryTimeoutError
from app.utils.url_fetcher import HostCheckError

_LOGGER = logging.getLogger("test.mariadb.check")
AUTH = {"host": "db.example.com", "port": "3306", "database": "shop", "username": "reader", "password": "s3cret-pw"}


class DriverError(Exception):
    def __init__(self, errno: int, message: str = "") -> None:
        super().__init__(message or f"error {errno}")
        self.errno = errno


def socket_failure(cause: BaseException) -> ConnectionError:
    """As the driver raises it: errno 2002 with the socket error as context, wrapped by our client."""
    driver = DriverError(2002, "")
    driver.__context__ = cause
    wrapped = ConnectionError("Failed to connect to MariaDB: ")
    wrapped.__cause__ = driver
    return wrapped


def factory(*, connect=None, query=None) -> tuple:
    client = MagicMock()
    client.connect = AsyncMock(side_effect=connect)
    client.execute_read_only_query = AsyncMock(
        side_effect=query, return_value=(["V", "U", "D"], [("11.4.2-MariaDB", "reader@%", "shop")], False),
    )
    client.close = AsyncMock()
    return client, (lambda config: client)


class TestCheck:
    async def test_success_reads_one_row_read_only_and_closes(self) -> None:
        client, make = factory()
        result = await check_connection(AUTH, _LOGGER, client_factory=make)
        client.connect.assert_not_called()  # no pool: it hides connect errors behind retries
        assert result.success and result.message == 'Connected to database "shop" as reader@% (MariaDB 11.4.2-MariaDB).'
        assert client.execute_read_only_query.await_args.args[0] == CHECK_STATEMENT
        client.close.assert_awaited_once()

    async def test_invalid_settings_make_no_connection(self) -> None:
        client, make = factory()
        result = await check_connection({**AUTH, "port": "x"}, _LOGGER, client_factory=make)
        assert not result.success and "port" in result.message
        client.connect.assert_not_awaited()

    async def test_a_refused_host_is_reported_as_the_policy_says(self) -> None:
        client, make = factory(query=HostCheckError("That address is not allowed."))
        result = await check_connection(AUTH, _LOGGER, client_factory=make)
        assert result.message == "That address is not allowed." and not result.success
        client.close.assert_awaited_once()

    async def test_a_hanging_handshake_is_bounded(self, monkeypatch) -> None:
        from app.connectors.sources.mariadb import connection_check

        monkeypatch.setattr(connection_check, "CHECK_WAIT_S", 0.01)

        async def hang(*args, **kwargs) -> None:
            await asyncio.sleep(3600)

        client, make = factory(query=hang)
        result = await check_connection(AUTH, _LOGGER, client_factory=make)
        assert not result.success and "No answer" in result.message
        client.close.assert_awaited_once()

    async def test_a_query_timeout_is_a_firewall_hint(self) -> None:
        client, make = factory(query=QueryTimeoutError("MariaDB did not answer within 10 seconds"))
        result = await check_connection(AUTH, _LOGGER, client_factory=make)
        assert "No answer" in result.message

    def test_the_connector_offers_it(self) -> None:
        assert MariaDBConnector.supports_connection_check()


class TestMessages:
    @pytest.mark.parametrize(("error", "says"), [
        (socket_failure(socket.gaierror(-2, "Name or service not known")), "could not be found"),
        (socket_failure(ConnectionRefusedError(111, "Connection refused")), "Nothing accepts connections"),
        (socket_failure(TimeoutError()), "No answer"),
        (ConnectionError("x").with_traceback(None), "Could not connect"),
        (DriverError(1045, "Access denied for user 'reader'@'10.0.0.1'"), "Access denied for reader"),
        (DriverError(1044, "Access denied for user to database 'shop'"), "no access to the database shop"),
        (DriverError(1049, "Unknown database 'shop'"), "does not exist"),
        (DriverError(1130, "Host '10.0.0.1' is not allowed to connect"), "does not let reader connect"),
        (socket_failure(ssl.SSLError(1, "certificate verify failed")), "TLS with db.example.com failed"),
        # The binary driver: errno 2002 with the OS errno in its text, 2005, 2026.
        (DriverError(2002, "Can't connect to server on 'db.example.com' (111)"), "Nothing accepts connections"),
        (DriverError(2002, "Can't connect to server on 'db.example.com' (115)"), "Nothing accepts connections"),
        (DriverError(2002, "Can't connect to server on 'db.example.com' (110)"), "No answer"),
        (DriverError(2005, "Unknown server host 'db.example.com' (-2)"), "could not be found"),
        (DriverError(2026, "TLS/SSL error: certificate verify failed"), "TLS with db.example.com failed"),
    ])
    def test_each_failure_says_what_to_change(self, error, says) -> None:
        assert says in describe_connection_error(error, config_from_auth(AUTH))

    def test_the_password_never_appears(self) -> None:
        message = describe_connection_error(DriverError(1045, "Access denied"), config_from_auth(AUTH))
        assert "s3cret-pw" not in message
