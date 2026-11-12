"""ArangoHTTPClient's db.query.failed / db.query.slow records: one per call, outside the client's retries, with errorNum and
error_class, never the server's text; exception types and messages unchanged (retry decisions read them)."""
from __future__ import annotations

import json
import logging
import types
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.connectors.sources.localKB.push.retry import is_attempt_lost
from app.exceptions.graph_db_exceptions import GraphQueryError
from app.services.graph_db.arango import arango_http_client as ahc
from app.services.graph_db.arango.arango_http_client import (
    ArangoHTTPClient,
    ArangoQueryError,
    is_write_conflict,
)
from app.utils import db_query_log as dql
from app.utils import error_class as EC
from app.utils.logger import log_record_fields

if TYPE_CHECKING:
    from collections.abc import Iterator

# As ArangoDB 3.12 answers (test_arango_purge_locks.py, checked against a real server).
LOCK_TIMEOUT_BODY = ('{"code":500,"error":true,"errorMessage":"timed out after 1 s waiting for exclusive-lock on collection '
                     'soft_delete_it/recordRelations on single","errorNum":18}')
BEGIN_CONFLICT_BODY = '{"code":409,"error":true,"errorNum":1200}'


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture() -> Iterator[tuple[logging.Logger, list[logging.LogRecord]]]:
    logger = logging.getLogger("test.arango_query_log")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, handler.records
    logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ahc.random, "uniform", lambda a, b: 0.0)


class Resp:
    def __init__(self, status: int, body: object = None) -> None:
        self.status, self._body = status, body

    async def json(self) -> object:
        return self._body if not isinstance(self._body, str) else json.loads(self._body)

    async def text(self) -> str:
        return self._body if isinstance(self._body, str) else json.dumps(self._body)

    async def __aenter__(self) -> Resp:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


def _client(logger: logging.Logger) -> ArangoHTTPClient:
    return ArangoHTTPClient("http://localhost:8529", "root", "pw", "db", logger)


def _session(**methods: object) -> MagicMock:
    session = MagicMock()
    for name, value in methods.items():
        if isinstance(value, list):
            getattr(session, name).side_effect = value
        else:
            getattr(session, name).return_value = value
    return session


def _db_records(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if r.getMessage().startswith("db.query.")]


async def provider_query(client: ArangoHTTPClient, **kwargs: object) -> object:
    return await client.execute_aql("FOR d IN people RETURN d", {"email": "alice@example.com"}, **kwargs)


def _body(status: int, num: int, message: str = "m") -> str:
    return json.dumps({"code": status, "error": True, "errorMessage": message, "errorNum": num})


class TestExecuteAql:
    @pytest.mark.parametrize(("status", "num", "cls", "level"), [
        (409, 1200, "conflict", logging.WARNING), (400, 1501, "internal", logging.ERROR),
        (410, 1500, "timeout", logging.WARNING), (404, 1203, "internal", logging.ERROR),
        (500, 1654, "conflict", logging.WARNING),
    ])
    async def test_one_record_with_code_and_class(self, capture, status: int, num: int, cls: str, level: int) -> None:
        logger, records = capture
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(post=Resp(status, _body(status, num)))):
            with pytest.raises(ArangoQueryError) as raised:
                await provider_query(client, txn_id="t1")
        (record,) = records
        fields = log_record_fields(record)
        assert record.getMessage() == "db.query.failed" and record.levelno == level
        assert fields["backend"] == "arango" and fields["op"] == "execute_aql" and fields["code"] == num
        assert fields["status"] == status and fields["error_class"] == cls
        assert fields["caller"] == "test_arango_query_log.provider_query" and record.funcName == "provider_query"
        assert "errorMessage" not in record.getMessage() and "alice" not in str(vars(record))
        assert EC.classify_error(raised.value) == cls

    async def test_a_bracket_in_the_message_does_not_win_over_errorNum(self, capture) -> None:
        logger, records = capture
        body = _body(409, 1200, "AQL: write-write conflict near 'x[0]' [node #2]")
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(post=Resp(409, body))):
            with pytest.raises(ArangoQueryError) as raised:
                await provider_query(client, txn_id="t1")
        fields = log_record_fields(records[0])
        assert fields["code"] == 1200 and fields["error_class"] == "conflict"
        assert is_write_conflict(raised.value) and is_write_conflict(Exception(f"Query failed (status=409): {body}"))

    async def test_conflict_the_client_retries_and_wins_writes_nothing(self, capture) -> None:
        logger, records = capture
        conflict = Resp(409, _body(409, 1200))
        ok = Resp(201, {"result": [1], "hasMore": False})
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(post=[conflict, conflict, ok])):
            assert await provider_query(client) == [1]
        assert _db_records(records) == [] and not [r for r in records if r.levelno >= logging.ERROR]

    async def test_conflict_that_exhausts_the_retries_writes_one(self, capture) -> None:
        logger, records = capture
        client = _client(logger)
        attempts = [Resp(409, _body(409, 1200)) for _ in range(ahc._WRITE_CONFLICT_ATTEMPTS)]
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(post=attempts)):
            with pytest.raises(ArangoQueryError):
                await provider_query(client)
        assert len(_db_records(records)) == 1

    async def test_slow_success(self, capture, monkeypatch: pytest.MonkeyPatch) -> None:
        logger, records = capture
        ticks = iter([0.0, 0.6, 0.6])
        monkeypatch.setattr(dql, "time", types.SimpleNamespace(perf_counter=lambda: next(ticks)))
        monkeypatch.setenv("DB_SLOW_QUERY_MS", "500")
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock,
                          return_value=_session(post=Resp(201, {"result": [], "hasMore": False}))):
            await provider_query(client)
        (record,) = records
        assert record.getMessage() == "db.query.slow" and log_record_fields(record)["duration_ms"] == 600

    async def test_handler_line_has_the_class_and_no_server_text(self, capture) -> None:
        logger, records = capture
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock,
                          return_value=_session(post=Resp(410, _body(410, 1500, "AQL: query killed")))):
            with pytest.raises(ArangoQueryError) as raised:
                await provider_query(client, txn_id="t1")
        records.clear()
        EC.log_failure(logger, "sync_run.end", raised.value)
        (line,) = records
        fields = log_record_fields(line)
        assert fields["error_class"] == "timeout" and "error_message" not in fields


class TestDeleteEdge:
    async def test_failed_lookup_is_one_query_record_and_a_text_free_edge_line(self, capture) -> None:
        logger, records = capture
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock,
                          return_value=_session(post=Resp(500, _body(500, 4, "AQL: bind value alice@example.com")))):
            assert await client.delete_edge("people/a", "people/b", "knows") is False
        assert [r.getMessage() for r in records] == ["db.query.failed", "db.edge.failed"]
        fields = log_record_fields(records[1])
        assert fields["op"] == "delete_edge" and fields["error_class"] and "error_message" not in fields
        assert all("alice@example.com" not in (r.getMessage() + str(log_record_fields(r))) for r in records)


class TestTransactions:
    async def test_lock_timeout_on_begin_is_a_timeout_not_internal(self, capture) -> None:
        logger, records = capture
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(post=Resp(500, LOCK_TIMEOUT_BODY))):
            with pytest.raises(Exception, match="Failed to begin transaction") as raised:
                await client.begin_transaction(read=[], write=["records"], exclusive=["records"])
        (record,) = records
        fields = log_record_fields(record)
        assert type(raised.value) is Exception  # unchanged type: the push loop and the purge locks read it
        assert fields["op"] == "begin_transaction" and fields["code"] == 18 and fields["status"] == 500
        assert fields["error_class"] == "timeout" and record.levelno == logging.WARNING

    async def test_begin_conflict(self, capture) -> None:
        logger, records = capture
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(post=Resp(409, BEGIN_CONFLICT_BODY))):
            with pytest.raises(Exception, match="Failed to begin transaction"):
                await client.begin_transaction(read=[], write=["records"])
        assert log_record_fields(records[0])["error_class"] == "conflict"


class TestBatchInsert:
    async def test_item_error_one_record_and_the_message_is_unchanged(self, capture) -> None:
        logger, records = capture
        reply = [{"error": True, "errorNum": 1210, "errorMessage": "unique constraint violated; conflicting key: k1"}]
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(post=Resp(202, reply))):
            with pytest.raises(Exception) as raised:
                await client.batch_insert_documents("people", [{"_key": "k2", "email": "alice@example.com"}])
        assert str(raised.value) == ("Batch insert failed with 1 error(s): Item 0: [1210] unique constraint violated; "
                                     "conflicting key: k1")
        assert set(vars(raised.value)) == {"log_error_class", "log_hide_text"}
        (record,) = records  # main wrote 2 ERROR lines here (:269 and :779)
        fields = log_record_fields(record)
        assert fields["op"] == "batch_insert_documents" and fields["code"] == 1210 and fields["error_class"] == "conflict"

    async def test_mixed_batch_stays_a_lost_attempt_after_stamping(self, capture) -> None:
        logger, records = capture
        reply = [{"error": True, "errorNum": 1210, "errorMessage": "dup"},
                 {"error": True, "errorNum": 1200, "errorMessage": "timeout waiting to lock key"}]
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(post=Resp(202, reply))):
            with pytest.raises(Exception) as raised:
                await client.batch_insert_documents("people", [{"_key": "a"}, {"_key": "b"}], txn_id="t1")
        assert is_attempt_lost(raised.value)
        fields = log_record_fields(_db_records(records)[0])
        assert fields["code"] == 1210 and fields["error_class"] == "conflict"

    async def test_no_reply_dump_at_debug(self, capture) -> None:
        logger, records = capture
        reply = [{"_id": "people/k", "_key": "k", "_rev": "_rev1"}]
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(post=Resp(202, reply))):
            await client.batch_insert_documents("people", [{"_key": "k"}])
        assert not [r for r in records if "_rev" in r.getMessage()]


class TestGetDocument:
    async def test_server_error_without_raise_is_none_and_one_record(self, capture) -> None:
        logger, records = capture
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(get=Resp(503, "restarting"))):
            assert await client.get_document("people", "k") is None
        (record,) = records
        fields = log_record_fields(record)
        assert fields["op"] == "get_document" and fields["status"] == 503 and fields["error_class"] == "unavailable"

    async def test_server_error_with_raise_keeps_graph_query_error(self, capture) -> None:
        logger, records = capture
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(get=Resp(503, "restarting"))):
            with pytest.raises(GraphQueryError, match="ArangoDB answered 503") as raised:
                await client.get_document("people", "k", raise_on_error=True)
        assert len(_db_records(records)) == 1
        assert raised.value.__cause__ is None and raised.value.__suppress_context__ is False  # not chained: the push retry walks causes
        assert EC.classify_error(raised.value) == "unavailable"  # main: internal, so the handler logged ERROR with a stack
        assert not getattr(raised.value, "log_hide_text", False)  # our own text, no driver text: kept
        records.clear()
        EC.log_failure(logging.getLogger("test.arango_query_log"), "sync_run.end", raised.value)
        (line,) = records
        assert line.levelno == logging.WARNING and "ArangoDB answered 503" in line.error_message

    async def test_missing_collection_is_recorded_but_still_none(self, capture) -> None:
        logger, records = capture
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(get=Resp(404, _body(404, 1203)))):
            assert await client.get_document("no_such", "k") is None
        fields = log_record_fields(records[0])
        assert fields["code"] == 1203 and fields["error_class"] == "internal"

    @pytest.mark.parametrize(("status", "body"), [(200, {"_key": "k"}), (404, _body(404, 1202))])
    @pytest.mark.parametrize(("elapsed", "slow"), [(0.6, True), (0.1, False)])
    async def test_a_lookup_writes_a_slow_record_only_when_slow(self, capture, monkeypatch: pytest.MonkeyPatch,
                                                                status: int, body: object, elapsed: float, slow: bool) -> None:
        logger, records = capture
        monkeypatch.setattr(ahc, "time", types.SimpleNamespace(perf_counter=lambda: 0.0))
        monkeypatch.setattr(dql, "time", types.SimpleNamespace(perf_counter=lambda: elapsed))
        monkeypatch.setenv("DB_SLOW_QUERY_MS", "500")
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(get=Resp(status, body))):
            await client.get_document("people", "k")
        assert [r.getMessage() for r in records] == (["db.query.slow"] if slow else [])
        if slow:
            fields = log_record_fields(records[0])
            assert fields["op"] == "get_document" and fields["duration_ms"] == 600 and "error_class" not in fields

    @pytest.mark.parametrize("body", [_body(404, 1202), ""])
    async def test_absent_document_writes_nothing(self, capture, body: str) -> None:
        logger, records = capture
        client = _client(logger)
        with patch.object(client, "_get_session", new_callable=AsyncMock, return_value=_session(get=Resp(404, body))):
            assert await client.get_document("people", "k") is None
        assert records == []


@pytest.mark.parametrize(("error", "expected"), [
    (Exception("Batch delete failed with 1 error(s): Document people/a: [1200] conflict"), (1200, "conflict", None)),
    (Exception('Failed to create document (status=409): {"errorNum":1210,"errorMessage":"dup"}'), (1210, "conflict", 409)),
    (Exception("Batch delete failed: HTTP 503 - down"), (None, "unavailable", 503)),
    (Exception("Batch insert failed with 1 error(s): Item 0: [1620] Document does not match"), (1620, "internal", None)),
    (Exception("see blocks[18] in the payload"), (None, "internal", None)),
    (ArangoQueryError("Query failed", 401, "unauthorized"), (None, "misconfigured", 401)),
])
def test_describe(error: Exception, expected: tuple) -> None:
    code, cls, extra, hide = ahc.describe_arango_error(error)
    assert (code, cls, extra.get("status")) == expected and hide is True
