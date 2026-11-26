"""A run of an old revision never stamps its pushed values as the newest, on PostgreSQL.

Needs a migrated database: PG_CORE_URL, PG_POOLER=none. The Neo4j and ArangoDB twin is
``../test_push_old_event_after_new_push.py``.
"""

from __future__ import annotations

import pytest

from tests.integration.graph_db.delete_guard_support import CHECKS

from .pg_lives import PgLives

pytestmark = pytest.mark.integration


async def test_stale_values_never_win(pg_db) -> None:
    await CHECKS["stale_values_never_win"](PgLives(pg_db))
