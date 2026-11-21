"""asyncpg pool settings for a pooler in transaction mode."""

from __future__ import annotations

import asyncpg

from app.core.db.errors import ConnectionLost, TooManyConnections
from app.utils import error_class

_LOST_STATES = frozenset({"57P01", "57P02", "57P03", "25P03"})
_TOO_MANY_CONNECTIONS = "53300"


class PooledConnection(asyncpg.Connection):
    """A connection that never sends the client-side reset on release.

    asyncpg's pool runs ``RESET ALL; CLOSE ALL; UNLISTEN *`` when a connection is released. Behind a transaction pooler
    that statement lands on whichever server connection is free and blanks ``application_name`` there. The runtime
    keeps no session state (every setting is transaction-local), and the pooler's own server reset removes what a
    buggy caller leaves behind.
    """

    def get_reset_query(self) -> str:
        return ""


async def create_pool(
    dsn: str, *, max_size: int, statement_cache_size: int, application_name: str, idle_seconds: float = 300.0, min_size: int = 1
) -> asyncpg.Pool:
    try:
        return await asyncpg.create_pool(
            dsn=dsn,
            min_size=min(min_size, max_size),
            max_size=max_size,
            max_inactive_connection_lifetime=idle_seconds,
            connection_class=PooledConnection,
            statement_cache_size=statement_cache_size,
            server_settings={"application_name": application_name},
        )
    except (OSError, asyncpg.PostgresError) as e:  # the pool opens its first min_size connections here
        lost = connect_failure(e)
        if lost is None:
            raise
        raise lost from e


def lost_state(state: str | None) -> bool:
    """A SQLSTATE that means the server connection is gone or cannot be had now: a connection exception (08xxx), a shutdown or
    start-up (57P01-57P03), an idle-in-transaction timeout (25P03)."""
    return state is not None and (state in _LOST_STATES or state.startswith("08"))


def connect_failure(e: Exception) -> ConnectionLost | None:
    """What a new connection that could not be opened raises: ``ConnectionLost`` for an outage (the OS refused it, or the server is
    starting, stopping or full), or None to re-raise ``e`` itself (a timeout, or a configuration refusal such as 28P01 or 3D000).
    Either is text-hidden: no statement exists to record it, and the text names the host or the database user. No statement ran, so
    a retry cannot repeat an effect; a full server (53300) is still not retried, as every attempt forks a backend on it."""
    error_class.mark_error(e, hide_text=True)
    state = getattr(e, "sqlstate", None)
    if isinstance(e, TimeoutError) or (
        isinstance(e, asyncpg.PostgresError) and not (lost_state(state) or state == _TOO_MANY_CONNECTIONS)
    ):
        return None
    lost = TooManyConnections() if state == _TOO_MANY_CONNECTIONS else ConnectionLost("cannot connect", retry_safe=True)
    error_class.mark_error(lost, hide_text=True)
    return lost
