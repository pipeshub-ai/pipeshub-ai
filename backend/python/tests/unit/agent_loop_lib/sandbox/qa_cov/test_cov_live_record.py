"""Live record ledger failure paths: no file locks, a lock file that cannot
be opened, short or unreadable bytes, a busy or failing lock, and close."""

from __future__ import annotations

import errno
import os
import threading
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox import live_record as record_mod
from app.agent_loop_lib.sandbox.live_record import LiveLedger, LiveRecord

if TYPE_CHECKING:
    from pathlib import Path

GOOD = LiveRecord(limit=3, ceiling=6, min=2, rule="start", stamp=10.0, last_step_at=10.0)


def _fd_open(fd: int) -> bool:
    try:
        os.fstat(fd)
    except OSError:
        return False
    return True


class TestOpening:
    def test_no_posix_locks_refuses_a_shared_record(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.setattr(record_mod, "fcntl", None)
        with pytest.raises(OSError, match="POSIX file locks"):
            LiveLedger(tmp_path / "state")
        # The in-process record needs no locks.
        assert LiveLedger(None).shared is False

    def test_a_lock_file_that_cannot_be_opened_closes_the_record_file(self, tmp_path: Path, monkeypatch) -> None:
        opened: list[int] = []
        real = record_mod.open_private_file

        def flaky(path: Path) -> int:
            if path.name == "live.lock":
                raise PermissionError("planted lock file")
            fd = real(path)
            opened.append(fd)
            return fd

        monkeypatch.setattr(record_mod, "open_private_file", flaky)
        with pytest.raises(PermissionError):
            LiveLedger(tmp_path / "state")
        assert len(opened) == 1
        assert not _fd_open(opened[0])


class TestReading:
    def test_short_bytes_are_damage_not_another_version(self, tmp_path: Path) -> None:
        ledger = LiveLedger(tmp_path / "state")
        try:
            os.pwrite(ledger._record_fd, b"PX", 0)
            with ledger.transaction(0.0) as txn:
                assert txn is not None
                assert txn.read() is None
                assert txn.corrupt is True
        finally:
            ledger.close()

    def test_an_empty_file_is_missing_not_damaged(self, tmp_path: Path) -> None:
        ledger = LiveLedger(tmp_path / "state")
        try:
            with ledger.transaction(0.0) as txn:
                assert txn is not None and txn.read() is None and txn.corrupt is False
        finally:
            ledger.close()

    def test_a_closed_ledger_answers_its_last_good_record(self, tmp_path: Path) -> None:
        ledger = LiveLedger(tmp_path / "state")
        with ledger.transaction() as txn:
            assert txn is not None
            txn.write(GOOD)
        fds = (ledger._record_fd, ledger._lock_fd)
        ledger.close()
        assert ledger._record_fd == ledger._lock_fd == -1
        assert not any(_fd_open(fd) for fd in fds)
        assert ledger.read() == GOOD  # pread fails on the closed file
        ledger.close()  # a second close is harmless

    def test_damage_while_the_lock_is_busy_keeps_the_last_good_record(self, tmp_path: Path) -> None:
        ledger = LiveLedger(tmp_path / "state")
        try:
            with ledger.transaction() as txn:
                assert txn is not None
                txn.write(GOOD)
            os.pwrite(ledger._record_fd, b"\x00" * record_mod.RECORD_SIZE, 0)
            held = threading.Event()
            release = threading.Event()

            def hold() -> None:
                with ledger.transaction(1.0) as txn:
                    assert txn is not None
                    held.set()
                    release.wait(5)

            worker = threading.Thread(target=hold)
            worker.start()
            assert held.wait(5)
            try:
                assert ledger.read() == GOOD
            finally:
                release.set()
                worker.join(5)
        finally:
            ledger.close()

    def test_a_record_repaired_under_the_lock_is_returned(self, tmp_path: Path, monkeypatch) -> None:
        ledger = LiveLedger(tmp_path / "state")
        try:
            other = LiveRecord(limit=5, ceiling=6, min=2, rule="start", stamp=20.0)
            calls = {"n": 0}
            real = ledger._read_raw

            def racing(*, remember: bool = False) -> LiveRecord | None:
                calls["n"] += 1
                if calls["n"] <= 3:
                    return None  # the three lock-free reads hit a torn write
                return other

            monkeypatch.setattr(ledger, "_read_raw", racing)
            assert ledger.read() == other
            assert calls["n"] == 4
            monkeypatch.setattr(ledger, "_read_raw", real)
        finally:
            ledger.close()


class TestLocking:
    def test_an_unexpected_flock_error_gives_no_transaction(self, tmp_path: Path, monkeypatch) -> None:
        ledger = LiveLedger(tmp_path / "state")
        try:
            def broken(fd: int, op: int) -> None:
                if op & record_mod.fcntl.LOCK_UN:
                    return
                raise OSError(errno.EBADF, "bad descriptor")

            monkeypatch.setattr(record_mod.fcntl, "flock", broken)
            with ledger.transaction(0.5) as txn:
                assert txn is None
            # The thread lock was given back.
            assert ledger._thread_lock.acquire(blocking=False)
            ledger._thread_lock.release()
        finally:
            monkeypatch.undo()
            ledger.close()

    def test_a_busy_thread_lock_gives_no_transaction(self, tmp_path: Path) -> None:
        ledger = LiveLedger(None)
        assert ledger._thread_lock.acquire()
        try:
            with ledger.transaction(0.0) as txn:
                assert txn is None
            with ledger.transaction(0.01) as txn:
                assert txn is None
        finally:
            ledger._thread_lock.release()
