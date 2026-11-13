"""db.query records on real ArangoDB and Neo4j: one per failed or slow call with the backend's code and class, and no bound value
(the seeded email) in any record, even where the server's own message names it (a Neo4j constraint violation).

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import logging
import os
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.logger import log_record_fields

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
EMAIL = "alice.p9a@example.com"
LOGGER = "test.query_log_it"
PEOPLE = CollectionNames.PEOPLE.value


@pytest.fixture(params=["neo4j", "arango"])
async def backend(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[str, object]]:
    logger = logging.getLogger(LOGGER)
    if request.param == "neo4j":
        monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
        monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
        monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
        monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
        provider = Neo4jProvider(logger, MagicMock())
        where = NEO4J_URI
    else:
        config = MagicMock()
        config.get_config = AsyncMock(return_value={"url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD,
                                                     "db": "query_log_it"})
        provider = ArangoHTTPProvider(logger, config)
        where = ARANGO_URL
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("connect returned False")
        await provider.ensure_schema()
    except Exception as exc:
        pytest.skip(f"{request.param} not available at {where}: {exc}")
    try:
        yield request.param, provider
    finally:
        await provider.disconnect()


def _records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.getMessage().startswith("db.query.")]


def _people() -> list[dict]:
    base = uuid.uuid4().hex[:10]
    org = uuid.uuid4().hex[:24]
    return [{"_key": f"qlog-{base}-{i}", "orgId": org, "email": EMAIL, "fullName": "Alice", "createdAtTimestamp": 1,
             "updatedAtTimestamp": 1, "sourceKey": f"src-{base}-{i}"} for i in range(2)]


async def _query(name: str, provider: object, query: str, params: dict, **kw: object) -> object:
    if name == "neo4j":
        return await provider.client.execute_query(query, params, **kw)
    return await provider.http_client.execute_aql(query, params, **kw)


async def test_unique_violation_is_one_conflict_record_without_the_value(backend, caplog) -> None:
    name, provider = backend
    with caplog.at_level(logging.DEBUG, logger=LOGGER), pytest.raises(Exception):  # noqa: B017 (the driver's own type differs per backend)
        await provider.batch_upsert_nodes(_people(), PEOPLE)
    (record,) = _records(caplog)
    fields = log_record_fields(record)
    assert fields["backend"] == name and fields["error_class"] == "conflict" and record.levelno == logging.WARNING
    assert fields["code"] == (1210 if name == "arango" else "Neo.ClientError.Schema.ConstraintValidationFailed")
    assert fields["caller"].endswith(".batch_upsert_nodes")
    assert not [r for r in caplog.records if EMAIL in r.getMessage() or EMAIL in str(vars(r))]


async def test_syntax_error_is_internal(backend, caplog) -> None:
    name, provider = backend
    query = ("MATCH (u:Person) WHERE u.email = $email RETRN u" if name == "neo4j"
             else f"FOR u IN {PEOPLE} FILTER u.email == @email RETRN u")
    with caplog.at_level(logging.DEBUG, logger=LOGGER), pytest.raises(Exception):  # noqa: B017
        await _query(name, provider, query, {"email": EMAIL})
    (record,) = _records(caplog)
    fields = log_record_fields(record)
    assert fields["error_class"] == "internal" and record.levelno == logging.ERROR
    assert fields["code"] == (1501 if name == "arango" else "Neo.ClientError.Statement.SyntaxError")


async def test_server_side_timeout(backend, caplog) -> None:
    name, provider = backend
    with caplog.at_level(logging.DEBUG, logger=LOGGER), pytest.raises(Exception):  # noqa: B017
        if name == "neo4j":
            await provider.client.execute_query(
                "UNWIND range(1, $n) AS x WITH x WHERE x % 7 = 0 RETURN count(*) AS c", {"n": 200_000_000}, timeout=0.3)
        else:
            await provider.http_client.execute_aql("RETURN SLEEP(3)", max_runtime=0.3)
    (record,) = _records(caplog)
    assert log_record_fields(record)["error_class"] == "timeout"


async def test_slow_call(backend, caplog, monkeypatch) -> None:
    name, provider = backend
    monkeypatch.setenv("DB_SLOW_QUERY_MS", "50")
    query = ("UNWIND range(1, $n) AS x WITH x WHERE x % 7 = 0 RETURN count(*) AS c" if name == "neo4j"
             else "RETURN SLEEP(@s)")
    params = {"n": 3_000_000} if name == "neo4j" else {"s": 0.2}
    with caplog.at_level(logging.DEBUG, logger=LOGGER):
        await _query(name, provider, query, params)
    (record,) = _records(caplog)
    fields = log_record_fields(record)
    assert record.getMessage() == "db.query.slow" and fields["threshold_ms"] == 50 and fields["duration_ms"] >= 50
    assert fields["caller"] == "test_query_log_real_backends._query"
