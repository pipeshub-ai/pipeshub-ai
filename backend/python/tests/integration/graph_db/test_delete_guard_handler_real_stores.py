"""The late-purge guard of the ``deleteRecord`` handler, against real Neo4j and ArangoDB.

The graph, the push (``claim_push_record``) and the record handler are real; the vector, paid-cache and storage steps
are recording fakes. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD. The PostgreSQL
twin is ``postgres/test_delete_guard_handler.py``.
"""

from __future__ import annotations

import pytest

from tests.integration.graph_db import test_kb_push_real_backends as IT
from tests.integration.graph_db.delete_guard_support import CHECKS
from tests.integration.graph_db.push_lives import PushLives

env = IT.env  # the fixture, shared with test_kb_push_real_backends.py
pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]


async def test_stale_delete_after_repush(env) -> None:
    await CHECKS["stale_delete_after_repush"](PushLives(env))


async def test_plain_delete(env) -> None:
    await CHECKS["plain_delete"](PushLives(env))


async def test_repush_between_read_and_delete(env) -> None:
    await CHECKS["repush_between_read_and_delete"](PushLives(env))
