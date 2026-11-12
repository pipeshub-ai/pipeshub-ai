"""The spool: a live run is never swept, a full disk is a 503, the timer survives, and flag-off leaves nothing."""

from __future__ import annotations

import asyncio
import builtins
import errno
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

import pytest

from app.connectors.sources.localKB.push import spool as S
from app.connectors.sources.localKB.push.contract import PushRefusal

BACKEND_ROOT = Path(__file__).resolve().parents[6]
OLD = 10_000.0  # seconds: past every floor and age used below


def age(path: str, seconds: float) -> None:
    t = time.time() - seconds
    os.utime(path, (t, t), follow_symlinks=False)


@pytest.fixture
def root(tmp_path) -> str:
    return str(tmp_path / "spool")


def spawn_owner(root: str) -> subprocess.Popen:
    """A second real process that owns a spool run until its stdin closes."""
    code = (
        "import sys\n"
        "from app.connectors.sources.localKB.push.spool import Spool\n"
        f"s = Spool({root!r})\n"
        "print(s.dir, flush=True)\n"
        "sys.stdin.read()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", code],
        cwd=BACKEND_ROOT,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    assert proc.stdout.readline().strip()
    return proc


def test_sweep_never_removes_a_dir_whose_owner_is_alive(root) -> None:
    owner = spawn_owner(root)
    mine = S.Spool(root)
    try:
        run_dirs = sorted(Path(root).glob("run-*"))
        assert len(run_dirs) == 2
        for d in run_dirs:
            age(str(d), OLD)  # idle for hours: the old rule would have swept both
        assert S.sweep(root, floor_s=0, max_age_s=0) == 0
        assert all(d.is_dir() for d in run_dirs)
        assert Path(mine.write(b"x")).read_bytes() == b"x"
    finally:
        owner.stdin.close()
        owner.wait(10)
        mine.close()


def test_a_dir_whose_owner_died_is_removed_once_older_than_the_floor(root) -> None:
    owner = spawn_owner(root)
    (run_dir,) = Path(root).glob("run-*")
    owner.kill()  # no cleanup runs: the lock is released by the kernel
    owner.wait(10)
    assert S.sweep(root) == 0, (
        "younger than the floor: kept even though the owner is gone"
    )
    age(str(run_dir), S.FLOOR_S + 60)
    assert S.sweep(root) == 1 and not run_dir.exists()


def test_the_lock_is_held_for_the_life_of_the_object_and_a_second_open_sees_it(
    root,
) -> None:
    spool = S.Spool(root)
    try:
        assert (
            S._owner_state(spool.dir) == "alive"
        )  # a same-process second open: flock is per open file description
    finally:
        spool.close()
    assert (
        S._owner_state(spool.dir) == "none"
    )  # closed: the directory is gone with its lock file


def test_a_lock_taken_between_open_and_flock_cannot_delete_a_live_dir(
    root, monkeypatch
) -> None:
    """The interleaving: the owner has opened its lock file but not yet locked it, and a sweeper runs right then."""
    seen = {}
    real_flock = S.fcntl.flock

    def sweeper_runs_first(fd, op) -> object:
        if (
            op == S.fcntl.LOCK_EX
        ):  # the owner's blocking lock; the sweeper's own probe is non-blocking
            seen["removed"] = S.sweep(root)  # the defaults a timer uses
            seen["listing"] = sorted(p.name for p in Path(root).iterdir())
        return real_flock(fd, op)

    monkeypatch.setattr(S.fcntl, "flock", sweeper_runs_first)
    spool = S.Spool(root)
    try:
        assert seen["removed"] == 0
        assert os.path.isdir(spool.dir) and S._owner_state(spool.dir) == "alive"
        assert not any(n.startswith("run-") for n in seen["listing"]), (
            "no run-* name is visible before the lock is held"
        )
    finally:
        spool.close()


def test_a_young_dir_with_a_free_lock_is_kept_by_the_floor(root) -> None:
    """Control for the floor: with it set to 0 the same dir is removed, so the floor is what protects it."""
    run_dir = Path(root) / "run-orphan"
    run_dir.mkdir(parents=True)
    (run_dir / S.ALIVE).write_text("")
    assert S.sweep(root) == 0
    assert S.sweep(root, floor_s=-1) == 1


@pytest.mark.parametrize("where", ["flock", "open"])
def test_a_lock_error_other_than_held_means_skip(root, monkeypatch, caplog, where) -> None:
    """A probe that fails says nothing about the owner, and an mtime does not prove it died: the run is kept, at any
    age, and only a count is logged."""
    run_dir = Path(root) / "run-other"
    run_dir.mkdir(parents=True)
    (run_dir / S.ALIVE).write_text("")
    if where == "open":
        (run_dir / S.ALIVE).chmod(0)
        if os.access(run_dir / S.ALIVE, os.R_OK):
            pytest.skip("not applicable when running as root: file modes do not refuse the open")
    else:

        def denied(fd, op) -> NoReturn:
            raise PermissionError(errno.EACCES, "not ours")

        monkeypatch.setattr(S.fcntl, "flock", denied)
    for seconds in (S.FLOOR_S + 60, S.UNLOCKABLE_AGE_S + 60, 30 * S.UNLOCKABLE_AGE_S):
        age(str(run_dir), seconds)
        with caplog.at_level("WARNING", logger=S.__name__):
            assert S.sweep(root, floor_s=0, max_age_s=0) == 0 and run_dir.exists()
    assert "skipped 1 run" in caplog.text and "run-other" not in caplog.text


def test_without_flock_a_run_goes_by_age_only(root, monkeypatch) -> None:
    old, young = Path(root) / "run-old", Path(root) / "run-young"
    for d in (old, young):
        d.mkdir(parents=True)
        (d / S.ALIVE).write_text("")
    age(str(old), S.UNLOCKABLE_AGE_S + 60)
    age(str(young), S.UNLOCKABLE_AGE_S - 60)
    monkeypatch.setattr(S, "fcntl", None)
    assert S.sweep(root) == 1 and not old.exists() and young.exists()


def test_missing_lock_file_uses_the_age_rule_and_a_symlinked_run_dir_is_never_followed(
    root, tmp_path
) -> None:
    old, young = Path(root) / "run-old", Path(root) / "run-young"
    for d in (old, young):
        d.mkdir(parents=True)
    age(str(old), S.NO_LOCK_AGE_S + 60)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep")
    (Path(root) / "run-link").symlink_to(outside, target_is_directory=True)
    assert S.sweep(root) == 1
    assert not old.exists() and young.exists() and (outside / "keep.txt").exists()


def test_new_and_trash_leftovers_go_by_age(root) -> None:
    new_old, new_young, trash_old = (
        Path(root) / n for n in (".new-a", ".new-b", ".trash-c")
    )
    for d in (new_old, new_young, trash_old):
        d.mkdir(parents=True)
    age(str(new_old), S.LEFTOVER_AGE_S + 60)
    age(str(trash_old), S.LEFTOVER_AGE_S + 60)
    assert S.sweep(root) == 2
    assert new_young.exists() and not new_old.exists() and not trash_old.exists()


def test_a_new_dir_whose_owner_is_still_creating_it_is_kept(root) -> None:
    held = Path(root) / ".new-live"
    held.mkdir(parents=True)
    fd = os.open(held / S.ALIVE, os.O_CREAT | os.O_RDWR)
    S.fcntl.flock(fd, S.fcntl.LOCK_EX)
    try:
        age(str(held), S.LEFTOVER_AGE_S + 60)
        assert S.sweep(root) == 0 and held.exists()
    finally:
        os.close(fd)


def test_a_live_run_sheds_only_old_work_dirs(root) -> None:
    spool = S.Spool(root)
    try:
        old_dir, young_dir = Path(spool.dir) / "img-old", Path(spool.dir) / "img-young"
        old_file = Path(spool.dir) / "old.in"
        for d in (old_dir, young_dir):
            d.mkdir()
            (d / "x.img").write_bytes(b"x")
        old_file.write_bytes(b"x")
        for p in (old_dir, old_file):
            age(str(p), S.LIVE_TRIM_AGE_S + 60)
        assert spool.trim() == 2
        assert not old_dir.exists() and not old_file.exists() and young_dir.exists()
        assert (Path(spool.dir) / S.ALIVE).exists()
        assert not list(Path(root).glob(".trash-*")), "renamed away, then removed"
    finally:
        spool.close()


# ---- a full disk --------------------------------------------------------------------------------


def full_disk(monkeypatch, *, ending: str, code: int = errno.ENOSPC) -> None:
    real_open = builtins.open

    def failing(path, *a, **kw) -> object:
        if str(path).endswith(ending):
            raise OSError(code, "No space left on device")
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", failing)


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EDQUOT])
def test_a_full_disk_at_the_body_write_is_503_spool_full(
    root, monkeypatch, code
) -> None:
    spool = S.Spool(root)
    full_disk(monkeypatch, ending=".in", code=code)
    with pytest.raises(PushRefusal) as e:
        spool.write(b"body")
    assert (e.value.status, e.value.reason, e.value.extra["retryAfter"]) == (
        503,
        "SPOOL_FULL",
        5,
    )
    assert not list(Path(spool.dir).glob("*.in"))
    spool.close()


def test_a_full_disk_when_the_run_dir_must_be_recreated_is_503_spool_full(
    root, monkeypatch
) -> None:
    spool = S.Spool(root)
    import shutil

    shutil.rmtree(spool.dir)
    real = os.makedirs

    def failing(path, *a, **kw) -> object:
        if str(path) == spool.dir:
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "makedirs", failing)
    with pytest.raises(PushRefusal) as e:
        spool.write(b"body")
    assert e.value.reason == "SPOOL_FULL"


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EDQUOT])
def test_a_full_disk_when_the_spool_root_is_made_is_503_spool_full(root, monkeypatch, code) -> None:
    real = os.makedirs

    def failing(path, *a, **kw) -> object:
        if str(path) == root:
            raise OSError(code, os.strerror(code))
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "makedirs", failing)
    with pytest.raises(PushRefusal) as e:
        S.Spool(root)
    assert (e.value.status, e.value.reason) == (503, "SPOOL_FULL")


def test_a_full_disk_at_startup_degrades_to_no_lock_and_does_not_fail_every_push(
    root, monkeypatch
) -> None:
    real = os.makedirs

    def failing(path, *a, **kw) -> object:
        if os.path.basename(str(path)).startswith(S.NEW_PREFIX):
            raise OSError(errno.ENOSPC, "No space left on device")
        return real(path, *a, **kw)

    monkeypatch.setattr(os, "makedirs", failing)
    spool = S.Spool(root)
    try:
        assert spool._lock_fd is None and os.path.isdir(spool.dir)
        assert Path(spool.write(b"x")).read_bytes() == b"x"
        assert S.sweep(root) == 0, "a young run with no lock file is kept"
    finally:
        spool.close()


# ---- the report ----------------------------------------------------------------------------------

MOUNTINFO = "\n".join(
    [
        "22 1 8:1 / / rw,relatime shared:1 - ext4 /dev/sda1 rw",
        "30 22 0:25 / /tmp rw,nosuid shared:2 - tmpfs tmpfs rw,size=65536k",
        "31 30 0:26 / /tmp/pipeshub\\040spool rw shared:3 - overlay overlay rw",
        "32 22 0:27 / /var/lib/data rw shared:4 - xfs /dev/sdb rw",
    ]
)


@pytest.mark.parametrize(
    ("path", "fstype", "mount"),
    [
        ("/tmp/pipeshub-push-spool", "tmpfs", "/tmp"),
        ("/var/lib/data/spool", "xfs", "/var/lib/data"),
        ("/home/u/spool", "ext4", "/"),
        ("/tmp/pipeshub spool/run", "overlay", "/tmp/pipeshub spool"),
        ("/tmpfoo/x", "ext4", "/"),
    ],
)
def test_report_from_mountinfo(tmp_path, path, fstype, mount) -> None:
    info = tmp_path / "mountinfo"
    info.write_text(MOUNTINFO)
    real = os.path.realpath
    os.path.realpath = lambda p: p  # the stub's paths do not exist here
    try:
        assert S.spool_report(path, str(info)) == {"fstype": fstype, "mount": mount}
    finally:
        os.path.realpath = real


def test_an_unreadable_mountinfo_is_unknown_not_a_crash(tmp_path) -> None:
    assert S.spool_report("/tmp", str(tmp_path / "missing"))["fstype"] == "unknown"


# ---- the timer -----------------------------------------------------------------------------------


async def test_sweep_does_not_stall_the_loop(root) -> None:
    for i in range(600):
        d = Path(root) / f"{S.RUN_PREFIX}dead{i}"
        d.mkdir(parents=True)
        (d / S.ALIVE).write_text("")
        for j in range(5):
            (d / f"f{j}.in").write_bytes(b"x" * 100)
        age(str(d), OLD)
    spool = S.Spool(root)
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    t = asyncio.create_task(ticker())
    loop_task = asyncio.create_task(S.sweep_loop(spool, 0.01))
    try:
        for _ in range(600):
            await asyncio.sleep(0.05)
            if not list(Path(root).glob("run-dead*")):
                break
    finally:
        stop.set()
        loop_task.cancel()
        await t
        spool.close()
    assert not list(Path(root).glob("run-dead*"))
    print(f"sweep: worst loop gap {max(gaps) * 1000:.0f} ms")
    assert (
        max(gaps) < 0.25
    )  # an inline rmtree of 600 dirs stalls for far longer; the margin is for a shared host


async def test_timer_survives_a_failing_sweep_and_jitters(root, monkeypatch) -> None:
    calls = []
    sleeps = []

    def flaky(*a, **kw) -> int:
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("scan failed")
        return 0

    monkeypatch.setattr(S, "sweep", flaky)
    spool = S.Spool(root)

    async def fake_sleep(s: float) -> None:
        sleeps.append(s)
        if len(sleeps) > 3:
            raise asyncio.CancelledError

    try:
        with pytest.raises(asyncio.CancelledError):
            await S.sweep_loop(spool, 300.0, sleep=fake_sleep)
    finally:
        spool.close()
    assert len(calls) == 4, "a pass before each of the 3 sleeps and one after; the first failure did not end the timer"
    assert all(270.0 <= s <= 330.0 for s in sleeps) and len(set(sleeps)) > 1


async def test_the_first_pass_runs_before_the_first_sleep_and_never_sees_this_run_as_dead(root) -> None:
    """The run is locked in ``Spool()``, before the timer exists, so even a pass with no floor and no age keeps it."""
    dead = Path(root) / f"{S.RUN_PREFIX}dead"
    dead.mkdir(parents=True)
    (dead / S.ALIVE).write_text("")
    age(str(dead), OLD)
    spool = S.Spool(root)

    async def stop(_s: float) -> None:
        raise asyncio.CancelledError

    try:
        with pytest.raises(asyncio.CancelledError):
            await S.sweep_loop(spool, 300.0, sleep=stop, floor_s=-1, max_age_s=-1)
        assert not dead.exists(), "the first pass did not wait for the interval"
        assert Path(spool.dir, S.ALIVE).exists()
    finally:
        spool.close()
