"""Tests for the indexing back-off signal record (yield_signal.py)."""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import yield_signal as ys
from app.services.resource_governor.yield_signal import (
    MAX_ACKS,
    RECORD_SIZE,
    YieldAck,
    YieldRecord,
    YieldSignal,
    YieldState,
    indexing_backed_off,
    read_yield_state,
)

if TYPE_CHECKING:
    from pathlib import Path

NOW = 5000.0
DEAD_PID = 2**22 + 12345


@pytest.fixture
def signal(tmp_path: Path) -> YieldSignal:
    sig = YieldSignal(tmp_path / "state")
    yield sig
    sig.close()


def _write(sig: YieldSignal, record: YieldRecord | bytes) -> None:
    data = record if isinstance(record, bytes) else record.encode()
    os.pwrite(sig._record_fd, data, 0)


class TestRecord:
    def test_round_trips_with_a_full_ack_table(self) -> None:
        acks = tuple(YieldAck(pid=100 + i, ack_at=NOW + i, at_floor=i % 2 == 0) for i in range(MAX_ACKS))
        record = YieldRecord(
            set_at=NOW, expires_at=NOW + 20, since=NOW - 30, writer_pid=42, reason="live_memory", acks=acks,
        )
        data = record.encode()
        assert len(data) == RECORD_SIZE and data.startswith(b"PXSBXYD\0")
        assert YieldRecord.decode(data) == record

    @pytest.mark.parametrize("mangle", ["torn", "version", "empty", "short"])
    def test_invalid_bytes_decode_to_none(self, mangle: str) -> None:
        data = bytearray(YieldRecord(set_at=NOW, expires_at=NOW + 20, since=NOW, writer_pid=1).encode())
        if mangle == "torn":
            data[40] ^= 0xFF
        elif mangle == "version":
            data[8] = 2
        elif mangle == "empty":
            data = bytearray(RECORD_SIZE)
        else:
            data = data[:100]
        assert YieldRecord.decode(bytes(data)) is None


class TestActive:
    def test_refresh_then_read_is_active(self, signal: YieldSignal) -> None:
        refreshed = signal.refresh(reason="host_memory_floor", ttl_s=20, now=NOW)
        assert refreshed is not None and refreshed.started
        state = signal.read_state(NOW + 1)
        assert state.active and state.since == NOW and state.ackers == 0 and not state.index_at_floor

    def test_inactive_after_the_ttl(self, signal: YieldSignal) -> None:
        signal.refresh(reason="host_memory_floor", ttl_s=20, now=NOW)
        assert not signal.read_state(NOW + 20).active

    def test_a_dead_writer_is_inactive_after_ten_seconds(self, signal: YieldSignal) -> None:
        signal.refresh(reason="host_memory_floor", ttl_s=60, now=NOW, pid=DEAD_PID)
        assert signal.read_state(NOW + 9).active
        assert not signal.read_state(NOW + 11).active

    def test_a_live_writer_is_active_within_the_ttl(self, signal: YieldSignal) -> None:
        signal.refresh(reason="host_memory_floor", ttl_s=60, now=NOW)
        assert signal.read_state(NOW + 50).active

    def test_an_expiry_too_far_out_is_inactive(self, signal: YieldSignal) -> None:
        _write(signal, YieldRecord(set_at=NOW, expires_at=NOW + 121, since=NOW, writer_pid=os.getpid()))
        assert not signal.read_state(NOW + 1).active

    def test_a_record_from_before_a_clock_restart_is_inactive(self, signal: YieldSignal) -> None:
        # The file outlives a host reboot in the container's /tmp; the
        # monotonic clock starts again, and the writer's pid may be reused.
        _write(signal, YieldRecord(set_at=NOW, expires_at=NOW + 20, since=NOW, writer_pid=os.getpid()))
        assert not signal.read_state(30.0).active
        refreshed = signal.refresh(reason="host_memory_floor", now=30.0)
        assert refreshed is not None and refreshed.started and refreshed.since == 30.0
        assert signal.read_state(31.0).active

    def test_a_refresh_just_after_the_readers_clock_read_is_active(self, signal: YieldSignal) -> None:
        signal.refresh(reason="host_memory_floor", now=NOW + 0.01)
        assert signal.read_state(NOW).active
        again = signal.refresh(reason="host_memory_floor", now=NOW)
        assert again is not None and not again.started and again.since == NOW + 0.01

    @pytest.mark.parametrize("data", [b"", b"\0" * RECORD_SIZE, b"garbage" * 40])
    def test_garbage_is_inactive(self, signal: YieldSignal, data: bytes) -> None:
        _write(signal, data)
        assert signal.read_state(NOW) == YieldState()

    def test_since_is_kept_across_refreshes_and_reset_after_expiry(self, signal: YieldSignal) -> None:
        signal.refresh(reason="host_memory_floor", now=NOW)
        again = signal.refresh(reason="live_memory", now=NOW + 5)
        assert again is not None and not again.started and again.since == NOW
        assert signal.read_state(NOW + 6).since == NOW
        later = signal.refresh(reason="host_memory_floor", now=NOW + 60)
        assert later is not None and later.started and later.since == NOW + 60

    def test_a_busy_lock_skips_without_waiting(self, signal: YieldSignal, tmp_path: Path) -> None:
        other = YieldSignal(tmp_path / "state")
        try:
            with other._locked(0.0) as locked:
                assert locked
                assert signal.refresh(reason="host_memory_floor", now=NOW) is None
        finally:
            other.close()
        assert not signal.read_state(NOW).active


class TestAck:
    def test_ack_keeps_the_writer_fields(self, signal: YieldSignal) -> None:
        signal.refresh(reason="live_memory", ttl_s=20, now=NOW)
        before = signal.read()
        assert signal.ack(os.getpid(), NOW + 1, at_floor=True)
        after = signal.read()
        assert before is not None and after is not None
        assert (after.set_at, after.expires_at, after.since, after.writer_pid, after.reason) == (
            before.set_at, before.expires_at, before.since, before.writer_pid, before.reason,
        )
        assert after.acks == (YieldAck(os.getpid(), NOW + 1, True),)

    def test_no_ack_on_an_inactive_signal(self, signal: YieldSignal) -> None:
        assert not signal.ack(os.getpid(), NOW, at_floor=True)

    def test_a_new_episode_drops_old_acks(self, signal: YieldSignal) -> None:
        signal.refresh(reason="live_memory", now=NOW)
        signal.ack(os.getpid(), NOW + 1, at_floor=True)
        signal.refresh(reason="live_memory", now=NOW + 40)
        record = signal.read()
        assert record is not None and record.acks == ()

    @pytest.mark.parametrize("last", ["floor", "above"])
    def test_one_worker_above_its_floor_keeps_index_at_floor_false(
        self, signal: YieldSignal, last: str,
    ) -> None:
        signal.refresh(reason="live_memory", now=NOW)
        order = [(101, True), (102, False)] if last == "above" else [(102, False), (101, True)]
        for i, (pid, at_floor) in enumerate(order):
            signal.ack(pid, NOW + 1 + i, at_floor=at_floor)
        state = ys.state_of(signal.read(), NOW + 3, alive=lambda pid: True)
        assert state.ackers == 2 and not state.index_at_floor and state.acked_at == NOW + 2

    def test_both_at_floor_is_true(self, signal: YieldSignal) -> None:
        signal.refresh(reason="live_memory", now=NOW)
        signal.ack(101, NOW + 1, at_floor=True)
        signal.ack(102, NOW + 2, at_floor=True)
        assert ys.state_of(signal.read(), NOW + 3, alive=lambda pid: True).index_at_floor

    def test_a_stale_or_dead_ack_above_floor_is_ignored(self, signal: YieldSignal) -> None:
        signal.refresh(reason="live_memory", ttl_s=60, now=NOW)
        signal.ack(102, NOW + 1, at_floor=False)
        signal.refresh(reason="live_memory", ttl_s=60, now=NOW + 40)
        signal.ack(101, NOW + 40, at_floor=True)
        stale = ys.state_of(signal.read(), NOW + 41, alive=lambda pid: True)
        assert stale.ackers == 1 and stale.index_at_floor
        signal.ack(102, NOW + 41, at_floor=False)
        dead = ys.state_of(signal.read(), NOW + 42, alive=lambda pid: pid != 102)
        assert dead.ackers == 1 and dead.index_at_floor

    def test_a_ninth_pid_replaces_the_oldest(self, signal: YieldSignal) -> None:
        signal.refresh(reason="live_memory", now=NOW)
        for i in range(MAX_ACKS):
            signal.ack(200 + i, NOW + 0.5 + i * 0.1, at_floor=True)
        signal.ack(999, NOW + 2, at_floor=True)
        record = signal.read()
        assert record is not None
        assert len(record.acks) == MAX_ACKS and {a.pid for a in record.acks} == {999, *range(201, 208)}

    def test_a_pid_updates_its_own_entry(self, signal: YieldSignal) -> None:
        signal.refresh(reason="live_memory", now=NOW)
        signal.ack(101, NOW + 1, at_floor=False)
        signal.ack(101, NOW + 2, at_floor=True)
        record = signal.read()
        assert record is not None and record.acks == (YieldAck(101, NOW + 2, True),)


class TestUnavailable:
    def test_a_directory_that_is_not_private_is_unavailable_with_one_warning(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
    ) -> None:
        shared = tmp_path / "shared"
        shared.mkdir(mode=0o755)
        shared.chmod(0o755)
        monkeypatch.setattr(ys, "sandbox_state_directory", lambda: shared)
        with pytest.raises(PermissionError):
            YieldSignal(shared)
        with caplog.at_level(logging.WARNING, logger=ys.__name__):
            assert YieldSignal.open_default(role="test") is None
        assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1

    def test_read_yield_state_of_a_missing_or_public_file_is_inactive(self, tmp_path: Path) -> None:
        assert read_yield_state(tmp_path / "nowhere") == YieldState()
        public = tmp_path / "public"
        public.mkdir()
        (public / "yield").write_bytes(
            YieldRecord(set_at=NOW, expires_at=NOW + 20, since=NOW, writer_pid=os.getpid()).encode(),
        )
        (public / "yield").chmod(0o644)
        assert read_yield_state(public, now=NOW + 1) == YieldState()
        assert not (tmp_path / "nowhere").exists()

    def test_read_yield_state_reads_the_writer(self, signal: YieldSignal) -> None:
        signal.refresh(reason="host_memory_floor", now=NOW)
        signal.ack(os.getpid(), NOW + 1, at_floor=True)
        state = read_yield_state(signal.directory, now=NOW + 2)
        assert state == YieldState(active=True, since=NOW, acked_at=NOW + 1, index_at_floor=True, ackers=1)


class TestBackedOff:
    def test_needs_an_active_recent_ack(self) -> None:
        assert not indexing_backed_off(YieldState(), NOW)
        no_ack = YieldState(active=True, since=NOW - 200)
        assert not indexing_backed_off(no_ack, NOW)
        old_ack = YieldState(active=True, since=NOW - 200, acked_at=NOW - 31, index_at_floor=True, ackers=1)
        assert not indexing_backed_off(old_ack, NOW)

    def test_at_floor_or_a_long_episode(self) -> None:
        above = YieldState(active=True, since=NOW - 60, acked_at=NOW - 5, index_at_floor=False, ackers=2)
        assert not indexing_backed_off(above, NOW)
        long_episode = YieldState(active=True, since=NOW - 120, acked_at=NOW - 5, index_at_floor=False, ackers=2)
        assert indexing_backed_off(long_episode, NOW)
        at_floor = YieldState(active=True, since=NOW - 10, acked_at=NOW - 5, index_at_floor=True, ackers=1)
        assert indexing_backed_off(at_floor, NOW)
