"""Cell claims and stats files: a planted link or foreign directory cannot redirect a write, a ``/health``
read never takes the claim, and a replacement or corrupt file cannot mislead ``/health``."""

from __future__ import annotations

import logging
import multiprocessing as mp
import os
import re
import stat
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from app.capacity import cells
from app.capacity.cells import claim_cell, read_cells, write_cell

pytestmark = pytest.mark.skipif(not cells._OFD, reason="cell claims need Linux open-file-description locks")


@pytest.fixture(autouse=True)
def _fresh_warnings() -> None:
    cells._warned.clear()


def _write(claim, **kw) -> None:
    kw.setdefault("loop_failure", None)
    kw.setdefault("tick_s", 5.0)
    write_cell(claim, {"x": 1}, **kw)


def test_write_cell_does_not_follow_a_planted_symlink(tmp_path: Path) -> None:
    claim = claim_cell(tmp_path / "cells", 1)
    assert claim is not None
    victim = tmp_path / "victim.txt"
    victim.write_text("precious")
    # Every name the old code could have picked, and the target itself.
    for name in (f".{claim.index}.{os.getpid()}.tmp", "0.json"):
        (claim.directory / name).unlink(missing_ok=True)
        (claim.directory / name).symlink_to(victim)
    _write(claim)
    assert victim.read_text() == "precious"
    assert not (claim.directory / "0.json").is_symlink()  # the link was replaced, not followed


def test_the_temp_file_name_is_random_and_opened_exclusively(tmp_path: Path) -> None:
    claim = claim_cell(tmp_path / "cells", 1)
    seen: list[str] = []
    real_open = os.open

    def spy(path: object, flags: int, *a: object, **kw: object) -> int:
        if str(path).endswith(".tmp"):
            seen.append(f"{Path(path).name}:{flags & (os.O_EXCL | os.O_NOFOLLOW | os.O_CREAT) == (os.O_EXCL | os.O_NOFOLLOW | os.O_CREAT)}")
        return real_open(path, flags, *a, **kw)

    with patch("app.capacity.cells.os.open", spy):
        _write(claim)
        _write(claim)
    assert len(seen) == 2 and seen[0] != seen[1] and all(s.endswith(":True") for s in seen)
    names = [s.rsplit(":", 1)[0] for s in seen]
    assert all(re.fullmatch(r"\.0\.[0-9a-f]{16}\.tmp", n) for n in names), names
    assert stat.S_IMODE((claim.directory / "0.json").stat().st_mode) == 0o600
    assert list(claim.directory.glob(".*.tmp")) == []


def test_a_planted_lock_file_link_is_not_followed(tmp_path: Path) -> None:
    directory = tmp_path / "cells"
    directory.mkdir(mode=0o700)
    (tmp_path / "victim").write_text("x")
    (directory / "0.lock").symlink_to(tmp_path / "victim")
    with pytest.raises(OSError):
        claim_cell(directory, 1)


def test_the_directory_is_created_private(tmp_path: Path) -> None:
    claim = claim_cell(tmp_path / "cells", 1)
    assert claim is not None and stat.S_IMODE(claim.directory.stat().st_mode) == 0o700


@pytest.mark.parametrize("mode", [0o777, 0o770, 0o702])
def test_a_directory_others_can_write_is_refused_with_one_warning(tmp_path: Path, caplog, mode: int) -> None:
    directory = tmp_path / "cells"
    directory.mkdir()
    os.chmod(directory, mode)
    with caplog.at_level(logging.WARNING, logger="app.capacity.cells"):
        assert claim_cell(directory, 1) is None
        assert claim_cell(directory, 1) is None
    assert [r.levelno for r in caplog.records] == [logging.WARNING]
    assert "writable by others" in caplog.records[0].getMessage()
    assert list(directory.iterdir()) == []


def test_a_directory_of_another_user_is_refused(tmp_path: Path, caplog) -> None:
    directory = tmp_path / "cells"
    directory.mkdir(mode=0o700)
    with patch("app.capacity.cells.os.getuid", return_value=os.getuid() + 1), caplog.at_level(logging.WARNING):
        assert claim_cell(directory, 1) is None
    assert "another user" in caplog.text


def test_a_symlinked_directory_is_refused(tmp_path: Path, caplog) -> None:
    real = tmp_path / "real"
    real.mkdir(mode=0o700)
    link = tmp_path / "cells"
    link.symlink_to(real)
    with caplog.at_level(logging.WARNING):
        assert claim_cell(link, 1) is None
    assert "not a directory" in caplog.text


def test_a_health_read_ignores_an_unsafe_directory(tmp_path: Path) -> None:
    directory = tmp_path / "cells"
    directory.mkdir()
    os.chmod(directory, 0o777)
    (directory / "0.json").write_text("{}")
    merged = read_cells(directory, 1)
    assert merged.healthy and merged.cells == []


def test_a_health_read_takes_no_lock(tmp_path: Path) -> None:
    held = claim_cell(tmp_path, 2)
    assert held is not None
    calls: list[object] = []
    real = cells.fcntl.fcntl
    with (
        patch("app.capacity.cells.fcntl.fcntl", lambda fd, cmd, *a: (calls.append(cmd), real(fd, cmd, *a))[1]),
        patch("app.capacity.cells.fcntl.flock", side_effect=AssertionError("a read took a flock")),
    ):
        read_cells(tmp_path, 2)
    assert calls and set(calls) == {cells._F_OFD_GETLK}, f"a read used {set(calls)}"


def test_a_health_read_does_not_leave_the_second_worker_without_a_cell(tmp_path: Path) -> None:
    """The reader's probe used to hold the free slot's lock for a moment; a worker claiming then got ``None``."""
    held = claim_cell(tmp_path, 2)
    assert held is not None and held.index == 0
    original = cells._slot_held
    outcome: list[object] = []

    def probe_and_claim(directory: Path, index: int) -> bool:
        result = original(directory, index)
        if index == 1:  # a worker starts while the reader is on the free slot
            outcome.append(claim_cell(tmp_path, 2))
        return result

    with patch("app.capacity.cells._slot_held", probe_and_claim):
        read_cells(tmp_path, 2)
    assert outcome and outcome[0] is not None and outcome[0].index == 1


def _reader(directory: str, stop: float, ready: object) -> None:
    while time.time() < stop:
        read_cells(Path(directory), 2)
        ready.set()  # type: ignore[attr-defined]


CLAIM_ATTEMPTS_UNDER_LOAD = 1500


def test_no_claim_is_missed_while_health_is_read_under_load(tmp_path: Path) -> None:
    """A fixed number of claim attempts, made while the readers run: the result does not depend on how fast the host is.
    The readers stop on a generous deadline, which only bounds a hang."""
    held = claim_cell(tmp_path, 2)
    assert held is not None
    stop = time.time() + 120.0
    ctx = mp.get_context("fork")
    ready = [ctx.Event() for _ in range(2)]
    readers = [ctx.Process(target=_reader, args=(str(tmp_path), stop, ready[i])) for i in range(2)]
    for r in readers:
        r.start()
    misses = 0
    try:
        assert all(e.wait(30) for e in ready), "a reader never completed a read, so the claims would not overlap one"
        for _ in range(CLAIM_ATTEMPTS_UNDER_LOAD):
            c = claim_cell(tmp_path, 2)
            if c is None:
                misses += 1
            else:
                c.release()
            assert time.time() < stop, "the claims did not finish: a hang, not a miss"
        assert all(r.is_alive() for r in readers), "a reader stopped before the claims ended"
    finally:
        for r in readers:
            r.terminate()
            r.join()
    assert misses == 0, f"{misses} of {CLAIM_ATTEMPTS_UNDER_LOAD} claims found no free cell while slot 1 was free"


def test_a_replacement_cell_is_not_reported_stale_from_its_predecessor_file(tmp_path: Path) -> None:
    first = claim_cell(tmp_path, 1)
    _write(first, now_ms=0)
    (tmp_path / ".0.deadbeef.tmp").write_text("left behind")
    first.release()  # the worker died
    second = claim_cell(tmp_path, 1)
    assert second is not None and second.index == 0
    assert not (tmp_path / "0.json").exists() and not (tmp_path / ".0.deadbeef.tmp").exists()
    assert read_cells(tmp_path, 1, now_ms=60_000).healthy


@pytest.mark.parametrize("body", ["[]", "null", "42", '"text"', "true"])
def test_valid_json_that_is_not_an_object_in_a_held_cells_file_is_unreadable(tmp_path: Path, body: str) -> None:
    claim_cell(tmp_path, 1)
    (tmp_path / "0.json").write_text(body)
    merged = read_cells(tmp_path, 1)
    assert not merged.healthy
    assert [(c["cell"], c["status"], c["reason"]) for c in merged.cells] == [(0, "unhealthy", "unreadable")]


def test_a_health_read_never_deletes_a_newly_claimed_cells_file(tmp_path: Path) -> None:
    """A reader's view of the lock can be one claim behind: it saw the slot free, then the cell claimed and wrote."""
    claim = claim_cell(tmp_path, 1)
    assert claim is not None
    write_cell(claim, {}, loop_failure=None, tick_s=5.0)
    with patch("app.capacity.cells._slot_held", return_value=False):
        merged = read_cells(tmp_path, 1)
    assert merged.cells == []
    assert (tmp_path / "0.json").exists(), "a /health read removed the file of a cell that has just claimed"


def test_a_health_read_hides_the_files_of_a_held_cell_in_an_unsafe_directory(tmp_path: Path) -> None:
    """The cell holds its lock and has written, so only the directory check keeps its file out of the answer."""
    directory = tmp_path / "cells"
    claim = claim_cell(directory, 1)
    assert claim is not None
    write_cell(claim, {}, loop_failure=None, tick_s=5.0)
    assert [c["cell"] for c in read_cells(directory, 1).cells] == [0], "the control: readable while the directory is private"
    os.chmod(directory, 0o777)
    merged = read_cells(directory, 1)
    assert merged.healthy and merged.cells == []


def test_oversized_and_garbage_files_stay_unhealthy(tmp_path: Path) -> None:
    claim_cell(tmp_path, 1)
    (tmp_path / "0.json").write_bytes(b"x" * ((1 << 20) + 1))
    assert read_cells(tmp_path, 1).cells[0]["reason"] == "unreadable"
    for body in ("{", '{"ts_ms": "soon"}', '{"ts_ms": 1, "tick_s": "fast"}'):
        (tmp_path / "0.json").write_text(body)
        merged = read_cells(tmp_path, 1)
        assert not merged.healthy and merged.cells[0]["reason"] == "unreadable", body


@pytest.mark.parametrize("count", [0, -3])
def test_nonpositive_cell_counts_mean_one_cell(tmp_path: Path, count: int) -> None:
    claim = claim_cell(tmp_path, count)
    assert claim is not None and claim.index == 0
    _write(claim)
    assert read_cells(tmp_path, count).healthy


def test_a_link_to_a_device_or_a_fifo_is_unreadable_and_never_blocks(tmp_path: Path) -> None:
    """A link to ``/dev/zero`` reports size 0 and never ends; a FIFO blocks a reader. ``/health`` must not follow either."""
    claim = claim_cell(tmp_path / "cells", 2)
    other = claim_cell(tmp_path / "cells", 2)
    assert claim is not None and other is not None
    (claim.directory / "0.json").symlink_to("/dev/zero")
    fifo = claim.directory / "1.json"
    os.mkfifo(fifo)
    started = time.monotonic()
    merged = read_cells(claim.directory, 2)
    # wall-clock-ok: a hang guard: reading a FIFO or /dev/zero cell would block for ever, skipping it takes ms
    assert time.monotonic() - started < 2
    assert not merged.healthy
    assert [c["reason"] for c in merged.cells] == ["unreadable", "unreadable"]


def test_a_regular_file_is_read_through_the_descriptor_with_a_bound(tmp_path: Path) -> None:
    """The size is checked on the open file, so a file that grows after ``stat`` cannot pass the cap."""
    claim = claim_cell(tmp_path / "cells", 1)
    assert claim is not None
    (claim.directory / "0.json").write_text('{"ts_ms": 1, "pad": "' + "x" * (cells._MAX_FILE_BYTES + 10) + '"}')
    merged = read_cells(claim.directory, 1)
    assert not merged.healthy and merged.cells[0]["reason"] == "unreadable"
