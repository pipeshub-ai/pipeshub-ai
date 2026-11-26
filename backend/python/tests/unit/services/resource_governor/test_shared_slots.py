from __future__ import annotations

import fcntl
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor.controller import ResourceGovernor
from app.services.resource_governor.models import ResourceSnapshot
from app.services.resource_governor.shared_slots import (
    LocalTaggedSlots,
    SharedSlots,
    SlotDenied,
    SlotHold,
    open_shared_slots,
    sandbox_state_directory,
    shared_slots_directory,
)

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.fixture(autouse=True)
def _close_shared_slots(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Each ``SharedSlots`` keeps its files open; close them so a run of
    these tests does not exhaust descriptors for the tests after it."""
    opened: list[SharedSlots] = []
    original = SharedSlots.__init__

    def tracking_init(self: SharedSlots, *args: object, **kwargs: object) -> None:
        original(self, *args, **kwargs)
        opened.append(self)

    monkeypatch.setattr(SharedSlots, "__init__", tracking_init)
    yield
    for slots in opened:
        slots.close()


def _hold_all(slots: SharedSlots, limit: int) -> list[SlotHold]:
    holds = []
    while (hold := slots.try_acquire(limit)) is not None:
        holds.append(hold)
    return holds


class TestSharedSlots:
    """Each ``SharedSlots`` on the same directory stands in for one worker
    process: ``flock`` locks taken through separate opens conflict even
    inside one process."""

    def test_workers_together_hold_no_more_than_the_limit(self, tmp_path: Path) -> None:
        one, two = SharedSlots(tmp_path, 8), SharedSlots(tmp_path, 8)
        first = one.try_acquire(2)
        second = two.try_acquire(2)
        assert first is not None and second is not None
        assert one.try_acquire(2) is None
        assert two.try_acquire(2) is None

        second.release()
        assert one.try_acquire(2) is not None

    def test_a_smaller_limit_counts_every_slot_already_held(self, tmp_path: Path) -> None:
        """Slots taken under a larger limit still count, so a shrink admits
        nothing new until the total is under it."""
        slots = SharedSlots(tmp_path, 8)
        holds = _hold_all(slots, 4)
        assert len(holds) == 4
        assert slots.try_acquire(2) is None
        holds[0].release()
        holds[1].release()
        assert slots.try_acquire(2) is None
        holds[2].release()
        assert slots.try_acquire(2) is not None

    def test_a_request_for_several_slots_takes_them_together(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path, 8)
        assert slots.try_acquire(4, count=2) is not None
        assert slots.try_acquire(4, count=2) is not None
        assert slots.try_acquire(4, count=2) is None
        assert slots.try_acquire(5, count=2) is None
        assert slots.try_acquire(5, count=1) is not None

    def test_an_oversized_request_is_admitted_alone(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path, 8)
        alone = slots.try_acquire(1, count=2)
        assert alone is not None
        assert slots.try_acquire(1) is None
        alone.release()
        assert slots.try_acquire(1) is not None

    def test_release_is_idempotent(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path, 1)
        hold = slots.try_acquire(1)
        assert hold is not None
        hold.release()
        hold.release()
        assert slots.try_acquire(1) is not None

    def test_another_worker_counting_reads_as_no_room_yet(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path, 4)
        mutex = os.open(tmp_path / "mutex", os.O_RDWR)
        try:
            fcntl.flock(mutex, fcntl.LOCK_EX)
            assert slots.try_acquire(4) is None
        finally:
            os.close(mutex)
        assert slots.try_acquire(4) is not None

    def test_a_worker_that_dies_gives_its_slot_back(self, tmp_path: Path) -> None:
        SharedSlots(tmp_path, 1)
        holder = subprocess.Popen(
            [
                sys.executable, "-I", "-c",
                "import fcntl, os, sys, time\n"
                "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                "print('held', flush=True)\n"
                "time.sleep(60)\n",
                str(tmp_path / "0"),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"
            slots = SharedSlots(tmp_path, 1)
            assert slots.try_acquire(1) is None
            holder.kill()
            holder.wait(timeout=10)
            assert slots.try_acquire(1) is not None
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)
            if holder.stdout is not None:
                holder.stdout.close()

    def test_slots_held_when_the_directory_is_removed_still_count(
        self, tmp_path: Path
    ) -> None:
        """A lock belongs to the open file, not its name: workers already
        running keep counting each other's slots after a cleaner removes the
        directory, instead of filling a new one up to the limit again."""
        directory = tmp_path / "slots"
        one, two = SharedSlots(directory, 4), SharedSlots(directory, 4)
        held = [one.try_acquire(2), two.try_acquire(2)]
        assert all(hold is not None for hold in held)
        for name in os.listdir(directory):
            os.unlink(directory / name)
        directory.rmdir()

        assert one.try_acquire(2) is None
        assert two.try_acquire(2) is None
        held[0].release()
        assert two.try_acquire(2) is not None

    def test_each_worker_holds_the_directory_against_cleaners(self, tmp_path: Path) -> None:
        """``systemd-tmpfiles`` does not clean a directory with a BSD lock on it."""
        slots = SharedSlots(tmp_path, 1)
        directory = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(directory, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            os.close(directory)
        assert slots.capacity == 1

    def test_closing_gives_every_held_slot_back(self, tmp_path: Path) -> None:
        one, two = SharedSlots(tmp_path, 2), SharedSlots(tmp_path, 2)
        assert one.try_acquire(2, count=2) is not None
        assert two.try_acquire(2) is None
        one.close()
        assert two.try_acquire(2, count=2) is not None

    def test_an_unusable_directory_turns_sharing_off(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        not_a_directory = tmp_path / "file"
        not_a_directory.write_text("")
        with caplog.at_level(logging.WARNING):
            assert open_shared_slots(not_a_directory / "slots", 4) is None
        assert "Shared heavy-parse slots unavailable" in caplog.text


def _tagged_pair(kind: str, tmp_path: Path) -> tuple[object, object]:
    """Two "workers" on one set of slots: two ``SharedSlots`` on one directory,
    or one ``LocalTaggedSlots`` seen twice (it is counted in-process only)."""
    if kind == "shared":
        return SharedSlots(tmp_path, 8), SharedSlots(tmp_path, 8)
    local = LocalTaggedSlots(8)
    return local, local


@pytest.mark.parametrize("kind", ["shared", "local"])
class TestTaggedSlots:
    def test_workers_together_hold_no_more_than_the_limit(self, kind: str, tmp_path: Path) -> None:
        one, two = _tagged_pair(kind, tmp_path)
        assert isinstance(one.try_acquire_tagged(3, tag=b"a", tag_limit=None), SlotHold)
        assert isinstance(two.try_acquire_tagged(3, tag=b"b", tag_limit=None), SlotHold)
        assert isinstance(one.try_acquire_tagged(3, tag=b"", tag_limit=None), SlotHold)
        denied = two.try_acquire_tagged(3, tag=b"a", tag_limit=None)
        assert denied == SlotDenied("full", held=3, tag_held=1)

    def test_the_tag_limit_holds_across_workers(self, kind: str, tmp_path: Path) -> None:
        one, two = _tagged_pair(kind, tmp_path)
        first = one.try_acquire_tagged(8, tag=b"a", tag_limit=2)
        assert isinstance(first, SlotHold) and first.held_before == 0
        assert isinstance(two.try_acquire_tagged(8, tag=b"a", tag_limit=2), SlotHold)
        assert two.try_acquire_tagged(8, tag=b"a", tag_limit=2) == SlotDenied("tag_full", 2, 2)
        assert isinstance(two.try_acquire_tagged(8, tag=b"b", tag_limit=2), SlotHold)
        first.release()
        assert isinstance(two.try_acquire_tagged(8, tag=b"a", tag_limit=2), SlotHold)

    def test_an_empty_tag_is_never_tag_limited(self, kind: str, tmp_path: Path) -> None:
        one, _ = _tagged_pair(kind, tmp_path)
        for _ in range(4):
            assert isinstance(one.try_acquire_tagged(8, tag=b"", tag_limit=1), SlotHold)

    def test_a_released_slots_old_tag_is_not_counted(self, kind: str, tmp_path: Path) -> None:
        one, two = _tagged_pair(kind, tmp_path)
        hold = one.try_acquire_tagged(8, tag=b"a", tag_limit=1)
        assert isinstance(hold, SlotHold)
        hold.release()
        hold.release()
        assert isinstance(two.try_acquire_tagged(8, tag=b"a", tag_limit=1), SlotHold)
        assert two.held_by_tag() == {b"a": 1}

    def test_held_by_tag_counts_every_worker(self, kind: str, tmp_path: Path) -> None:
        one, two = _tagged_pair(kind, tmp_path)
        one.try_acquire_tagged(8, tag=b"a", tag_limit=None)
        two.try_acquire_tagged(8, tag=b"a", tag_limit=None)
        two.try_acquire_tagged(8, tag=b"", tag_limit=None)
        assert one.held_by_tag() == {b"a": 2, b"": 1}
        assert two.held_by_tag() == {b"a": 2, b"": 1}


class TestSharedTaggedSlots:
    def test_the_file_holds_the_tag_only(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path, 2)
        slots.try_acquire_tagged(2, tag=b"0123456789abcdef", tag_limit=None)
        assert (tmp_path / "0").read_bytes() == b"0123456789abcdef\n"

    def test_a_worker_counting_reads_as_busy(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path, 4)
        mutex = os.open(tmp_path / "mutex", os.O_RDWR)
        try:
            fcntl.flock(mutex, fcntl.LOCK_EX)
            assert slots.try_acquire_tagged(4, tag=b"a", tag_limit=None) == SlotDenied("busy")
        finally:
            os.close(mutex)
        assert isinstance(slots.try_acquire_tagged(4, tag=b"a", tag_limit=None), SlotHold)

    def test_held_by_tag_does_not_wait_for_a_busy_mutex(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from app.services.resource_governor import shared_slots

        slots = SharedSlots(tmp_path, 256)
        tries: list[int] = []
        sleeps: list[float] = []
        try_lock = shared_slots._try_lock

        def counted(fd: int) -> bool:
            tries.append(fd)
            return try_lock(fd)

        monkeypatch.setattr(shared_slots, "_try_lock", counted)
        monkeypatch.setattr(shared_slots.time, "sleep", sleeps.append)
        mutex = os.open(tmp_path / "mutex", os.O_RDWR)
        try:
            fcntl.flock(mutex, fcntl.LOCK_EX)
            with pytest.raises(TimeoutError):
                slots.held_by_tag(budget=0.0)
            assert tries == [slots._mutex_fd] and sleeps == []  # one try at the mutex, no spin
        finally:
            os.close(mutex)

    def test_a_dead_holders_tagged_slot_is_free(self, tmp_path: Path) -> None:
        SharedSlots(tmp_path, 1)
        holder = subprocess.Popen(
            [
                sys.executable, "-I", "-c",
                "import fcntl, os, sys, time\n"
                "fd = os.open(sys.argv[1], os.O_RDWR | os.O_CREAT, 0o600)\n"
                "fcntl.flock(fd, fcntl.LOCK_EX)\n"
                "os.pwrite(fd, b'a'.ljust(16) + b'\\n', 0)\n"
                "print('held', flush=True)\n"
                "time.sleep(60)\n",
                str(tmp_path / "0"),
            ],
            stdout=subprocess.PIPE,
            text=True,
        )
        try:
            assert holder.stdout is not None
            assert holder.stdout.readline().strip() == "held"
            slots = SharedSlots(tmp_path, 1)
            assert slots.try_acquire_tagged(1, tag=b"a", tag_limit=1) == SlotDenied("full", 1, 1)
            holder.kill()
            holder.wait(timeout=10)
            assert isinstance(slots.try_acquire_tagged(1, tag=b"a", tag_limit=1), SlotHold)
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=10)
            if holder.stdout is not None:
                holder.stdout.close()

    def test_untagged_and_tagged_holders_count_each_other(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path, 4)
        assert slots.try_acquire(2) is not None
        assert isinstance(slots.try_acquire_tagged(2, tag=b"a", tag_limit=None), SlotHold)
        assert slots.held_count() == 2
        assert slots.try_acquire(2) is None


class TestSandboxStateDirectory:
    def test_unset_is_one_directory_per_uid(self) -> None:
        assert sandbox_state_directory({}) == (
            Path(tempfile.gettempdir()) / f"pipeshub-sandbox-{os.getuid()}"
        )

    def test_an_id_suffixes_the_directory(self) -> None:
        assert sandbox_state_directory({"SANDBOX_STATE_ID": "wt1"}) == (
            Path(tempfile.gettempdir()) / f"pipeshub-sandbox-wt1-{os.getuid()}"
        )

    @pytest.mark.parametrize("raw", ["a/b", "x" * 64, "../up", "a b"])
    def test_an_invalid_id_is_ignored_with_a_warning(
        self, raw: str, caplog: pytest.LogCaptureFixture,
    ) -> None:
        with caplog.at_level(logging.WARNING):
            assert sandbox_state_directory({"SANDBOX_STATE_ID": raw}) == sandbox_state_directory({})
        assert "SANDBOX_STATE_ID" in caplog.text


class TestSharedSlotsDirectory:
    def test_one_directory_per_uid_in_the_temp_directory(self) -> None:
        assert shared_slots_directory("slots") == (
            Path(tempfile.gettempdir()) / f"slots-{os.getuid()}"
        )

    def test_none_without_posix_file_locks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Windows has neither ``fcntl`` nor ``os.getuid``, and naming the
        directory must not stop the indexing service from starting there."""
        monkeypatch.setattr("app.services.resource_governor.shared_slots.fcntl", None)
        monkeypatch.delattr(os, "getuid")
        assert shared_slots_directory("slots") is None


class _FixedProbe:
    def __init__(self, snapshot: ResourceSnapshot) -> None:
        self._snapshot = snapshot

    def snapshot(self) -> ResourceSnapshot:
        return self._snapshot


def _snapshot(*, mem_limit_gb: float, mem_used_gb: float) -> ResourceSnapshot:
    return ResourceSnapshot(
        cpu_quota=32.0,
        cpu_utilisation=0.1,
        cpu_throttled_ratio=0.0,
        cpu_pressure=0.0,
        mem_limit_bytes=int(mem_limit_gb * 1024 ** 3),
        mem_working_set_bytes=int(mem_used_gb * 1024 ** 3),
        source="test",
    )


def _workers(
    count: int, slots_dir: Path, snapshot: ResourceSnapshot
) -> list[ResourceGovernor]:
    return [
        ResourceGovernor(
            logger=logging.getLogger("test.shared_slots"),
            probe=_FixedProbe(snapshot),
            worker_count=count,
            heavy_parse_slots_dir=slots_dir,
        )
        for _ in range(count)
    ]


def _admitted(governors: list[ResourceGovernor]) -> list[SlotHold]:
    """Every heavy parse the workers can start, taking turns."""
    holds: list[SlotHold] = []
    progress = True
    while progress:
        progress = False
        for governor in governors:
            hold = governor.try_acquire_heavy_parse_slots()
            if hold is not None:
                holds.append(hold)
                progress = True
    return holds


class TestWorkersShareHeavyParseSlots:
    @pytest.mark.parametrize(
        ("mem_limit_gb", "mem_used_gb", "expected"),
        [
            (20.0, 13.0, 4),  # 7 GiB free / 1.5
            (20.0, 12.0, 5),  # 8 GiB free: one floor per worker would be 8
            (64.0, 40.0, 16),  # 24 GiB free
            (256.0, 8.0, 32),  # free memory for more than the budget
        ],
    )
    def test_eight_workers_together_start_what_one_process_may(
        self, tmp_path: Path, mem_limit_gb: float, mem_used_gb: float, expected: int
    ) -> None:
        governors = _workers(8, tmp_path, _snapshot(mem_limit_gb=mem_limit_gb, mem_used_gb=mem_used_gb))
        assert governors[0].fleet_ceilings.heavy == 32
        assert len(_admitted(governors)) == expected

    def test_one_worker_is_left_to_its_own_gate(self, tmp_path: Path) -> None:
        (governor,) = _workers(1, tmp_path, _snapshot(mem_limit_gb=20.0, mem_used_gb=19.0))
        holds = [governor.try_acquire_heavy_parse_slots() for _ in range(40)]
        assert all(hold is not None for hold in holds)
