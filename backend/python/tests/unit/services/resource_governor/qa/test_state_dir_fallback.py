"""Where the shared state goes when ``TMPDIR`` is open to every user.

A ``/tmp`` of mode 0777 without the sticky bit (a Kubernetes ``emptyDir``
mounted there, or a 0777 bind mount) lets another user rename our directory,
so it is refused. Refusing it used to turn all shared state off: every worker
counted alone, so the host cap became N workers x the cap. Now the state goes
to ``~/.cache/<name>-<uid>``, chosen the same way by every process of the
user, with one WARNING; only when no home is safe either does it give up,
with one ERROR.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.agent_loop_lib.sandbox.governor import _open_slots
from app.services.resource_governor import shared_slots
from app.services.resource_governor.shared_slots import (
    LocalTaggedSlots,
    SharedSlots,
    sandbox_state_directory,
    shared_slots_directory,
)

_UID = os.getuid()
_LEAF = f"pipeshub-sandbox-{_UID}"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared_slots, "_reported", set())
    monkeypatch.setattr(shared_slots, "_warned_locations", set())
    monkeypatch.delenv("SANDBOX_STATE_DIR", raising=False)
    monkeypatch.delenv("SANDBOX_STATE_ID", raising=False)


def _dir(path: Path, mode: int) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(mode)
    return path


def _env(
    monkeypatch: pytest.MonkeyPatch, tmp: Path, home: Path, account: Path | None = None,
) -> None:
    monkeypatch.setattr(tempfile, "tempdir", str(tmp))
    monkeypatch.setenv("TMPDIR", str(tmp))
    monkeypatch.setenv("HOME", str(home))
    import pwd

    monkeypatch.setattr(
        pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(account or home)),
    )


def _messages(caplog: pytest.LogCaptureFixture, level: int) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.levelno == level]


@pytest.mark.parametrize("mode", [0o777, 0o2777])  # emptyDir, with and without fsGroup
def test_an_open_tmpdir_moves_the_state_to_the_home_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, mode: int,
) -> None:
    tmp = _dir(tmp_path / "tmp", mode)
    home = _dir(tmp_path / "home", 0o755)
    _env(monkeypatch, tmp, home)
    with caplog.at_level(logging.WARNING):
        state = sandbox_state_directory()
        assert state == home / ".cache" / _LEAF
        assert sandbox_state_directory() == state  # asked again: same place, no new log
        slots = _open_slots(state, 4)
    assert isinstance(slots, SharedSlots)
    slots.close()
    assert (home / ".cache").stat().st_mode & 0o777 == 0o700
    assert state.stat().st_mode & 0o777 == 0o700
    warnings = _messages(caplog, logging.WARNING)
    assert len(warnings) == 1
    assert str(tmp / _LEAF) in warnings[0] and str(state) in warnings[0]
    assert _messages(caplog, logging.ERROR) == []
    # The heavy-parse lanes use the same rule.
    assert shared_slots_directory("heavy") == home / ".cache" / f"heavy-{_UID}"


def test_two_processes_pick_the_same_place_and_share_the_slots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The query and the indexing service (separate processes, same env)."""
    tmp = _dir(tmp_path / "tmp", 0o777)
    home = _dir(tmp_path / "home", 0o700)
    _env(monkeypatch, tmp, home)
    expected = home / ".cache" / _LEAF
    child = textwrap.dedent(
        """
        import sys, time
        from app.agent_loop_lib.sandbox.governor import _open_slots
        from app.services.resource_governor.shared_slots import sandbox_state_directory
        state = sandbox_state_directory()
        slots = _open_slots(state, 4)
        # The mutex is never waited on: a "busy" try is retried, as the governor's acquire does.
        hold = slots.try_acquire_tagged(4, tag=b"org", tag_limit=None)
        while getattr(hold, "reason", None) == "busy":
            time.sleep(0.01)
            hold = slots.try_acquire_tagged(4, tag=b"org", tag_limit=None)
        print(state, type(slots).__name__, flush=True)
        sys.stdin.read()  # hold the slot until the parent is done
        hold.release()
        """,
    )
    env = {**os.environ, "TMPDIR": str(tmp), "HOME": str(home)}
    env.pop("SANDBOX_STATE_DIR", None)
    env.pop("SANDBOX_STATE_ID", None)
    procs = [
        subprocess.Popen(
            [sys.executable, "-c", child], cwd=Path(shared_slots.__file__).parents[3], env=env, text=True,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        )
        for _ in range(2)
    ]
    try:
        lines = [p.stdout.readline().split() for p in procs if p.stdout]
        assert lines == [[str(expected), "SharedSlots"]] * 2
        assert sandbox_state_directory() == expected
        slots = _open_slots(expected, 4)
        assert isinstance(slots, SharedSlots)
        assert slots.held_by_tag()[b"org"] == 2  # both children's slots, seen here
        slots.close()
    finally:
        for p in procs:
            p.communicate("", timeout=30)


def test_a_sticky_tmp_is_used_as_before(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    tmp = _dir(tmp_path / "tmp", 0o1777)
    home = _dir(tmp_path / "home", 0o755)
    _env(monkeypatch, tmp, home)
    with caplog.at_level(logging.WARNING):
        assert sandbox_state_directory() == tmp / _LEAF
        assert sandbox_state_directory({"SANDBOX_STATE_ID": "wt1"}) == tmp / f"pipeshub-sandbox-wt1-{_UID}"
    assert caplog.records == []
    assert not (home / ".cache").exists()


def test_an_open_home_falls_back_to_the_account_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Helm chart points HOME at an emptyDir (0777, or 2777 with
    fsGroup); the image's account home is the user's own."""
    tmp = _dir(tmp_path / "tmp", 0o777)
    home = _dir(tmp_path / "scratch-home", 0o2777)
    account = _dir(tmp_path / "account", 0o700)
    _env(monkeypatch, tmp, home, account)
    assert sandbox_state_directory() == account / ".cache" / _LEAF


def test_no_safe_place_gives_up_with_one_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    tmp = _dir(tmp_path / "tmp", 0o777)
    home = _dir(tmp_path / "home", 0o777)
    _env(monkeypatch, tmp, home)
    with caplog.at_level(logging.WARNING):
        state = sandbox_state_directory()
        assert state == tmp / _LEAF
        assert isinstance(_open_slots(state, 4), LocalTaggedSlots)
        sandbox_state_directory()
    assert not state.exists()
    assert not (home / ".cache").exists()
    errors = _messages(caplog, logging.ERROR)
    assert len(errors) == 1
    assert str(tmp) in errors[0] and str(home / ".cache" / _LEAF) in errors[0]
    assert "SANDBOX_STATE_DIR" in errors[0]


def test_a_home_with_an_open_cache_is_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp = _dir(tmp_path / "tmp", 0o777)
    home = _dir(tmp_path / "home", 0o755)
    _dir(home / ".cache", 0o777)  # another user could rename our directory out of it
    _env(monkeypatch, tmp, home)
    assert sandbox_state_directory() == tmp / _LEAF  # refused by every user


def test_a_tmpdir_and_home_of_another_user_are_not_used(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both are owned by "someone else" once ``getuid`` reports another uid."""
    tmp = _dir(tmp_path / "tmp", 0o755)
    home = _dir(tmp_path / "home", 0o755)
    _env(monkeypatch, tmp, home)
    monkeypatch.setattr(os, "getuid", lambda: _UID + 1)
    state = sandbox_state_directory()
    assert state == tmp / f"pipeshub-sandbox-{_UID + 1}"
    assert isinstance(_open_slots(state, 4), LocalTaggedSlots)
    assert not state.exists()


def test_a_planted_state_directory_under_a_sticky_tmp_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp = _dir(tmp_path / "tmp", 0o1777)
    home = _dir(tmp_path / "home", 0o755)
    _env(monkeypatch, tmp, home)
    _dir(tmp / _LEAF, 0o777)
    assert sandbox_state_directory() == tmp / _LEAF
    assert isinstance(_open_slots(tmp / _LEAF, 4), LocalTaggedSlots)


def test_an_explicit_state_dir_wins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    tmp = _dir(tmp_path / "tmp", 0o777)
    home = _dir(tmp_path / "home", 0o755)
    chosen = _dir(tmp_path / "state", 0o700)
    _env(monkeypatch, tmp, home)
    monkeypatch.setenv("SANDBOX_STATE_DIR", str(chosen))
    with caplog.at_level(logging.WARNING):
        assert sandbox_state_directory() == chosen / _LEAF
        assert sandbox_state_directory({"SANDBOX_STATE_ID": "a"}) == chosen / f"pipeshub-sandbox-a-{_UID}"
    assert caplog.records == []
    monkeypatch.setenv("SANDBOX_STATE_DIR", "relative/dir")
    with caplog.at_level(logging.WARNING):
        assert sandbox_state_directory() == home / ".cache" / _LEAF
    assert any("SANDBOX_STATE_DIR" in m for m in _messages(caplog, logging.WARNING))


def test_an_open_explicit_state_dir_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The operator's choice is not second-guessed, but still checked."""
    tmp = _dir(tmp_path / "tmp", 0o1777)
    home = _dir(tmp_path / "home", 0o755)
    chosen = _dir(tmp_path / "state", 0o777)
    _env(monkeypatch, tmp, home)
    monkeypatch.setenv("SANDBOX_STATE_DIR", str(chosen))
    state = sandbox_state_directory()
    assert state == chosen / _LEAF
    assert isinstance(_open_slots(state, 4), LocalTaggedSlots)
