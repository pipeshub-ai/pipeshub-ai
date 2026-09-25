"""`_sync_until` in the Google Drive folder-filter suites, with the stack stubbed.

The loop re-syncs until a Drive edit is visible in the graph. What it must get
right is its time budget: every sync wait it starts must be long enough to
settle, a sync wait that times out must not skip the graph check, and a change
that never arrives must end in this loop's own error within the budget.
"""

from __future__ import annotations

import asyncio
import importlib.util
import time
from pathlib import Path
from types import ModuleType

import pytest

_SUITES = Path(__file__).resolve().parents[1] / "connectors"
_MODULES = {
    "individual": _SUITES / "google_drive_individual" / "google_drive_individual_integration_test.py",
    "workspace": _SUITES / "google_drive_workspace" / "google_drive_workspace_integration_test.py",
}


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"_drive_suite_{name}", _MODULES[name])
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _Client:
    def toggle_sync(self, connector_id: str, *, enable: bool) -> None:
        pass

    def wait(self, seconds: float) -> None:
        time.sleep(seconds)


@pytest.fixture(params=sorted(_MODULES))
def suite(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    module = _load(request.param)
    # Scaled down 100x: a 3s budget, 0.13s of restart, a 0.39s settle floor.
    monkeypatch.setattr(module, "_SYNC_TIMEOUT_SEC", 3.0)
    monkeypatch.setattr(module, "_RESTART_SYNC_PAUSE_SEC", (0.05, 0.08))
    monkeypatch.setattr(module, "_SYNC_WAIT_FLOOR_SEC", 0.39)
    monkeypatch.setattr(module, "_RESYNC_INTERVAL_SEC", 0.15)
    monkeypatch.setattr(module, "_GRAPH_POLL_INTERVAL_SEC", 0.1)
    return module


def _fake_wait(suite: ModuleType, timeouts: list[float], *, settles: bool = True):
    async def wait_for_sync_completion(client, graph, connector_id, *, timeout):
        timeouts.append(timeout)
        if not settles or timeout < suite._SYNC_WAIT_FLOOR_SEC:
            await asyncio.sleep(min(timeout, suite._SYNC_WAIT_FLOOR_SEC))
            raise TimeoutError("did not settle")
        await asyncio.sleep(0.05)
        return 1

    return wait_for_sync_completion


def _run(suite: ModuleType, check) -> None:
    asyncio.run(suite._sync_until(_Client(), object(), "c1", check, description="the moved folder"))


def test_a_change_seen_on_a_later_round_passes(suite, monkeypatch) -> None:
    timeouts: list[float] = []
    monkeypatch.setattr(suite, "wait_for_sync_completion", _fake_wait(suite, timeouts))

    async def check() -> bool:
        return len(timeouts) >= 3

    _run(suite, check)
    assert len(timeouts) == 3
    assert all(t >= suite._SYNC_WAIT_FLOOR_SEC for t in timeouts)


def test_a_sync_wait_that_times_out_still_checks_the_graph(suite, monkeypatch) -> None:
    timeouts: list[float] = []
    monkeypatch.setattr(suite, "wait_for_sync_completion", _fake_wait(suite, timeouts, settles=False))

    async def check() -> bool:
        return True

    _run(suite, check)
    assert len(timeouts) == 1


def test_a_change_that_never_arrives_fails_within_the_budget(suite, monkeypatch) -> None:
    timeouts: list[float] = []
    monkeypatch.setattr(suite, "wait_for_sync_completion", _fake_wait(suite, timeouts))

    async def check() -> bool:
        return False

    started = time.monotonic()
    with pytest.raises(TimeoutError, match="not seen within"):
        _run(suite, check)
    assert time.monotonic() - started <= suite._SYNC_TIMEOUT_SEC + 0.5
    assert timeouts, "at least one sync should have run"
    assert all(t >= suite._SYNC_WAIT_FLOOR_SEC for t in timeouts), timeouts


def test_a_change_that_lands_after_the_last_round_is_still_seen(suite, monkeypatch) -> None:
    timeouts: list[float] = []
    monkeypatch.setattr(suite, "wait_for_sync_completion", _fake_wait(suite, timeouts))
    started = time.monotonic()
    # Late enough that no further round fits, early enough to beat the deadline.
    visible_at = suite._SYNC_TIMEOUT_SEC - 0.2

    async def check() -> bool:
        return time.monotonic() - started >= visible_at

    _run(suite, check)
    assert time.monotonic() - started < suite._SYNC_TIMEOUT_SEC


def test_the_last_round_that_fits_is_not_lost_to_the_pause(suite, monkeypatch) -> None:
    # Room for exactly two rounds: the pause after the first must shrink so the
    # second still starts, instead of dropping into graph polling.
    round_sec = sum(suite._RESTART_SYNC_PAUSE_SEC) + suite._SYNC_WAIT_FLOOR_SEC
    monkeypatch.setattr(suite, "_SYNC_TIMEOUT_SEC", 2 * round_sec + 0.1)
    monkeypatch.setattr(suite, "_RESYNC_INTERVAL_SEC", 1.0)
    timeouts: list[float] = []

    async def slow_wait(client, graph, connector_id, *, timeout):
        timeouts.append(timeout)
        await asyncio.sleep(suite._SYNC_WAIT_FLOOR_SEC)
        return 1

    monkeypatch.setattr(suite, "wait_for_sync_completion", slow_wait)

    async def check() -> bool:
        return len(timeouts) >= 2

    _run(suite, check)
    assert len(timeouts) == 2


def test_a_budget_shorter_than_one_round_is_refused(suite, monkeypatch) -> None:
    monkeypatch.setattr(suite, "_SYNC_TIMEOUT_SEC", 0.1)

    async def check() -> bool:
        return True

    with pytest.raises(ValueError, match="SYNC_TIMEOUT"):
        _run(suite, check)

