"""Conditional writes on ArangoDB retry a write-write conflict with backoff and never report one as a result."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.graph_db.arango import arango_http_provider as module
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

CONFLICT = RuntimeError('Query failed (status=409): {"errorMessage":"write-write conflict","errorNum":1200}')


@pytest.fixture
def provider(monkeypatch: pytest.MonkeyPatch) -> tuple[ArangoHTTPProvider, AsyncMock]:
    sleep = AsyncMock()
    monkeypatch.setattr(module.asyncio, "sleep", sleep)
    p = ArangoHTTPProvider(logger=MagicMock(spec=logging.Logger), config_service=MagicMock())
    p.http_client = AsyncMock()
    return p, sleep


class TestCreateNodeIfAbsent:
    async def test_a_conflict_is_retried_with_backoff_and_the_insert_can_land(self, provider) -> None:
        p, sleep = provider
        p.http_client.execute_aql = AsyncMock(side_effect=[CONFLICT, CONFLICT, [{"_key": "k"}]])

        assert await p.create_node_if_absent("k", "c", {"a": 1}) is True
        assert p.http_client.execute_aql.await_count == 3
        assert sleep.await_count == 2

    async def test_a_retry_that_finds_the_key_reports_not_created(self, provider) -> None:
        p, _ = provider
        p.http_client.execute_aql = AsyncMock(side_effect=[CONFLICT, []])

        assert await p.create_node_if_absent("k", "c", {"a": 1}) is False

    async def test_a_conflict_that_persists_is_raised_not_reported_as_existing(self, provider) -> None:
        p, _ = provider
        p.http_client.execute_aql = AsyncMock(side_effect=CONFLICT)

        with pytest.raises(RuntimeError, match="1200"):
            await p.create_node_if_absent("k", "c", {"a": 1})
        assert p.http_client.execute_aql.await_count == module._WRITE_CONFLICT_ATTEMPTS

    async def test_a_conflict_inside_a_transaction_is_raised_at_once(self, provider) -> None:
        p, _ = provider
        p.http_client.execute_aql = AsyncMock(side_effect=CONFLICT)

        with pytest.raises(RuntimeError):
            await p.create_node_if_absent("k", "c", {"a": 1}, transaction="txn")
        assert p.http_client.execute_aql.await_count == 1


class TestUpdateNodeFieldsIfMatch:
    async def test_a_conflict_is_retried_with_backoff(self, provider) -> None:
        p, sleep = provider
        p.http_client.execute_aql = AsyncMock(side_effect=[CONFLICT, [1]])

        assert await p.update_node_fields_if_match("k", "c", {"x": 1}, {"y": 2}) is True
        assert sleep.await_count == 1

    async def test_the_attempts_are_bounded_and_other_errors_are_not_retried(self, provider) -> None:
        p, _ = provider
        p.http_client.execute_aql = AsyncMock(side_effect=CONFLICT)
        with pytest.raises(RuntimeError):
            await p.update_node_fields_if_match("k", "c", {"x": 1}, {"y": 2})
        assert p.http_client.execute_aql.await_count == module._WRITE_CONFLICT_ATTEMPTS

        p.http_client.execute_aql = AsyncMock(side_effect=ValueError("boom"))
        with pytest.raises(ValueError):
            await p.update_node_fields_if_match("k", "c", {"x": 1}, {"y": 2})
        assert p.http_client.execute_aql.await_count == 1
