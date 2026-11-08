"""The Outlook suite's bounded re-sync for a source change Graph has not settled everywhere.

A sync that read the directory before every replica had the change stores the old state.
The loop must re-sync only while the graph is still wrong, wait before each re-sync so the
directory can settle, and stop after its attempts so a real connector bug still fails.
"""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

_UTILS = Path(__file__).resolve().parents[1] / "connectors" / "outlook" / "outlook_test_utils.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("_outlook_test_utils", _UTILS)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def utils(monkeypatch: pytest.MonkeyPatch) -> tuple[ModuleType, list[str]]:
    module = _load()
    events: list[str] = []

    async def _sleep(seconds: float) -> None:
        events.append(f"wait {seconds:g}")

    monkeypatch.setattr(module.asyncio, "sleep", _sleep)
    return module, events


def _run(module: ModuleType, events: list[str], wrong_for: int, attempts: int = 3) -> int:
    checks = iter([True] * wrong_for + [False] * 10)

    async def _still_wrong() -> bool:
        events.append("check")
        return next(checks)

    async def _resync(n: int) -> None:
        events.append(f"sync {n}")

    return asyncio.run(
        module.resync_while(_still_wrong, _resync, attempts=attempts, wait_sec=30, description="edge")
    )


def test_a_graph_that_is_already_right_is_not_synced_again(utils: tuple[ModuleType, list[str]]) -> None:
    module, events = utils
    assert _run(module, events, wrong_for=0) == 0
    assert events == ["check"]


def test_it_stops_once_a_re_sync_fixed_the_graph(utils: tuple[ModuleType, list[str]]) -> None:
    module, events = utils
    assert _run(module, events, wrong_for=1) == 1
    assert events == ["check", "wait 30", "sync 1", "check"]


def test_a_change_that_never_arrives_stops_after_the_attempts(utils: tuple[ModuleType, list[str]]) -> None:
    module, events = utils
    assert _run(module, events, wrong_for=10, attempts=2) == 2
    assert events == ["check", "wait 30", "sync 1", "check", "wait 30", "sync 2"]
