"""Fallback and busy paths of the shared slot files (shared_slots.py)."""

from __future__ import annotations

import logging
import os
import time
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import shared_slots as ss
from app.services.resource_governor.shared_slots import (
    LocalTaggedSlots,
    SharedSlots,
    SlotHold,
    sandbox_state_directory,
)

if TYPE_CHECKING:
    from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - not POSIX
    fcntl = None  # type: ignore[assignment]


@pytest.mark.skipif(fcntl is None, reason="needs POSIX file locks")
class TestBusyMutex:
    def _hold_mutex(self, slots: SharedSlots) -> int:
        fd = os.open(slots.directory / ss._MUTEX_FILE, os.O_RDWR)
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd

    def test_held_by_tag_spins_for_its_budget_then_times_out(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path / "slots", 2)
        other = self._hold_mutex(slots)
        try:
            started = time.monotonic()
            with pytest.raises(TimeoutError):
                slots.held_by_tag(budget=0.02)
            # wall-clock-ok: the other holder keeps the mutex for the whole block; 2 s is a hundred times the 20 ms budget
            assert 0.015 <= time.monotonic() - started < 2.0
            with pytest.raises(TimeoutError):
                slots.held_count(budget=0.0)
        finally:
            os.close(other)
            slots.close()

    def test_held_by_tag_answers_once_the_mutex_is_free(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path / "slots", 2)
        try:
            hold = slots.try_acquire_tagged(2, tag=b"org-a", tag_limit=None)
            assert isinstance(hold, SlotHold)
            assert slots.held_by_tag() == {b"org-a": 1}
            hold.release()
            assert slots.held_by_tag() == {}
        finally:
            slots.close()

    def test_a_closed_set_counts_nothing(self, tmp_path: Path) -> None:
        slots = SharedSlots(tmp_path / "slots", 2)
        slots.close()
        slots.close()  # twice is fine
        assert slots.held_by_tag() == {}
        assert slots.held_count() == 0


class TestLocalTaggedSlots:
    def test_close_frees_every_slot(self) -> None:
        slots = LocalTaggedSlots(2)
        assert isinstance(slots.try_acquire_tagged(2, tag=b"a", tag_limit=None), SlotHold)
        assert isinstance(slots.try_acquire_tagged(2, tag=b"b", tag_limit=None), SlotHold)
        assert slots.try_acquire_tagged(2, tag=b"c", tag_limit=None).reason == "full"
        slots.close()
        assert slots.held_by_tag() == {}
        assert isinstance(slots.try_acquire_tagged(2, tag=b"c", tag_limit=None), SlotHold)

    def test_a_capacity_below_one_still_has_one_slot(self) -> None:
        assert LocalTaggedSlots(0).capacity == 1


class TestStateId:
    @pytest.fixture(autouse=True)
    def _fresh_warnings(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ss, "_warned_state_ids", set())

    def test_a_bad_id_is_ignored_with_one_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        default = sandbox_state_directory({})
        with caplog.at_level(logging.WARNING, logger=ss.logger.name):
            for _ in range(3):
                assert sandbox_state_directory({"SANDBOX_STATE_ID": "../escape"}) == default
        assert caplog.text.count("Ignoring SANDBOX_STATE_ID") == 1

    @pytest.mark.parametrize(
        ("raw", "suffix"),
        [
            ("stack1", "pipeshub-sandbox-stack1-"),
            ("  a.b_c-1  ", "pipeshub-sandbox-a.b_c-1-"),
            ("x" * 63, f"pipeshub-sandbox-{'x' * 63}-"),
            ("", "pipeshub-sandbox-"),
            ("   ", "pipeshub-sandbox-"),
            ("x" * 64, "pipeshub-sandbox-"),
            ("a/b", "pipeshub-sandbox-"),
            ("é", "pipeshub-sandbox-"),
        ],
    )
    def test_ids_name_the_directory(self, raw: str, suffix: str) -> None:
        if fcntl is None:  # pragma: no cover - not POSIX
            assert sandbox_state_directory({"SANDBOX_STATE_ID": raw}) is None
            return
        path = sandbox_state_directory({"SANDBOX_STATE_ID": raw})
        assert path is not None
        assert path.name == f"{suffix}{os.getuid()}"
