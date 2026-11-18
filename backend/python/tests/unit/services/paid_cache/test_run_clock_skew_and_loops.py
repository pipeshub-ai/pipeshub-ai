"""A manifest's completion time is another replica's clock, and a path lock belongs to one event loop."""
from __future__ import annotations

import asyncio
import json
import threading

from app.services.paid_cache.run import COMPLETION_CLOCK_SKEW_MS, RunScope, _path_lock

ORG, REC = "org-1", "rec-1"


class _Store:
    def __init__(self, body: dict) -> None:
        self._raw = json.dumps(body).encode()

    async def get(self, org_id: str, record_id: str, path: str) -> bytes | None:
        return self._raw


async def _adopted(completed_ms: int) -> list[str]:
    scope = RunScope(ORG, REC, "rev", "w", clock=lambda: 1_000_000.0)
    store = _Store({"keys": ["k1"], "completedMs": completed_ms})
    await scope._adopt(store, "manifests/rev/w.final.json", completed_since_ms=scope.started_ms)  # type: ignore[arg-type]
    return list(scope._keys)


async def test_a_sibling_that_completed_just_before_this_run_by_a_skewed_clock_is_still_adopted() -> None:
    started = 1_000_000_000
    assert await _adopted(started - COMPLETION_CLOCK_SKEW_MS // 2) == ["k1"]       # its clock ran behind ours


async def test_a_completion_long_before_this_run_is_not_a_sibling() -> None:
    started = 1_000_000_000
    assert await _adopted(started - COMPLETION_CLOCK_SKEW_MS - 60_000) == []


def test_two_event_loops_get_different_locks_for_one_manifest_path_and_never_raise() -> None:
    errors: list[BaseException] = []
    locks: list[object] = []
    gate = threading.Barrier(2)

    def worker() -> None:
        async def contend() -> None:
            lock = _path_lock(ORG, REC, "manifests/rev/w.final.json")
            locks.append(lock)
            gate.wait(timeout=5)
            for _ in range(200):
                async with lock:
                    await asyncio.sleep(0)

        try:
            asyncio.run(contend())
        except BaseException as exc:  # the test reports whatever a loop raised
            errors.append(exc)

    threads = [threading.Thread(target=worker, daemon=True) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)

    assert not any(t.is_alive() for t in threads)                     # both workers finished
    assert errors == []
    assert len(locks) == 2 and locks[0] is not locks[1]               # one lock per loop, by construction
