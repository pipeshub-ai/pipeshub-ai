"""The MariaDB setup form's connection check.

One connection to a vetted host, one read-only statement, a hard time limit
(the driver has no read timeout), and a message that names what to change.
The driver turns every socket failure into errno 2002 and keeps the real one
on the exception chain, so the chain is what is classified.
"""

from __future__ import annotations

import asyncio
import re
import socket
import ssl
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from app.connectors.core.base.connector.connector_service import ConnectionCheckResult
from app.sources.client.mariadb.mariadb import (
    MariaDBClient,
    MariaDBConfig,
    QueryTimeoutError,
)
from app.utils.url_fetcher import HostCheckError

if TYPE_CHECKING:
    from logging import Logger

CONNECTION_CHECK_TIMEOUT_S = 10.0
# Connect, the statement and the driver's read timeout; past this the check gives up.
CHECK_WAIT_S = 3 * CONNECTION_CHECK_TIMEOUT_S
CHECK_STATEMENT = "SELECT VERSION(), CURRENT_USER(), DATABASE()"

_CR_CONNECTION_ERROR = 2002
_CR_UNKNOWN_HOST = 2005
_CR_SSL_CONNECTION_ERROR = 2026
_ER_DBACCESS_DENIED = 1044
_ER_ACCESS_DENIED = 1045
_ER_BAD_DB = 1049
_ER_HOST_NOT_PRIVILEGED = 1130

ClientFactory = Callable[[MariaDBConfig], MariaDBClient]


def config_from_auth(auth: dict[str, Any]) -> MariaDBConfig:
    """The form's values as a client config. Raises ValueError with a message for the user."""
    host = str(auth.get("host") or "").strip()
    database = str(auth.get("database") or "").strip()
    user = str(auth.get("username") or auth.get("user") or "").strip()
    if not host:
        raise ValueError("The MariaDB host is missing.")
    if not database:
        raise ValueError("The MariaDB database is missing.")
    if not user:
        raise ValueError("The MariaDB username is missing.")
    try:
        port = int(str(auth.get("port") or 3306).strip())
    except ValueError:
        raise ValueError("The MariaDB port must be a number.") from None
    if not 1 <= port <= 65535:
        raise ValueError("The MariaDB port must be between 1 and 65535.")
    return MariaDBConfig(
        host=host, port=port, database=database, user=user, password=str(auth.get("password") or ""),
        timeout=int(CONNECTION_CHECK_TIMEOUT_S), ssl_ca_pem=auth.get("sslCa") or None,
    )


def _chain(error: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


def describe_connection_error(error: BaseException, config: MariaDBConfig) -> str:
    """What went wrong, in terms of what to change on the server or on this form."""
    host, port, user, database = config.host, config.port, config.user, config.database
    chain = _chain(error)
    errnos = {getattr(e, "errno", None) for e in chain}
    texts = " ".join(str(e) for e in chain).lower()

    if _ER_ACCESS_DENIED in errnos:
        return (
            f"Access denied for {user}: check the username and password, and that the user may "
            "connect from this server's addresses (shown on this form)."
        )
    if _ER_HOST_NOT_PRIVILEGED in errnos:
        return (
            f"The server does not let {user} connect from this address. Allow the addresses shown "
            "on this form for that user."
        )
    if _ER_DBACCESS_DENIED in errnos:
        # MariaDB answers this, not "unknown database", when the user may not see it.
        return (
            f"{user} has no access to the database {database}, or it does not exist. "
            f"Check the name, and grant SELECT on {database}."
        )
    if _ER_BAD_DB in errnos:
        return f"The database {database} does not exist on {host}."
    if _CR_SSL_CONNECTION_ERROR in errnos or any(isinstance(e, ssl.SSLError) for e in chain) or "ssl" in texts:
        return (
            f"TLS with {host} failed. Check that the CA certificate signed the server's certificate, "
            f"and that the certificate names {host}."
        )
    if _CR_UNKNOWN_HOST in errnos or any(isinstance(e, socket.gaierror) for e in chain):
        return f"The host {host} could not be found. Check the host name."
    # The binary driver reports the socket's errno in the text: (111)/(115) refused, (110) timed out.
    os_errno = _os_errno(texts) if _CR_CONNECTION_ERROR in errnos else None
    if os_errno in (111, 115) or any(isinstance(e, ConnectionRefusedError) for e in chain):
        return (
            f"Nothing accepts connections on {host}:{port}. Check the port, and that the server "
            "listens on the network (bind-address)."
        )
    if os_errno == 110 or any(isinstance(e, (TimeoutError, asyncio.TimeoutError)) for e in chain):
        return (
            f"No answer from {host}:{port} within {int(CONNECTION_CHECK_TIMEOUT_S)} seconds. A firewall "
            "may be dropping the connection; allow the addresses shown on this form."
        )
    return f"Could not connect to {host}:{port}: {chain[-1] if chain else error}"


def _os_errno(text: str) -> int | None:
    match = re.search(r"\((\d+)\)", text)
    return int(match.group(1)) if match else None


def _default_client(config: MariaDBConfig) -> MariaDBClient:
    return config.create_client()


async def check_connection(
    auth_config: dict[str, Any],
    logger: Logger,
    *,
    client_factory: ClientFactory = _default_client,
) -> ConnectionCheckResult:
    """Try the setup form's MariaDB settings: connect, read one row, close."""
    try:
        config = config_from_auth(auth_config)
    except ValueError as e:
        return ConnectionCheckResult(success=False, message=str(e))

    client = client_factory(config)
    try:
        # One connection of its own, no pool: a pool hides connect errors behind retries.
        _, rows, _ = await asyncio.wait_for(
            client.execute_read_only_query(CHECK_STATEMENT, CONNECTION_CHECK_TIMEOUT_S), CHECK_WAIT_S,
        )
    except HostCheckError as e:
        return ConnectionCheckResult(success=False, message=str(e))
    except (asyncio.TimeoutError, QueryTimeoutError) as e:
        logger.info("MariaDB connection check to %s:%s timed out", config.host, config.port)
        return ConnectionCheckResult(success=False, message=describe_connection_error(TimeoutError(str(e)), config))
    except Exception as e:
        logger.info("MariaDB connection check to %s:%s failed: %r", config.host, config.port, e)
        return ConnectionCheckResult(success=False, message=describe_connection_error(e, config))
    finally:
        await client.close()

    version, current_user, database = ([*list(rows[0]), None, None, None])[:3] if rows else (None, None, None)
    return ConnectionCheckResult(
        success=True,
        message=f'Connected to database "{database or config.database}" as {current_user or config.user} '
                f"(MariaDB {version or 'server'}).",
    )
