"""The shared live record against corruption, old versions and odd values.

Every byte of a valid record is flipped in turn; the checksum must reject
each one, and a ledger holding a corrupt file must fall back to its last good
record (a reader never lifts a limit on a bad read). An old 128-byte record
left by an earlier version must read as no record and be replaced by the
first step.
"""

from __future__ import annotations

import struct
import zlib
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from app.agent_loop_lib.sandbox.host_reading import LiveMode
from app.agent_loop_lib.sandbox.live_limit import LiveLimit, _add_charge, live_charges
from app.agent_loop_lib.sandbox.live_record import (
    BURST_SLOTS,
    RECORD_SIZE,
    RULES,
    LiveLedger,
    LiveRecord,
)
from app.agent_loop_lib.sandbox.live_settings import LiveSettings
from tests.unit.agent_loop_lib.sandbox.live_fakes import (
    FakeClock,
    FakeReader,
    capacity,
    sample,
)

if TYPE_CHECKING:
    from pathlib import Path

GiB = 2**30

FULL = LiveRecord(
    limit=5, ceiling=6, min=2, target=4, rule="mem_target", shrink_cause="memory", stamp=1000.5,
    owner_pid=4242, last_step_at=1000.5, cooldown_until=1030.5, cpu_high_streak=1, in_slow_start=True,
    slow_start_step=2, last_wait_limit_at=999.0, last_wait_mem_at=998.0, wait_events=7,
    charges=tuple((1001.0 + i, (i + 1) * 2**20) for i in range(BURST_SLOTS)),
    sample_at=1000.5, last_valid_at=1000.5, mem_total=16 * GiB, mem_available=7 * GiB,
    cpu_busy=123456, cpu_total=999999,
)


def _live(directory: Path, clock: FakeClock, reader: FakeReader) -> LiveLimit:
    mode = LiveMode(mode="on:host", reader=reader, profile="host")
    live = LiveLimit(
        capacity(), None, settings=LiveSettings(), resolver=lambda: mode,  # type: ignore[arg-type]
        directory=directory, clock=clock,
    )
    live.apply_mode(mode)
    return live


class TestChecksum:
    def test_every_single_byte_flip_is_rejected(self) -> None:
        data = FULL.encode()
        assert LiveRecord.decode(data) == FULL
        for i in range(RECORD_SIZE):
            for mask in (0x01, 0x80, 0xFF):
                corrupt = bytearray(data)
                corrupt[i] ^= mask
                assert LiveRecord.decode(bytes(corrupt)) is None, (i, mask)

    @pytest.mark.parametrize("length", [0, 1, 16, 127, 128, 255, 257, 512])
    def test_any_other_length_is_rejected(self, length: int) -> None:
        data = (FULL.encode() * 3)[:length]
        assert LiveRecord.decode(data) is None

    def test_a_valid_checksum_over_an_unknown_rule_is_rejected(self) -> None:
        # Bytes that pass the checksum but name a rule this version lacks.
        raw = bytearray(FULL.encode())
        rule_offset = 16 + 6 * 2
        raw[rule_offset] = len(RULES)
        struct.pack_into("<8sII", raw, 0, b"PXSBXLV\0", 2, 0)
        struct.pack_into("<8sII", raw, 0, b"PXSBXLV\0", 2, zlib.crc32(bytes(raw)))
        assert LiveRecord.decode(bytes(raw)) is None

    def test_a_record_of_another_version_is_rejected(self) -> None:
        raw = bytearray(FULL.encode())
        struct.pack_into("<8sII", raw, 0, b"PXSBXLV\0", 3, 0)
        struct.pack_into("<8sII", raw, 0, b"PXSBXLV\0", 3, zlib.crc32(bytes(raw)))
        assert LiveRecord.decode(bytes(raw)) is None


class TestLedgerFallback:
    def test_a_corrupt_file_keeps_the_last_good_record_for_readers(self, tmp_path: Path) -> None:
        ledger = LiveLedger(tmp_path / "state")
        with ledger.transaction(0.1) as txn:
            assert txn is not None
            txn.write(FULL)
        assert ledger.read() == FULL
        data = bytearray(FULL.encode())
        for i in range(0, RECORD_SIZE, 7):
            corrupt = bytearray(data)
            corrupt[i] ^= 0x5A
            (tmp_path / "state" / "live").write_bytes(bytes(corrupt))
            assert ledger.read() == FULL, i

    def test_another_worker_with_no_good_record_reads_none(self, tmp_path: Path) -> None:
        ledger = LiveLedger(tmp_path / "state")
        (tmp_path / "state" / "live").write_bytes(b"\x00" * RECORD_SIZE)
        assert ledger.read() is None

    def test_a_corrupt_file_holds_the_view_at_the_shrunk_limit(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path / "state", clock, FakeReader(sample(2.0)))
        rec = live.sample_once()
        assert rec is not None and rec.limit == 2
        raw = bytearray((tmp_path / "state" / "live").read_bytes())
        raw[100] ^= 0xFF
        (tmp_path / "state" / "live").write_bytes(bytes(raw))
        clock.now += 2
        assert live.view(fresh=True).limit == 2

    def test_the_next_step_replaces_a_corrupt_file(self, tmp_path: Path) -> None:
        clock = FakeClock()
        live = _live(tmp_path / "state", clock, FakeReader(sample(2.0)))
        (tmp_path / "state" / "live").write_bytes(b"\xff" * RECORD_SIZE)
        rec = live.sample_once()
        assert rec is not None and rec.limit == 2 and rec.rule == "start"
        assert LiveLedger(tmp_path / "state").read() == rec


class TestUpgradeFromV1:
    def _v1(self) -> bytes:
        # The 128-byte record of the first version: a header and a body.
        raw = bytearray(128)
        struct.pack_into("<8sII", raw, 0, b"PXSBXLV\0", 1, 0)
        struct.pack_into("<HHH", raw, 16, 3, 6, 2)
        struct.pack_into("<8sII", raw, 0, b"PXSBXLV\0", 1, zlib.crc32(bytes(raw)))
        return bytes(raw)

    def test_a_v1_record_reads_as_none_and_the_view_is_the_ceiling(self, tmp_path: Path) -> None:
        (tmp_path / "state").mkdir(mode=0o700)
        (tmp_path / "state" / "live").write_bytes(self._v1())
        (tmp_path / "state" / "live").chmod(0o600)
        clock = FakeClock()
        live = _live(tmp_path / "state", clock, FakeReader(sample(7.0)))
        assert live._ledger.read() is None
        view = live.view(fresh=True)
        assert view.limit == view.ceiling == 6 and view.rule == "start"

    def test_the_first_step_overwrites_it_whole(self, tmp_path: Path) -> None:
        (tmp_path / "state").mkdir(mode=0o700)
        (tmp_path / "state" / "live").write_bytes(self._v1())
        (tmp_path / "state" / "live").chmod(0o600)
        live = _live(tmp_path / "state", FakeClock(), FakeReader(sample(2.0)))
        rec = live.sample_once()
        assert rec is not None and rec.limit == 2
        assert len((tmp_path / "state" / "live").read_bytes()) == RECORD_SIZE
        assert LiveLedger(tmp_path / "state").read() == rec

    async def test_an_admission_before_the_first_step_keeps_its_charge(self, tmp_path: Path) -> None:
        (tmp_path / "state").mkdir(mode=0o700)
        (tmp_path / "state" / "live").write_bytes(self._v1())
        (tmp_path / "state" / "live").chmod(0o600)
        clock = FakeClock()
        live = _live(tmp_path / "state", clock, FakeReader(sample(4.0)))
        assert await live.admit_and_charge(held=0) is None
        rec = live.sample_once()
        assert rec is not None and rec.charges == ((clock.now + 5.0, 512 * 2**20),)


_finite = st.floats(min_value=-1e12, max_value=1e12, allow_nan=False)
_record = st.builds(
    LiveRecord,
    limit=st.integers(0, 0xFFFE), ceiling=st.integers(0, 0xFFFE), min=st.integers(0, 0xFFFE),
    target=st.none() | st.integers(0, 0xFFFE), rule=st.sampled_from(RULES),
    shrink_cause=st.sampled_from((None, "memory", "cpu")), stamp=_finite, owner_pid=st.integers(0, 2**32 - 1),
    last_step_at=_finite, cooldown_until=_finite, cpu_high_streak=st.integers(0, 0xFFFE),
    in_slow_start=st.booleans(), slow_start_step=st.integers(0, 0xFFFE), last_wait_limit_at=_finite,
    last_wait_mem_at=_finite, wait_events=st.integers(0, 2**32 - 1),
    charges=st.lists(st.tuples(_finite, st.integers(1, 2**63)), max_size=BURST_SLOTS).map(tuple),
    sample_at=_finite, last_valid_at=_finite, mem_total=st.none() | st.integers(0, 2**63),
    mem_available=st.none() | st.integers(0, 2**63), cpu_busy=st.integers(0, 2**63),
    cpu_total=st.integers(0, 2**63),
)


class TestProperties:
    @settings(max_examples=300, deadline=None)
    @given(_record)
    def test_in_range_records_round_trip(self, rec: LiveRecord) -> None:
        assert LiveRecord.decode(rec.encode()) == rec

    @settings(max_examples=200, deadline=None)
    @given(
        limit=st.integers(-(2**40), 2**40), pid=st.integers(-(2**40), 2**40),
        total=st.none() | st.integers(-(2**70), 2**70),
    )
    def test_out_of_range_values_encode_clamped_and_never_raise(self, limit: int, pid: int, total: int | None) -> None:
        rec = LiveRecord(limit=limit, owner_pid=pid, mem_total=total, rule="not-a-rule")
        back = LiveRecord.decode(rec.encode())
        assert back is not None
        assert 0 <= back.limit <= 0xFFFE and back.rule == ""
        assert back.mem_total is None if total is None else 0 <= back.mem_total < 2**64 - 1

    @settings(max_examples=200, deadline=None)
    @given(st.binary(min_size=RECORD_SIZE, max_size=RECORD_SIZE))
    def test_random_bytes_never_raise(self, data: bytes) -> None:
        LiveRecord.decode(data)

    @settings(max_examples=300, deadline=None)
    @given(
        st.lists(st.tuples(st.floats(0, 1e6, allow_nan=False), st.integers(1, 2**40)), max_size=40),
    )
    def test_merging_charges_keeps_their_sum_and_latest_end(self, adds: list[tuple[float, int]]) -> None:
        charges: tuple[tuple[float, int], ...] = ()
        for until, size in adds:
            charges = _add_charge(charges, until, size)
            assert len(charges) <= BURST_SLOTS
        assert sum(size for _, size in charges) == sum(size for _, size in adds)
        if adds:
            assert max(until for until, _ in charges) == max(until for until, _ in adds)
        # Merging never makes a charge end before the earliest one did.
        assert all(until >= min(u for u, _ in adds) for until, _ in charges)

    @settings(max_examples=200, deadline=None)
    @given(
        st.lists(st.tuples(st.floats(-1e9, 1e9, allow_nan=False), st.integers(1, 2**40)), max_size=BURST_SLOTS),
        st.floats(0, 1e6, allow_nan=False),
    )
    def test_only_charges_in_their_window_count(self, charges: list[tuple[float, int]], now: float) -> None:
        rec = LiveRecord(charges=tuple(charges))
        for until, _ in live_charges(rec, now, 5.0):
            assert now < until <= now + 6.0

    def test_a_nan_charge_never_counts(self) -> None:
        # No writer stores a non-finite time: the record reads as damaged.
        rec = LiveRecord(charges=((float("nan"), 2**30), (float("inf"), 2**30)))
        assert LiveRecord.decode(rec.encode()) is None
        assert live_charges(rec, 1000.0, 5.0) == ()
