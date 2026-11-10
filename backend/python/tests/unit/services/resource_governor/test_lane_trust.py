"""The shared directory must be ours alone, and a closed ledger must be inert.

Two review findings: the slot directory name under ``/tmp`` is predictable, so
a directory or symlink planted by another user must not be trusted or written
through; and a descriptor number freed by ``close()`` can be reused by an
unrelated file while a late caller still holds the old number.
"""
from __future__ import annotations

import os
import threading
import time
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor.lane_ledger import BLANK, DomainLedger, LedgerRecord
from app.services.resource_governor.lanes import (
    LaneBudget,
    LaneBudgetClosed,
    LanePlan,
    open_lane_budget,
    plan_lanes,
)
from app.services.resource_governor.shared_slots import SharedSlots
from tests.unit.lane_gauge import Mem

if TYPE_CHECKING:
    from pathlib import Path

GIB = 2**30
CANARY = b"canary: must never be overwritten"


def plan(lanes: int = 4) -> LanePlan:
    return plan_lanes(cpus=lanes * 2, heavy=lanes * 2)


class TestForeignDirectory:
    @pytest.mark.parametrize("mode", [0o777, 0o750, 0o705, 0o770])
    def test_a_directory_open_to_others_is_not_trusted(self, tmp_path: Path, mode: int) -> None:
        directory = tmp_path / "lanes"
        directory.mkdir()
        directory.chmod(mode)
        with pytest.raises(PermissionError):
            DomainLedger(directory)
        with pytest.raises(PermissionError):
            SharedSlots(directory, 4)
        with pytest.raises(PermissionError):
            LaneBudget.open(plan(), directory, memory_reader=Mem())
        assert not (directory / "ledger").exists()

    def test_a_directory_owned_by_someone_else_is_not_trusted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        directory = tmp_path / "lanes"
        directory.mkdir(mode=0o700)
        monkeypatch.setattr(os, "getuid", lambda: os.stat(directory).st_uid + 1)
        with pytest.raises(PermissionError):
            DomainLedger(directory)
        with pytest.raises(PermissionError):
            SharedSlots(directory, 4)

    def test_a_symlinked_directory_is_not_followed(self, tmp_path: Path) -> None:
        target = tmp_path / "elsewhere"
        target.mkdir(mode=0o700)
        link = tmp_path / "lanes"
        link.symlink_to(target)
        with pytest.raises(OSError):
            DomainLedger(link)
        assert list(target.iterdir()) == []

    def test_a_symlinked_ledger_is_never_written_through(self, tmp_path: Path) -> None:
        directory = tmp_path / "lanes"
        directory.mkdir(mode=0o700)
        canary = tmp_path / "canary"
        canary.write_bytes(CANARY)
        (directory / "ledger").symlink_to(canary)
        with pytest.raises(OSError):
            DomainLedger(directory)
        assert canary.read_bytes() == CANARY

    def test_a_ledger_that_is_not_private_is_refused(self, tmp_path: Path) -> None:
        directory = tmp_path / "lanes"
        directory.mkdir(mode=0o700)
        (directory / "ledger").write_bytes(CANARY)
        (directory / "ledger").chmod(0o666)
        with pytest.raises(PermissionError):
            DomainLedger(directory)
        assert (directory / "ledger").read_bytes() == CANARY

    def test_a_foreign_owned_ledger_is_refused(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        directory = tmp_path / "lanes"
        DomainLedger(directory).close()
        uid = os.getuid()
        monkeypatch.setattr(os, "getuid", lambda: uid + 1)
        with pytest.raises(PermissionError):
            DomainLedger(directory)

    def test_the_budget_degrades_to_local_with_one_error(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        directory = tmp_path / "lanes"
        directory.mkdir(mode=0o700)
        canary = tmp_path / "canary"
        canary.write_bytes(CANARY)
        (directory / "ledger").symlink_to(canary)
        with caplog.at_level("ERROR"):
            budget = open_lane_budget(
                plan(), local_share=2, environ={"PARSE_LANES": "on"}, directory=directory, memory_reader=Mem()
            )
        assert budget is not None
        try:
            assert budget.stats()["slots"] == "local"
            assert sum(r.levelname == "ERROR" for r in caplog.records) == 1
            assert budget.limit() == 2
            budget.report_incident()
        finally:
            budget.close()
        assert canary.read_bytes() == CANARY

    def test_our_own_private_directory_still_works(self, tmp_path: Path) -> None:
        budget = LaneBudget.open(plan(), tmp_path / "fresh", memory_reader=Mem(10 * GIB, GIB))
        try:
            assert budget.try_acquire() is not None
            info = os.stat(tmp_path / "fresh" / "ledger")
            assert info.st_mode & 0o177 == 0
        finally:
            budget.close()


def _sentinel(path: Path) -> int:
    path.write_bytes(CANARY)
    return os.open(path, os.O_RDWR)


class TestUseAfterClose:
    def test_a_transaction_parked_before_the_thread_lock_does_not_outlive_close(self, tmp_path: Path) -> None:
        """Deterministic: the caller has passed its first closed check and is held
        just before taking the thread lock while close() runs and sentinels take
        exactly the freed descriptor numbers."""
        ledger = DomainLedger(tmp_path / "d", view_ttl=0.0, lock_budget=2.0)
        gate = threading.Event()
        real = ledger._thread_lock

        class GatedLock:
            def acquire(self, blocking: bool = True, timeout: float = -1) -> bool:
                if threading.current_thread().name == "late":
                    gate.wait(5)
                return real.acquire(blocking, timeout)

            def release(self) -> None:
                real.release()

            def __enter__(self) -> None:
                real.acquire()

            def __exit__(self, *exc: object) -> None:
                real.release()

        ledger._thread_lock = GatedLock()  # type: ignore[assignment]
        outcome: list[object] = []

        def late_writer() -> None:
            try:
                with ledger.transaction() as txn:
                    outcome.append(txn)
                    if txn is not None:
                        txn.write(LedgerRecord(plan_lanes=4, cap=2, cap_stamp=1.0))
            except BaseException as exc:
                outcome.append(exc)

        record_fd, lock_fd = ledger._record_fd, ledger._lock_fd
        writer = threading.Thread(target=late_writer, name="late")
        writer.start()
        time.sleep(0.05)
        ledger.close()
        fds = []
        for name, number in (("sentinel", record_fd), ("sentinel2", lock_fd)):
            fd = _sentinel(tmp_path / name)
            os.dup2(fd, number)  # exactly the number the late caller still holds
            fds += sorted({fd, number})
        gate.set()
        writer.join(5)
        for fd in fds:
            os.close(fd)
        assert (tmp_path / "sentinel").read_bytes() == CANARY
        assert (tmp_path / "sentinel2").read_bytes() == CANARY
        assert not any(isinstance(o, BaseException) for o in outcome), outcome
        assert outcome == [None]

    def test_an_unlocked_read_does_not_outlive_close(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        ledger = DomainLedger(tmp_path / "d", view_ttl=0.0, lock_budget=2.0)
        entered, proceed = threading.Event(), threading.Event()
        seen: dict[str, object] = {}
        real_pread = os.pread
        ledger_inode = os.fstat(ledger._record_fd).st_ino

        def parked_pread(fd: int, length: int, offset: int) -> bytes:
            entered.set()
            proceed.wait(5)
            try:
                seen["inode"] = os.fstat(fd).st_ino
            except OSError as exc:
                seen["error"] = exc
            return real_pread(fd, length, offset)

        monkeypatch.setattr(os, "pread", parked_pread)
        reader = threading.Thread(target=lambda: seen.setdefault("record", ledger.read()))
        reader.start()
        assert entered.wait(5)
        closer = threading.Thread(target=ledger.close)
        closer.start()
        time.sleep(0.05)
        fd = _sentinel(tmp_path / "sentinel")
        sentinel_inode = os.fstat(fd).st_ino
        proceed.set()
        reader.join(5)
        closer.join(5)
        os.close(fd)
        assert seen.get("inode") == ledger_inode != sentinel_inode, seen
        assert "error" not in seen

    # Hundreds of rounds of four threads that spin on the GIL: about 25 s of CPU on a
    # quiet host, so the 30 s default fails on a slower or busier one, and the timed-out
    # test leaves its threads spinning, which starves the tests after it in that worker.
    @pytest.mark.timeout(300)
    def test_ledger_calls_racing_close_never_touch_a_reused_descriptor(self, tmp_path: Path) -> None:
        errors: list[BaseException] = []
        record = LedgerRecord(plan_lanes=4, cap=2, cap_stamp=1.0)
        for round_no in range(200):
            directory = tmp_path / f"d{round_no}"
            ledger = DomainLedger(directory, view_ttl=0.0)
            stop = threading.Event()
            ready = threading.Barrier(5)

            def write(ledger: DomainLedger = ledger, stop: threading.Event = stop, ready: threading.Barrier = ready) -> None:
                ready.wait()
                try:
                    while not stop.is_set():
                        with ledger.transaction() as txn:
                            if txn is not None:
                                txn.write(record)
                except BaseException as exc:
                    errors.append(exc)

            def read(ledger: DomainLedger = ledger, stop: threading.Event = stop, ready: threading.Barrier = ready) -> None:
                ready.wait()
                try:
                    while not stop.is_set():
                        assert ledger.read() in (BLANK, record)
                except BaseException as exc:
                    errors.append(exc)

            threads = [threading.Thread(target=f) for f in (write, write, read, read)]
            for t in threads:
                t.start()
            ready.wait()
            ledger.close()
            fd = _sentinel(tmp_path / f"sentinel{round_no}")  # takes a just-freed descriptor number
            stop.wait(0.002)
            stop.set()
            for t in threads:
                t.join()
            os.close(fd)
            assert (tmp_path / f"sentinel{round_no}").read_bytes() == CANARY, round_no
        assert not errors, errors[:3]

    # Hundreds of rounds of four threads that spin on the GIL: about 25 s of CPU on a
    # quiet host, so the 30 s default fails on a slower or busier one, and the timed-out
    # test leaves its threads spinning, which starves the tests after it in that worker.
    @pytest.mark.timeout(300)
    def test_budget_calls_racing_close_get_only_budget_closed(self, tmp_path: Path) -> None:
        unexpected: list[BaseException] = []
        for round_no in range(25):
            directory = tmp_path / f"b{round_no}"
            budget = LaneBudget.open(plan(), directory, memory_reader=Mem(10 * GIB, GIB), view_ttl=0.0)
            stop = threading.Event()
            ready = threading.Barrier(5)

            def hammer(
                call: object, budget: LaneBudget = budget, stop: threading.Event = stop, ready: threading.Barrier = ready
            ) -> None:
                ready.wait()
                while not stop.is_set():
                    try:
                        call()  # type: ignore[operator]
                    except LaneBudgetClosed:
                        return
                    except BaseException as exc:
                        unexpected.append(exc)
                        return

            def lease(budget: LaneBudget = budget) -> None:
                held = budget.try_acquire()
                if held is not None:
                    held.release()

            def spawn(budget: LaneBudget = budget) -> None:
                ticket = budget.admit_spawn(0)
                if ticket is not None:
                    ticket.release(started=False)

            calls = [lease, spawn, budget.held_count, budget.request_reap]
            threads = [threading.Thread(target=hammer, args=(c,)) for c in calls]
            for t in threads:
                t.start()
            ready.wait()
            budget.close()
            fd = _sentinel(tmp_path / f"sentinel-b{round_no}")
            stop.wait(0.003)
            stop.set()
            for t in threads:
                t.join()
            os.close(fd)
            assert (tmp_path / f"sentinel-b{round_no}").read_bytes() == CANARY, round_no
        assert not unexpected, unexpected[:3]

    def test_waiters_on_a_closed_budget_get_budget_closed(self, tmp_path: Path) -> None:
        budget = LaneBudget.open(plan(), tmp_path / "w", memory_reader=Mem(10 * GIB, GIB))
        budget.close()
        with pytest.raises(LaneBudgetClosed):
            budget.admit_spawn(0)
        with pytest.raises(LaneBudgetClosed):
            budget.acquire(timeout=0.1)
