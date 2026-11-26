"""The late-purge guard of the ``deleteRecord`` handler on PostgreSQL, with the queue in the same database or outside.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none. The graph reads and the
record handler are real; the vector, paid-cache and storage steps are recording fakes. The Neo4j and ArangoDB twin is
``../test_delete_guard_handler_real_stores.py``.
"""

from __future__ import annotations

import pytest

from tests.integration.graph_db.delete_guard_support import CHECKS

from .pg_lives import PgLives

pytestmark = pytest.mark.integration


async def test_stale_delete_after_repush(pg_db) -> None:
    await CHECKS["stale_delete_after_repush"](PgLives(pg_db))


async def test_plain_delete(pg_db) -> None:
    await CHECKS["plain_delete"](PgLives(pg_db))


async def test_repush_between_read_and_delete(pg_db) -> None:
    await CHECKS["repush_between_read_and_delete"](PgLives(pg_db))
