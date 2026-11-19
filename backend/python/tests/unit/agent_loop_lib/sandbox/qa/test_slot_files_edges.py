"""Slot files that are not what the governor wrote, and a state directory
that changes under running workers."""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.capacity import FixedLimit, SandboxCapacity, org_tag
from app.agent_loop_lib.sandbox.governor import (
    SandboxLimitExceeded,
    SandboxResourceGovernor,
    _open_slots,
)
from app.services.resource_governor.shared_slots import LocalTaggedSlots, SharedSlots

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def opened() -> list[SharedSlots]:
    slots: list[SharedSlots] = []
    yield slots
    for s in slots:
        s.close()


def _gov(
    directory: Path, opened: list, *, limit: int = 3, org_cap: int | None = 2, files: int = 4,
) -> tuple[SandboxResourceGovernor, SharedSlots]:
    slots = SharedSlots(directory, files)
    opened.append(slots)
    capacity = SandboxCapacity.fixed(limit, org_cap, slot_files=files)
    return SandboxResourceGovernor(capacity, slots, limit_provider=FixedLimit(capacity), poll_s=0.02), slots


class TestForeignContent:
    async def test_a_held_slot_with_garbage_bytes_counts_but_matches_no_org(self, tmp_path, opened) -> None:
        a, a_slots = _gov(tmp_path / "slots", opened)
        b, _ = _gov(tmp_path / "slots", opened)
        lease = await a.acquire(org_id="o")
        index = lease.hold._indices[0]
        os.pwrite(a_slots._slot_fds[index], b"\xff\xfe\x00not-utf8\xe2\x82", 0)

        snap = b.snapshot()  # must not raise on bytes that are not text
        assert snap["total"] == 1
        # "o" still has its full share in the other worker: the garbage is not "o".
        first = await b.acquire(org_id="o")
        second = await b.acquire(org_id="o")
        await b.release(first)
        await b.release(second)
        await a.release(lease)

    async def test_a_waiting_log_survives_a_garbage_tag(self, tmp_path, opened, caplog) -> None:
        a, a_slots = _gov(tmp_path / "slots", opened, limit=1)
        b, _ = _gov(tmp_path / "slots", opened, limit=1)
        lease = await a.acquire()
        os.pwrite(a_slots._slot_fds[lease.hold._indices[0]], b"\xff" * 16 + b"\n", 0)
        caplog.set_level(logging.INFO)
        with pytest.raises(SandboxLimitExceeded):
            await b.acquire(wait_s=0.05)
        assert "sandbox slots: total=1" in caplog.text
        await a.release(lease)

    async def test_a_truncated_held_file_counts_as_untagged(self, tmp_path, opened) -> None:
        a, a_slots = _gov(tmp_path / "slots", opened)
        b, _ = _gov(tmp_path / "slots", opened)
        lease = await a.acquire(org_id="o")
        os.ftruncate(a_slots._slot_fds[lease.hold._indices[0]], 0)
        assert b.snapshot()["total"] == 1
        assert b.snapshot()["by_org"] == {}
        await a.release(lease)

    async def test_a_stale_tag_in_a_free_file_is_never_counted(self, tmp_path, opened) -> None:
        a, _ = _gov(tmp_path / "slots", opened)
        for index in range(4):
            (tmp_path / "slots" / str(index)).write_bytes(org_tag("o").ljust(16) + b"\n")
        assert a.snapshot()["total"] == 0
        leases = [await a.acquire(org_id="o") for _ in range(2)]
        with pytest.raises(SandboxLimitExceeded):
            await a.acquire(org_id="o")
        for lease in leases:
            await a.release(lease)


class TestStateDirectory:
    async def test_a_removed_directory_splits_old_and_new_workers(self, tmp_path, opened) -> None:
        """Known limit (module docstring): a worker started after the
        directory was removed does not see slots held on the old files."""
        old, _ = _gov(tmp_path / "slots", opened, limit=1, org_cap=None)
        lease = await old.acquire()
        shutil.rmtree(tmp_path / "slots")
        # The old worker still counts its own and frees it normally.
        with pytest.raises(SandboxLimitExceeded):
            await old.acquire()
        new, _ = _gov(tmp_path / "slots", opened, limit=1, org_cap=None)
        extra = await new.acquire()
        assert new.held_total() == 1  # the old worker's slot is invisible here
        await new.release(extra)
        await old.release(lease)
        assert old.held_total() == 0

    def test_a_symlink_at_the_directory_name_is_refused(self, tmp_path, caplog) -> None:
        real = tmp_path / "elsewhere"
        real.mkdir(mode=0o700)
        os.symlink(real, tmp_path / "state")
        (tmp_path / "state2").mkdir(mode=0o700)
        os.symlink(real, tmp_path / "state2" / "slots")
        for state in (tmp_path / "state2",):
            slots = _open_slots(state, 4)
            assert isinstance(slots, LocalTaggedSlots)
        assert "counted per worker" in caplog.text

    @pytest.mark.parametrize("mode", [0o755, 0o770, 0o701])
    def test_a_directory_others_can_reach_falls_back_to_per_worker(self, tmp_path, mode, caplog) -> None:
        (tmp_path / "slots").mkdir()
        os.chmod(tmp_path / "slots", mode)
        assert isinstance(_open_slots(tmp_path, 4), LocalTaggedSlots)
        assert "counted per worker" in caplog.text

    def test_a_directory_where_a_slot_file_belongs_falls_back(self, tmp_path, caplog) -> None:
        (tmp_path / "slots").mkdir(mode=0o700)
        (tmp_path / "slots" / "2").mkdir()
        assert isinstance(_open_slots(tmp_path, 4), LocalTaggedSlots)
        assert "counted per worker" in caplog.text


class TestSlotFileCount:
    async def test_a_worker_with_fewer_files_misses_the_high_slots(self, tmp_path, opened) -> None:
        """Known limit: every worker must open the same number of files
        (``slot_file_count()`` reads only the environment). A worker restarted
        with fewer files than one still running cannot see the higher ones."""
        big, _ = _gov(tmp_path / "slots", opened, limit=8, org_cap=None, files=8)
        leases = [await big.acquire() for _ in range(6)]
        for lease in leases[:4]:
            await big.release(lease)  # slots 4 and 5 stay held
        small, _ = _gov(tmp_path / "slots", opened, limit=4, org_cap=None, files=4)
        assert big.held_total() == 2
        assert small.held_total() == 0
        for lease in leases[4:]:
            await big.release(lease)

    async def test_a_worker_with_more_files_sees_every_low_slot(self, tmp_path, opened) -> None:
        small, _ = _gov(tmp_path / "slots", opened, limit=4, org_cap=None, files=4)
        leases = [await small.acquire() for _ in range(4)]
        big, _ = _gov(tmp_path / "slots", opened, limit=4, org_cap=None, files=8)
        assert big.held_total() == 4
        with pytest.raises(SandboxLimitExceeded):
            await big.acquire()
        for lease in leases:
            await small.release(lease)


async def test_held_slots_are_dropped_with_the_closed_files(tmp_path, opened) -> None:
    a, _ = _gov(tmp_path / "slots", opened, limit=2, org_cap=None)
    b, _ = _gov(tmp_path / "slots", opened, limit=2, org_cap=None)
    await a.acquire()
    await a.acquire()
    assert b.held_total() == 2
    a.close()
    assert b.held_total() == 0
    await asyncio.sleep(0)
