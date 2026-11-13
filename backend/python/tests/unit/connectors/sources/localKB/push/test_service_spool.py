"""A push does its spool file work in threads: a slow disk must not stall the event loop every connector shares."""

from __future__ import annotations

import asyncio
import builtins
import contextlib
import errno
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.sources.localKB.push import spool as S
from app.connectors.sources.localKB.push.contract import PushRefusal

from .image_harness import PNG, image_body, make_harness, ref

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


@pytest.fixture
async def h(tmp_path) -> AsyncIterator[object]:
    harness = await make_harness(tmp_path)
    harness.docs.put(1, PNG)
    yield harness
    await harness.store.stop()
    harness.svc.spool.close()


def spool_files(h) -> list[str]:
    return sorted(p.name for p in Path(h.svc.spool.dir).iterdir() if p.name != S.ALIVE)


async def test_a_push_does_no_spool_file_work_on_the_loop_thread(h, monkeypatch) -> None:
    loop_thread = threading.get_ident()
    calls: list[tuple[str, bool]] = []

    def recorded(name: str, real: Callable[..., object]) -> Callable[..., object]:
        def call(*a: object, **kw: object) -> object:
            calls.append((name, threading.get_ident() == loop_thread))
            return real(*a, **kw)

        return call

    monkeypatch.setattr(h.svc.spool, "write", recorded("write", h.svc.spool.write))
    monkeypatch.setattr(h.svc.spool, "remove", recorded("remove", h.svc.spool.remove))
    monkeypatch.setattr(h.mat, "make_dir", recorded("make_dir", h.mat.make_dir))
    monkeypatch.setattr(h.mat, "remove_dir", recorded("remove_dir", h.mat.remove_dir))
    monkeypatch.setattr(os.path, "getsize", recorded("getsize", os.path.getsize))
    await h.push(image_body([ref(1)]))
    assert {name for name, _ in calls} == {"write", "remove", "make_dir", "remove_dir", "getsize"}
    assert [name for name, on_loop in calls if on_loop] == []
    assert spool_files(h) == []


async def test_concurrent_pushes_and_sweeps_leave_nothing_and_never_fail(h, tmp_path) -> None:
    dead = tmp_path / f"{S.RUN_PREFIX}dead"
    dead.mkdir()
    (dead / S.ALIVE).write_text("")
    async def kb_without_records(key: str, collection: str, *a: object, **kw: object) -> dict | None:
        return None if collection == CollectionNames.RECORDS.value else {"id": key}

    h.graph.get_document = kb_without_records  # the fake graph holds one record; here each push is a new one
    sweeper = asyncio.create_task(S.sweep_loop(h.svc.spool, 0.0, floor_s=-1))
    try:
        results = await asyncio.gather(*(h.push(image_body([], text=f"t{i}", key=f"k{i}")) for i in range(20)))
    finally:
        sweeper.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweeper
    assert len(results) == 20 and not dead.exists()
    assert spool_files(h) == []


@pytest.mark.parametrize("code", [errno.ENOSPC, errno.EDQUOT])
async def test_a_full_disk_at_the_body_write_is_still_503_spool_full(h, monkeypatch, code) -> None:
    real_open = builtins.open

    def full(path, *a, **kw) -> object:
        if str(path).endswith(".in"):
            raise OSError(code, "No space left on device")
        return real_open(path, *a, **kw)

    monkeypatch.setattr(builtins, "open", full)
    with pytest.raises(PushRefusal) as e:
        await h.push(image_body([]))
    assert (e.value.status, e.value.reason, e.value.extra["retryAfter"]) == (503, "SPOOL_FULL", 5)
    assert spool_files(h) == []


async def test_a_push_cancelled_during_its_body_write_leaves_no_file(h, monkeypatch) -> None:
    started, release, written = threading.Event(), threading.Event(), threading.Event()
    real_write = h.svc.spool.write

    def slow_write(data: bytes) -> str:
        started.set()
        release.wait(5)
        try:
            return real_write(data)
        finally:
            written.set()

    monkeypatch.setattr(h.svc.spool, "write", slow_write)
    push = asyncio.create_task(h.push(image_body([])))
    assert await asyncio.to_thread(started.wait, 5)
    push.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await push
    release.set()
    assert await asyncio.to_thread(written.wait, 5)  # the write lands in its thread after the cancel
    for _ in range(250):  # then its file is removed in another
        if not spool_files(h):
            break
        await asyncio.sleep(0.02)
    assert spool_files(h) == []


async def test_a_push_cancelled_while_its_cleanup_waits_for_a_thread_still_removes_its_files(h) -> None:
    """A cancel must not drop a cleanup that is still queued for a thread (it would wait for the trim)."""
    loop = asyncio.get_running_loop()
    loop.set_default_executor(ThreadPoolExecutor(max_workers=1))  # this test's loop only
    gate, failed = threading.Event(), asyncio.Event()

    async def validate_then_fail(*a: object, **kw: object) -> NoReturn:
        loop.run_in_executor(None, gate.wait, 5)  # the one thread is busy, so the cleanup queues behind it
        failed.set()
        raise PushRefusal(422, "BLOCKS_INVALID", "invalid")

    h.svc.worker.validate = validate_then_fail
    push = asyncio.create_task(h.push(image_body([])))
    await failed.wait()
    for _ in range(5):  # let the push reach its cleanup
        await asyncio.sleep(0)
    assert spool_files(h), "the body was written"
    push.cancel()
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await push
    assert spool_files(h) == []
