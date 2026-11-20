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


class FreeLock:
    """A lock nobody else wants (the guard's own default is the ArangoDB lease lock, tested on a real server)."""

    async def acquire(self, ttl_ms: int) -> bool:
        return True

    async def renew(self, ttl_ms: int) -> bool:
        return True

    async def release(self) -> None:
        return None


async def _free_lock() -> FreeLock:
    return FreeLock()


def _guard(server: FakeServer, **kw: Any) -> RecordSchemaGuard:  # noqa: ANN401
    kw.setdefault("lock_factory", _free_lock)
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


class TestADeadlineHoldsWhenARequestNeverAnswers:
    async def test_wait_wide_returns_false_at_its_deadline_when_the_read_hangs(self) -> None:
        server = FakeServer(live=0)

        async def hang(name: str) -> None:
            await asyncio.sleep(3600)

        server.get_collection_schema = hang  # type: ignore[method-assign]
        assert await asyncio.wait_for(_guard(server).wait_wide(0.2), timeout=3) is False

    async def test_the_quiet_wait_gives_up_at_its_deadline_when_the_listing_hangs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeServer(live=0)

        async def hang() -> list[dict[str, Any]]:
            await asyncio.sleep(3600)
            return []

        server.list_running_transactions = hang  # type: ignore[method-assign]
        monkeypatch.setattr(guard_module, "REWIDEN_TIMEOUT_S", 0.2)
        assert await asyncio.wait_for(_guard(server)._wait_quiet(), timeout=3) is False


class TestANewerSchemaWrittenDuringTheQuietWait:
    async def test_is_not_overwritten_by_an_older_generation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeServer(live=0)
        server.running = [{"id": "1", "state": "running"}]
        monkeypatch.setattr(guard_module, "REWIDEN_TIMEOUT_S", 0.3)
        monkeypatch.setattr(guard_module, "QUIET_POLL_S", 0.05)
        real_list = server.list_running_transactions

        async def newer_arrives() -> list[dict[str, Any]]:
            server.live = RECORD_SCHEMA_GENERATION + 1  # another process installs a newer schema while this one waits
            return await real_list()

        server.list_running_transactions = newer_arrives  # type: ignore[method-assign]
        assert await _guard(server).ensure_wide("test") is False
        assert server.puts == [] and server.live == RECORD_SCHEMA_GENERATION + 1


class TestADeadlineThatExpiresDuringASleep:
    async def test_wait_wide_makes_no_read_after_its_deadline(self) -> None:
        """The schema turns wide after the deadline and before the next poll would have run: it is not reported as wide in time."""
        server = FakeServer(live=0)

        async def turns_wide_later() -> None:
            await asyncio.sleep(0.07)
            server.live = RECORD_SCHEMA_GENERATION

        task = asyncio.get_running_loop().create_task(turns_wide_later())
        assert await _guard(server).wait_wide(0.05) is False
        await task

    async def test_the_quiet_wait_does_not_sleep_past_its_deadline(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeServer(live=0)
        server.running = [{"id": "1", "state": "running"}]
        monkeypatch.setattr(guard_module, "REWIDEN_TIMEOUT_S", 0.2)
        monkeypatch.setattr(guard_module, "QUIET_POLL_S", 30.0)
        calls = {"n": 0}
        real = server.list_running_transactions

        async def counting() -> list[dict[str, Any]]:
            calls["n"] += 1
            return await real()

        server.list_running_transactions = counting  # type: ignore[method-assign]
        assert await asyncio.wait_for(_guard(server)._wait_quiet(), timeout=3) is False
        assert calls["n"] == 1, "no request after the deadline"


class TestQuietPoint:
    async def test_the_change_is_not_sent_while_a_transaction_is_still_open_after_the_wait_and_the_lock_is_released(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A schema change waits for every open stream transaction: one sent now could outlast its timeout and the lease. The poll tries again."""
        server = FakeServer(live=0)
        server.running = [{"id": "1", "state": "running"}]
        monkeypatch.setattr(guard_module, "REWIDEN_TIMEOUT_S", 0.3)
        monkeypatch.setattr(guard_module, "QUIET_POLL_S", 0.05)
        before = _counter(guard_module.REWIDEN_CONTENDED_TOTAL)
        released = {"n": 0}

        class Counting(FreeLock):
            async def release(self) -> None:
                released["n"] += 1

        async def factory() -> Counting:
            return Counting()

        guard = _guard(server, lock_factory=factory)
        for _ in range(guard_module.FORCE_AFTER_SKIPS):
            assert await guard.ensure_wide("test") is False

        assert server.puts == [], "nothing was sent"
        assert _counter(guard_module.REWIDEN_CONTENDED_TOTAL) == before + guard_module.FORCE_AFTER_SKIPS
        assert released["n"] == guard_module.FORCE_AFTER_SKIPS, "nothing is in flight, so the lock is free again"

    async def test_after_a_few_skips_it_is_sent_anyway_so_an_always_open_transaction_cannot_starve_it(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeServer(live=0)
        server.running = [{"id": "1", "state": "running"}]
        monkeypatch.setattr(guard_module, "REWIDEN_TIMEOUT_S", 0.1)
        monkeypatch.setattr(guard_module, "QUIET_POLL_S", 0.02)
        guard = _guard(server)

        results = [await guard.ensure_wide("test") for _ in range(guard_module.FORCE_AFTER_SKIPS + 1)]

        # exactly FORCE_AFTER_SKIPS widenings are skipped, and the next one is sent (the documented rule: after three skips, the fourth try)
        assert guard_module.FORCE_AFTER_SKIPS == 3
        assert results == [False, False, False, True]
        assert len(server.puts) == 1

    async def test_the_skip_count_starts_again_after_a_widening_is_sent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        server = FakeServer(live=0)
        server.running = [{"id": "1", "state": "running"}]
        monkeypatch.setattr(guard_module, "REWIDEN_TIMEOUT_S", 0.05)
        monkeypatch.setattr(guard_module, "QUIET_POLL_S", 0.01)
        guard = _guard(server)
        for _ in range(guard_module.FORCE_AFTER_SKIPS + 1):
            await guard.ensure_wide("test")
        server.live = 0  # narrowed again

        results = [await guard.ensure_wide("test") for _ in range(guard_module.FORCE_AFTER_SKIPS + 1)]

        assert results == [False, False, False, True]

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

    async def renew(self, ttl_ms: int) -> bool:
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

            async def renew(self, ttl_ms: int) -> bool:
                return taken["by"] == self.me

            async def release(self) -> None:
                taken["by"] = None

        guards = [_guard(server, lock_factory=_factory(Shared(f"p{i}"))) for i in range(3)]

        await asyncio.gather(*(g.ensure_wide("test") for g in guards))

        assert len(server.puts) == 1

    async def test_an_unreachable_lock_means_no_schema_write(self) -> None:
        """Fails closed: another process can install a newer schema after the last generation check, and an unlocked write would narrow it."""
        server = FakeServer(live=0)

        async def broken() -> Any:  # noqa: ANN401
            raise ConnectionError("lock store down")

        assert await _guard(server, lock_factory=broken).ensure_wide("test") is False
        assert server.puts == []

    async def test_a_factory_that_gives_no_lock_means_no_schema_write(self) -> None:
        server = FakeServer(live=0)

        async def none() -> None:
            return None

        assert await _guard(server, lock_factory=none).ensure_wide("test") is False
        assert server.puts == []

    @staticmethod
    def _server_where_a_newer_schema_lands_after_the_first_check() -> FakeServer:
        """The race: the older process reads the narrow schema, and a newer process installs its schema before the older would write."""
        server = FakeServer(live=0)
        real = server.get_collection_schema
        reads = {"n": 0}

        async def get(name: str) -> dict | None:
            reads["n"] += 1
            answer = await real(name)
            if reads["n"] == 3:  # after the last generation check an unlocked widening makes
                server.live = RECORD_SCHEMA_GENERATION + 1
            return answer

        server.get_collection_schema = get  # type: ignore[method-assign]
        return server

    async def test_a_newer_schema_installed_while_the_lock_is_unavailable_is_never_narrowed(self) -> None:
        server = self._server_where_a_newer_schema_lands_after_the_first_check()

        async def broken() -> Any:  # noqa: ANN401
            raise ConnectionError("lock store down")

        await _guard(server, lock_factory=broken).ensure_wide("test")
        await server.get_collection_schema("records")  # the newer process installs its schema now, whatever this one did
        await server.get_collection_schema("records")

        assert server.puts == [], "no lock, no schema write"
        assert server.live == RECORD_SCHEMA_GENERATION + 1

    async def test_a_process_that_lost_its_lease_before_the_write_is_fenced(self) -> None:
        """The lease expired during the quiet wait and another process took it: the renew before the write says no, and nothing is written."""
        server = FakeServer(live=0)

        class Expired(FreeLock):
            async def renew(self, ttl_ms: int) -> bool:
                return False

        async def factory() -> Expired:
            return Expired()

        assert await _guard(server, lock_factory=factory).ensure_wide("test") is False
        assert server.puts == []

    async def test_a_newer_schema_that_lands_during_the_renewal_is_not_overwritten(self) -> None:
        """The last look before the write is after the renewal: a newer schema installed meanwhile is left alone."""
        server = FakeServer(live=0)

        class Slow(FreeLock):
            async def renew(self, ttl_ms: int) -> bool:
                server.live = RECORD_SCHEMA_GENERATION + 1  # the newer process writes during the renewal
                return True

        async def factory() -> Slow:
            return Slow()

        assert await _guard(server, lock_factory=factory).ensure_wide("test") is False
        assert server.puts == [] and server.live == RECORD_SCHEMA_GENERATION + 1

    async def test_a_live_generation_that_cannot_be_read_before_the_write_means_no_write(self) -> None:
        server = FakeServer(live=0)
        reads = {"n": 0}
        real = server.get_collection_schema

        async def get(name: str) -> dict | None:
            reads["n"] += 1
            if reads["n"] == 4:  # the read right before the write (initial, under the lock, after the wait, then this one)
                raise ConnectionError("down")
            return await real(name)

        server.get_collection_schema = get  # type: ignore[method-assign]
        assert await _guard(server).ensure_wide("test") is False
        assert server.puts == []

    async def test_a_lock_that_cannot_be_renewed_for_an_error_is_fenced_too(self) -> None:
        server = FakeServer(live=0)

        class Broken(FreeLock):
            async def renew(self, ttl_ms: int) -> bool:
                raise ConnectionError("down")

        async def factory() -> Broken:
            return Broken()

        assert await _guard(server, lock_factory=factory).ensure_wide("test") is False
        assert server.puts == []

    async def test_the_lock_is_released_when_the_write_was_applied_or_never_sent(self) -> None:
        released = {"n": 0}

        class Counting(FreeLock):
            async def release(self) -> None:
                released["n"] += 1

        async def factory() -> Counting:
            return Counting()

        await _guard(FakeServer(live=0), lock_factory=factory).ensure_wide("test")  # applied

        class Expired(Counting):
            async def renew(self, ttl_ms: int) -> bool:
                return False

        async def expired() -> Expired:
            return Expired()

        await _guard(FakeServer(live=0), lock_factory=expired).ensure_wide("test")  # fenced before the write: never sent
        assert released["n"] == 2

    @pytest.mark.parametrize("failure", ["not_applied", "raises"])
    async def test_the_lock_is_left_to_expire_when_the_write_may_still_be_in_flight(self, failure: str) -> None:
        """The client gave up (a timeout is a false answer) but the server may still apply the change: releasing now would let another
        process start while it is in flight."""
        released = {"n": 0}
        server = FakeServer(live=0, applies=False)
        if failure == "raises":
            async def boom(name: str, schema: dict, timeout: float | None = None) -> bool:
                raise TimeoutError("no answer")

            server.update_collection_schema = boom  # type: ignore[method-assign]

        class Counting(FreeLock):
            async def release(self) -> None:
                released["n"] += 1

        async def factory() -> Counting:
            return Counting()

        assert await _guard(server, lock_factory=factory).ensure_wide("test") is False
        assert released["n"] == 0


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

    async def test_a_refusal_with_no_lock_to_widen_under_raises_the_transient_narrow_error_and_writes_no_schema(self) -> None:
        server = FakeServer(live=0)
        client = _Client(server, [Exception(REFUSAL), Exception(REFUSAL)])

        async def broken() -> Any:  # noqa: ANN401
            raise ConnectionError("lock store down")

        client.record_guard = _guard(server, lock_factory=broken)
        with pytest.raises(RecordSchemaNarrowError):
            await _hooked(client)(client, "Q")
        assert server.puts == []

    async def test_a_refusal_that_outlives_a_widening_that_did_not_apply_raises_the_narrow_error(self) -> None:
        client = _Client(FakeServer(live=0, applies=False), [Exception(REFUSAL), Exception(REFUSAL)])

        with pytest.raises(RecordSchemaNarrowError):
            await _hooked(client)(client, "Q")

    async def test_a_refusal_under_a_wide_schema_aborts_and_is_run_again_with_the_refusal_as_its_cause(self) -> None:
        """A wide schema at the check cannot tell a wrong-typed value from a narrow schema widened since, so the transaction
        is aborted and run again (a bounded number of times); a bad value is refused again there and stays the cause."""
        client = _Client(FakeServer(live=RECORD_SCHEMA_GENERATION), [Exception(f"Query failed (status=400): {REFUSAL}")])

        with pytest.raises(RecordSchemaNarrowError) as raised:
            await _hooked(client)(client, "Q", None, "txn-1")
        assert "1620" in str(raised.value.__cause__)
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
        with pytest.raises(RecordSchemaNarrowError):
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


class _Response:
    def __init__(self, status: int, body: Any) -> None:  # noqa: ANN401
        self.status, self.body = status, body

    async def __aenter__(self) -> "_Response":
        return self

    async def __aexit__(self, *exc: object) -> bool:
        return False

    async def json(self) -> Any:  # noqa: ANN401
        return self.body

    async def text(self) -> str:
        return str(self.body)


class _BatchSession:
    """A records collection whose validator refuses the documents named in ``refused`` until the schema is widened; every document that is
    accepted is written (and counted), as ArangoDB does for an array body."""

    def __init__(self, server: FakeServer, refused: set[str]) -> None:
        self.server, self.refused = server, refused
        self.posts: list[list[str]] = []
        self.writes: dict[str, int] = {}

    def post(self, url: str, json: list[dict[str, Any]], params: dict[str, str], headers: dict[str, str]) -> _Response:
        self.posts.append([d["_key"] for d in json])
        answers: list[dict[str, Any]] = []
        for doc in json:
            if doc["_key"] in self.refused and self.server.live != RECORD_SCHEMA_GENERATION:
                answers.append({"error": True, "errorNum": 1620, "errorMessage": "Document does not match the record schema."})
            elif params.get("overwrite") == "false" and doc["_key"] in self.writes:
                answers.append({"error": True, "errorNum": 1210, "errorMessage": "unique constraint violated"})
            else:
                self.writes[doc["_key"]] = self.writes.get(doc["_key"], 0) + 1
                answers.append({"_key": doc["_key"], "_rev": "1"})
        return _Response(202, answers)


class TestABatchOutsideATransaction:
    """Items land one by one. After a refusal only the refused items are sent again: the others already landed."""

    @pytest.mark.parametrize("kwargs", [{}, {"overwrite_mode": "replace"}, {"overwrite": False}])
    async def test_only_the_refused_items_are_sent_again(self, kwargs: dict[str, Any]) -> None:
        server = FakeServer(live=0)
        client = _Client(server, [])
        session = _BatchSession(server, {"b"})

        async def get_session() -> _BatchSession:
            return session

        client._get_session = get_session  # type: ignore[method-assign]
        result = await client.batch_insert_documents("records", [{"_key": "a"}, {"_key": "b"}, {"_key": "c"}], **kwargs)

        assert session.posts == [["a", "b", "c"], ["b"]]
        assert session.writes == {"a": 1, "b": 1, "c": 1}, "no document was written twice"
        assert result["errors"] == 0

    async def test_an_item_that_the_wide_schema_still_refuses_is_raised_once_without_a_whole_batch_resend(self) -> None:
        server = FakeServer(live=0)
        client = _Client(server, [])
        session = _BatchSession(server, {"b"})

        async def get_session() -> _BatchSession:
            return session

        client._get_session = get_session  # type: ignore[method-assign]
        server.applies = False  # the widening does not apply: the item stays refused and the schema narrow
        with pytest.raises(RecordSchemaNarrowError):
            await client.batch_insert_documents("records", [{"_key": "a"}, {"_key": "b"}])

        assert session.posts == [["a", "b"], ["b"]]
        assert session.writes == {"a": 1}


class _DeleteSession:
    """The transaction endpoint: each DELETE answers with the next scripted status, or raises it when it is an exception."""

    def __init__(self, answers: list[Any]) -> None:
        self.answers, self.deletes = list(answers), 0

    def delete(self, url: str) -> _Response:
        self.deletes += 1
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return _Response(answer, "")


class TestTheAbortAfterARefusal:
    def _client(self, answers: list[Any]) -> tuple[ArangoHTTPClient, _DeleteSession]:
        client = ArangoHTTPClient("http://x", "u", "p", "db", logging.getLogger("test"))
        client.record_guard = _guard(FakeServer(live=0))
        session = _DeleteSession(answers)

        async def get_session() -> _DeleteSession:
            return session

        client._get_session = get_session  # type: ignore[method-assign]
        return client, session

    async def test_an_abort_the_server_never_answered_is_sent_again_by_the_owners_clean_up(self) -> None:
        client, session = self._client([ConnectionError("reset"), 204])

        await client._abort_record_schema_refused_transaction("t1")  # does not raise: the caller must get the narrow error
        assert client.record_guard.is_aborted("t1") and not client.record_guard.is_abort_confirmed("t1")
        with pytest.raises(RecordSchemaNarrowError):
            client.record_guard.check_usable("t1")

        await client.abort_transaction("t1")
        assert session.deletes == 2 and client.record_guard.is_abort_confirmed("t1")

    async def test_an_abort_the_server_refused_is_sent_again(self) -> None:
        client, session = self._client([500, 200])

        await client._abort_record_schema_refused_transaction("t1")
        await client.abort_transaction("t1")
        assert session.deletes == 2

    async def test_a_confirmed_abort_is_not_sent_again_and_a_404_counts_as_confirmed(self) -> None:
        client, session = self._client([204])
        await client._abort_record_schema_refused_transaction("t1")
        await client.abort_transaction("t1")
        assert session.deletes == 1

        client, session = self._client([404])
        await client._abort_record_schema_refused_transaction("t2")
        await client.abort_transaction("t2")
        assert session.deletes == 1 and client.record_guard.is_abort_confirmed("t2")


class _ConnectableClient:
    def __init__(self, **_: Any) -> None:  # noqa: ANN401
        self.record_guard: RecordSchemaGuard | None = None

    async def connect(self) -> bool:
        return True

    async def database_exists(self, name: str) -> bool:
        return True

    async def disconnect(self) -> bool:
        return True


class TestTheGuardFollowsTheConnection:
    def _provider(self, monkeypatch: pytest.MonkeyPatch) -> Any:  # noqa: ANN401
        from app.services.graph_db.arango import arango_http_provider as module

        monkeypatch.setattr(module, "ArangoHTTPClient", _ConnectableClient)
        config = MagicMock()
        config.get_config = AsyncMock(return_value={"url": "http://x", "username": "u", "password": "p", "db": "d"})
        return module.ArangoHTTPProvider(logger=logging.getLogger("test"), config_service=config)

    async def test_disconnect_stops_the_poll(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider(monkeypatch)
        assert await provider.connect()
        guard = provider.http_client.record_guard
        guard.start(60)
        poller = guard._poller
        assert poller is not None and not poller.done()

        assert await provider.disconnect()
        await asyncio.sleep(0)
        assert poller.cancelled() or poller.done()

    async def test_a_second_connect_stops_the_poll_of_the_guard_it_replaces(self, monkeypatch: pytest.MonkeyPatch) -> None:
        provider = self._provider(monkeypatch)
        assert await provider.connect()
        first = provider.http_client.record_guard
        first.start(60)
        poller = first._poller

        assert await provider.connect()
        await asyncio.sleep(0)
        assert provider.http_client.record_guard is not first
        assert poller is not None and (poller.cancelled() or poller.done())
        await provider.disconnect()


class TestApplyingTheRecordsSchemaAtStart:
    def _provider(self, server: FakeServer) -> Any:  # noqa: ANN401
        from types import SimpleNamespace

        from app.services.graph_db.arango import arango_http_provider as module

        provider = module.ArangoHTTPProvider(logger=logging.getLogger("test"), config_service=MagicMock())
        provider.http_client = SimpleNamespace(record_guard=_guard(server), update_collection_schema=AsyncMock(return_value=True))  # type: ignore[assignment]
        return provider

    async def test_a_lower_live_generation_is_replaced_through_the_guard(self) -> None:
        server = FakeServer(live=0)

        assert await self._provider(server)._apply_schema("records", record_schema) is True
        assert server.live == RECORD_SCHEMA_GENERATION and len(server.puts) == 1

    async def test_a_live_schema_that_cannot_be_read_is_not_written_over(self) -> None:
        server = FakeServer(live=0)

        async def unreadable(name: str) -> None:
            raise ConnectionError("down")

        server.get_collection_schema = unreadable  # type: ignore[method-assign]
        provider = self._provider(server)

        assert await provider._apply_schema("records", record_schema) is False
        assert server.puts == [] and not provider.http_client.update_collection_schema.await_count

    async def test_a_newer_generation_is_left_alone(self) -> None:
        server = FakeServer(live=RECORD_SCHEMA_GENERATION + 2)

        assert await self._provider(server)._apply_schema("records", record_schema) is True
        assert server.puts == []


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
