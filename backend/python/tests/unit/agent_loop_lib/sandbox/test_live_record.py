"""Tests for the live limit's shared record."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.live_record import RECORD_SIZE, LiveLedger, LiveRecord

if TYPE_CHECKING:
    from pathlib import Path

RECORD = LiveRecord(
    limit=5, ceiling=6, min=2, target=4, rule="mem_target", shrink_cause="memory", stamp=12.5,
    owner_pid=4321, last_step_at=12.5, cooldown_until=42.5, cpu_high_streak=1, in_slow_start=True,
    slow_start_step=2, last_wait_limit_at=11.0, last_wait_mem_at=3.0, wait_events=9,
    charges=((17.5, 2**29), (19.0, 2**28)), sample_at=12.5, last_valid_at=12.5,
    mem_total=16 * 2**30, mem_available=4 * 2**30, cpu_busy=12345, cpu_total=67890,
)


class TestEncoding:
    def test_round_trip(self) -> None:
        data = RECORD.encode()
        assert len(data) == RECORD_SIZE
        assert LiveRecord.decode(data) == RECORD

    def test_none_fields_round_trip(self) -> None:
        record = LiveRecord(limit=3, target=None, mem_total=None, mem_available=None)
        assert LiveRecord.decode(record.encode()) == record

    @pytest.mark.parametrize("mangle", [
        lambda d: b"XXXXXXXX" + d[8:],
        lambda d: d[:8] + (1).to_bytes(4, "little") + d[12:],
        lambda d: d[:40] + bytes([d[40] ^ 1]) + d[41:],
        lambda d: d[:100],
        lambda d: b"",
        lambda d: bytes(RECORD_SIZE),
    ], ids=["magic", "version", "flipped-byte", "short", "empty", "zeros"])
    def test_anything_else_is_none(self, mangle) -> None:
        assert LiveRecord.decode(mangle(RECORD.encode())) is None

    def test_uninitialised_is_limit_zero(self) -> None:
        assert not LiveRecord(charges=((1.0, 1),)).initialised
        assert LiveRecord(limit=1).initialised


class TestLedger:
    def test_write_then_read(self, tmp_path: Path) -> None:
        ledger = LiveLedger(tmp_path / "state")
        assert ledger.read() is None
        with ledger.transaction() as txn:
            assert txn is not None and txn.read() is None
            txn.write(RECORD)
        assert LiveLedger(tmp_path / "state").read() == RECORD
        assert (tmp_path / "state" / "live").stat().st_size == RECORD_SIZE
        assert (tmp_path / "state" / "live.lock").exists()

    def test_torn_read_keeps_the_last_good_record(self, tmp_path: Path) -> None:
        ledger = LiveLedger(tmp_path)
        with ledger.transaction() as txn:
            assert txn is not None
            txn.write(RECORD)
        assert ledger.read() == RECORD
        fd = os.open(tmp_path / "live", os.O_WRONLY)
        os.pwrite(fd, b"garbage", 40)
        os.close(fd)
        assert ledger.read() == RECORD
        assert LiveLedger(tmp_path).read() is None

    def test_a_busy_lock_gives_none_within_the_budget(self, tmp_path: Path) -> None:
        holder, other = LiveLedger(tmp_path), LiveLedger(tmp_path)
        with holder.transaction() as held:
            assert held is not None
            started = time.monotonic()
            with other.transaction(0.02) as txn:
                assert txn is None
            # wall-clock-ok: the holder keeps the lock for the whole block; 0.2 s is ten times the 20 ms budget
            assert time.monotonic() - started < 0.2
            with other.transaction(0.0) as txn:
                assert txn is None
        with other.transaction(0.0) as txn:
            assert txn is not None

    def test_a_shared_directory_is_refused(self, tmp_path: Path) -> None:
        directory = tmp_path / "state"
        directory.mkdir(mode=0o777)
        directory.chmod(0o777)
        with pytest.raises(PermissionError):
            LiveLedger(directory)

    def test_in_memory_ledger(self) -> None:
        ledger = LiveLedger(None)
        assert not ledger.shared and ledger.read() is None
        with ledger.transaction() as txn:
            assert txn is not None
            txn.write(RECORD)
        assert ledger.read() == RECORD
