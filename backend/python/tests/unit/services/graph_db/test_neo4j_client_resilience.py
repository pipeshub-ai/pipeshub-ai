"""How ``Neo4jClient`` behaves when the server misbehaves.

The production cascade this guards against: a rebuilt driver closes every
connection in its pool, including the ones other coroutines are mid-query
on, so rebuilding on the first ``ServiceUnavailable`` turned one dead
connection into a failure for every in-flight record -- and with no cooldown
the next failure did it again. Sessions nobody committed or aborted held a
pool connection until process shutdown.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j.exceptions import ServiceUnavailable

from app.services.graph_db.neo4j.neo4j_client import Neo4jClient

if TYPE_CHECKING:
    from collections.abc import Iterator


class _Transaction:
    def __init__(self, outcomes: list) -> None:
        self._outcomes = outcomes
        self.commit = AsyncMock()
        self.rollback = AsyncMock()
        self.close = AsyncMock()
        self.ran: list[str] = []

    async def run(self, query, parameters=None) -> MagicMock:
        self.ran.append(query)
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        result = MagicMock()
        result.data = AsyncMock(return_value=[{"ok": True}])
        return result


class _Session:
    """An ``AsyncSession`` whose ``run`` fails as scripted, then succeeds."""

    def __init__(self, outcomes: list) -> None:
        self._outcomes = outcomes
        self.close = AsyncMock()
        self.ran: list[str] = []
        self.transactions: list[_Transaction] = []
        self.begin_transaction = AsyncMock(side_effect=self._begin)

    async def _begin(self, timeout=None) -> _Transaction:
        tx = _Transaction(self._outcomes)
        tx.timeout = timeout
        self.transactions.append(tx)
        return tx

    async def __aenter__(self) -> "_Session":
        return self

    async def __aexit__(self, *exc) -> bool:
        await self.close()
        return False

    async def run(self, query, parameters=None) -> MagicMock:
        self.ran.append(query)
        # ``None`` (or an exhausted script) is a success.
        outcome = self._outcomes.pop(0) if self._outcomes else None
        if isinstance(outcome, Exception):
            raise outcome
        result = MagicMock()
        result.data = AsyncMock(return_value=[{"ok": True}])
        return result


def _driver(outcomes: list, *, reachable: bool = True) -> MagicMock:
    driver = MagicMock()
    driver.sessions = []

    def _open(**kw) -> _Session:
        session = _Session(outcomes)
        driver.sessions.append(session)
        return session

    driver.session = MagicMock(side_effect=_open)
    driver.verify_connectivity = (
        AsyncMock() if reachable else AsyncMock(side_effect=ServiceUnavailable("down"))
    )
    driver.get_server_info = AsyncMock(return_value="fake")
    driver.close = AsyncMock()
    return driver


def _client(**kwargs) -> Neo4jClient:
    return Neo4jClient(
        uri="bolt://localhost:7687", username="neo4j", password="pw", database="neo4j",
        logger=MagicMock(), **kwargs,
    )


@pytest.fixture
def ensure_db() -> Iterator[AsyncMock]:
    with patch.object(Neo4jClient, "_ensure_database_exists", AsyncMock()) as ensured:
        yield ensured


class TestReconnect:
    @pytest.mark.asyncio
    async def test_one_dead_connection_is_retried_on_the_same_driver(self, ensure_db) -> None:
        outcomes: list = [ServiceUnavailable("connection reset")]
        built: list = []

        def factory(*a, **kw) -> MagicMock:
            built.append(_driver(outcomes))
            return built[-1]

        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", side_effect=factory):
            client = _client()
            await client.connect()
            rows = await client.execute_query("RETURN 1")

        assert rows == [{"ok": True}]
        assert len(built) == 1
        built[0].close.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_reachable_driver_is_kept_after_two_failures(self, ensure_db) -> None:
        outcomes: list = [ServiceUnavailable("a"), ServiceUnavailable("b")]
        built: list = []

        def factory(*a, **kw) -> MagicMock:
            built.append(_driver(outcomes, reachable=True))
            return built[-1]

        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", side_effect=factory):
            client = _client()
            await client.connect()
            rows = await client.execute_query("RETURN 1")

        assert rows == [{"ok": True}]
        assert len(built) == 1, "a driver that can still reach the server keeps its pool"
        built[0].verify_connectivity.assert_awaited()

    @pytest.mark.asyncio
    async def test_the_driver_is_rebuilt_only_once_unreachable(self, ensure_db) -> None:
        outcomes: list = [ServiceUnavailable("a"), ServiceUnavailable("b")]
        built: list = []

        def factory(*a, **kw) -> MagicMock:
            driver = _driver(outcomes)
            built.append(driver)
            return driver

        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", side_effect=factory):
            client = _client()
            await client.connect()
            built[0].verify_connectivity = AsyncMock(side_effect=ServiceUnavailable("gone"))
            rows = await client.execute_query("RETURN 1")

        assert rows == [{"ok": True}]
        assert len(built) == 2
        built[0].close.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_rebuilds_are_rate_limited(self, ensure_db) -> None:
        # Two failures, a success on the rebuilt driver, then two more failures.
        outcomes: list = [
            ServiceUnavailable("a"), ServiceUnavailable("a"), None,
            ServiceUnavailable("a"), ServiceUnavailable("a"),
        ]
        built: list = []

        def factory(*a, **kw) -> MagicMock:
            driver = _driver(outcomes, reachable=True)
            built.append(driver)
            return driver

        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", side_effect=factory):
            client = _client(rebuild_cooldown=30.0)
            await client.connect()
            built[0].verify_connectivity = AsyncMock(side_effect=ServiceUnavailable("gone"))
            await client.execute_query("RETURN 1")  # two failures, verify fails, rebuilt, success
            assert len(built) == 2
            built[1].verify_connectivity = AsyncMock(side_effect=ServiceUnavailable("gone again"))
            with pytest.raises(RuntimeError, match="not rebuilding"):
                await client.execute_query("RETURN 1")

        assert len(built) == 2, "a second rebuild inside the cooldown must not happen"

    @pytest.mark.asyncio
    async def test_the_database_is_ensured_once_not_per_rebuild(self, ensure_db) -> None:
        outcomes: list = [ServiceUnavailable("a"), ServiceUnavailable("b")]
        built: list = []

        def factory(*a, **kw) -> MagicMock:
            driver = _driver(outcomes)
            built.append(driver)
            return driver

        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", side_effect=factory):
            client = _client()
            await client.connect()
            built[0].verify_connectivity = AsyncMock(side_effect=ServiceUnavailable("gone"))
            await client.execute_query("RETURN 1")

        assert len(built) == 2
        assert ensure_db.await_count == 1


class TestStaleSessions:
    @pytest.mark.asyncio
    async def test_sessions_nobody_finished_are_reaped(self, ensure_db) -> None:
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=_driver([])):
            client = _client(stale_session_max_age=100.0)
            await client.connect()
            old = await client.begin_transaction([], [])
            fresh = await client.begin_transaction([], [])
            client._session_started[old] -= 101.0

            assert client.active_session_count == 2
            reaped = await client._reap_stale_sessions()

        assert reaped == 1
        assert client.active_session_count == 1
        assert old not in client._active_sessions
        assert fresh in client._active_sessions

    @pytest.mark.asyncio
    async def test_begin_transaction_reaps_once_the_pool_is_half_held(self, ensure_db) -> None:
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=_driver([])):
            client = _client(max_connection_pool_size=4, stale_session_max_age=100.0)
            await client.connect()
            stale_ids = [await client.begin_transaction([], []) for _ in range(3)]
            for txn_id in stale_ids:
                client._session_started[txn_id] -= 101.0

            newest = await client.begin_transaction([], [])

        assert client.active_session_count == 1
        assert newest in client._active_sessions

    @pytest.mark.asyncio
    async def test_commit_and_abort_forget_the_start_time(self, ensure_db) -> None:
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=_driver([])):
            client = _client()
            await client.connect()
            committed = await client.begin_transaction([], [])
            aborted = await client.begin_transaction([], [])
            await client.commit_transaction(committed)
            await client.abort_transaction(aborted)

        assert client._session_started == {}
        assert client.active_session_count == 0


class TestDriverSettings:
    @pytest.mark.asyncio
    async def test_settings_reach_the_driver(self, ensure_db) -> None:
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=_driver([])) as ctor:
            client = _client(max_connection_pool_size=7, connection_acquisition_timeout=3.0)
            await client.connect()

        kwargs = ctor.call_args.kwargs
        assert kwargs["max_connection_pool_size"] == 7
        assert kwargs["connection_acquisition_timeout"] == 3.0


class TestExplicitTransactions:
    """Behind ``explicit_transactions``: a real transaction per
    ``begin_transaction``, so abort can roll back and commit is atomic."""

    @pytest.mark.asyncio
    async def test_flag_off_keeps_autocommit_sessions(self, ensure_db) -> None:
        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client()
            await client.connect()
            txn = await client.begin_transaction([], [])
            await client.execute_query("MATCH (n) RETURN n", txn_id=txn)
            await client.abort_transaction(txn)

        session = driver.sessions[-1]
        session.begin_transaction.assert_not_awaited()
        assert session.ran == ["MATCH (n) RETURN n"]
        session.close.assert_awaited_once()
        assert not client.explicit_transactions

    @pytest.mark.asyncio
    async def test_queries_run_inside_the_open_transaction(self, ensure_db) -> None:
        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client(explicit_transactions=True, transaction_timeout=42.0)
            await client.connect()
            txn = await client.begin_transaction([], [])
            await client.execute_query("CREATE (n)", txn_id=txn)
            await client.commit_transaction(txn)

        session = driver.sessions[-1]
        tx = session.transactions[0]
        assert tx.timeout == 42.0
        assert session.ran == [], "with the flag on, nothing auto-commits on the session"
        assert tx.ran == ["CREATE (n)"]
        tx.commit.assert_awaited_once()
        session.close.assert_awaited_once()
        assert client.active_session_count == 0

    @pytest.mark.asyncio
    async def test_abort_rolls_back_and_closes_even_when_rollback_raises(self, ensure_db) -> None:
        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client(explicit_transactions=True)
            await client.connect()
            txn = await client.begin_transaction([], [])
            tx = driver.sessions[-1].transactions[0]
            tx.rollback = AsyncMock(side_effect=ServiceUnavailable("gone"))
            await client.abort_transaction(txn)

        tx.rollback.assert_awaited_once()
        driver.sessions[-1].close.assert_awaited_once()
        assert client.active_session_count == 0

    @pytest.mark.asyncio
    async def test_a_failing_commit_still_closes_the_session(self, ensure_db) -> None:
        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client(explicit_transactions=True)
            await client.connect()
            txn = await client.begin_transaction([], [])
            tx = driver.sessions[-1].transactions[0]
            tx.commit = AsyncMock(side_effect=ServiceUnavailable("gone"))
            with pytest.raises(ServiceUnavailable):
                await client.commit_transaction(txn)

        driver.sessions[-1].close.assert_awaited_once()
        assert client.active_session_count == 0

    @pytest.mark.asyncio
    async def test_a_failed_begin_does_not_leak_the_session(self, ensure_db) -> None:
        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client(explicit_transactions=True)
            await client.connect()

            def _open(**kw) -> _Session:
                session = _Session([])
                session.begin_transaction = AsyncMock(side_effect=ServiceUnavailable("gone"))
                driver.sessions.append(session)
                return session

            driver.session = MagicMock(side_effect=_open)
            with pytest.raises(ServiceUnavailable):
                await client.begin_transaction([], [])

        driver.sessions[-1].close.assert_awaited_once()
        assert client.active_session_count == 0

    @pytest.mark.asyncio
    async def test_the_reaper_closes_the_transaction_before_the_session(self, ensure_db) -> None:
        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client(explicit_transactions=True, stale_session_max_age=10.0)
            await client.connect()
            txn = await client.begin_transaction([], [])
            client._session_started[txn] -= 11.0
            assert await client._reap_stale_sessions() == 1

        session = driver.sessions[-1]
        session.transactions[0].close.assert_awaited_once()
        session.close.assert_awaited_once()
