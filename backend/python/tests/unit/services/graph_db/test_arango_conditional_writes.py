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


class TestBoundedByTheStoreClock:
    """With ``clock_at_most`` the write runs in a stream transaction of its own; it must end that transaction on every path, or ArangoDB keeps it
    (and the document's lock) until its idle timeout."""

    @staticmethod
    def _client(p: ArangoHTTPProvider, *, late: bool = False) -> None:
        p.http_client.begin_transaction = AsyncMock(return_value="t1")
        p.http_client.execute_aql = AsyncMock(side_effect=[[1], [late]])
        p.http_client.commit_transaction = AsyncMock()
        p.http_client.abort_transaction = AsyncMock()

    async def test_a_write_within_the_bound_commits_and_one_past_it_is_aborted(self, provider) -> None:
        p, _ = provider
        self._client(p)
        assert await p.update_node_fields_if_match("k", "apps", {"a": 1}, {"a": None}, clock_at_most=1.0) is True
        p.http_client.commit_transaction.assert_awaited_once_with("t1")
        p.http_client.abort_transaction.assert_not_awaited()
        self._client(p, late=True)
        assert await p.update_node_fields_if_match("k", "apps", {"a": 1}, {"a": None}, clock_at_most=1.0) is False
        p.http_client.commit_transaction.assert_not_awaited()
        p.http_client.abort_transaction.assert_awaited_once_with("t1")

    async def test_a_commit_that_raises_aborts_the_transaction(self, provider) -> None:
        p, _ = provider
        self._client(p)
        p.http_client.commit_transaction = AsyncMock(side_effect=RuntimeError("connection reset"))
        with pytest.raises(RuntimeError, match="connection reset"):
            await p.update_node_fields_if_match("k", "apps", {"a": 1}, {"a": None}, clock_at_most=1.0)
        p.http_client.abort_transaction.assert_awaited_once_with("t1")

    async def test_a_cancel_while_the_commit_waits_still_aborts_the_transaction(self, provider) -> None:
        import asyncio

        p, _ = provider
        self._client(p)
        waiting = asyncio.Event()

        async def stuck(txn: str) -> None:
            waiting.set()
            await asyncio.Event().wait()            # a commit waiting for a connection
        p.http_client.commit_transaction = AsyncMock(side_effect=stuck)
        task = asyncio.ensure_future(p.update_node_fields_if_match("k", "apps", {"a": 1}, {"a": None}, clock_at_most=1.0))
        await waiting.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        p.http_client.abort_transaction.assert_awaited_once_with("t1")

    async def test_an_abort_that_fails_does_not_hide_the_answer(self, provider) -> None:
        p, _ = provider
        self._client(p, late=True)
        p.http_client.abort_transaction = AsyncMock(side_effect=RuntimeError("gone"))
        assert await p.update_node_fields_if_match("k", "apps", {"a": 1}, {"a": None}, clock_at_most=1.0) is False
