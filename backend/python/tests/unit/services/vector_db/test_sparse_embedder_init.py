"""Unit tests for ``SparseEmbedder``'s initialisation failure handling (#5904).

A failed model build must be shared, not re-run: during a failed attempt the
callers queued on the initialisation lock all raise the same outcome, and
callers arriving within the retry window raise it at once instead of each
re-running fastembed's multi-minute download (one attempt costs ~90 s on an
unreachable Hub and every waiter used to repeat it serially).

``fastembed`` is never imported here — ``_build_model`` is stubbed per
instance, so these tests run without the package or network.
"""

from __future__ import annotations

import asyncio
import time

import pytest

from app.services.vector_db import sparse_embeddings as sparse_embeddings_module
from app.services.vector_db.sparse_embeddings import SparseEmbedder


class _FakeClock:
    """Deterministic ``time.monotonic`` stand-in the tests can advance."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _CountingBuilder:
    """Stand-in for ``_build_model``: fails, hangs, or succeeds on demand."""

    def __init__(self, outcome: Exception | str | None, sleep: float = 0.0) -> None:
        self.calls = 0
        self._outcome = outcome
        self._sleep = sleep

    def __call__(self) -> str | None:
        self.calls += 1
        if self._sleep:
            time.sleep(self._sleep)
        if isinstance(self._outcome, Exception):
            raise self._outcome
        return self._outcome  # a str stands in for the built model


@pytest.fixture
def clock() -> _FakeClock:
    return _FakeClock()


@pytest.fixture(autouse=True)
def _fixed_retry_window(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sparse_embeddings_module, "_failure_retry_seconds", lambda: 60)


def _embedder(clock: _FakeClock, builder: _CountingBuilder) -> SparseEmbedder:
    embedder = SparseEmbedder(clock=clock)
    embedder._build_model = builder  # type: ignore[method-assign]  # instance-level stub
    return embedder


def test_concurrent_callers_share_one_failed_build(clock: _FakeClock) -> None:
    builder = _CountingBuilder(RuntimeError("Hub unreachable"))
    embedder = _embedder(clock, builder)

    async def gather_callers() -> list[BaseException | object]:
        return await asyncio.gather(
            *[embedder._ensure_initialized() for _ in range(4)], return_exceptions=True
        )

    results = asyncio.run(gather_callers())

    assert all(isinstance(r, RuntimeError) for r in results), results
    assert builder.calls == 1, "each waiter must raise the shared failure, not rebuild"


def test_caller_within_the_retry_window_raises_without_rebuilding(
    clock: _FakeClock,
) -> None:
    builder = _CountingBuilder(RuntimeError("Hub unreachable"))
    embedder = _embedder(clock, builder)

    with pytest.raises(RuntimeError):
        asyncio.run(embedder._ensure_initialized())
    assert builder.calls == 1

    clock.advance(5.0)  # well inside the 60 s window
    with pytest.raises(RuntimeError):
        asyncio.run(embedder._ensure_initialized())
    assert builder.calls == 1, "a fresh failure must be raised, not attempted again"


def test_caller_after_the_retry_window_attempts_again(clock: _FakeClock) -> None:
    builder = _CountingBuilder(RuntimeError("Hub unreachable"))
    embedder = _embedder(clock, builder)

    with pytest.raises(RuntimeError):
        asyncio.run(embedder._ensure_initialized())

    clock.advance(61.0)
    with pytest.raises(RuntimeError):
        asyncio.run(embedder._ensure_initialized())
    assert builder.calls == 2


def test_success_after_the_retry_window_clears_the_failure(clock: _FakeClock) -> None:
    builder = _CountingBuilder(RuntimeError("Hub unreachable"))
    embedder = _embedder(clock, builder)
    with pytest.raises(RuntimeError):
        asyncio.run(embedder._ensure_initialized())

    builder._outcome = "the-model"
    clock.advance(61.0)
    model = asyncio.run(embedder._ensure_initialized())
    assert model == "the-model"

    clock.advance(500.0)
    assert asyncio.run(embedder._ensure_initialized()) == "the-model"
    assert builder.calls == 2, (
        "the warmed model must be reused after the failure cleared"
    )


def test_a_timed_out_attempt_is_recorded_as_a_shared_failure(
    clock: _FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    builder = _CountingBuilder("the-model", sleep=0.5)
    embedder = _embedder(clock, builder)
    monkeypatch.setattr(
        sparse_embeddings_module, "_init_attempt_timeout_seconds", lambda: 0.05
    )

    async def gather_callers() -> list[BaseException | object]:
        return await asyncio.gather(
            *[embedder._ensure_initialized() for _ in range(3)], return_exceptions=True
        )

    results = asyncio.run(gather_callers())

    assert all(isinstance(r, asyncio.TimeoutError) for r in results), results
    assert builder.calls == 1, "the timed-out download must not be re-run per waiter"
    clock.advance(0.1)  # still inside the retry window
    with pytest.raises(asyncio.TimeoutError):
        asyncio.run(embedder._ensure_initialized())
    assert builder.calls == 1


def test_a_build_that_never_fails_is_cached(clock: _FakeClock) -> None:
    builder = _CountingBuilder("the-model")
    embedder = _embedder(clock, builder)

    first = asyncio.run(embedder._ensure_initialized())
    second = asyncio.run(embedder._ensure_initialized())

    assert first == second == "the-model"
    assert builder.calls == 1
    assert embedder._init_failure is None
