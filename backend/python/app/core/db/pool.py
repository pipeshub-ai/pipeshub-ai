"""asyncpg pool settings for a pooler in transaction mode."""

from __future__ import annotations

import asyncpg


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
    return await asyncpg.create_pool(
        dsn=dsn,
        min_size=min(min_size, max_size),
        max_size=max_size,
        max_inactive_connection_lifetime=idle_seconds,
        connection_class=PooledConnection,
        statement_cache_size=statement_cache_size,
        server_settings={"application_name": application_name},
    )
