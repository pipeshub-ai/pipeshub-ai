"""How ``Neo4jClient`` behaves when the server misbehaves.

The production cascade this guards against: a rebuilt driver closes every
connection in its pool, including the ones other coroutines are mid-query
on, so rebuilding on the first ``ServiceUnavailable`` turned one dead
connection into a failure for every in-flight record -- and with no cooldown
the next failure did it again. Sessions nobody committed or aborted held a
pool connection until process shutdown.
"""
from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from neo4j.exceptions import ClientError, ServiceUnavailable

from app.services.graph_db.neo4j.neo4j_client import (
    DEFAULT_LIVENESS_CHECK_TIMEOUT,
    DEFAULT_MAX_CONNECTION_LIFETIME,
    Neo4jClient,
)

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


class _SystemSession:
    """The system-database session: SHOW DATABASES answers from a script of rows.

    A status string stands for a standalone server's row, which is its own writer.
    """

    def __init__(self, statuses: list[list[str | dict]], create_error: Exception | None = None,
                 hang: bool = False) -> None:
        self._statuses = statuses
        self._create_error = create_error
        self._hang = hang
        self.created: list[MagicMock] = []
        self.shows = 0
        self.names: list[str] = []

    async def __aenter__(self) -> "_SystemSession":
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def run(self, query, parameters=None) -> MagicMock:
        if query.startswith("CREATE DATABASE"):
            if self._create_error:
                raise self._create_error
            result = MagicMock(consume=AsyncMock())
            result.query = query
            self.created.append(result)
            return result
        self.shows += 1
        self.names.append(parameters["dbName"])
        if self._hang:
            await asyncio.Event().wait()
        # The last status repeats once the script runs out.
        current = self._statuses.pop(0) if len(self._statuses) > 1 else self._statuses[0]
        rows = [r if isinstance(r, dict) else {"currentStatus": r, "writer": True} for r in current]
        return MagicMock(data=AsyncMock(return_value=rows))


def _ensure_client(session: _SystemSession, database: str = "neo4j") -> Neo4jClient:
    client = _client()
    client.database = database
    client.driver = MagicMock(session=MagicMock(return_value=session))
    return client


def _client_error(code: str) -> ClientError:
    error = ClientError(code)
    error._neo4j_code = code
    return error


@pytest.fixture
def no_sleep() -> Iterator[AsyncMock]:
    real_sleep = asyncio.sleep

    async def yield_once(_delay: float) -> None:
        # Still yields, so a timeout that has passed can fire.
        await real_sleep(0)

    with patch("app.services.graph_db.neo4j.neo4j_client.asyncio.sleep", AsyncMock(side_effect=yield_once)) as slept:
        yield slept


class TestEnsureDatabase:
    @pytest.mark.asyncio
    async def test_a_missing_database_is_created_and_waited_for(self, no_sleep) -> None:
        session = _SystemSession([[], ["starting"], ["starting"], ["online"]])

        assert await _ensure_client(session)._ensure_database_exists() is True

        assert [c.query for c in session.created] == ["CREATE DATABASE `neo4j` IF NOT EXISTS"]
        session.created[0].consume.assert_awaited_once()
        assert session.shows == 4

    @pytest.mark.asyncio
    async def test_an_existing_database_still_starting_is_waited_for(self, no_sleep) -> None:
        # Another service created it a moment ago: Neo4j 2025.11 lists it as "unknown" at first.
        session = _SystemSession([["unknown"], ["online"]])

        assert await _ensure_client(session)._ensure_database_exists() is True

        assert session.created == []
        assert session.shows == 2

    @pytest.mark.asyncio
    async def test_an_online_database_is_not_waited_for(self, no_sleep) -> None:
        session = _SystemSession([["online"]])

        assert await _ensure_client(session)._ensure_database_exists() is True

        assert session.created == []
        no_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_the_online_writer_is_enough_in_a_cluster(self, no_sleep) -> None:
        session = _SystemSession([["starting", "online"]])

        assert await _ensure_client(session)._ensure_database_exists() is True
        no_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_an_online_secondary_waits_for_a_writer(self, no_sleep) -> None:
        secondary = {"currentStatus": "online", "writer": False}
        session = _SystemSession([[secondary], [secondary, {"currentStatus": "online", "writer": True}]])

        assert await _ensure_client(session)._ensure_database_exists() is True
        assert session.shows == 2

    @pytest.mark.asyncio
    async def test_a_server_without_a_writer_column_counts_as_writer(self, no_sleep) -> None:
        session = _SystemSession([[{"currentStatus": "online"}]])

        assert await _ensure_client(session)._ensure_database_exists() is True
        no_sleep.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_a_mixed_case_name_is_looked_up_in_lowercase(self, no_sleep) -> None:
        # Neo4j stores "WorkGraph" as "workgraph"; the exact name would never be listed.
        session = _SystemSession([["online"]])

        assert await _ensure_client(session, database="WorkGraph")._ensure_database_exists() is True
        assert session.names == ["workgraph"]
        assert session.created == []

    @pytest.mark.asyncio
    async def test_a_database_that_never_comes_online_is_reported_not_ready(self, no_sleep) -> None:
        session = _SystemSession([["starting"]])
        client = _ensure_client(session)

        with patch("app.services.graph_db.neo4j.neo4j_client.DATABASE_ONLINE_TIMEOUT", 0.0):
            assert await client._ensure_database_exists() is False

        assert "not online" in client.logger.warning.call_args.args[0]
        assert "starting" in client.logger.warning.call_args.args[0]

    @pytest.mark.asyncio
    async def test_a_status_query_that_hangs_ends_at_the_deadline(self) -> None:
        client = _ensure_client(_SystemSession([["online"]], hang=True))

        with patch("app.services.graph_db.neo4j.neo4j_client.DATABASE_ONLINE_TIMEOUT", 0.05):
            assert await asyncio.wait_for(client._ensure_database_exists(), timeout=5) is False

        assert "not listed" in client.logger.warning.call_args.args[0]

    @pytest.mark.asyncio
    async def test_an_edition_that_cannot_create_databases_is_not_retried(self, no_sleep) -> None:
        error = _client_error("Neo.ClientError.Statement.UnsupportedAdministrationCommand")
        session = _SystemSession([[]], create_error=error)

        assert await _ensure_client(session)._ensure_database_exists() is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize("code", ["Neo.ClientError.Security.Forbidden", "Neo.ClientError.Transaction.Terminated"])
    async def test_any_other_refusal_leaves_the_database_unchecked(self, no_sleep, code) -> None:
        session = _SystemSession([[]], create_error=_client_error(code))

        assert await _ensure_client(session)._ensure_database_exists() is False

    @pytest.mark.asyncio
    async def test_connect_checks_again_while_the_database_is_not_online(self) -> None:
        ensure = AsyncMock(side_effect=[False, True])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", side_effect=lambda *a, **kw: _driver([])),                 patch.object(Neo4jClient, "_ensure_database_exists", ensure):
            client = _client()
            await client.connect()
            assert client._database_ensured is False
            client.driver = None
            await client.connect()

        assert ensure.await_count == 2
        assert client._database_ensured is True


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
    async def test_a_rebuild_logs_the_failure_class_never_its_text(self, ensure_db) -> None:
        driver_text = "Couldn't connect to 10.0.0.7:7687"
        outcomes: list = [ServiceUnavailable(driver_text), ServiceUnavailable(driver_text)]
        built: list = []

        def factory(*a, **kw) -> MagicMock:
            built.append(_driver(outcomes))
            return built[-1]

        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", side_effect=factory):
            client = _client()
            await client.connect()
            built[0].verify_connectivity = AsyncMock(side_effect=ServiceUnavailable(driver_text))
            await client.execute_query("RETURN 1")

        (warning,) = [c for c in client.logger.warning.call_args_list if "rebuilding" in c.args[0]]
        assert warning.args[1:] == ("ServiceUnavailable",)
        assert driver_text not in repr(client.logger.mock_calls)

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
            client = _client(
                max_connection_pool_size=7,
                connection_acquisition_timeout=3.0,
                max_connection_lifetime=240.0,
                liveness_check_timeout=0.0,
            )
            await client.connect()

        kwargs = ctor.call_args.kwargs
        assert kwargs["max_connection_pool_size"] == 7
        assert kwargs["connection_acquisition_timeout"] == 3.0
        assert kwargs["max_connection_lifetime"] == 240.0
        # Zero is the driver's "check every time", not "off".
        assert kwargs["liveness_check_timeout"] == 0.0
        # Only the "does not exist" names are filtered; deprecations and the rest still reach the log.
        assert kwargs["notifications_disabled_classifications"] == ["UNRECOGNIZED"]

    def test_default_lifetime_retires_connections_before_a_nat_idle_timeout(self) -> None:
        # A NAT gateway drops a flow idle for 350s and resets the next packet
        # on it; a pooled connection kept longer is reused after the drop.
        assert DEFAULT_MAX_CONNECTION_LIFETIME < 350
        assert DEFAULT_LIVENESS_CHECK_TIMEOUT < DEFAULT_MAX_CONNECTION_LIFETIME

    # 0 retires every connection before reuse; inf never retires one.
    @pytest.mark.parametrize("lifetime", [0, 0.0, -1.0, float("nan"), float("inf"), "abc", None])
    def test_an_unusable_lifetime_falls_back(self, lifetime: object) -> None:
        client = _client(max_connection_lifetime=lifetime)

        assert client._max_connection_lifetime == DEFAULT_MAX_CONNECTION_LIFETIME
        client.logger.warning.assert_called_once()

    # The driver rejects a negative timeout; inf would never check a connection.
    @pytest.mark.parametrize("timeout", [-1.0, float("nan"), float("inf"), "abc", None])
    def test_an_unusable_liveness_timeout_falls_back(self, timeout: object) -> None:
        client = _client(liveness_check_timeout=timeout)

        assert client._liveness_check_timeout == DEFAULT_LIVENESS_CHECK_TIMEOUT
        client.logger.warning.assert_called_once()

    def test_usable_timeouts_are_kept_without_a_warning(self) -> None:
        client = _client(max_connection_lifetime=240, liveness_check_timeout=0)

        assert client._max_connection_lifetime == 240.0
        assert client._liveness_check_timeout == 0.0
        client.logger.warning.assert_not_called()


class TestExplicitTransactions:
    """Behind ``explicit_transactions``: a real transaction per
    ``begin_transaction``, so abort can roll back and commit is atomic."""

    @pytest.mark.asyncio
    async def test_flag_off_keeps_autocommit_sessions(self, ensure_db) -> None:
        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client(explicit_transactions=False)
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
    async def test_one_call_can_ask_for_a_real_transaction_with_the_flag_off(self, ensure_db) -> None:
        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client(explicit_transactions=False, transaction_timeout=42.0)
            await client.connect()
            txn = await client.begin_transaction([], [], explicit=True)
            await client.execute_query("CREATE (n)", txn_id=txn)
            await client.commit_transaction(txn)
            plain = await client.begin_transaction([], [])
            await client.abort_transaction(plain)

        asked, default = driver.sessions[-2], driver.sessions[-1]
        assert asked.transactions[0].timeout == 42.0
        assert asked.ran == []  # the statement ran inside the transaction, not on the session
        default.begin_transaction.assert_not_awaited()
        assert not client.explicit_transactions

    def test_the_client_opens_real_transactions_by_default(self) -> None:
        assert _client().explicit_transactions

    @pytest.mark.asyncio
    async def test_one_call_can_decline_the_transaction_with_the_flag_on(self, ensure_db) -> None:
        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client(explicit_transactions=True)
            await client.connect()
            txn = await client.begin_transaction([], [], explicit=False)
            await client.abort_transaction(txn)

        driver.sessions[-1].begin_transaction.assert_not_awaited()

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


class TestServerTimeout:
    @pytest.mark.asyncio
    async def test_a_timeout_wraps_the_query_and_survives_a_retry(self, ensure_db) -> None:
        from neo4j import Query

        built: list = []

        def factory(*a, **kw) -> MagicMock:
            built.append(_driver([ServiceUnavailable("connection reset")]))
            return built[-1]

        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", side_effect=factory):
            client = _client()
            await client.connect()
            await client.execute_query("RETURN 1", timeout=2.5)

        ran = [q for session in built[0].sessions for q in session.ran]
        assert len(ran) == 2
        assert all(isinstance(q, Query) and q.timeout == 2.5 and q.text == "RETURN 1" for q in ran)

    @pytest.mark.asyncio
    async def test_no_timeout_runs_the_plain_text(self, ensure_db) -> None:
        built: list = []

        def factory(*a, **kw) -> MagicMock:
            built.append(_driver([]))
            return built[-1]

        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", side_effect=factory):
            client = _client()
            await client.connect()
            await client.execute_query("RETURN 1")

        assert built[0].sessions[0].ran == ["RETURN 1"]


class TestServerTimeoutInASession:
    @pytest.mark.asyncio
    async def test_a_session_query_keeps_its_timeout(self, ensure_db) -> None:
        """Without explicit transactions a "transaction" is a session whose
        queries each auto-commit, so the requested limit must go with them."""
        from neo4j import Query

        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client(explicit_transactions=False)
            await client.connect()
            txn = await client.begin_transaction([], [])
            await client.execute_query("RETURN 1", txn_id=txn, timeout=2.5)

        (ran,) = driver.sessions[-1].ran
        assert isinstance(ran, Query) and ran.timeout == 2.5 and ran.text == "RETURN 1"

    @pytest.mark.asyncio
    async def test_an_explicit_transaction_keeps_the_limit_it_began_with(self, ensure_db) -> None:
        driver = _driver([])
        with patch("app.services.graph_db.neo4j.neo4j_client.AsyncGraphDatabase.driver", return_value=driver):
            client = _client(explicit_transactions=True)
            await client.connect()
            txn = await client.begin_transaction([], [])
            await client.execute_query("RETURN 1", txn_id=txn, timeout=2.5)

        assert driver.sessions[-1].transactions[0].ran == ["RETURN 1"]
