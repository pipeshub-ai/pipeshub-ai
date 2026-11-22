"""A graph client's connect and disconnect failures carry no driver text (docs/logging.md, "Database queries").

The driver's message names the host and port it could not reach; the line keeps its class and type instead.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j.exceptions import ServiceUnavailable

from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.neo4j.neo4j_client import Neo4jClient
from app.utils.logger import log_record_fields
from tests.support.log_capture import assert_not_logged

LOGGER = "test.graph_client_connect"
# Built at run time: a traceback prints source lines, so a literal there would show in the stack.
SENTINEL = "-".join(("SENTINEL", "driver-text")) + " neo4j-7.internal:7687 bob@" + "example.com"


@pytest.fixture
def records(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    caplog.set_level(logging.DEBUG, logger=LOGGER)
    return caplog


def _event(caplog: pytest.LogCaptureFixture, event: str) -> dict[str, object]:
    ours = [r for r in caplog.records if r.name == LOGGER]
    (line,) = [r for r in ours if r.getMessage() == event]
    fields = log_record_fields(line)
    assert "error_message" not in fields
    assert_not_logged(ours, SENTINEL)
    return fields


def _neo4j() -> Neo4jClient:
    return Neo4jClient(uri="bolt://localhost:7687", username="neo4j", password="pw", database="neo4j",
                       logger=logging.getLogger(LOGGER))


def _driver(*, reachable: bool) -> MagicMock:
    driver = MagicMock()
    driver.verify_connectivity = AsyncMock(side_effect=None if reachable else ServiceUnavailable(SENTINEL))
    driver.get_server_info = AsyncMock(return_value=MagicMock(agent="Neo4j/5"))
    driver.close = AsyncMock()
    return driver


async def test_a_neo4j_rebuild_whose_reconnect_fails_logs_no_driver_text(records) -> None:
    client = _neo4j()
    stale, unreachable = _driver(reachable=True), _driver(reachable=False)
    with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", side_effect=[stale, unreachable]), \
            patch.object(client, "_ensure_database_exists", AsyncMock(return_value=True)):
        assert await client.connect()
        stale.verify_connectivity.side_effect = ServiceUnavailable(SENTINEL)

        with pytest.raises(RuntimeError, match="reconnection failed"):
            await client._rebuild_driver_if_unreachable(ServiceUnavailable(SENTINEL))

    fields = _event(records, "db.connect.failed")
    assert (fields.get("backend"), fields.get("error_type"), fields.get("outcome")) == ("neo4j", "ServiceUnavailable", "failure")
    assert fields.get("error_class")


async def test_a_neo4j_disconnect_that_fails_logs_no_driver_text(records) -> None:
    client = _neo4j()
    with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=_driver(reachable=True)), \
            patch.object(client, "_ensure_database_exists", AsyncMock(return_value=True)):
        assert await client.connect()
    client._close_on_owning_loop = AsyncMock(side_effect=ServiceUnavailable(SENTINEL))

    await client.disconnect()

    assert _event(records, "db.disconnect.failed").get("error_type") == "ServiceUnavailable"


async def test_an_arango_connect_that_fails_logs_no_driver_text(records) -> None:
    client = ArangoHTTPClient(base_url="http://127.0.0.1:1", username="root", password="pw", database="db",
                              logger=logging.getLogger(LOGGER))
    client._get_session = AsyncMock(side_effect=ConnectionError(SENTINEL))

    assert await client.connect() is False

    assert _event(records, "db.connect.failed").get("error_type") == "ConnectionError"
