"""A malformed ``KB_PUSH_SIGNED_URL_ORIGINS`` stops pushes with images only, never the push route."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from app.connectors.sources.localKB.push.api import PushRuntime
from app.connectors.sources.localKB.push.contract import PushRefusal

from .image_harness import image_body, make_harness, ref


def runtime() -> PushRuntime:
    return PushRuntime(worker=MagicMock(), spool=MagicMock(), admission=MagicMock())


@pytest.mark.parametrize("origins", ["*", "https://*.example.com", "http://s3.example.com", "https://s3.example.com/bucket", "https://user@s3.example.com"])
def test_a_malformed_origin_list_leaves_no_materialiser_and_does_not_raise(monkeypatch, origins) -> None:
    monkeypatch.setenv("KB_PUSH_SIGNED_URL_ORIGINS", origins)
    rt = runtime()
    assert rt.materialiser is None and rt.materialiser is None  # asked twice: still None, no exception
    assert rt.image_admission is not None


def test_a_valid_origin_list_builds_one_materialiser(monkeypatch) -> None:
    monkeypatch.setenv("KB_PUSH_SIGNED_URL_ORIGINS", "https://s3.example.com")
    rt = runtime()
    assert rt.materialiser is not None and rt.materialiser is rt.materialiser


async def test_a_push_with_images_is_503_when_the_materialiser_could_not_be_made(tmp_path) -> None:
    h = await make_harness(tmp_path)
    try:
        h.svc.materialiser = None  # what the runtime hands over for a malformed origin list
        with pytest.raises(PushRefusal) as e:
            await h.push(image_body([ref(1)]))
        assert (e.value.status, e.value.reason) == (503, "IMAGES_UNAVAILABLE")
        assert (await h.push(image_body([]))).status == "created"  # text-only is untouched
    finally:
        await h.store.stop()


# ---- the flag is off: nothing is made ------------------------------------------------------------------

def test_flag_off_leaves_no_timer_lock_file_or_process(tmp_path, monkeypatch) -> None:
    """With the push routes off no request reaches ``runtime_for``, so no spool directory, lock file, timer or process exists."""
    import multiprocessing

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.connectors.sources.localKB.api import kb_router as R
    from app.connectors.sources.localKB.push import api as push_api
    from app.connectors.sources.localKB.push import gate

    spool_root = tmp_path / "spool"
    monkeypatch.setenv("PUSH_SPOOL_DIR", str(spool_root))
    made = []
    monkeypatch.setattr(push_api, "runtime_for", lambda app: made.append(app) or MagicMock())

    async def off(_config) -> bool:
        return False

    monkeypatch.setattr(gate, "is_kb_push_enabled", off)
    app = FastAPI()
    app.include_router(R.kb_push_router)
    app.add_middleware(gate.KbPushFlagMiddleware)
    app.container = MagicMock()
    client = TestClient(app)
    before = {p.pid for p in multiprocessing.active_children()}
    assert client.post("/api/v1/kb/kb1/records", content=b"{}", headers={"content-type": "application/json"}).status_code in (404, 405)
    assert client.put("/api/v1/kb/kb1/schema", json={"properties": []}).status_code in (404, 405)
    assert client.get("/api/v1/kb/kb1/schema").status_code in (404, 405)
    assert not made and not spool_root.exists()
    assert {p.pid for p in multiprocessing.active_children()} <= before, "no process was started"


async def test_flag_on_first_use_creates_the_spool_lock_and_timer_and_a_second_call_adds_nothing(tmp_path, monkeypatch) -> None:
    import asyncio
    from pathlib import Path

    from fastapi import FastAPI

    from app.connectors.sources.localKB.push import api as push_api

    monkeypatch.setenv("PUSH_SPOOL_DIR", str(tmp_path / "spool"))
    app = FastAPI()
    runtime = push_api.runtime_for(app)
    try:
        assert push_api.runtime_for(app) is runtime
        assert len(list(Path(tmp_path / "spool").glob("run-*/.alive"))) == 1
        assert [t for t in asyncio.all_tasks() if t.get_name().endswith("kb-push-spool-sweep")]
        assert runtime.ledger is not None
    finally:
        await push_api.close_runtime(app)


def test_runtime_for_needs_a_running_loop_for_its_timer(tmp_path, monkeypatch) -> None:
    from fastapi import FastAPI

    from app.connectors.sources.localKB.push import api as push_api

    monkeypatch.setenv("PUSH_SPOOL_DIR", str(tmp_path / "spool"))
    with pytest.raises(RuntimeError):
        push_api.runtime_for(FastAPI())


async def test_first_use_removes_nothing_on_the_loop_and_the_timer_cleans_up_at_once(tmp_path, monkeypatch) -> None:
    """A large stale spool: ``runtime_for`` (on the event loop, in the first request) removes nothing; the timer's
    first pass, in a thread and without waiting for its interval, removes the dead runs and keeps this one."""
    import asyncio
    import os
    import time
    from pathlib import Path

    from fastapi import FastAPI

    from app.connectors.sources.localKB.push import api as push_api
    from app.connectors.sources.localKB.push import spool as S

    root = tmp_path / "spool"
    stale = time.time() - 10_000
    for i in range(600):
        d = root / f"{S.RUN_PREFIX}dead{i}"
        d.mkdir(parents=True)
        (d / S.ALIVE).write_text("")
        for j in range(5):
            (d / f"f{j}.in").write_bytes(b"x" * 100)
        os.utime(d, (stale, stale))
    monkeypatch.setenv("PUSH_SPOOL_DIR", str(root))
    gaps: list[float] = []
    stop = asyncio.Event()

    async def ticker() -> None:
        last = time.monotonic()
        while not stop.is_set():
            await asyncio.sleep(0.01)
            now = time.monotonic()
            gaps.append(now - last)
            last = now

    app = FastAPI()
    t = asyncio.create_task(ticker())
    await asyncio.sleep(0.02)
    runtime = push_api.runtime_for(app)
    try:
        assert len(list(root.glob("run-dead*"))) == 600, "runtime_for removed directories on the event loop"
        for _ in range(600):
            await asyncio.sleep(0.05)
            if not list(root.glob("run-dead*")):
                break
        assert not list(root.glob("run-dead*")), "the first pass waited for the interval"
        assert Path(runtime.spool.dir, S.ALIVE).exists()
    finally:
        stop.set()
        await t
        await push_api.close_runtime(app)
    assert max(gaps) < 0.25  # the margin is for a shared host


async def test_close_runtime_stops_the_timer_and_removes_this_run(tmp_path, monkeypatch) -> None:
    from pathlib import Path

    from fastapi import FastAPI

    from app.connectors.sources.localKB.push import api as push_api

    monkeypatch.setenv("PUSH_SPOOL_DIR", str(tmp_path / "spool"))
    app = FastAPI()
    await push_api.close_runtime(app)  # no push ran: nothing to do
    runtime = push_api.runtime_for(app)
    run_dir = runtime.spool.dir
    await push_api.close_runtime(app)
    assert runtime.sweeper.done() and runtime.sweeper.cancelled()
    assert not Path(run_dir).exists() and runtime.spool._lock_fd is None
    assert app.state.kb_push_runtime is None
    await push_api.close_runtime(app)


def test_the_connectors_lifespan_closes_the_push_runtime_before_the_container() -> None:
    from pathlib import Path

    src = (Path(__file__).resolve().parents[6] / "app" / "connectors_main.py").read_text()
    teardown = src[src.index("async def lifespan"):]
    teardown = teardown[teardown.index("\n    yield\n"):]
    assert "await kb_push_api.close_runtime(app)" in teardown
    assert teardown.index("await kb_push_api.close_runtime(app)") < teardown.index("await shutdown_container_resources(app_container)")
