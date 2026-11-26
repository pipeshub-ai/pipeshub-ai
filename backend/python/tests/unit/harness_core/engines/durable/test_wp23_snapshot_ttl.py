"""SNT-1..SNT-3 (OWD-12, WP-23): a sandbox snapshot older than the run's snapshot TTL is never restored, whether it is
the run's own or the one its session carries, and the turn starts cold instead of failing."""

from __future__ import annotations

import dataclasses
import logging
from datetime import timedelta
from typing import TYPE_CHECKING

from harness_core.engines.snapshots import SnapshotPolicy
from harness_core.journal.types import TERMINAL_STATUSES
from harness_core.testing.engines.durable import EngineWorld, ScriptedEngineAdapter

if TYPE_CHECKING:
    import pytest

POLICY = SnapshotPolicy(("/tmp/.cache",), max_bytes=1024 * 1024)
CACHE = "/tmp/.cache/tool/index.json"
DAY_S = 24 * 3600.0


class Watching(ScriptedEngineAdapter):
    def __init__(self) -> None:
        super().__init__()
        self.files_at_open: list[dict[str, bytes]] = []

    async def open(self, sandbox, launch, restore, *, correlation=None):  # type: ignore[no-untyped-def] # noqa: ANN201
        self.files_at_open.append(dict(sandbox.files))
        return await super().open(sandbox, launch, restore, correlation=correlation)


def world(ttl: timedelta) -> tuple[EngineWorld, Watching]:
    adapter = Watching()
    return EngineWorld(adapter=adapter, snapshots=POLICY, snapshot_ttl=ttl), adapter


async def finish(w: EngineWorld, run_id: str) -> str:
    for _ in range(6):
        if w.journal.status(run_id) in TERMINAL_STATUSES:
            break
        if w.journal.status(run_id) == "running":
            w.expire()
        _ = await w.attempt(run_id)
    return str(w.journal.status(run_id))


def expired(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "harness_event", "") == "engine.snapshot.expired"]


async def test_snt1_a_carried_snapshot_inside_the_ttl_is_restored(caplog: pytest.LogCaptureFixture) -> None:
    w, adapter = world(timedelta(days=7))
    w.admit("run-a", f"write {CACHE} warm\nsay ok")
    assert await finish(w, "run-a") == "completed"
    _ = w.clock.advance(6 * DAY_S)
    w.admit("run-b", "say again")
    with caplog.at_level(logging.INFO):
        assert await finish(w, "run-b") == "completed"
    assert adapter.files_at_open[-1].get(CACHE) == b"warm"
    assert expired(caplog) == []


async def test_snt2_a_carried_snapshot_past_the_ttl_is_not_restored_and_the_turn_starts_cold(
    caplog: pytest.LogCaptureFixture,
) -> None:
    w, adapter = world(timedelta(days=7))
    w.admit("run-a", f"write {CACHE} warm\nsay ok")
    assert await finish(w, "run-a") == "completed"
    _ = w.clock.advance(8 * DAY_S)
    w.admit("run-b", "say again")
    with caplog.at_level(logging.INFO):
        assert await finish(w, "run-b") == "completed"
    assert CACHE not in adapter.files_at_open[-1]
    [record] = expired(caplog)
    fields = record.harness  # type: ignore[attr-defined]
    assert (fields["ageS"], fields["ttlDays"]) == (8 * 24 * 3600, 7)


async def test_snt3_the_runs_own_snapshot_past_the_ttl_is_not_restored_on_a_redrive(
    caplog: pytest.LogCaptureFixture,
) -> None:
    w, adapter = world(timedelta(minutes=5))
    w.config = dataclasses.replace(w.config, autosave_changes=2)
    w.admit("run-a", "write /tmp/.cache/a 1\nwrite /tmp/.cache/b 2\nsay ok")
    assert await w.crash_at("run-a", "engine.turn.after_snapshot")
    _ = w.clock.advance(10 * 60.0)
    with caplog.at_level(logging.INFO):
        assert await finish(w, "run-a") == "completed"
    assert "/tmp/.cache/a" not in adapter.files_at_open[-1]
    assert len(expired(caplog)) == 1
