"""``get_user_by_user_id`` on each graph provider writes no driver text (docs/logging.md, "Database queries").

With ``raise_on_error`` it writes nothing: the 503 the caller raises is the one line. Without it, the ``None`` it answers is
one ``graph.provider.failed op=get_user_by_user_id`` line, classed, without the text.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import for_org
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.identity import IdentityMixin
from app.utils.logger import log_record_fields
from tests.support.log_capture import assert_not_logged

LOGGER = "test.user_by_user_id"
# Built at run time: a traceback prints source lines, so a literal there would show in the stack.
SENTINEL = "-".join(("SENTINEL", "driver-text")) + " bob@" + "example.com"


def _arango() -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logging.getLogger(LOGGER), MagicMock())
    p.http_client = MagicMock(execute_aql=AsyncMock(side_effect=ConnectionError(SENTINEL)))
    return p


def _neo4j() -> Neo4jProvider:
    p = Neo4jProvider(logger=logging.getLogger(LOGGER), config_service=MagicMock())
    p.client = MagicMock(execute_query=AsyncMock(side_effect=ConnectionError(SENTINEL)))
    return p


def _pg() -> IdentityMixin:
    p = object.__new__(IdentityMixin)
    p.logger = logging.getLogger(LOGGER)
    p._one = AsyncMock(side_effect=ConnectionError(SENTINEL))
    return p


_STORES = pytest.mark.parametrize("make", [_arango, _neo4j, _pg], ids=["arango", "neo4j", "postgres"])


def _lines(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == LOGGER]


@_STORES
async def test_a_raising_lookup_writes_nothing(make, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    with for_org("o1", job="t"), pytest.raises(ConnectionError):
        await make().get_user_by_user_id("m1", raise_on_error=True)

    assert _lines(caplog) == [], [r.getMessage() for r in _lines(caplog)]


@_STORES
async def test_a_swallowed_lookup_is_one_text_free_line(make, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.DEBUG, logger=LOGGER)

    with for_org("o1", job="t"):
        assert await make().get_user_by_user_id("m1") is None

    (line,) = _lines(caplog)
    fields = log_record_fields(line)
    assert (line.getMessage(), fields.get("op"), fields.get("error_type"), "error_message" in fields) == (
        "graph.provider.failed", "get_user_by_user_id", "ConnectionError", False,
    )
    assert_not_logged(_lines(caplog), SENTINEL)
