"""Error and fallback paths of the indexing back-off signal (yield_signal.py).

Every failure here must read as "inactive" or "not written": indexing is never
slowed by a signal it cannot read, and query never blocks on one it cannot write.
"""

from __future__ import annotations

import errno
import os
import threading
import time
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import yield_signal as ys
from app.services.resource_governor.yield_signal import (
    INACTIVE,
    YieldSignal,
    read_yield_state,
)

if TYPE_CHECKING:
    from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(fcntl is None, reason="needs POSIX file locks")

NOW = 5000.0


@pytest.fixture
def signal(tmp_path: Path) -> YieldSignal:
    sig = YieldSignal(tmp_path / "state", clock=lambda: NOW)
    yield sig
    sig.close()


def _read_only(sig: YieldSignal) -> None:
    """Swap the record descriptor for a read-only one: reads work, writes fail."""
    fd = os.open(sig.directory / "yield", os.O_RDONLY)
    os.close(sig._record_fd)
    sig._record_fd = fd


class TestOpening:
    def test_without_file_locks_the_signal_refuses_to_open(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(ys, "fcntl", None)
        with pytest.raises(OSError, match="POSIX file locks"):
            YieldSignal(tmp_path / "state")

    def test_a_lock_file_that_cannot_be_opened_closes_the_record_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        opened: list[int] = []
        real_open = ys.open_private_file

        def fake_open(path: Path) -> int:
            if path.name == "yield.lock":
                raise PermissionError("planted")
            fd = real_open(path)
            opened.append(fd)
            return fd

        monkeypatch.setattr(ys, "open_private_file", fake_open)
        with pytest.raises(PermissionError):
            YieldSignal(tmp_path / "state")
        assert len(opened) == 1
        with pytest.raises(OSError) as info:
            os.fstat(opened[0])
        assert info.value.errno == errno.EBADF

    def test_open_default_is_none_without_a_state_directory(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ys, "sandbox_state_directory", lambda: None)
        assert YieldSignal.open_default(role="query") is None

    def test_open_default_logs_and_is_none_for_an_unusable_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        shared = tmp_path / "shared"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        monkeypatch.setattr(ys, "sandbox_state_directory", lambda: shared)
        with caplog.at_level("WARNING"):
            assert YieldSignal.open_default(role="indexing worker") is None
        assert "the indexing worker does not use it" in caplog.text


class TestReadFailures:
    def test_a_closed_record_reads_as_nothing(self, signal: YieldSignal) -> None:
        assert signal.refresh(reason="live_memory") is not None
        os.close(signal._record_fd)
        signal._record_fd = -1
        assert signal.read() is None
        assert signal.read_state() == INACTIVE

    def test_read_yield_state_without_a_state_directory_is_inactive(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ys, "sandbox_state_directory", lambda: None)
        assert read_yield_state() == INACTIVE

    def test_read_yield_state_with_a_missing_file_is_inactive(self, tmp_path: Path) -> None:
        assert read_yield_state(tmp_path / "nowhere", now=NOW) == INACTIVE

    def test_a_read_error_after_opening_is_inactive_and_closes_the_file(
        self, signal: YieldSignal, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        assert signal.refresh(reason="live_memory") is not None
        assert read_yield_state(signal.directory, now=NOW).active
        closed: list[int] = []
        real_close = os.close

        def failing_pread(fd: int, size: int, offset: int) -> bytes:
            raise OSError(errno.EIO, "disk")

        def tracking_close(fd: int) -> None:
            closed.append(fd)
            real_close(fd)

        monkeypatch.setattr(ys.os, "pread", failing_pread)
        monkeypatch.setattr(ys.os, "close", tracking_close)
        assert read_yield_state(signal.directory, now=NOW) == INACTIVE
        assert len(closed) == 1

    def test_a_record_that_is_a_directory_is_inactive(self, tmp_path: Path) -> None:
        state = tmp_path / "state"
        (state / "yield").mkdir(parents=True, mode=0o700)
        assert read_yield_state(state, now=NOW) == INACTIVE


class TestWriteFailures:
    def test_a_failed_refresh_write_returns_none(self, signal: YieldSignal) -> None:
        _read_only(signal)
        assert signal.refresh(reason="live_memory") is None

    def test_a_failed_ack_write_returns_false(self, signal: YieldSignal) -> None:
        assert signal.refresh(reason="live_memory") is not None
        _read_only(signal)
        assert signal.ack(os.getpid(), at_floor=True) is False

    def test_an_ack_with_no_active_record_is_refused(self, signal: YieldSignal) -> None:
        assert signal.ack(os.getpid(), at_floor=False) is False


class TestLock:
    def test_a_thread_already_holding_the_lock_makes_refresh_give_up_at_once(self, signal: YieldSignal) -> None:
        assert signal._thread_lock.acquire()
        try:
            assert signal.refresh(reason="live_memory") is None
            assert signal.ack(os.getpid(), at_floor=False, budget=0.0) is False
        finally:
            signal._thread_lock.release()
        # The lock is usable again afterwards.
        assert signal.refresh(reason="live_memory") is not None

    def test_a_thread_lock_held_past_the_budget_fails_the_ack(self, signal: YieldSignal) -> None:
        assert signal.refresh(reason="live_memory") is not None
        held = threading.Event()
        release = threading.Event()

        def hold() -> None:
            with signal._thread_lock:
                held.set()
                release.wait(5)

        thread = threading.Thread(target=hold)
        thread.start()
        try:
            assert held.wait(5)
            assert signal.ack(os.getpid(), at_floor=False, budget=0.01) is False
        finally:
            release.set()
            thread.join(5)

    def test_another_process_holding_the_file_lock_times_the_ack_out(self, signal: YieldSignal) -> None:
        assert signal.refresh(reason="live_memory") is not None
        other = os.open(signal.directory / "yield.lock", os.O_RDWR)
        try:
            fcntl.flock(other, fcntl.LOCK_EX | fcntl.LOCK_NB)
            assert signal.ack(os.getpid(), at_floor=True, budget=0.01) is False
            assert signal.refresh(reason="live_memory") is None
        finally:
            os.close(other)
        assert signal.ack(os.getpid(), at_floor=True) is True
        # A failed lock attempt never leaves the thread lock taken.
        assert signal._thread_lock.acquire(blocking=False)
        signal._thread_lock.release()

    def test_an_unexpected_lock_error_gives_up_without_spinning(
        self, signal: YieldSignal, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        calls: list[int] = []

        def broken_flock(fd: int, op: int) -> None:
            calls.append(op)
            raise OSError(errno.EBADF, "bad descriptor")

        monkeypatch.setattr(ys.fcntl, "flock", broken_flock)
        started = time.monotonic()
        assert signal.refresh(reason="live_memory", budget=5.0) is None
        # wall-clock-ok: a spin would last the whole 5 s budget; 1 s is a fifth of it (the call count is the exact check)
        assert time.monotonic() - started < 1.0
        assert len(calls) == 1  # no retry, and no unlock of a lock never taken
        assert signal._thread_lock.acquire(blocking=False)
        signal._thread_lock.release()
