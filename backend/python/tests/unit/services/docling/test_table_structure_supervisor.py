"""The supervisor of the table-structure child: a real child process with a stand-in model."""
from __future__ import annotations

import asyncio
import os
import sys
import time

import cv2
import numpy as np

from app.services.docling import table_structure as ts

FACTORY = "tests.unit.services.docling.fake_table_model:build"
PNG = cv2.imencode(".png", np.zeros((8, 6, 3), np.uint8))[1].tobytes()


def spec(tid: str = "a", cap: int = 50, tokens: int = 2) -> dict:
    return {
        "id": tid, "scale_factor": 1.0, "page_w": 100.0, "page_h": 100.0, "table_bbox": [0, 0, 8, 6], "cap": cap,
        "tokens": [{"id": k, "bbox": [0, 0, 1, 1], "text": "x"} for k in range(tokens)],
    }


def supervisor(**kw) -> ts.TableSupervisor:
    kw.setdefault("memory_available", lambda: 10**12)
    kw.setdefault("weights_dir", lambda _mode: "unused")
    kw.setdefault("factory", FACTORY)
    kw.setdefault("kill_grace_s", 2.0)
    kw.setdefault("start_timeout_s", 60.0)
    return ts.TableSupervisor(**kw)


def alive(pid: int | None) -> bool:
    if pid is None:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


async def test_a_table_is_answered_and_the_model_block_is_built() -> None:
    s = supervisor()
    try:
        r = await s.run_table(spec(), PNG)
        assert r["status"] == "ok" and len(r["cells"]) == 2 and r["shape"] == [8, 6, 3]
        info = s.model_info
        assert info is not None and info.docling_ibm_models == "0.0-fake" and len(info.config_sha256) == 64
    finally:
        await s.shutdown()


async def test_the_timeout_kills_only_that_table_and_the_child_is_replaced() -> None:
    s = supervisor(model_timeout_ceiling=1.0)
    try:
        first = await s.run_table(spec("a"), PNG)
        pid1 = s.child_pid
        slow = await s.run_table(spec("sleep-30"), PNG)
        assert slow == {"status": "error", "err": "timeout"}
        assert s.stats["kills"] == 1 and s.stats["timeouts"] == 1
        assert not alive(pid1)
        after = await s.run_table(spec("b"), PNG)
        assert first["status"] == "ok" and after["status"] == "ok" and s.stats["respawns"] == 1
        assert after["pid"] != first["pid"]
    finally:
        await s.shutdown()


async def test_a_child_that_ignores_terminate_is_killed_after_the_grace_period() -> None:
    s = supervisor(model_timeout_ceiling=1.0, kill_grace_s=0.5)
    try:
        await s.run_table(spec("a"), PNG)
        pid = s.child_pid
        import signal

        # the stand-in sleeps in time.sleep; make it ignore SIGTERM so only kill() works
        os.kill(pid, signal.SIGSTOP)  # a stopped process cannot handle SIGTERM: terminate() leaves it, kill() ends it
        t0 = time.monotonic()
        r = await s.run_table(spec("b"), PNG)
        assert r == {"status": "error", "err": "timeout"}
        assert time.monotonic() - t0 < 10 and not alive(pid)
    finally:
        await s.shutdown()


async def test_a_model_exception_fails_one_table_and_the_child_lives_on() -> None:
    s = supervisor()
    try:
        boom = await s.run_table(spec("boom"), PNG)
        ok = await s.run_table(spec("a"), PNG)
        assert boom == {"status": "error", "err": "RuntimeError"} and ok["status"] == "ok"
        assert s.stats["kills"] == 0
    finally:
        await s.shutdown()


async def test_a_child_that_dies_is_reported_and_replaced() -> None:
    s = supervisor()
    try:
        r = await s.run_table(spec("die"), PNG)
        assert r == {"status": "error", "err": "child_exit"}
        again = await s.run_table(spec("a"), PNG)
        assert again["status"] == "ok" and s.stats["respawns"] == 1
    finally:
        await s.shutdown()


async def test_a_library_that_prints_on_stdout_does_not_corrupt_the_answers() -> None:
    s = supervisor()
    try:
        assert (await s.run_table(spec("print"), PNG))["status"] == "ok"
        assert (await s.run_table(spec("after"), PNG))["status"] == "ok"
    finally:
        await s.shutdown()


async def test_the_child_runs_with_one_thread_unless_told_otherwise() -> None:
    assert ts.DEFAULT_THREADS == 1
    s = supervisor()
    try:
        assert (await s.run_table(spec("a"), PNG))["threads"] == 1
    finally:
        await s.shutdown()


async def test_a_cap_hit_is_counted() -> None:
    s = supervisor()
    try:
        r = await s.run_table(spec("cap"), PNG)
        assert r["status"] == "cap_hit" and s.stats["cap_hits"] == 1
    finally:
        await s.shutdown()


async def test_the_child_exits_when_idle_and_the_next_table_starts_a_new_one() -> None:
    s = supervisor(idle_seconds=1.0)
    try:
        await s.run_table(spec("a"), PNG)
        pid = s.child_pid
        for _ in range(60):
            if not alive(pid):
                break
            await asyncio.sleep(0.1)
        assert not alive(pid)
        r = await s.run_table(spec("b"), PNG)
        assert r["status"] == "ok" and r["pid"] != pid
    finally:
        await s.shutdown()


async def test_shutdown_leaves_no_child() -> None:
    s = supervisor()
    await s.run_table(spec("a"), PNG)
    pid = s.child_pid
    await s.shutdown()
    for _ in range(30):
        if not alive(pid):
            break
        await asyncio.sleep(0.1)
    assert not alive(pid)


async def test_too_little_memory_is_an_error_not_a_start(tmp_path) -> None:
    marker = tmp_path / "loads"
    os.environ["PIPESHUB_TABLE_FAKE_MARKER"] = str(marker)
    try:
        s = supervisor(memory_available=lambda: 1024)
        r = await s.run_table(spec("a"), PNG)
        assert r == {"status": "error", "err": "memory"}
        assert not marker.exists()
        s2 = supervisor(memory_available=lambda: None)
        assert (await s2.run_table(spec("a"), PNG))["err"] == "memory"
    finally:
        os.environ.pop("PIPESHUB_TABLE_FAKE_MARKER", None)


async def test_missing_weights_are_an_error() -> None:
    def no_weights(_mode: str) -> str:
        raise FileNotFoundError("nowhere")

    s = supervisor(weights_dir=no_weights)
    assert (await s.run_table(spec("a"), PNG)) == {"status": "error", "err": "weights"}


async def test_the_child_loads_the_model_once_for_many_tables(tmp_path) -> None:
    marker = tmp_path / "loads"
    os.environ["PIPESHUB_TABLE_FAKE_MARKER"] = str(marker)
    s = supervisor()
    try:
        for i in range(3):
            assert (await s.run_table(spec(f"t{i}"), PNG))["status"] == "ok"
        assert len(marker.read_text().split()) == 1
    finally:
        os.environ.pop("PIPESHUB_TABLE_FAKE_MARKER", None)
        await s.shutdown()


async def test_the_child_does_not_see_the_services_secrets_or_import_the_service(tmp_path) -> None:
    os.environ["SOME_SERVICE_SECRET"] = "hunter2"
    try:
        env = ts._child_env()
        assert "SOME_SERVICE_SECRET" not in env and "PATH" in env
    finally:
        os.environ.pop("SOME_SERVICE_SECRET", None)
    import subprocess

    code = "import sys, app.services.docling.table_structure_child as c; print(sorted(m for m in sys.modules if m.startswith(('app.services.docling', 'app.docling_main', 'fastapi'))))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert "docling_service" not in out.stdout and "fastapi" not in out.stdout and "docling_main" not in out.stdout


def test_the_per_table_timeout_formula() -> None:
    assert ts.table_timeout_s(50, 120) == 10.0  # floor
    assert ts.table_timeout_s(1000, 120) == 60.0
    assert ts.table_timeout_s(1000, 20) == 20.0  # ceiling
