"""A full connection pool is recognised as that, whichever class the driver raises it as.

Driver 6 raises ``ConnectionAcquisitionTimeoutError`` (a ``DriverError``), driver 5 raised a ``ClientError``. Only the
latter was recognised, so after the upgrade a full pool was logged as an internal error and the concurrency
governor was never told to slow down."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import neo4j.exceptions
import pytest
from neo4j.exceptions import ClientError, DriverError, ServiceUnavailable

from app.services.graph_db.neo4j import neo4j_client as module
from app.services.graph_db.neo4j.neo4j_client import Neo4jClient, describe_neo4j_error

MESSAGE = "failed to obtain a connection from the pool within 30.0s (timeout)"
FULL_POOL = [neo4j.exceptions.ConnectionAcquisitionTimeoutError(MESSAGE), ClientError(MESSAGE)]


@pytest.mark.parametrize("error", FULL_POOL, ids=lambda e: type(e).__name__)
def test_it_is_classed_as_unavailable(error: Exception) -> None:
    assert describe_neo4j_error(error)[1] == "unavailable"


@pytest.mark.parametrize("error", [DriverError("misuse of the driver"), ClientError("a bad query")], ids=lambda e: type(e).__name__)
def test_another_driver_or_client_error_is_not(error: Exception) -> None:
    assert describe_neo4j_error(error)[1] != "unavailable"


def _client(error: Exception, *, in_transaction: bool) -> Neo4jClient:
    client = Neo4jClient(uri="bolt://x", username="u", password="p", database="d", logger=logging.getLogger("pool-test"))
    session = MagicMock()
    session.run = AsyncMock(side_effect=error)
    if in_transaction:
        client._active_sessions["t1"] = session
    driver = MagicMock()
    driver.session.return_value.__aenter__ = AsyncMock(return_value=session)
    driver.session.return_value.__aexit__ = AsyncMock(return_value=False)
    client._driver_override = driver
    return client


@pytest.mark.parametrize("in_transaction", [False, True], ids=["auto-commit", "transaction"])
@pytest.mark.parametrize("error", FULL_POOL, ids=lambda e: type(e).__name__)
async def test_the_governor_is_told_once(error: Exception, in_transaction: bool) -> None:
    feedback = MagicMock()
    client = _client(error, in_transaction=in_transaction)

    with patch.object(module, "get_default_downstream_feedback", return_value=feedback), pytest.raises(type(error)):
        await client.execute_query("RETURN 1", txn_id="t1" if in_transaction else None)

    feedback.report_pool_exhausted.assert_called_once_with("neo4j")
    feedback.report_unavailable.assert_not_called()


async def test_a_lost_server_is_still_reported_as_unavailable() -> None:
    feedback = MagicMock()
    client = _client(ServiceUnavailable("gone"), in_transaction=True)

    with patch.object(module, "get_default_downstream_feedback", return_value=feedback), pytest.raises(ServiceUnavailable):
        await client.execute_query("RETURN 1", txn_id="t1")

    feedback.report_unavailable.assert_called_once_with("neo4j")
    feedback.report_pool_exhausted.assert_not_called()


@pytest.mark.parametrize("error", FULL_POOL, ids=lambda e: type(e).__name__)
async def test_a_transaction_that_cannot_start_tells_it_too(error: Exception) -> None:
    feedback = MagicMock()
    client = _client(error, in_transaction=False)
    session = MagicMock()
    session.begin_transaction = AsyncMock(side_effect=error)
    session.close = AsyncMock()
    client._driver_override.session = MagicMock(return_value=session)

    with patch.object(module, "get_default_downstream_feedback", return_value=feedback), pytest.raises(type(error)):
        await client.begin_transaction([], [], explicit=True)

    feedback.report_pool_exhausted.assert_called_once_with("neo4j")
    session.close.assert_awaited_once()
