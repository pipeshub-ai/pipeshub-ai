"""Edge paths of the alert cooldown, the leader lock and the window reader."""

from __future__ import annotations

import errno
import json
import os
from typing import TYPE_CHECKING

import pytest

from app.services.sandbox_alerts import leader as leader_mod
from app.services.sandbox_alerts.cooldown import (
    STATE_CLAIMED,
    STATE_RESOLVED,
    AlertCooldown,
)
from app.services.sandbox_alerts.leader import AlertLeader
from app.services.sandbox_alerts.window import read_window
from tests.unit.services.sandbox_alerts.fakes import FakeConfig

if TYPE_CHECKING:
    from pathlib import Path

T0 = 1_800_000_000.0
KEY = "/services/sandbox/alerts/install/warning"


class Clock:
    def __init__(self) -> None:
        self.now = T0

    def __call__(self) -> float:
        return self.now


class TestCooldownRestore:
    def _cooldown(self) -> tuple[AlertCooldown, FakeConfig, Clock]:
        clock = Clock()
        store = FakeConfig(clock)
        return AlertCooldown(store, "install", 86400, clock=clock, host="h"), store, clock  # type: ignore[arg-type]

    async def test_marking_a_claim_without_a_claim_time_keeps_it_for_a_full_cooldown(self) -> None:
        cooldown, store, _ = self._cooldown()
        assert await cooldown.mark_resolved("warning", {"alertId": "a", "state": STATE_CLAIMED})
        assert store.values[KEY]["state"] == STATE_RESOLVED
        assert store.ttls[KEY] == 86400

    async def test_marking_a_claim_with_a_bad_cooldown_keeps_it_for_a_full_cooldown(self) -> None:
        cooldown, store, _ = self._cooldown()
        value = {"alertId": "a", "state": STATE_CLAIMED, "claimedAt": int(T0 * 1000), "cooldownS": "1d"}
        assert await cooldown.mark_resolved("warning", value)
        assert store.ttls[KEY] == 86400

    async def test_marking_keeps_the_original_expiry(self) -> None:
        cooldown, store, clock = self._cooldown()
        claimed = {"alertId": "a", "state": STATE_CLAIMED, "claimedAt": int(T0 * 1000), "cooldownS": 86400}
        clock.now = T0 + 3600.5
        assert await cooldown.mark_resolved("warning", claimed)
        assert store.ttls[KEY] == 86400 - 3600

    async def test_a_claim_past_its_cooldown_is_deleted_not_marked(self) -> None:
        cooldown, store, clock = self._cooldown()
        claimed = {"alertId": "a", "state": STATE_CLAIMED, "claimedAt": int(T0 * 1000), "cooldownS": 86400}
        store.values[KEY] = claimed
        clock.now = T0 + 86400
        assert await cooldown.mark_resolved("warning", claimed) is True
        assert KEY not in store.values

    async def test_a_release_whose_resolved_value_has_expired_deletes_the_claim(self) -> None:
        cooldown, store, clock = self._cooldown()
        expired = {"alertId": "old", "state": STATE_RESOLVED, "claimedAt": int((T0 - 90000) * 1000), "cooldownS": 86400}
        store.values[KEY] = {"alertId": "new", "state": STATE_CLAIMED}
        assert await cooldown.release_if_ours("warning", "new", expired)
        assert KEY not in store.values

    async def test_a_release_of_someone_elses_claim_leaves_it(self) -> None:
        cooldown, store, _ = self._cooldown()
        store.values[KEY] = {"alertId": "theirs", "state": STATE_CLAIMED}
        assert await cooldown.release_if_ours("warning", "mine") is False
        assert store.values[KEY]["alertId"] == "theirs"

    async def test_a_value_that_is_not_a_dict_reads_as_nothing(self) -> None:
        cooldown, store, _ = self._cooldown()
        store.values[KEY] = "garbage"
        assert await cooldown.read("warning") is None
        assert await cooldown.release_if_ours("warning", "x") is False


class TestLeader:
    def test_without_file_locks_the_leader_raises(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(leader_mod, "fcntl", None)
        with pytest.raises(OSError, match="no POSIX file locks"):
            AlertLeader(tmp_path / "state").try_acquire()

    def test_an_unexpected_lock_error_closes_the_file_and_raises(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        closed: list[int] = []
        real_close = os.close

        def broken_flock(fd: int, op: int) -> None:
            raise OSError(errno.ENOLCK, "no locks")

        def tracking_close(fd: int) -> None:
            closed.append(fd)
            real_close(fd)

        opened: list[int] = []
        real_open = leader_mod.open_private_file

        def tracking_open(path: Path) -> int:
            opened.append(real_open(path))
            return opened[-1]

        monkeypatch.setattr(leader_mod.fcntl, "flock", broken_flock)
        monkeypatch.setattr(leader_mod.os, "close", tracking_close)
        monkeypatch.setattr(leader_mod, "open_private_file", tracking_open)
        leader = AlertLeader(tmp_path / "state")
        with pytest.raises(OSError, match="no locks"):
            leader.try_acquire()
        assert len(opened) == 1 and closed[-1] == opened[0] and not leader.held

    def test_release_without_holding_is_a_no_op_and_held_is_idempotent(self, tmp_path: Path) -> None:
        leader = AlertLeader(tmp_path / "state")
        leader.release()
        assert leader.try_acquire() and leader.try_acquire()
        assert leader.held
        leader.release()
        leader.release()
        assert not leader.held


class TestWindowFiles:
    def _dir(self, tmp_path: Path) -> Path:
        load = tmp_path / "load"
        load.mkdir(mode=0o700)
        return load

    def test_a_missing_directory_reads_as_empty(self, tmp_path: Path) -> None:
        totals = read_window(tmp_path / "nowhere", T0, 1800)
        assert totals.files == 0 and totals.runs == 0 and totals.stale == ()

    def test_files_that_are_not_worker_json_are_ignored(self, tmp_path: Path) -> None:
        load = self._dir(tmp_path)
        (load / "leader").write_text("")
        (load / "w-1-1.txt").write_text(json.dumps({"v": 1}))
        (load / "notes.json").write_text(json.dumps({"v": 1}))
        totals = read_window(load, T0, 1800)
        assert totals.files == 0 and totals.stale == ()

    def test_a_tmp_entry_that_vanishes_while_listed_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        load = self._dir(tmp_path)
        (load / "w-1-1.json.tmp").write_text("")
        real_scandir = os.scandir

        class Vanished:
            def __init__(self, entry: os.DirEntry) -> None:
                self.name, self.path = entry.name, entry.path

            def stat(self, follow_symlinks: bool = True) -> os.stat_result:
                raise FileNotFoundError(errno.ENOENT, "gone")

        def scandir(path: Path) -> list[Vanished]:
            with real_scandir(path) as it:
                return [Vanished(e) for e in it]

        monkeypatch.setattr("app.services.sandbox_alerts.window.os.scandir", scandir)
        totals = read_window(load, T0, 1800)
        assert totals.stale == () and totals.files == 0
