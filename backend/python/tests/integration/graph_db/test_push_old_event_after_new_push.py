"""A run of an old revision never stamps its pushed values as the newest, against real Neo4j and ArangoDB.

The push commits revision 2 while a run holds the bytes of revision 1; ``process_blocks`` reads the real record row.
The PostgreSQL twin is ``postgres/test_push_old_event_after_new_push.py``. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import pytest

from tests.integration.graph_db import test_kb_push_real_backends as IT
from tests.integration.graph_db.delete_guard_support import CHECKS
from tests.integration.graph_db.push_lives import PushLives

env = IT.env
pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]


async def test_stale_values_never_win(env) -> None:
    await CHECKS["stale_values_never_win"](PushLives(env))
