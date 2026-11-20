"""``get_record_by_id(raise_on_error=True)`` lets a caller tell a failed read from "no such record".
The record-content resolver needs it: a graph it could not reach was reported to the agent as
"Record not found". The default still answers None, so existing callers are unchanged, and a
record that is there but will not build is still None: that is not an outage."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.org_context import OrgContext, bind
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.graph_db.postgres.provider import PostgresGraphProvider

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Down(ConnectionError):
    pass


def _neo4j(error: Exception | None, rows: list) -> Neo4jProvider:
    p = Neo4jProvider(logger=MagicMock(), config_service=MagicMock())
    p.client = AsyncMock()
    p.client.execute_query = AsyncMock(side_effect=error) if error else AsyncMock(return_value=rows)
    return p


def _arango(error: Exception | None, rows: list) -> ArangoHTTPProvider:
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    p.http_client.execute_aql = AsyncMock(side_effect=error) if error else AsyncMock(return_value=rows)
    return p


def _postgres(error: Exception | None, rows: list) -> PostgresGraphProvider:
    db = MagicMock()
    db.run = AsyncMock(side_effect=error) if error else AsyncMock(return_value=rows[0] if rows else None)
    return PostgresGraphProvider(MagicMock(spec=logging.Logger), db)


# A record row whose type document is missing: every provider refuses to build it.
_UNBUILDABLE = {
    "neo4j": [{"record": {"id": "r1", "recordType": "FILE", "orgId": "org-1"}, "typeDoc": None}],
    "arango": [{"record": {"_key": "r1", "recordType": "FILE", "orgId": "org-1"}, "typeDoc": None}],
    "postgres": [{"rec": {"id": "r1", "recordType": "FILE", "orgId": "org-1"}, "typ": None}],
}


@pytest.fixture(autouse=True)
def _org() -> Iterator[None]:
    with bind(OrgContext("org-1", None, "job", "job:test", "t")):
        yield


@pytest.mark.parametrize("make", [_neo4j, _arango, _postgres], ids=["neo4j", "arango", "postgres"])
class TestGetRecordByIdRaiseOnError:
    async def test_a_failed_read_answers_none_by_default(self, make) -> None:
        p = make(_Down("graph down"), [])
        assert await p.get_record_by_id("r1") is None
        assert [c for c in p.logger.error.call_args_list if "r1" in str(c)], "the default path keeps its ERROR line"
        # Its own line names the error type, not the driver text (Arango's shared execute_query line is a known limitation).
        assert not [c for c in p.logger.error.call_args_list if "r1" in str(c) and "graph down" in str(c)]

    async def test_a_failed_read_is_raised_when_asked(self, make) -> None:
        with pytest.raises(_Down, match="graph down"):
            await make(_Down("graph down"), []).get_record_by_id("r1", raise_on_error=True)

    async def test_a_raised_failure_is_left_to_the_caller_to_log(self, make) -> None:
        p = make(_Down("graph down"), [])
        with pytest.raises(_Down):
            await p.get_record_by_id("r1", raise_on_error=True)
        # Arango's shared execute_query keeps its own line; get_record_by_id adds none at WARNING or above.
        calls = [str(c) for level in ("warning", "error", "exception", "critical") for c in getattr(p.logger, level).call_args_list]
        assert not [c for c in calls if "record" in c.lower() or "r1" in c]

    async def test_a_missing_record_is_still_none_when_asked(self, make) -> None:
        assert await make(None, []).get_record_by_id("r1", raise_on_error=True) is None

    async def test_a_record_that_will_not_build_is_still_none_when_asked(self, make, request) -> None:
        backend = request.node.callspec.id
        assert await make(None, _UNBUILDABLE[backend]).get_record_by_id("r1", raise_on_error=True) is None

    async def test_a_build_failure_line_quotes_nothing_from_the_type_document(self, make, request) -> None:
        """The builders' errors embed the whole type document, which can carry names and URLs."""
        type_doc = {"_key": "r1", "id": "r1", "name": "Plan of Jane Roe"}
        rows = {
            "neo4j": [{"record": {"id": "r1", "recordType": "DRIVE", "orgId": "org-1"}, "typeDoc": type_doc}],
            "arango": [{"record": {"_key": "r1", "recordType": "DRIVE", "orgId": "org-1"}, "typeDoc": type_doc}],
            "postgres": [{"rec": {"id": "r1", "recordType": "DRIVE", "orgId": "org-1"}, "typ": type_doc}],
        }[request.node.callspec.id]
        p = make(None, rows)
        assert await p.get_record_by_id("r1") is None
        calls = [str(c) for level in ("warning", "error", "exception") for c in getattr(p.logger, level).call_args_list]
        assert calls, "a record that will not build is still logged"
        assert not [c for c in calls if "Jane" in c]
