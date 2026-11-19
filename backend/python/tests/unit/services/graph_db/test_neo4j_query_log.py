"""Neo4jClient's db.query records: one per call, outside the conflict and reconnect retries; the driver's text (which can
name a parameter's value) never logged; our own operator hints kept."""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j.exceptions import ClientError, Neo4jError, ServiceUnavailable

from app.exceptions.graph_db_exceptions import GraphMemoryLimitError
from app.services.graph_db.neo4j import neo4j_client as nc
from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
from app.utils import error_class as EC
from app.utils.logger import log_record_fields

if TYPE_CHECKING:
    from collections.abc import Iterator

EMAIL = "alice@example.com"


class _Capture(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.records: list[logging.LogRecord] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.records.append(record)


@pytest.fixture
def capture() -> Iterator[tuple[logging.Logger, list[logging.LogRecord]]]:
    logger = logging.getLogger("test.neo4j_query_log")
    handler = _Capture()
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False
    yield logger, handler.records
    logger.removeHandler(handler)
    logger.setLevel(logging.NOTSET)
    logger.propagate = True


def _client(logger: logging.Logger, autocommit: AsyncMock) -> Neo4jClient:
    client = Neo4jClient(uri="bolt://localhost:7687", username="neo4j", password="pw", database="neo4j", logger=logger)
    client.driver = MagicMock()
    client._run_autocommit = autocommit  # type: ignore[method-assign]
    return client


def _neo4j(code: str, message: str) -> Neo4jError:
    return Neo4jError._hydrate_neo4j(code=code, message=message)


async def provider_upsert(client: Neo4jClient) -> object:
    return await client.execute_query("MERGE (p:Person {id: $id}) SET p.email = $email", {"id": "p1", "email": EMAIL})


def _db(records: list[logging.LogRecord]) -> list[logging.LogRecord]:
    return [r for r in records if r.getMessage().startswith("db.query.")]


class TestExecuteQuery:
    async def test_constraint_violation_one_conflict_record_without_the_value(self, capture) -> None:
        logger, records = capture
        error = _neo4j("Neo.ClientError.Schema.ConstraintValidationFailed",
                       f"Node(25) already exists with label `Person` and properties `email` = '{EMAIL}'")
        client = _client(logger, AsyncMock(side_effect=error))
        with pytest.raises(Neo4jError) as raised:
            await provider_upsert(client)
        (record,) = _db(records)
        fields = log_record_fields(record)
        assert record.levelno == logging.WARNING and fields["error_class"] == "conflict" and fields["backend"] == "neo4j"
        assert fields["code"] == "Neo.ClientError.Schema.ConstraintValidationFailed" and fields["op"] == "execute_query"
        assert fields["caller"] == "test_neo4j_query_log.provider_upsert" and record.funcName == "provider_upsert"
        assert not [r for r in records if EMAIL in r.getMessage() or EMAIL in str(vars(r))]
        records.clear()
        EC.log_failure(logger, "sync_run.end", raised.value)  # main: internal, ERROR, the email in error_message and the stack
        (line,) = records
        assert line.levelno == logging.WARNING and EMAIL not in str(vars(line))

    @pytest.mark.parametrize(("error", "cls", "level"), [
        (_neo4j("Neo.ClientError.Transaction.TransactionTimedOutClientConfiguration", "terminated"), "timeout", logging.WARNING),
        (_neo4j("Neo.ClientError.Security.Unauthorized", "bad credentials"), "misconfigured", logging.ERROR),
        (_neo4j("Neo.ClientError.Statement.SyntaxError", "Invalid input 'RETRN'"), "internal", logging.ERROR),
        (ClientError._hydrate_neo4j(code="Neo.ClientError.Statement.EntityNotFound", message="Unable to load NODE 4:1"),
         "conflict", logging.WARNING),
    ])
    async def test_classes(self, capture, error: Exception, cls: str, level: int) -> None:
        logger, records = capture
        with pytest.raises(Neo4jError):
            await provider_upsert(_client(logger, AsyncMock(side_effect=error)))
        (record,) = _db(records)
        assert log_record_fields(record)["error_class"] == cls and record.levelno == level

    async def test_lost_connection_retried_on_the_pool_is_a_retry_not_a_failure(self, capture) -> None:
        logger, records = capture
        client = _client(logger, AsyncMock(side_effect=[ServiceUnavailable("connection reset by 10.0.0.1"), [{"ok": 1}]]))
        assert await provider_upsert(client) == [{"ok": 1}]
        (retry,) = [r for r in records if r.getMessage() == "db.query.retry"]
        fields = log_record_fields(retry)
        assert retry.levelno == logging.WARNING and fields["reason"] == "connection_lost"
        assert fields["error_type"] == "ServiceUnavailable" and "10.0.0.1" not in str(vars(retry))
        assert _db(records) == [retry]

    async def test_bulk_memory_limit_is_one_error_with_our_hint_kept(self, capture) -> None:
        logger, records = capture
        error = nc._memory_limit_error("group replace", 10_000)
        client = _client(logger, AsyncMock(side_effect=error))
        with pytest.raises(GraphMemoryLimitError) as raised:
            await provider_upsert(client)
        (record,) = _db(records)  # main logged its own ERROR line (:111) instead
        fields = log_record_fields(record)
        assert record.levelno == logging.ERROR and fields["error_class"] == "misconfigured"
        assert fields["bulk"] == "group replace" and fields["size"] == 10_000
        records.clear()
        EC.log_failure(logger, "sync_run.end", raised.value)
        assert "NEO4J_HEAP_MAX" in records[0].error_message

    async def test_not_connected_is_unavailable_and_keeps_its_text(self, capture) -> None:
        logger, records = capture
        client = Neo4jClient(uri="bolt://localhost:7687", username="neo4j", password="pw", database="neo4j", logger=logger)
        with patch.object(client, "connect", AsyncMock(return_value=False)), pytest.raises(RuntimeError) as raised:
            await provider_upsert(client)
        (record,) = _db(records)
        assert log_record_fields(record)["error_class"] == "unavailable"
        records.clear()
        EC.log_failure(logger, "x.end", raised.value)
        assert "not connected" in records[0].error_message


class TestConnect:
    def test_no_username_at_info(self, capture) -> None:
        logger, records = capture
        Neo4jClient(uri="bolt://db:7687", username="graph-admin", password="pw", database="neo4j", logger=logger)
        assert records and not [r for r in records if "graph-admin" in r.getMessage()]


def test_describe_data_exception_by_gql_status() -> None:
    error = Neo4jError._hydrate_gql(gql_status="22N36", message=f"Cannot parse '{EMAIL}' as a Date", description="d",
                                    diagnostic_record={"_classification": "CLIENT_ERROR"})
    code, cls, extra, hide = nc.describe_neo4j_error(error)
    assert cls == "bad_request" and hide is True and extra == {}
