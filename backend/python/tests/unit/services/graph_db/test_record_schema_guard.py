"""The records-schema guard and the client hook (no store): what a 1620 does inside and outside a transaction, the
generation rule, the single flight, and "never inside a transaction"."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.exceptions.graph_db_exceptions import RecordSchemaNarrowError
from app.schema.arango.documents import RECORD_SCHEMA_GENERATION, record_schema
from app.services.graph_db.arango import record_schema_guard as guard_module
from app.services.graph_db.arango.arango_http_client import ArangoHTTPClient
from app.services.graph_db.arango.record_schema_guard import RecordSchemaGuard
from app.utils.error_class import classify_error

REFUSAL = '{"error":true,"errorNum":1620,"errorMessage":"Document does not match the record schema."}'


def _schema(gen: int | None) -> dict[str, Any] | None:
    return None if gen is None else {"rule": {"$comment": f"pipeshub-record-schema gen={gen}"}}


class FakeServer:
    """The parts of ArangoDB the guard talks to: the live schema, the running transactions, the PUT."""

    def __init__(self, live: int | None, *, applies: bool = True) -> None:
        self.live = live
        self.applies = applies
        self.running: list[dict[str, Any]] = []
        self.puts: list[float | None] = []

    async def get_collection_schema(self, name: str) -> dict | None:
        return _schema(self.live)

    async def list_running_transactions(self) -> list[dict[str, Any]]:
        return list(self.running)

    async def update_collection_schema(self, name: str, schema: dict, timeout: float | None = None) -> bool:
        self.puts.append(timeout)
        if self.applies:
            self.live = RECORD_SCHEMA_GENERATION
        return self.applies


def _guard(server: FakeServer, **kw: Any) -> RecordSchemaGuard:  # noqa: ANN401
    return RecordSchemaGuard(server, logging.getLogger("test"), schema=record_schema, generation=RECORD_SCHEMA_GENERATION, **kw)


class TestGeneration:
    async def test_a_lower_live_generation_is_replaced_by_our_own_schema(self) -> None:
        server = FakeServer(live=0)

        assert await _guard(server).ensure_wide("test") is True
        assert server.puts == [5.0]

    @pytest.mark.parametrize("live", [RECORD_SCHEMA_GENERATION, RECORD_SCHEMA_GENERATION + 1])
    async def test_an_equal_or_higher_generation_is_never_touched(self, live: int) -> None:
        server = FakeServer(live=live)

        assert await _guard(server).ensure_wide("test") is False
        assert server.puts == []

    async def test_a_missing_marker_counts_as_zero(self) -> None:
        server = FakeServer(live=None)

        assert await _guard(server).ensure_wide("test") is True


class TestNeverInATransaction:
    async def test_ensure_wide_with_a_bound_transaction_asserts(self) -> None:
        with pytest.raises(AssertionError):
            await _guard(FakeServer(live=0)).ensure_wide("test", transaction="123")


class TestQuietPoint:
    async def test_the_change_waits_for_the_running_transactions_and_counts_a_contended_one(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeServer(live=0)
        server.running = [{"id": "1", "state": "running"}]
        monkeypatch.setattr(guard_module, "REWIDEN_TIMEOUT_S", 0.3)
        monkeypatch.setattr(guard_module, "QUIET_POLL_S", 0.05)
        before = _counter(guard_module.REWIDEN_CONTENDED_TOTAL)

        await _guard(server).ensure_wide("test")

        assert server.puts, "the change is issued anyway after the wait"
        assert _counter(guard_module.REWIDEN_CONTENDED_TOTAL) == before + 1

    async def test_with_no_open_transaction_it_is_issued_at_once_and_not_contended(self) -> None:
        server = FakeServer(live=0)
        before = _counter(guard_module.REWIDEN_CONTENDED_TOTAL)

        await _guard(server).ensure_wide("test")

        assert _counter(guard_module.REWIDEN_CONTENDED_TOTAL) == before

    async def test_a_widening_is_counted(self) -> None:
        before = _counter(guard_module.REWIDEN_TOTAL)

        await _guard(FakeServer(live=0)).ensure_wide("1620")

        assert _counter(guard_module.REWIDEN_TOTAL) == before + 1


def _counter(handle: Any) -> float:  # noqa: ANN401
    """Value of a counter handle on the Prometheus backend (0 on the no-op one)."""
    inner = getattr(handle, "_counter", None) or getattr(handle, "_metric", None)
    if inner is None:
        return 0.0
    return sum(sample.value for metric in inner.collect() for sample in metric.samples if sample.name.endswith("_total"))


class FakeLock:
    def __init__(self, held_elsewhere: bool) -> None:
        self.held_elsewhere, self.released = held_elsewhere, False

    async def acquire(self, ttl_ms: int) -> bool:
        return not self.held_elsewhere

    async def release(self) -> None:
        self.released = True


class TestSingleFlight:
    async def test_a_process_that_does_not_get_the_lock_waits_for_the_one_that_did_and_issues_nothing(self) -> None:
        server = FakeServer(live=0)

        async def other_process_fixes_it() -> None:
            await asyncio.sleep(0.05)
            server.live = RECORD_SCHEMA_GENERATION

        asyncio.get_running_loop().create_task(other_process_fixes_it())
        lock = FakeLock(held_elsewhere=True)

        async def factory() -> FakeLock:
            return lock

        assert await _guard(server, lock_factory=factory).ensure_wide("test") is False
        assert server.puts == []

    async def test_three_processes_issue_one_change(self) -> None:
        server = FakeServer(live=0)
        taken = {"by": None}

        class Shared:
            def __init__(self, me: str) -> None:
                self.me = me

            async def acquire(self, ttl_ms: int) -> bool:
                if taken["by"] is None:
                    taken["by"] = self.me
                    return True
                return False

            async def release(self) -> None:
                taken["by"] = None

        guards = [_guard(server, lock_factory=_factory(Shared(f"p{i}"))) for i in range(3)]

        await asyncio.gather(*(g.ensure_wide("test") for g in guards))

        assert len(server.puts) == 1

    async def test_an_unreachable_lock_does_not_stop_the_widening(self) -> None:
        server = FakeServer(live=0)

        async def broken() -> Any:  # noqa: ANN401
            raise ConnectionError("no redis")

        assert await _guard(server, lock_factory=broken).ensure_wide("test") is True


def _factory(lock: Any):  # noqa: ANN202, ANN401
    async def make() -> Any:  # noqa: ANN401
        return lock

    return make


# ---- the client hook ---------------------------------------------------------------------------------------------


class _Client(ArangoHTTPClient):
    """The client with its network calls replaced; the hook under test is the real one."""

    def __init__(self, server: FakeServer, replies: list[Any]) -> None:
        super().__init__("http://x", "u", "p", "db", logging.getLogger("test"))
        self.server, self.replies = server, list(replies)
        self.sent: list[str | None] = []
        self.aborted: list[str] = []
        self.record_guard = _guard(server)

    async def _get_session(self) -> Any:  # noqa: ANN401
        raise AssertionError("no network in this test")

    async def get_collection_schema(self, name: str) -> dict | None:
        return await self.server.get_collection_schema(name)

    async def list_running_transactions(self) -> list[dict[str, Any]]:
        return await self.server.list_running_transactions()

    async def update_collection_schema(self, name: str, schema: dict | None = None, timeout: float | None = None) -> bool:
        return await self.server.update_collection_schema(name, schema, timeout)

    async def _abort_record_schema_refused_transaction(self, txn_id: str) -> None:
        self.aborted.append(txn_id)
        self.record_guard.mark_aborted(txn_id)


def _hooked(client: _Client):  # noqa: ANN202
    """``execute_aql``'s record-schema hook around a scripted send, without the network."""
    from app.services.graph_db.arango.arango_http_client import _record_schema_guarded

    async def send(self: _Client, query: str, bind_vars: dict | None = None, txn_id: str | None = None) -> list[Any]:
        self.sent.append(txn_id)
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    return _record_schema_guarded(send)


class TestTheClientHook:
    async def test_a_refusal_outside_a_transaction_widens_and_sends_the_statement_once_more(self) -> None:
        server = FakeServer(live=0)
        client = _Client(server, [Exception(f"Query failed (status=400): {REFUSAL}"), [1]])

        assert await _hooked(client)(client, "Q") == [1]
        assert len(client.sent) == 2 and server.live == RECORD_SCHEMA_GENERATION

    async def test_a_refusal_that_outlives_a_widening_that_did_not_apply_raises_the_narrow_error(self) -> None:
        client = _Client(FakeServer(live=0, applies=False), [Exception(REFUSAL), Exception(REFUSAL)])

        with pytest.raises(RecordSchemaNarrowError):
            await _hooked(client)(client, "Q")

    async def test_a_refusal_under_a_wide_schema_is_the_datas_and_raises_as_it_always_did(self) -> None:
        """A wrong-typed value is refused by a schema that is as wide as ours: the error stays permanent, and the transaction
        is still aborted, so a caller that catches it cannot commit the statements around it."""
        client = _Client(FakeServer(live=RECORD_SCHEMA_GENERATION), [Exception(f"Query failed (status=400): {REFUSAL}")])

        with pytest.raises(Exception, match="1620") as raised:
            await _hooked(client)(client, "Q", None, "txn-1")
        assert not isinstance(raised.value, RecordSchemaNarrowError)
        assert client.aborted == ["txn-1"] and len(client.sent) == 1
        with pytest.raises(RecordSchemaNarrowError):
            client.record_guard.check_usable("txn-1")

    async def test_a_schema_widened_between_the_refusal_and_the_check_still_aborts_the_transaction(self) -> None:
        """The refusal came from a narrow schema; another process widened it before the check read it."""
        server = FakeServer(live=0)
        client = _Client(server, [Exception(REFUSAL)])
        real_is_wide = client.record_guard.is_wide

        async def widened_first() -> bool:
            server.live = RECORD_SCHEMA_GENERATION
            return await real_is_wide()

        client.record_guard.is_wide = widened_first  # type: ignore[method-assign]
        with pytest.raises(Exception, match="1620"):
            await _hooked(client)(client, "Q", None, "txn-1")
        assert client.aborted == ["txn-1"]
        with pytest.raises(RecordSchemaNarrowError):
            client.record_guard.check_usable("txn-1")

    async def test_a_refusal_under_a_wide_schema_this_process_saw_narrow_is_the_schemas(self) -> None:
        client = _Client(FakeServer(live=RECORD_SCHEMA_GENERATION), [Exception(REFUSAL)])
        client.record_guard.note_narrow()

        with pytest.raises(RecordSchemaNarrowError):
            await _hooked(client)(client, "Q", None, "txn-1")
        assert client.aborted == ["txn-1"]

    async def test_a_data_refusal_outside_a_transaction_is_sent_once_more_and_raised_when_it_fails_again(self) -> None:
        client = _Client(FakeServer(live=RECORD_SCHEMA_GENERATION), [Exception(REFUSAL), Exception(REFUSAL)])

        with pytest.raises(Exception, match="1620"):
            await _hooked(client)(client, "Q")
        assert len(client.sent) == 2

    async def test_a_schema_widened_by_another_process_between_the_refusal_and_the_check_still_gets_the_write_sent_again(self) -> None:
        """The refusal came from a narrow schema; by the time the guard looks, a poll in another process has widened it."""
        server = FakeServer(live=RECORD_SCHEMA_GENERATION)  # already wide when read
        client = _Client(server, [Exception(REFUSAL), [1]])

        assert await _hooked(client)(client, "Q") == [1]
        assert len(client.sent) == 2

    async def test_a_refusal_inside_a_transaction_aborts_it_and_raises_before_any_caller_code_runs(self) -> None:
        client = _Client(FakeServer(live=0), [Exception(REFUSAL)])

        with pytest.raises(RecordSchemaNarrowError):
            await _hooked(client)(client, "Q", None, "txn-1")

        assert client.aborted == ["txn-1"]
        assert client.sent == ["txn-1"], "the statement was not sent again"

    async def test_after_the_abort_a_caller_that_catches_and_goes_on_cannot_send_or_commit(self) -> None:
        client = _Client(FakeServer(live=0), [Exception(REFUSAL), [1]])
        hooked = _hooked(client)
        with pytest.raises(RecordSchemaNarrowError):
            await hooked(client, "Q", None, "txn-1")

        with pytest.raises(RecordSchemaNarrowError):
            await hooked(client, "Q2", None, "txn-1")  # the next statement is refused before it is sent
        assert client.sent == ["txn-1"]
        with pytest.raises(RecordSchemaNarrowError):
            await client.commit_transaction("txn-1")

    async def test_an_error_that_is_not_the_record_schema_is_untouched(self) -> None:
        client = _Client(FakeServer(live=0), [Exception("boom")])

        with pytest.raises(Exception, match="boom"):
            await _hooked(client)(client, "Q", None, "txn-1")
        assert client.aborted == []

    async def test_a_client_with_no_guard_behaves_as_before(self) -> None:
        client = _Client(FakeServer(live=0), [Exception(REFUSAL)])
        client.record_guard = None

        with pytest.raises(Exception, match="1620"):
            await _hooked(client)(client, "Q", None, "txn-1")
        assert client.aborted == []


class TestTheErrorClass:
    def test_it_is_transient_to_the_retry_owners_and_classified_a_conflict(self) -> None:
        from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

        provider = ArangoHTTPProvider(logging.getLogger("t"), MagicMock())
        error = RecordSchemaNarrowError("x")
        assert provider.is_transient_error(error) is True
        assert classify_error(error) == "conflict"

    def test_a_handler_that_logs_and_goes_on_must_let_it_through(self) -> None:
        from app.connectors.core.base.data_processor.data_source_entities_processor import (
            DataSourceEntitiesProcessor,
        )

        processor = DataSourceEntitiesProcessor.__new__(DataSourceEntitiesProcessor)
        processor.data_store_provider = MagicMock()
        processor.data_store_provider.is_transient_error = MagicMock(return_value=False)

        assert processor._must_propagate(RecordSchemaNarrowError("x"), None) is True

    async def test_a_transaction_begun_after_a_narrowing_waits_for_the_schema_to_be_wide(self) -> None:
        server = FakeServer(live=0)
        client = _Client(server, [])
        client.record_guard.note_narrow()

        async def widen() -> None:
            await asyncio.sleep(0.05)
            server.live = RECORD_SCHEMA_GENERATION

        asyncio.get_running_loop().create_task(widen())
        assert await client.record_guard.wait_wide(2.0) is True
        assert client.record_guard.narrowed is False


class TestWriteAttemptStartIsValidated:
    async def test_the_neo4j_provider_refuses_a_page_count_of_zero(self) -> None:
        from app.schema.node_validator import SchemaValidationError
        from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

        provider = Neo4jProvider.__new__(Neo4jProvider)
        provider.logger = logging.getLogger("t")
        provider.client = MagicMock()
        provider.client.execute_query = AsyncMock(return_value=[{"id": "r"}])
        from app.schema.node_validator import NodeSchemaValidator

        provider.validator = NodeSchemaValidator()
        with pytest.raises(SchemaValidationError):
            await provider.write_attempt_start("r", {"indexingStatus": "IN_PROGRESS", "pageCount": 0})
        provider.client.execute_query.assert_not_awaited()
