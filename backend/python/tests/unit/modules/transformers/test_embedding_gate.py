# ruff: noqa: ANN401
"""The indexing-side gate in front of the bundled embedding server.

Time is scaled: a "600 s" attempt timeout is a fraction of a second, and every wait is a real (short)
sleep on the real event loop, so the tests exercise the real ``asyncio.wait_for``, ``asyncio.timeout``
and ``RecordProgress``. The fake server queues requests in front of its own slots, as the real one does,
which is what makes a request's timeout count queueing.
"""

from __future__ import annotations

import asyncio
import contextlib
import http.server
import threading
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.documents import Document

import app.utils.embedding_gate as gate_module
from app.exceptions.indexing_exceptions import EmbeddingError
from app.models.blocks import BlocksContainer
from app.modules.indexing.record_progress import (
    RecordProgress,
    StallHistory,
    begin_progress,
    current_progress,
    end_progress,
)
from app.services.resource_governor.feedback import (
    DownstreamFeedback,
    set_default_downstream_feedback,
)
from app.utils.aimodels import get_embedding_model
from app.utils.embedding_gate import (
    BulkGate,
    GateFailFast,
    HealthUnreachable,
    fetch_health,
    get_bulk_gate,
)
from app.utils.embedding_server_client import EmbeddingServerEmbeddings
from app.utils.indexing_workers import indexing_worker_count
from tests.unit.modules.transformers.test_vectorstore_deep import _make_vectorstore
from tests.unit.modules.transformers.test_vectorstore_point_ids import (
    _reconciling_store,
    _text_block,
)

SERVICE = "default"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.delenv(gate_module.CONCURRENCY_ENV, raising=False)
    monkeypatch.delenv("INDEXING_UVICORN_WORKERS", raising=False)
    feedback = DownstreamFeedback()
    set_default_downstream_feedback(feedback)
    yield feedback
    set_default_downstream_feedback(None)


class Health:
    """A fake ``/health``: the body to answer with, or an error."""

    def __init__(self, body: dict[str, Any] | None = None, error: Exception | None = None, delay: float = 0.0) -> None:
        self.body = body if body is not None else {}
        self.error = error
        self.delay = delay
        self.calls = 0

    async def __call__(self, url: str, timeout: float) -> dict[str, Any]:
        self.calls += 1
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.body


def encode_gate(bulk_limit: int, age: float = 0.0) -> dict[str, Any]:
    return {"encode_gate": {"bulk_limit": bulk_limit, "oldest_in_use_age_s": {"query": 0.0, "bulk": age}}}


def make_gate(health: Health) -> BulkGate:
    return BulkGate("http://fake-embedding:8002/v1", fetch=health)


class FakeServer(EmbeddingServerEmbeddings):
    """The bundled-server client with a server behind it that has ``slots`` slots and queues the rest."""

    def __init__(self, gate: BulkGate, *, slots: int = 1, work: Any = 0.0) -> None:
        super().__init__()
        self._test_gate = gate
        self.slots = asyncio.Semaphore(slots)
        self.work = work
        self.arrived = 0
        self.max_arrived = 0
        self.requests = 0
        self.oldest_started: float | None = None
        self.tasks: list[asyncio.Future[None]] = []

    def bulk_gate(self) -> BulkGate:
        return self._test_gate

    def age(self) -> float:
        return 0.0 if self.oldest_started is None else time.monotonic() - self.oldest_started

    async def _serve(self, number: int) -> None:
        async with self.slots:
            self.oldest_started = time.monotonic()
            try:
                await asyncio.sleep(self.work(number) if callable(self.work) else self.work)
            finally:
                self.oldest_started = None

    async def aembed_documents(self, texts: list[str], **_kwargs: Any) -> list[list[float]]:
        # The real server cannot cancel an encode that is running: the slot is held until it ends,
        # whatever the client does.
        self.requests += 1
        self.arrived += 1
        self.max_arrived = max(self.max_arrived, self.arrived)
        task = asyncio.ensure_future(self._serve(self.requests))
        self.tasks.append(task)
        try:
            await asyncio.shield(task)
        finally:
            self.arrived -= 1
        return [[1.0] for _ in texts]

    def stop(self) -> None:
        for task in self.tasks:
            task.cancel()


def store_for(embedder: Any, timeout: float = 0.5) -> Any:
    vs = _make_vectorstore()
    vs.embedding_provider = "default"
    vs.dense_embeddings = embedder
    vs._is_local_cpu_embedding = lambda: True
    return vs


@contextlib.contextmanager
def fast(timeout: float, delay: float = 0.001) -> Any:
    with patch("app.modules.transformers.vectorstore._LOCAL_EMBEDDING_BATCH_TIMEOUT_S", timeout), patch(
        "app.modules.transformers.vectorstore.retry_delay_seconds", return_value=delay
    ):
        yield


async def embed(vs: Any, n: int) -> list[Any]:
    return await asyncio.gather(*(vs._embed_documents_with_retry([f"t{i}"], f"r{i}") for i in range(n)), return_exceptions=True)


class TestWhatIsGated:
    async def test_the_bundled_server_client_is_gated(self) -> None:
        health = Health(encode_gate(2))
        server = FakeServer(make_gate(health), slots=2, work=0.01)
        with fast(5.0):
            results = await embed(store_for(server), 8)
        assert all(not isinstance(r, Exception) for r in results)
        assert server.max_arrived == 2

    async def test_the_clients_own_backoff_does_not_hold_the_permit(self) -> None:
        import openai

        from app.utils.embedding_server_client import (
            EmbeddingServerEmbeddings as Client,
        )

        gate = make_gate(Health(encode_gate(1)))
        with patch("app.utils.embedding_server_client.OpenAIEmbeddings") as openai_cls:
            inner = MagicMock()
            inner.aembed_documents = AsyncMock(side_effect=[openai.APIConnectionError(request=MagicMock()), [[1.0]]])
            openai_cls.return_value = inner
            client = Client(max_retries=3)
        client.bulk_gate = lambda: gate  # type: ignore[method-assign]
        # asyncio.sleep is one object for both loops, so tell them apart by the delay: 7.7 is the client's.
        with (
            patch("app.utils.embedding_retry.retry_delay_seconds", return_value=7.7),
            patch("asyncio.sleep", new_callable=AsyncMock) as slept,
            fast(5.0),
        ):
            result = await store_for(client)._embed_documents_with_retry(["t"], "r")
        assert result == [[1.0]]
        assert inner.aembed_documents.await_count == 2  # the gated loop retried it, with the permit released between
        assert all(c.args != (7.7,) for c in slept.await_args_list)

    async def test_a_mock_embedder_and_other_providers_are_not_gated(self) -> None:
        # A duck-typed lookup would answer for a Mock; a hosted or LAN provider has no gate and takes main's path.
        class OtherProvider:
            def __init__(self) -> None:
                self.in_flight = self.max = 0

            def bulk_gate(self) -> Any:
                raise AssertionError("a provider that is not the bundled client must not be asked for a gate")

            async def aembed_documents(self, texts: list[str]) -> list[list[float]]:
                self.in_flight += 1
                self.max = max(self.max, self.in_flight)
                await asyncio.sleep(0.01)
                self.in_flight -= 1
                return [[1.0] for _ in texts]

        other = OtherProvider()
        with fast(5.0):
            await embed(store_for(other), 6)
        assert other.max == 6

        mock = MagicMock()
        mock.aembed_documents = MagicMock(side_effect=lambda texts: asyncio.sleep(0, result=[[1.0]]))
        with fast(5.0):
            assert (await embed(store_for(mock), 3))[0] == [[1.0]]

    @pytest.mark.parametrize("provider", ["default", "huggingFace", "sentenceTransformers"])
    def test_every_bundled_provider_finds_the_same_gate(self, provider: str) -> None:
        config = {"configuration": {"model": "BAAI/bge-small-en-v1.5"}}
        first = get_embedding_model(provider, config)
        second = get_embedding_model(provider, config)
        assert isinstance(first, EmbeddingServerEmbeddings)
        assert isinstance(first.bulk_gate(), BulkGate)
        assert first.bulk_gate() is second.bulk_gate() is get_bulk_gate(first.base_url)


class TestQueueingIsNotTimingOut:
    async def test_24_batches_reach_the_server_no_wider_than_the_limit(self, _clean_env: DownstreamFeedback) -> None:
        # The queue (24 / 3 x 0.02 s) is longer than the attempt timeout (0.1 s); one batch's work is not.
        server = FakeServer(make_gate(Health(encode_gate(3))), slots=3, work=0.02)
        with fast(0.1):
            results = await embed(store_for(server), 24)
        assert all(not isinstance(r, Exception) for r in results)
        assert server.max_arrived == 3
        assert server.requests == 24
        assert dict(_clean_env.drain().timeouts) == {}

    async def test_150_queued_attempts_at_width_one_none_times_out_on_queue_wait(self, _clean_env: DownstreamFeedback) -> None:
        # The queue (150 x 0.01 s) is three attempt timeouts long; one batch's work is a fiftieth of one.
        server = FakeServer(make_gate(Health(encode_gate(1))), slots=1, work=0.01)
        with fast(0.5):
            results = await embed(store_for(server), 150)
        assert [r for r in results if isinstance(r, Exception)] == []
        assert server.requests == 150
        assert dict(_clean_env.drain().timeouts) == {}


class TestCancellation:
    @staticmethod
    async def hold(gate: BulkGate, release: asyncio.Event, entered: asyncio.Event | None = None) -> None:
        async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
            if entered:
                entered.set()
            await release.wait()

    async def make(self) -> BulkGate:
        gate = make_gate(Health(encode_gate(1)))
        await gate._current_width()
        return gate

    async def test_cancelled_while_queued_leaves_no_permit_behind(self) -> None:
        gate = await self.make()
        release, entered = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(self.hold(gate, release, entered))
        await entered.wait()
        waiter = asyncio.create_task(self.hold(gate, asyncio.Event()))
        await asyncio.sleep(0.01)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        release.set()
        await holder
        assert gate._s.holders == 0 and not gate._s.waiters

    async def test_cancelled_after_the_grant_but_before_it_ran_passes_the_permit_on(self) -> None:
        # The exact sequence: the holder releases (grant), one loop turn lets the grant complete, then the
        # waiter's task is cancelled before it resumes.
        gate = await self.make()
        release, entered = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(self.hold(gate, release, entered))
        await entered.wait()
        waiter = asyncio.create_task(self.hold(gate, asyncio.Event()))
        third_release, third_entered = asyncio.Event(), asyncio.Event()
        await asyncio.sleep(0.01)
        third = asyncio.create_task(self.hold(gate, third_release, third_entered))
        await asyncio.sleep(0.01)
        release.set()
        await holder
        # The grant has completed (the holder's exit queued it ahead of this task's wake-up) and the
        # waiter's task has not run yet.
        assert gate._s.holders == 1
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await asyncio.wait_for(third_entered.wait(), 1.0)
        third_release.set()
        await third
        assert gate._s.holders == 0 and not gate._s.waiters

    async def test_cancelled_while_holding_releases_the_permit(self) -> None:
        gate = await self.make()
        entered = asyncio.Event()
        holder = asyncio.create_task(self.hold(gate, asyncio.Event(), entered))
        await entered.wait()
        holder.cancel()
        with pytest.raises(asyncio.CancelledError):
            await holder
        assert gate._s.holders == 0

    async def test_a_permit_for_a_waiter_on_a_closed_loop_passes_to_the_next_waiter(self) -> None:
        gate = await self.make()
        release, entered = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(self.hold(gate, release, entered))
        await entered.wait()
        dead_loop = asyncio.new_event_loop()
        gate._s.waiters.append(gate_module._Waiter(dead_loop, dead_loop.create_future()))
        dead_loop.close()
        live_release, live_entered = asyncio.Event(), asyncio.Event()
        live = asyncio.create_task(self.hold(gate, live_release, live_entered))
        await asyncio.sleep(0.01)
        release.set()
        await holder
        await asyncio.wait_for(live_entered.wait(), 1.0)
        live_release.set()
        await live
        assert gate._s.holders == 0

    async def test_a_waiter_on_another_loop_is_woken_on_its_own_loop(self) -> None:
        gate = await self.make()
        release, entered = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(self.hold(gate, release, entered))
        await entered.wait()
        other = asyncio.new_event_loop()
        thread = threading.Thread(target=other.run_forever, daemon=True)
        thread.start()
        try:

            async def second() -> int:
                async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                    return threading.get_ident()

            future = asyncio.run_coroutine_threadsafe(second(), other)
            await asyncio.sleep(0.05)
            assert not future.done()
            release.set()
            await holder
            woke_on = await asyncio.wrap_future(future)
            assert woke_on == thread.ident
            assert gate._s.holders == 0
        finally:
            other.call_soon_threadsafe(other.stop)
            thread.join(2)
            other.close()


class TestServerThatIsHungOrDown:
    @staticmethod
    async def run_waiters(gate: BulkGate, count: int, *, holder_error: BaseException) -> list[Any]:
        """A holder fails with ``holder_error`` while ``count`` waiters that queued before it are waiting."""
        release, entered = asyncio.Event(), asyncio.Event()

        async def holder() -> None:
            with pytest.raises(type(holder_error)):
                async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                    entered.set()
                    await release.wait()
                    raise holder_error

        async def waiter() -> str:
            try:
                async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                    return "ran"
            except GateFailFast:
                return "failfast"

        h = asyncio.create_task(holder())
        await entered.wait()
        waiters = [asyncio.create_task(waiter()) for _ in range(count)]
        await asyncio.sleep(0.02)
        release.set()
        await h
        return await asyncio.gather(*waiters)

    async def gate_with(self, health: Health) -> BulkGate:
        gate = make_gate(Health(encode_gate(1)))
        await gate._current_width()
        gate._fetch = health
        return gate

    async def test_a_healthy_slow_server_fails_nobody_fast(self, _clean_env: DownstreamFeedback) -> None:
        # One holder's attempt timed out; the oldest encode reads about one attempt timeout (0.5 of 1.0 x 2).
        gate = await self.gate_with(Health(encode_gate(1, age=1.0)))
        assert await self.run_waiters(gate, 24, holder_error=TimeoutError()) == ["ran"] * 24
        assert dict(_clean_env.drain().timeouts) == {}

    async def test_a_hung_server_fails_every_earlier_waiter_fast_and_reports_once(self, _clean_env: DownstreamFeedback) -> None:
        gate = await self.gate_with(Health(encode_gate(1, age=2.5)))
        started = time.monotonic()
        assert await self.run_waiters(gate, 24, holder_error=TimeoutError()) == ["failfast"] * 24
        assert time.monotonic() - started < 2.0
        assert dict(_clean_env.drain().timeouts) == {SERVICE: 1}

    async def test_a_stuck_query_does_not_fail_bulk_waiters_fast(self, _clean_env: DownstreamFeedback) -> None:
        # The reserved query lane holds an old encode, but bulk is moving: its age is what counts.
        body = {"encode_gate": {"bulk_limit": 1, "oldest_in_use_age_s": {"query": 99.0, "bulk": 0.0}}}
        gate = await self.gate_with(Health(body))
        assert await self.run_waiters(gate, 4, holder_error=TimeoutError()) == ["ran"] * 4

    @pytest.mark.parametrize("ages", [{"query": 99.0}, None, 7])
    async def test_a_health_body_without_a_bulk_age_is_read_as_not_stuck(self, ages: Any) -> None:
        assert gate_module._oldest_in_use_age({"encode_gate": {"oldest_in_use_age_s": ages}}) == 0.0

    async def test_a_server_that_is_down_fails_every_earlier_waiter_fast(self, _clean_env: DownstreamFeedback) -> None:
        gate = await self.gate_with(Health(error=HealthUnreachable("ConnectError")))
        import openai

        error = openai.APIConnectionError(request=MagicMock())
        assert await self.run_waiters(gate, 24, holder_error=error) == ["failfast"] * 24
        assert dict(_clean_env.drain().timeouts) == {SERVICE: 1}

    async def test_a_failure_that_is_not_retriable_does_not_start_a_check(self) -> None:
        health = Health(error=HealthUnreachable("x"))
        gate = await self.gate_with(health)
        assert await self.run_waiters(gate, 3, holder_error=ValueError("bad model")) == ["ran"] * 3
        assert health.calls == 0

    async def test_a_healthy_slow_encode_of_661_seconds_fails_no_queued_record(self, _clean_env: DownstreamFeedback) -> None:
        # Scaled by 1/3000: attempt timeout 0.2 s, first encode 0.22 s (661 s of 600 s). The holder gives up
        # at 0.2 s while the encode still runs, so the server's oldest encode reads 0.2 s: not above 2 x 0.2 s.
        server = FakeServer(make_gate(Health()), slots=1, work=lambda n: 0.22 if n == 1 else 0.02)
        server._test_gate._fetch = lambda url, timeout: _health_of(server)
        failfasts: list[Any] = []
        original = BulkGate._failfast_if_server_stuck

        async def watch(self: BulkGate, *a: Any) -> None:
            try:
                await original(self, *a)
            except GateFailFast as exc:
                failfasts.append(exc)
                raise

        with fast(0.2), patch.object(BulkGate, "_failfast_if_server_stuck", watch):
            results = await embed(store_for(server), 5)
        assert failfasts == []
        assert [r for r in results if isinstance(r, Exception)] == []

    async def test_a_hung_server_costs_less_than_serial_timeouts(self) -> None:
        async def run(factor: float) -> float:
            server = FakeServer(make_gate(Health()), slots=1, work=3600.0)
            server._test_gate._fetch = lambda url, timeout: _health_of(server)
            with fast(0.1), patch.object(gate_module, "STUCK_AGE_ATTEMPTS", factor):
                started = time.monotonic()
                try:
                    results = await embed(store_for(server), 6)
                finally:
                    server.stop()
            assert all(isinstance(r, EmbeddingError) for r in results)
            return time.monotonic() - started

        with_rule = await run(2.0)
        # A rule that never fires is the same gate without the hung-server check: every waiter runs its own timeouts in turn.
        without = await run(1e9)
        assert with_rule < without * 0.75


async def _health_of(server: FakeServer) -> dict[str, Any]:
    return encode_gate(1, age=server.age())


class TestFailFastInTheRetryLoop:
    async def test_a_gate_fail_fast_is_retried_and_not_reported_again_by_the_loop(self, _clean_env: DownstreamFeedback) -> None:
        gate = make_gate(Health(encode_gate(1)))

        @contextlib.asynccontextmanager
        async def refuses(progress: Any, **kw: Any) -> Any:
            raise GateFailFast("stuck")
            yield

        gate.slot = refuses  # type: ignore[method-assign]
        server = FakeServer(gate)
        with fast(5.0), pytest.raises(EmbeddingError):
            await store_for(server)._embed_documents_with_retry(["t"], "r")
        assert server.requests == 0
        # The gate reports once per failure; the loop must not add one per waiter's attempt.
        assert dict(_clean_env.drain().timeouts) == {}


class TestWidth:
    async def width(self, health: Health, **kw: Any) -> int | None:
        gate = make_gate(health)
        return await gate._current_width()

    @pytest.mark.parametrize(
        ("body", "workers", "expected"),
        [
            (encode_gate(15), "1", 15),
            (encode_gate(15), "4", 3),
            (encode_gate(3), "8", 1),
            ({"cpu": {"max_concurrent_embeddings": 16}}, "1", 15),
            ({"cpu": {"max_concurrent_embeddings": 1}}, "1", 1),
            ({"cpu": {"max_concurrent_embeddings": 16}, **encode_gate(2)}, "1", 2),
            ({"status": "healthy"}, "1", None),
            (encode_gate(0), "1", None),
        ],
    )
    async def test_width_is_the_servers_limit_over_the_workers_floor_one(
        self, monkeypatch: pytest.MonkeyPatch, body: dict[str, Any], workers: str, expected: int | None
    ) -> None:
        monkeypatch.setenv("INDEXING_UVICORN_WORKERS", workers)
        assert await self.width(Health(body)) == expected

    @pytest.mark.parametrize(("env", "expected"), [("5", 5), ("0", None), ("", 7), ("abc", 7), ("-2", None)])
    async def test_the_env_var_wins_zero_is_unlimited_and_empty_or_invalid_is_unset(
        self, monkeypatch: pytest.MonkeyPatch, env: str, expected: int | None
    ) -> None:
        monkeypatch.setenv(gate_module.CONCURRENCY_ENV, env)
        assert await self.width(Health(encode_gate(7))) == expected

    async def test_no_gate_is_logged_once_per_process_across_many_batches(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gate_module, "_logged_no_gate", False)
        gate = make_gate(Health({"status": "healthy"}))
        with patch.object(gate_module.logger, "info") as info:
            for _ in range(20):
                async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                    pass
            other = make_gate(Health({"status": "healthy"}))
            async with other.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                pass
        assert info.call_count == 1
        assert "gate is off" in info.call_args.args[0]

    async def test_a_gate_that_is_on_logs_nothing(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(gate_module, "_logged_no_gate", False)
        gate = make_gate(Health(encode_gate(2)))
        with patch.object(gate_module.logger, "info") as info:
            async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                pass
        info.assert_not_called()

    async def test_an_unreachable_server_before_any_answer_means_no_gate(self) -> None:
        assert await self.width(Health(error=HealthUnreachable("x"))) is None

    async def test_an_error_keeps_the_last_known_width_and_refreshes_single_flight(self) -> None:
        now = [1000.0]
        health = Health(encode_gate(6))
        gate = BulkGate("http://fake:8002/v1", fetch=health, clock=lambda: now[0])
        assert await gate._current_width() == 6
        now[0] += 30
        await gate._current_width()
        assert health.calls == 1
        now[0] += 31
        health.error = HealthUnreachable("x")
        assert await asyncio.gather(*(gate._current_width() for _ in range(10))) == [6] * 10
        assert health.calls == 2
        now[0] += 6
        health.error, health.body = None, encode_gate(9)
        assert await gate._current_width() == 9

    async def test_a_wider_limit_wakes_queued_waiters(self) -> None:
        now = [0.0]
        health = Health(encode_gate(1))
        gate = BulkGate("http://fake:8002/v1", fetch=health, clock=lambda: now[0])
        release, entered = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(TestCancellation.hold(gate, release, entered))
        await entered.wait()
        second_entered = asyncio.Event()
        second = asyncio.create_task(TestCancellation.hold(gate, release, second_entered))
        await asyncio.sleep(0.01)
        assert not second_entered.is_set()
        now[0] += 61
        health.body = encode_gate(2)
        await gate._current_width()
        await asyncio.wait_for(second_entered.wait(), 1.0)
        release.set()
        await asyncio.gather(holder, second)

    def test_the_worker_count_comes_from_one_helper(self, monkeypatch: pytest.MonkeyPatch) -> None:
        for raw, expected in [("3", 3), ("0", 1), ("-1", 1)]:
            monkeypatch.setenv("INDEXING_UVICORN_WORKERS", raw)
            assert indexing_worker_count() == expected
        # The service refuses a non-numeric value at start; the gate, which only reads it, counts one worker.
        monkeypatch.setenv("INDEXING_UVICORN_WORKERS", "x")
        with pytest.raises(ValueError):
            indexing_worker_count()
        assert gate_module._workers() == 1
        monkeypatch.delenv("INDEXING_UVICORN_WORKERS")
        assert indexing_worker_count() == 1


class _Handler(http.server.BaseHTTPRequestHandler):
    status = 200
    body = b'{"encode_gate": {"bulk_limit": 4}}'

    def do_GET(self) -> None:
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(self.body)

    def log_message(self, *a: Any) -> None:
        pass


class TestHealthRead:
    @contextlib.contextmanager
    def serve(self, status: int, body: bytes = b"{}") -> Any:
        handler = type("H", (_Handler,), {"status": status, "body": body})
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{server.server_address[1]}"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)

    async def test_a_200_is_parsed(self) -> None:
        with self.serve(200, b'{"encode_gate": {"bulk_limit": 4}}') as url:
            assert await fetch_health(f"{url}/health", 2.0) == {"encode_gate": {"bulk_limit": 4}}

    async def test_a_5xx_is_unreachable(self) -> None:
        with self.serve(503) as url, pytest.raises(HealthUnreachable):
            await fetch_health(f"{url}/health", 2.0)

    async def test_a_refused_connection_is_unreachable(self) -> None:
        with self.serve(200) as url:
            pass
        with pytest.raises(HealthUnreachable):
            await fetch_health(f"{url}/health", 2.0)

    async def test_an_old_server_without_the_route_gives_no_information(self) -> None:
        with self.serve(404) as url:
            assert await fetch_health(f"{url}/health", 2.0) == {}

    def test_the_health_url_sits_beside_the_openai_root(self) -> None:
        assert BulkGate("http://h:8002/v1").health_url == "http://h:8002/health"
        assert BulkGate("http://h:8002").health_url == "http://h:8002/health"


class TestTheRecordBudget:
    """A real ``RecordProgress`` and a real ``asyncio.timeout``: the pause must survive the next batch's re-arm."""

    @staticmethod
    def progress(*, floor: float, gate_limit: float = 1800.0) -> RecordProgress:
        return RecordProgress(
            floor_seconds=floor, ceiling_seconds=floor, record_ceiling_seconds=1e9,
            history=StallHistory(), gate_pause_limit_seconds=gate_limit,
        )

    async def run_record(self, *, cap: float, floor: float, waits: list[float], gate_limit: float = 1800.0) -> tuple[RecordProgress, asyncio.Timeout]:
        """Three batches; before each, another record holds the only permit for ``wait`` seconds."""
        gate = make_gate(Health(encode_gate(1)))
        await gate._current_width()
        server = FakeServer(gate, slots=1, work=0.0)
        vs = store_for(server)
        progress = self.progress(floor=floor, gate_limit=gate_limit)

        async def other_record(wait: float, ready: asyncio.Event) -> None:
            async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                ready.set()
                await asyncio.sleep(wait)

        begin_progress(progress)
        try:
            async with asyncio.timeout(cap) as budget:
                progress.attach(budget)
                progress.plan(len(waits), 0)
                for i, wait in enumerate(waits):
                    ready = asyncio.Event()
                    blocker = asyncio.create_task(other_record(wait, ready))
                    await ready.wait()
                    with fast(5.0):
                        await vs._embed_documents_with_retry([f"t{i}"], "r")
                    await blocker
                    progress.beat(1)
                progress.finish()
        finally:
            end_progress()
        return progress, budget

    async def test_three_batches_each_waiting_over_a_third_of_the_budget_are_not_cut(self) -> None:
        # Waits total 0.36 s against a 0.3 s budget, and each wait is longer than the 0.05 s stall floor.
        progress, budget = await self.run_record(cap=0.3, floor=0.05, waits=[0.12, 0.12, 0.12])
        assert not budget.expired()
        assert progress.new_units == 3
        # The wait is not embedding time.
        assert progress.attempt_seconds() < 0.2

    async def test_without_a_pause_the_same_record_is_cut(self) -> None:
        with patch.object(RecordProgress, "gate_wait", contextlib.asynccontextmanager(_no_pause)), pytest.raises(TimeoutError):
            await self.run_record(cap=0.3, floor=5.0, waits=[0.12, 0.12, 0.12])

    async def test_the_pause_is_bounded_and_then_the_wait_counts_again(self) -> None:
        # 0.1 s of pause allowed, then the 0.2 s budget counts down: cut at about 0.3 s, not after the 5 s wait.
        # The bound sits halfway, so a loaded runner does not fail it.
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await self.run_record(cap=0.2, floor=5.0, waits=[5.0], gate_limit=0.1)
        assert time.monotonic() - started < 2.5

    async def test_a_first_wait_longer_than_the_stall_floor_is_not_a_stall_cut(self) -> None:
        progress, budget = await self.run_record(cap=5.0, floor=0.05, waits=[0.2])
        assert not budget.expired()
        assert progress.new_units == 1

    async def test_with_no_progress_object_nothing_is_paused(self) -> None:
        gate = make_gate(Health(encode_gate(1)))
        await gate._current_width()
        ready = asyncio.Event()

        async def other() -> None:
            async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                ready.set()
                await asyncio.sleep(0.2)

        blocker = asyncio.create_task(other())
        await ready.wait()
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.05):
                async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                    pass
        await blocker

    async def test_a_progress_object_that_is_not_planned_pauses_nothing(self) -> None:
        progress = self.progress(floor=1.0)
        async with progress.gate_wait():
            pass
        assert progress._gate_paused == 0.0


async def _no_pause(self: RecordProgress) -> Any:
    yield


class TestColdStartAndConfiguration:
    async def test_24_concurrent_first_calls_with_a_slow_health_stay_within_the_width(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Before the first read the width is unknown; callers must wait for that read, not run ungated.
        monkeypatch.setattr(gate_module, "_logged_no_gate", False)
        health = Health(encode_gate(2), delay=0.1)
        gate = make_gate(health)
        in_flight = peak = 0

        async def batch() -> None:
            nonlocal in_flight, peak
            async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                in_flight += 1
                peak = max(peak, in_flight)
                await asyncio.sleep(0.01)
                in_flight -= 1

        with patch.object(gate_module.logger, "info") as info:
            await asyncio.gather(*(batch() for _ in range(24)))
        assert peak == 2
        assert health.calls == 1
        info.assert_not_called()

    async def test_a_stale_width_is_refreshed_by_one_caller_while_the_rest_use_the_last_one(self) -> None:
        now = [0.0]
        health = Health(encode_gate(4), delay=0.05)
        gate = BulkGate("http://fake:8002/v1", fetch=health, clock=lambda: now[0])
        await gate._current_width()
        now[0] += 61
        assert await asyncio.gather(*(gate._current_width() for _ in range(10))) == [4] * 10
        assert health.calls == 2

    async def test_a_garbage_env_value_warns_once_per_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(gate_module.CONCURRENCY_ENV, "abc")
        monkeypatch.setattr(gate_module, "_warned_env", set())
        gate = make_gate(Health(encode_gate(2)))
        with patch.object(gate_module.logger, "warning") as warning:
            for _ in range(10):
                async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                    pass
        assert warning.call_count == 1

    def test_one_gate_per_server_url(self) -> None:
        a, b = get_bulk_gate("http://one:8002/v1"), get_bulk_gate("http://two:8002/v1")
        assert a is not b
        assert a is get_bulk_gate("http://one:8002/v1")


class TestGrantsAndFairness:
    async def make(self) -> BulkGate:
        gate = make_gate(Health(encode_gate(1)))
        await gate._current_width()
        return gate

    async def test_a_permit_granted_to_a_waiter_on_a_stopped_loop_comes_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # The loop is stopped, not closed: the grant is never delivered, and the permit must not stay lost.
        monkeypatch.setattr(gate_module, "GRANT_DELIVERY_SECONDS", 0.1)
        gate = await self.make()
        release, entered = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(TestCancellation.hold(gate, release, entered))
        await entered.wait()
        dead = asyncio.new_event_loop()
        thread = threading.Thread(target=dead.run_forever, daemon=True)
        thread.start()

        async def doomed() -> None:
            async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                pass

        asyncio.run_coroutine_threadsafe(doomed(), dead)
        while not gate._s.waiters:  # noqa: ASYNC110
            await asyncio.sleep(0.005)
        dead.call_soon_threadsafe(dead.stop)
        thread.join(2)
        live_release, live_entered = asyncio.Event(), asyncio.Event()
        live = asyncio.create_task(TestCancellation.hold(gate, live_release, live_entered))
        await asyncio.sleep(0.02)
        release.set()
        await holder
        await asyncio.wait_for(live_entered.wait(), 2.0)
        live_release.set()
        await live
        assert gate._s.holders == 0
        dead.close()

    async def test_a_waiter_cancelled_between_the_grant_and_its_delivery_returns_the_permit(self) -> None:
        gate = await self.make()
        gate._s.holders = 1  # another batch holds the only permit
        other = asyncio.new_event_loop()
        thread = threading.Thread(target=other.run_forever, daemon=True)
        thread.start()
        tasks: list[asyncio.Task[None]] = []

        async def second() -> None:
            tasks.append(asyncio.current_task())  # type: ignore[arg-type]
            async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                pass

        try:
            asyncio.run_coroutine_threadsafe(second(), other)
            while not gate._s.waiters:  # noqa: ASYNC110
                await asyncio.sleep(0.005)
            # The waiter's cancellation is stuck behind the gate's lock while the holder's release grants it the
            # permit: the grant is made, then the waiter finds itself cancelled before the grant is delivered.
            with gate._lock:
                other.call_soon_threadsafe(tasks[0].cancel)
                await asyncio.sleep(0.1)
                gate._s.holders = 0
                gate._pump_locked()
                assert gate._s.holders == 1
            await asyncio.sleep(0.3)
            assert gate._s.holders == 0
            assert not gate._s.waiters and not gate._s.granted
        finally:
            other.call_soon_threadsafe(other.stop)
            thread.join(2)
            other.close()

    async def test_waiters_are_served_in_arrival_order_and_a_late_caller_does_not_jump_the_queue(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        gate = await self.make()
        release, entered = asyncio.Event(), asyncio.Event()
        holder = asyncio.create_task(TestCancellation.hold(gate, release, entered))
        await entered.wait()
        first_entered, late_entered = asyncio.Event(), asyncio.Event()
        first_release, late_release = asyncio.Event(), asyncio.Event()
        first = asyncio.create_task(TestCancellation.hold(gate, first_release, first_entered))
        await asyncio.sleep(0.02)
        # Room appears (the width was raised) while the first waiter is still queued.
        monkeypatch.setenv(gate_module.CONCURRENCY_ENV, "2")
        late = asyncio.create_task(TestCancellation.hold(gate, late_release, late_entered))
        await asyncio.sleep(0.05)
        assert first_entered.is_set()
        assert not late_entered.is_set()
        release.set()
        first_release.set()
        await asyncio.wait_for(late_entered.wait(), 1.0)
        late_release.set()
        await asyncio.gather(holder, first, late)

    async def test_the_permit_goes_back_when_the_loop_refuses_the_grant(self) -> None:
        gate = await self.make()
        loop = MagicMock()
        loop.is_closed.return_value = False
        loop.call_soon_threadsafe.side_effect = RuntimeError("closing")
        gate._s.holders = 1
        gate._s.waiters.append(gate_module._Waiter(loop, MagicMock()))
        gate._release()
        assert gate._s.holders == 0
        assert not gate._s.granted


class TestSharedHealthCheck:
    async def test_one_health_answer_serves_every_waiter_of_a_generation(self, _clean_env: DownstreamFeedback) -> None:
        health = Health(encode_gate(1, age=9.0), delay=0.05)
        gate = make_gate(Health(encode_gate(1)))
        await gate._current_width()
        gate._fetch = health
        started = time.monotonic()
        assert await TestServerThatIsHungOrDown.run_waiters(gate, 24, holder_error=TimeoutError()) == ["failfast"] * 24
        assert health.calls == 1
        assert time.monotonic() - started < 0.6


class TestOverlappingWaits:
    async def test_overlapping_waits_of_one_record_are_one_pause_from_the_first_start_to_the_last_end(self) -> None:
        progress = TestTheRecordBudget.progress(floor=5.0)
        begin_progress(progress)
        try:
            async with asyncio.timeout(0.2) as budget:
                progress.attach(budget)
                progress.plan(1, 0)

                async def wait(delay: float, length: float) -> None:
                    await asyncio.sleep(delay)
                    async with progress.gate_wait():
                        await asyncio.sleep(length)

                await asyncio.gather(wait(0.0, 0.15), wait(0.05, 0.35))
        finally:
            end_progress()
        assert not budget.expired()
        assert 0.38 < progress._gate_paused < 0.6
        assert progress._gate_waits == 0

    async def test_a_gate_wait_turns_the_stall_timer_off(self) -> None:
        progress = TestTheRecordBudget.progress(floor=0.05)
        begin_progress(progress)
        try:
            async with asyncio.timeout(5.0) as budget:
                progress.attach(budget)
                progress.plan(1, 0)
                async with progress.gate_wait():
                    assert progress._calls == 1
                    await asyncio.sleep(0.25)
        finally:
            end_progress()
        assert not budget.expired()


class TestBudgetPause:
    async def test_one_wait_longer_than_the_whole_budget_does_not_cut_a_record_that_is_only_queued(self) -> None:
        progress, budget = await TestTheRecordBudget().run_record(cap=0.3, floor=5.0, waits=[0.5])
        assert not budget.expired()
        assert progress.new_units == 1

    async def test_the_pause_ends_at_the_allowance_not_at_the_end_of_the_wait(self) -> None:
        # Budget 0.3 s, 0.4 s of pause allowed: held until 0.7 s, then counting resumes and the record is cut.
        # The bounds sit halfway to the wrong answers: no pause cuts at 0.3 s, a pause to the end of the wait at 2.3 s.
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await TestTheRecordBudget().run_record(cap=0.3, floor=5.0, waits=[2.0], gate_limit=0.4)
        assert 0.5 < time.monotonic() - started < 1.5

    async def test_the_allowance_is_shared_by_every_wait_of_the_record(self) -> None:
        # 2 s budget, 1.6 s allowance: the first wait uses 1 s of it, so the second has 0.6 s left and the record is
        # cut at 3.6 s. A fresh allowance for each wait would cut at 4.6 s and no pause at 2 s; the bounds sit
        # halfway to each, so a loaded runner's scheduling delay cannot move the answer across them.
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            await TestTheRecordBudget().run_record(cap=2.0, floor=20.0, waits=[1.0, 12.0], gate_limit=1.6)
        assert 2.8 < time.monotonic() - started < 4.1


class TestEmbedderSwappedDuringTheWait:
    async def test_the_embedder_that_chose_the_gate_makes_every_call(self) -> None:
        gate = make_gate(Health(encode_gate(1)))
        await gate._current_width()
        server = FakeServer(gate, slots=1, work=0.0)
        vs = store_for(server)
        # Not the bundled client: it does not take ``single_attempt``.
        replacement = MagicMock()
        replacement.aembed_documents = AsyncMock(side_effect=lambda texts: [[1.0] for _ in texts])

        ready = asyncio.Event()

        async def other_record() -> None:
            async with gate.slot(None, attempt_timeout=1.0, service_name=SERVICE):
                ready.set()
                await asyncio.sleep(0.1)
                vs.dense_embeddings = replacement

        blocker = asyncio.create_task(other_record())
        await ready.wait()
        with fast(5.0):
            result = await vs._embed_documents_with_retry(["a"], "r")
        await blocker
        assert result == [[1.0]]
        replacement.aembed_documents.assert_not_called()
        assert server.requests == 1


class TestReconciliationEmbedIsPlanned:
    @pytest.mark.parametrize("slow", ["changed blocks", "record summary", "code residue"])
    async def test_a_gate_wait_longer_than_the_budget_does_not_cancel_a_reconciliation(self, slow: str) -> None:
        store, _ = _reconciling_store()
        member = object()
        store._build_record_summary_document = lambda *_a: Document(page_content="record summary", metadata={})

        async def queued(docs: Any, *_args: Any) -> None:
            if docs[0].page_content == slow or (slow == "changed blocks" and docs[0].metadata.get("blockId")):
                async with current_progress().gate_wait():
                    await asyncio.sleep(1.5)

        store._process_document_chunks = queued
        progress = RecordProgress(
            floor_seconds=5.0, ceiling_seconds=5.0, record_ceiling_seconds=1e9,
            history=StallHistory(), gate_pause_limit_seconds=1800.0,
        )
        container = BlocksContainer(
            blocks=[_text_block("b-new", "a long enough sentence to embed as text", 0)], block_groups=[]
        )
        record = MagicMock(semantic_metadata={"summary": "s"}, record_name="n", external_record_id="p")
        begin_progress(progress)
        try:
            with patch("app.modules.transformers.vectorstore._is_code_block", lambda b: b is member), patch(
                "app.modules.transformers.vectorstore.code_residue_documents",
                lambda *_a: [Document(page_content="code residue", metadata={})],
            ):
                async with asyncio.timeout(1.0) as budget:
                    progress.attach(budget)
                    await store.index_documents(
                        container, "org-1", "rec-1", "vr-1", record=record, member_blocks=[member],
                        block_ids_to_delete={"b-gone"}, is_reconciliation=True,
                    )
        finally:
            end_progress()
        assert not budget.expired()


class TestReconciliationStallTimerRunsOnlyWhileEmbedding:
    @staticmethod
    async def run(*, floor: float, between: float, stalled: float) -> asyncio.Timeout:
        """Summary embed, a non-embed stretch of ``between`` s, then a changed-blocks embed that hangs ``stalled`` s."""
        store, _ = _reconciling_store()
        store._build_record_summary_document = lambda *_a: Document(page_content="record summary", metadata={})
        real_delete = store.delete_blocks_by_ids

        async def embed(docs: Any, *_args: Any) -> None:
            if docs[0].page_content != "record summary":
                await asyncio.sleep(stalled)

        async def slow_delete(*args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(between)
            return await real_delete(*args, **kwargs)

        store._process_document_chunks = embed
        store.delete_blocks_by_ids = slow_delete
        progress = RecordProgress(
            floor_seconds=floor, ceiling_seconds=floor, record_ceiling_seconds=1e9, history=StallHistory(),
        )
        container = BlocksContainer(
            blocks=[_text_block("b-new", "a long enough sentence to embed as text", 0)], block_groups=[]
        )
        record = MagicMock(semantic_metadata={"summary": "s"}, record_name="n", external_record_id="p")
        begin_progress(progress)
        try:
            async with asyncio.timeout(5.0) as budget:
                progress.attach(budget)
                await store.index_documents(
                    container, "org-1", "rec-1", "vr-1", record=record, block_ids_to_delete={"b-gone"},
                    is_reconciliation=True,
                )
        except TimeoutError:
            pass
        finally:
            end_progress()
        return budget

    async def test_a_long_stretch_that_is_not_embedding_is_not_a_stall(self) -> None:
        # The reindex delete between the summary and the blocks runs 0.4 s against a 0.15 s stall floor.
        assert not (await self.run(floor=0.15, between=0.4, stalled=0.0)).expired()

    async def test_an_embed_that_stops_beating_is_still_a_stall(self) -> None:
        assert (await self.run(floor=0.15, between=0.0, stalled=3.0)).expired()
