"""Property and edge tests for the indexing back-off signal and its policy.

The signal: any bytes that are not a valid record, and any record past its
TTL, set too far ahead, spanning too long or left by a dead writer, read as
inactive. Acks: more than eight indexing pids keep the eight newest, and one
pid never holds two entries. The policy: while the yield is on, heavy parse
loses one slot and the index pools a quarter per sample down to their floor,
and never grow; the heavy holdoff doubles on repeats up to 64 samples.
"""

from __future__ import annotations

import math
import struct
import zlib
from dataclasses import replace
from typing import TYPE_CHECKING

from hypothesis import given, settings
from hypothesis import strategies as st

from app.services.resource_governor import policy
from app.services.resource_governor.models import (
    Ceilings,
    ControllerState,
    Pool,
    PoolDemand,
    PoolState,
    ResourceSnapshot,
)
from app.services.resource_governor.yield_signal import (
    CLOCK_SLACK_S,
    DEAD_WRITER_S,
    MAX_ACKS,
    MAX_SPAN_S,
    REASONS,
    RECORD_SIZE,
    YieldAck,
    YieldRecord,
    YieldSignal,
    indexing_backed_off,
    read_yield_state,
    state_of,
)

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _alive(pid: int) -> bool:
    return pid < 1_000_000


GOOD = YieldRecord(
    set_at=1000.0, expires_at=1020.0, since=990.0, writer_pid=7, reason="host_memory_floor",
    acks=tuple(YieldAck(pid=100 + i, ack_at=1000.0 + i, at_floor=bool(i % 2)) for i in range(MAX_ACKS)),
)


class TestRecordBytes:
    def test_every_single_byte_flip_reads_inactive(self) -> None:
        data = GOOD.encode()
        assert YieldRecord.decode(data) == GOOD
        for i in range(RECORD_SIZE):
            corrupt = bytearray(data)
            corrupt[i] ^= 0x41
            assert YieldRecord.decode(bytes(corrupt)) is None, i

    @settings(max_examples=300, deadline=None)
    @given(st.binary(max_size=2 * RECORD_SIZE))
    def test_random_bytes_are_never_active(self, data: bytes) -> None:
        assert not state_of(YieldRecord.decode(data), 1000.0, _alive).active

    @settings(max_examples=300, deadline=None)
    @given(
        set_at=st.floats(allow_nan=True, allow_infinity=True),
        expires_at=st.floats(allow_nan=True, allow_infinity=True),
        since=st.floats(allow_nan=True, allow_infinity=True),
        writer=st.integers(0, 2**32 - 1),
        now=st.floats(0, 1e7),
    )
    def test_a_checksummed_record_is_active_only_when_believable(
        self, set_at: float, expires_at: float, since: float, writer: int, now: float,
    ) -> None:
        # A record with a valid checksum but any times at all.
        raw = bytearray(RECORD_SIZE)
        struct.pack_into("<3dIB", raw, 16, set_at, expires_at, since, writer, 1)
        struct.pack_into("<8sII", raw, 0, b"PXSBXYD\0", 1, 0)
        struct.pack_into("<8sII", raw, 0, b"PXSBXYD\0", 1, zlib.crc32(bytes(raw)))
        record = YieldRecord.decode(bytes(raw))
        if record is None:
            # Only a non-finite time makes a checksummed record unreadable.
            assert not all(map(math.isfinite, (set_at, expires_at, since)))
            return
        state = state_of(record, now, _alive)
        if state.active:
            assert 0 < set_at <= now + CLOCK_SLACK_S and now < expires_at
            assert expires_at - set_at <= MAX_SPAN_S
            assert now - set_at < DEAD_WRITER_S or _alive(writer)
            assert math.isfinite(set_at) and math.isfinite(expires_at)

    @settings(max_examples=200, deadline=None)
    @given(st.floats(0, 200), st.integers(0, 3))
    def test_inactive_after_the_ttl_whatever_the_acks(self, after: float, extra: int) -> None:
        record = replace(GOOD, set_at=1000.0, expires_at=1020.0)
        state = state_of(record, 1020.0 + after, _alive)
        assert not state.active and state.ackers == 0 and not state.index_at_floor

    def test_reason_out_of_range_is_rejected(self) -> None:
        raw = bytearray(GOOD.encode())
        struct.pack_into("<B", raw, 16 + 3 * 8 + 4, len(REASONS))
        struct.pack_into("<8sII", raw, 0, b"PXSBXYD\0", 1, 0)
        struct.pack_into("<8sII", raw, 0, b"PXSBXYD\0", 1, zlib.crc32(bytes(raw)))
        assert YieldRecord.decode(bytes(raw)) is None


class TestAcks:
    def _signal(self, tmp_path: Path) -> YieldSignal:
        signal = YieldSignal(tmp_path / "state", clock=lambda: 1000.0)
        assert signal.refresh(reason="host_memory_floor", now=1000.0, pid=os_pid()) is not None
        return signal

    @settings(max_examples=60, deadline=None)
    @given(st.lists(st.tuples(st.integers(1, 20), st.booleans()), min_size=1, max_size=40))
    def test_many_pids_keep_the_newest_eight_once_each(self, tmp_path_factory: pytest.TempPathFactory,
                                                       acks: list[tuple[int, bool]]) -> None:
        signal = self._signal(tmp_path_factory.mktemp("y"))
        latest: dict[int, tuple[float, bool]] = {}
        for i, (pid, at_floor) in enumerate(acks):
            now = 1000.0 + i * 0.01
            assert signal.ack(pid, now, at_floor=at_floor)
            latest[pid] = (now, at_floor)
        record = signal.read()
        assert record is not None
        pids = [ack.pid for ack in record.acks]
        assert len(pids) == len(set(pids)) <= MAX_ACKS
        newest = sorted(latest, key=lambda pid: latest[pid][0])[-MAX_ACKS:]
        assert set(pids) == set(newest)
        for ack in record.acks:
            assert (ack.ack_at, ack.at_floor) == latest[ack.pid]
        signal.close()

    def test_a_ninth_worker_above_its_floor_can_be_hidden(self, tmp_path: Path) -> None:
        # Known limit: with more than eight indexing workers the oldest ack is
        # dropped, so index_at_floor speaks for the eight newest only.
        signal = self._signal(tmp_path)
        signal.ack(1, 1000.0, at_floor=False)
        for pid in range(2, 10):
            signal.ack(pid, 1000.1 + pid / 100, at_floor=True)
        state = state_of(signal.read(), 1001.0, _alive)
        assert state.ackers == MAX_ACKS and state.index_at_floor
        signal.close()

    def test_an_ack_on_garbage_is_refused(self, tmp_path: Path) -> None:
        signal = self._signal(tmp_path)
        (tmp_path / "state" / "yield").write_bytes(b"\x00" * RECORD_SIZE)
        assert not signal.ack(5, 1001.0, at_floor=True)
        assert not read_yield_state(tmp_path / "state", now=1001.0).active
        signal.close()

    def test_backed_off_needs_a_recent_ack(self) -> None:
        record = replace(GOOD, set_at=1000.0, expires_at=1020.0, since=850.0,
                         acks=(YieldAck(pid=5, ack_at=1000.0, at_floor=False),))
        state = state_of(record, 1001.0, _alive)
        assert indexing_backed_off(state, 1001.0)  # a 151 s episode
        assert not indexing_backed_off(state, 1031.0)  # the ack is 31 s old


def os_pid() -> int:
    import os

    return os.getpid()


# -- the policy's yield ---------------------------------------------------------

CEILINGS = Ceilings(heavy=6, light=12, index_heavy=16, index_light=40)
SNAP = ResourceSnapshot(
    cpu_quota=8.0, cpu_utilisation=0.2, cpu_throttled_ratio=0.0, cpu_pressure=0.0,
    mem_limit_bytes=32 * 2**30, mem_working_set_bytes=4 * 2**30, mem_working_set_raw_bytes=4 * 2**30, source="test",
)


def _demand(limits: policy.Limits) -> dict[Pool, PoolDemand]:
    return {
        pool: PoolDemand(permit_seconds=limits.get(pool) * 15, blocked_acquires=5, completions=10) for pool in Pool
    }


def _run(signal: list[bool], *, workers: int = 1) -> list[policy.Limits]:
    limits = policy.warm_start_limits(CEILINGS)
    state = ControllerState.initial()
    out = []
    for i, on in enumerate(signal):
        limits, state = policy.next_limits(
            limits, SNAP, CEILINGS, state, _demand(limits), now=1000.0 + 15 * i, interval=15,
            worker_count=workers, host_yield=on,
        )
        out.append(limits)
    return out


class TestPolicyYield:
    @settings(max_examples=80, deadline=None)
    @given(st.lists(st.booleans(), min_size=1, max_size=60), st.integers(1, 6))
    def test_never_grows_while_yielding_and_never_below_the_floor(self, signal: list[bool], workers: int) -> None:
        trace = _run(signal, workers=workers)
        previous = policy.warm_start_limits(CEILINGS)
        for on, limits in zip(signal, trace):
            for pool in policy.YIELD_POOLS:
                floor = policy.pressure_floor(pool, policy.ceiling_for(pool, CEILINGS), workers)
                assert limits.get(pool) >= min(floor, previous.get(pool))
                if on:
                    assert limits.get(pool) <= previous.get(pool)
            previous = limits

    def test_shrink_rates(self) -> None:
        trace = _run([True])
        warm = policy.warm_start_limits(CEILINGS)
        assert trace[0].get(Pool.HEAVY_PARSE) == max(
            policy.pressure_floor(Pool.HEAVY_PARSE, 6, 1), warm.get(Pool.HEAVY_PARSE) - 1,
        )
        for pool in (Pool.INDEX_HEAVY, Pool.INDEX_LIGHT):
            expected = min(warm.get(pool) - 1, math.ceil(warm.get(pool) * 0.75))
            floor = policy.pressure_floor(pool, policy.ceiling_for(pool, CEILINGS), 1)
            assert trace[0].get(pool) == max(floor, expected)
        assert trace[0].get(Pool.LIGHT_PARSE) >= warm.get(Pool.LIGHT_PARSE)

    def test_heavy_holdoff_doubles_on_repeats_up_to_sixty_four(self) -> None:
        state = PoolState()
        now, holdoffs = 0.0, []
        for _ in range(8):
            holdoff = policy._yield_holdoff(Pool.HEAVY_PARSE, state, now, 15.0)
            holdoffs.append(holdoff)
            # The holdoff runs out, and the yield comes back one sample later.
            now += 15.0 * (holdoff + 1)
            state = replace(state, yield_holdoff=0, yield_holdoff_set=holdoff, yield_released_at=now - 15.0)
        assert holdoffs == [4, 8, 16, 32, 64, 64, 64, 64]
        assert policy._yield_holdoff(Pool.INDEX_HEAVY, state, now, 15.0) == policy.YIELD_GROW_CONFIRM_SAMPLES
        # After a quiet spell longer than the repeat window it starts again.
        later = now + 15.0 * (policy.YIELD_REPEAT_WINDOW_SAMPLES + 1)
        assert policy._yield_holdoff(Pool.HEAVY_PARSE, state, later, 15.0) == policy.YIELD_GROW_CONFIRM_SAMPLES

    def test_index_pools_resume_after_four_quiet_samples(self) -> None:
        signal = [True] * 6 + [False] * 12
        trace = _run(signal)
        index = [limits.get(Pool.INDEX_HEAVY) for limits in trace]
        low = index[5]
        # Four samples without the signal hold, the next grows by one.
        assert index[6:10] == [low] * 4
        assert index[10] == low + 1
