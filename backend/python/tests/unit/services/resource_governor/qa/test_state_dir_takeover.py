"""The code sandbox's state directory against another local user.

Its name under ``/tmp`` is predictable, so on a host shared with other users
someone could create it (or a symlink at its name) before the stack starts.
Every component that uses it must then refuse it, fall back to per-worker
state, and log one ERROR naming the path, its owner and mode, and the fix,
rather than share files with that user or turn the load alerts off silently.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

import pytest

from app.agent_loop_lib.sandbox.governor import _open_slots
from app.agent_loop_lib.sandbox.live_record import LiveLedger
from app.agent_loop_lib.sandbox.load_ledger import LoadLedger
from app.services.resource_governor import shared_slots
from app.services.resource_governor.shared_slots import (
    LocalTaggedSlots,
    SharedSlots,
    ensure_private_directory,
)
from app.services.resource_governor.yield_signal import YieldSignal
from app.services.sandbox_alerts.leader import AlertLeader

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def _fresh_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared_slots, "_reported", set())


def _errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]


def test_an_open_state_directory_is_refused_for_the_slots(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    state = tmp_path / "pipeshub-sandbox-1000"
    state.mkdir()
    state.chmod(0o777)  # as another user would leave it for us to fill
    with caplog.at_level(logging.WARNING):
        slots = _open_slots(state, 8)
    assert isinstance(slots, LocalTaggedSlots)
    assert not (state / "slots").exists()
    errors = _errors(caplog)
    assert len(errors) == 1
    assert str(state) in errors[0] and "mode 0777" in errors[0] and "SANDBOX_STATE_ID" in errors[0]


def test_a_subdirectory_of_an_open_directory_is_refused(tmp_path: Path) -> None:
    state = tmp_path / "state"
    state.mkdir()
    state.chmod(0o777)
    with pytest.raises(PermissionError, match="parent directory"):
        SharedSlots(state / "slots", 4)
    assert not (state / "slots").exists()


def test_a_sticky_shared_parent_like_tmp_is_fine(tmp_path: Path) -> None:
    shared_tmp = tmp_path / "tmp"
    shared_tmp.mkdir()
    shared_tmp.chmod(0o1777)
    ensure_private_directory(shared_tmp / "pipeshub-sandbox-1000" / "slots")
    assert (shared_tmp / "pipeshub-sandbox-1000").stat().st_mode & 0o777 == 0o700


def test_a_directory_owned_by_another_user_turns_every_component_off_with_one_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    state = tmp_path / "state"
    state.mkdir(mode=0o700)
    uid = os.getuid()
    monkeypatch.setattr(os, "getuid", lambda: uid + 1)  # the directory is now someone else's
    # ...but not its parent, as ``/tmp`` (root's) would not be.
    monkeypatch.setattr(shared_slots, "_check_parent", lambda parent: None)
    with caplog.at_level(logging.WARNING):
        assert isinstance(_open_slots(state, 8), LocalTaggedSlots)
        with pytest.raises(PermissionError):
            LiveLedger(state)
        with pytest.raises(PermissionError):
            YieldSignal(state)
        ledger = LoadLedger(state, mode="compose", backed_off=lambda: False)
        assert ledger.flush() is False
        with pytest.raises(OSError):
            AlertLeader(state).try_acquire()
    errors = _errors(caplog)
    assert len(errors) == 1, errors
    assert f"owner uid {uid}" in errors[0] and str(state) in errors[0]


def test_a_symlink_at_the_state_name_is_refused(
    tmp_path: Path, caplog: pytest.LogCaptureFixture,
) -> None:
    theirs = tmp_path / "theirs"
    theirs.mkdir(mode=0o700)
    state = tmp_path / "state"
    state.symlink_to(theirs)
    with caplog.at_level(logging.WARNING):
        assert isinstance(_open_slots(state, 4), LocalTaggedSlots)
    assert list(theirs.iterdir()) == []
    assert any("symlink" in e for e in _errors(caplog))


def test_slots_swapped_for_a_link_after_the_check_are_never_opened(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # The race: the name is checked, then replaced by a link to another
    # user's directory before the files are opened.
    attacker = tmp_path / "attacker"
    attacker.mkdir()
    for name in ("mutex", "0", "1"):
        (attacker / name).write_bytes(b"")
        (attacker / name).chmod(0o666)
    real_check = shared_slots.ensure_private_directory

    def check_then_swap(directory: Path) -> None:
        real_check(directory)
        directory.rename(directory.with_name("slots.moved"))
        directory.symlink_to(attacker)

    monkeypatch.setattr(shared_slots, "ensure_private_directory", check_then_swap)
    with pytest.raises(PermissionError, match="symlink"):
        SharedSlots(tmp_path / "state" / "slots", 2)
    assert all((attacker / name).read_bytes() == b"" for name in ("mutex", "0", "1"))


def test_a_slot_file_that_is_a_link_is_refused(tmp_path: Path) -> None:
    slots = tmp_path / "state" / "slots"
    ensure_private_directory(slots)
    target = tmp_path / "elsewhere"
    target.write_bytes(b"keep")
    (slots / "0").symlink_to(target)
    with pytest.raises(PermissionError, match="symlink"):
        SharedSlots(slots, 2)
    assert target.read_bytes() == b"keep"


def test_our_own_private_directory_still_shares_the_slots(tmp_path: Path) -> None:
    state = tmp_path / "state"
    slots = _open_slots(state, 4)
    try:
        assert isinstance(slots, SharedSlots)
        assert state.stat().st_mode & 0o777 == 0o700
        assert (state / "slots").stat().st_mode & 0o777 == 0o700
    finally:
        slots.close()
