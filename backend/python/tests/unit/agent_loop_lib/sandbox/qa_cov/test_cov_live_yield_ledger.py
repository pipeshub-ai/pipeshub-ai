"""Indexing yield writer with a busy signal, and the load ledger's disabled,
unrecorded and flush-failure paths."""

from __future__ import annotations

import json
import logging
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox import load_ledger as ledger_mod
from app.agent_loop_lib.sandbox.capacity import HoldReason
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from app.agent_loop_lib.sandbox.live_yield import (
    YIELD_MIN_WAIT_S,
    YIELD_REFRESH_S,
    IndexingYield,
)
from app.agent_loop_lib.sandbox.load_ledger import LoadLedger

if TYPE_CHECKING:
    from pathlib import Path


class _BusySignal:
    def __init__(self) -> None:
        self.refreshes = 0

    def refresh(self, **kwargs: object) -> None:
        self.refreshes += 1
        # the lock was busy


class TestIndexingYieldBusySignal:
    def test_a_busy_lock_is_tried_again_on_the_next_poll(self) -> None:
        now = [100.0]
        signal = _BusySignal()
        writer = IndexingYield(
            SimpleNamespace(), signal, LiveSettings(), clock=lambda: now[0],  # type: ignore[arg-type]
        )
        writer.on_memory_wait(HoldReason.HOST_MEMORY_FLOOR, YIELD_MIN_WAIT_S)
        now[0] += 0.25  # well inside the refresh interval: still tried, nothing was written
        writer.on_memory_wait(HoldReason.HOST_MEMORY_FLOOR, YIELD_MIN_WAIT_S + 0.25)
        assert signal.refreshes == 2
        assert writer._refreshed_at is None and writer._episode is None
        assert 0.25 < YIELD_REFRESH_S

    def test_short_waits_and_the_setting_off_do_not_touch_the_signal(self) -> None:
        signal = _BusySignal()
        on = IndexingYield(SimpleNamespace(), signal, LiveSettings())  # type: ignore[arg-type]
        on.on_memory_wait(HoldReason.LIVE_MEMORY, YIELD_MIN_WAIT_S - 0.01)
        off = IndexingYield(SimpleNamespace(), signal, LiveSettings(yield_indexing=False))  # type: ignore[arg-type]
        off.on_memory_wait(HoldReason.LIVE_MEMORY, 60.0)
        assert not off.enabled
        assert IndexingYield(SimpleNamespace(), None, LiveSettings()).enabled is False  # type: ignore[arg-type]
        assert signal.refreshes == 0


class TestDisabledLedger:
    def test_no_state_directory_records_nothing_and_says_so(self, caplog) -> None:
        with caplog.at_level(logging.WARNING, logger=ledger_mod.__name__):
            ledger = LoadLedger(None, mode="compose")
        assert not ledger.enabled and ledger.path is None
        assert any("sandbox load ledger: disabled" in r.getMessage() for r in caplog.records)
        ledger.wait_start(HoldReason.SLOTS_FULL)
        ledger.outcome("refused", 5.0, HoldReason.SLOTS_FULL)
        assert ledger.flush() is False
        assert ledger._ticks == {} and ledger._minutes == {}


class TestOutcomeWithoutReason:
    def test_a_refusal_with_no_known_reason_is_not_counted(self, tmp_path: Path) -> None:
        ledger = LoadLedger(tmp_path / "state", mode="compose", clock=lambda: 6000.0)
        ledger.outcome("refused", 3.0, None)
        ledger.outcome("cancelled", 3.0, None)
        assert ledger._minutes == {} and not ledger._dirty
        ledger.outcome("granted", 0.0, None)
        assert sum(b.runs for b in ledger._minutes.values()) == 1


class TestFlushFailure:
    def test_failures_warn_at_most_once_per_interval_and_keep_the_data_dirty(
        self, tmp_path: Path, monkeypatch, caplog,
    ) -> None:
        ledger = LoadLedger(tmp_path / "state", mode="compose", clock=lambda: 6000.0)
        assert ledger.flush() is True  # directory ready
        real_open = os.open

        def failing(path: object, *args: object, **kwargs: object) -> int:
            if str(path).endswith(".tmp"):
                raise OSError(28, "No space left on device")
            return real_open(path, *args, **kwargs)

        monkeypatch.setattr(ledger_mod.os, "open", failing)
        with caplog.at_level(logging.WARNING, logger=ledger_mod.__name__):
            ledger.outcome("granted", 0.0, None)  # no loop: flushes inline, and fails
            assert ledger._dirty
            assert ledger.flush() is False
            assert ledger.flush() is False
        warned = [r for r in caplog.records if "flush failed" in r.getMessage()]
        assert len(warned) == 1
        monkeypatch.setattr(ledger_mod.os, "open", real_open)
        ledger.close()  # the final flush writes what was left
        assert not ledger._dirty
        assert ledger.path is not None
        data = json.loads(ledger.path.read_text())
        assert sum(m["runs"] for m in data["minutes"].values()) == 1

    def test_an_unusable_directory_disables_the_ledger(self, tmp_path: Path, caplog) -> None:
        blocker = tmp_path / "state"
        blocker.write_text("a file")
        ledger = LoadLedger(blocker, mode="compose")
        with caplog.at_level(logging.WARNING, logger=ledger_mod.__name__):
            assert ledger.flush() is False
        assert not ledger.enabled
        assert any("sandbox load ledger: disabled" in r.getMessage() for r in caplog.records)

    def test_close_without_changes_writes_nothing(self, tmp_path: Path) -> None:
        ledger = LoadLedger(tmp_path / "state", mode="compose")
        ledger.close()
        assert ledger.path is not None and not ledger.path.exists()


class TestBackOffReadFailure:
    def test_a_failing_back_off_read_counts_a_memory_wait_as_not_yet_qualifying(self, tmp_path: Path) -> None:
        def broken() -> bool:
            raise OSError("signal file unreadable")

        ledger = LoadLedger(tmp_path / "state", mode="compose", clock=lambda: 6000.0, backed_off=broken)
        ledger.outcome("refused", 30.0, HoldReason.HOST_MEMORY_FLOOR)
        bucket = next(iter(ledger._minutes.values()))
        assert (bucket.waited, bucket.refused) == (0, 0)
        assert (bucket.waited_unqualified, bucket.refused_unqualified) == (1, 1)
        # A CPU wait does not ask about the back-off and counts at once.
        ledger.outcome("refused", 30.0, HoldReason.LIVE_CPU)
        assert (bucket.waited, bucket.refused) == (1, 1)
