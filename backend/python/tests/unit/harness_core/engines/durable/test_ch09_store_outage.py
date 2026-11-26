"""CH-09: a blob-store outage under an engine run is waited out, never a crash loop.

Before: a checkpoint, snapshot or workspace object the blob store could not keep raised out of the turn, the kernel
released the attempt ``engine_kernel_error``, and three immediate claims quarantined the run ``crash_loop``. The
object port now raises ``ObjectStoreUnavailable``; the kernel yields ``store_wait`` with backoff (no attempt
counted), the next attempt re-drives the turn, and only a store that stays away past the bound fails the run with a
code the chat words (``store_unavailable``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.kernel.durable.capability import (
    STORE_RECHECK_S,
    STORE_UNAVAILABLE,
    STORE_WAIT,
)
from harness_core.testing.engines.durable import EngineWorld
from harness_core.workspace import ObjectStoreUnavailable
from harness_core.workspace.memory import InMemoryBinaryObjectStore

if TYPE_CHECKING:
    import pytest

RUN = "run-ch09"


def _blob_outage(monkeypatch: pytest.MonkeyPatch, failing_puts: int | None) -> list[int]:
    """The blob store refuses its first ``failing_puts`` writes (every write when ``None``)."""
    put = InMemoryBinaryObjectStore.put
    calls = [0]

    async def flaky(self: InMemoryBinaryObjectStore, *args: object, **kwargs: object) -> object:
        calls[0] += 1
        if failing_puts is None or calls[0] <= failing_puts:
            raise ObjectStoreUnavailable("storage answered 503")
        return await put(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(InMemoryBinaryObjectStore, "put", flaky)
    return calls


async def test_ch09_a_blob_outage_longer_than_the_crash_bound_completes_without_quarantine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    world = EngineWorld()
    world.admit(RUN, "write the notes")
    calls = _blob_outage(monkeypatch, None)
    outage = len(STORE_RECHECK_S) - 1
    seen = []
    for delay in STORE_RECHECK_S[:outage]:
        disposition = await world.attempt(RUN)
        seen.append((disposition.status, disposition.reason))
        assert world.journal.status(RUN) == "queued"
        _ = world.clock.advance(delay)
    assert calls[0] >= outage, "the turn never wrote to the blob store, so nothing was proven"
    monkeypatch.undo()
    final = await world.attempt(RUN)
    assert seen == [("yielded", STORE_WAIT)] * outage
    assert final.status == "completed", final
    types = [e.type for e in world.events(RUN)]
    assert "run.quarantined" not in types and "run.failed" not in types
    assert [e.data["reason"] for e in world.events(RUN) if e.type == "run.yielded"] == [STORE_WAIT] * outage


async def test_ch09_a_store_that_never_returns_fails_typed_after_the_bound(monkeypatch: pytest.MonkeyPatch) -> None:
    world = EngineWorld()
    world.admit(RUN, "write the notes")
    _ = _blob_outage(monkeypatch, None)
    for delay in STORE_RECHECK_S:
        disposition = await world.attempt(RUN)
        assert (disposition.status, disposition.reason) == ("yielded", STORE_WAIT)
        _ = world.clock.advance(delay)
    final = await world.attempt(RUN)
    assert (final.status, final.reason) == ("failed", STORE_UNAVAILABLE)
    assert [e.data for e in world.events(RUN) if e.type == "run.failed"] == [{"errorType": STORE_UNAVAILABLE}]
    assert "run.quarantined" not in [e.type for e in world.events(RUN)]


async def test_ch09_a_brief_outage_resumes_and_the_turn_runs_once_more_at_most(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One failed write: one wait, then the re-driven turn completes; the journal holds one answer."""
    world = EngineWorld()
    world.admit(RUN, "write the notes")
    _ = _blob_outage(monkeypatch, 1)
    first = await world.attempt(RUN)
    assert (first.status, first.reason) == ("yielded", STORE_WAIT)
    _ = world.clock.advance(STORE_RECHECK_S[0])
    final = await world.attempt(RUN)
    assert final.status == "completed", final
    completed = [e for e in world.events(RUN) if e.type == "run.completed"]
    assert len(completed) == 1
