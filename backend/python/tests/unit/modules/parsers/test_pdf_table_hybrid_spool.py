"""The spool of the shadow run: owned by the parent, removed on every way out, swept by owner and never by age."""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import TYPE_CHECKING

import httpx
import pytest

from app.modules.parsers.pdf import table_hybrid_flow as flow
from app.modules.parsers.pdf.table_hybrid_client import TableStructureClient
from tests.support.table_shadow import spool_tables

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture(autouse=True)
def spool_root(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "spool-root"
    monkeypatch.setenv("PIPESHUB_TABLE_SPOOL_DIR", str(root))
    monkeypatch.setenv("PDF_TABLE_EXTRACTION", "shadow")
    monkeypatch.setenv("PDF_TABLE_HYBRID_SHADOW", "true")
    return root


def coordinator(handler=None, **kw) -> flow.ShadowCoordinator:
    transport = httpx.MockTransport(handler or (lambda r: httpx.Response(500, json={})))
    return flow.ShadowCoordinator(TableStructureClient("http://svc", transport), **kw)


def test_the_spool_is_created_in_the_parent_with_its_owner(spool_root) -> None:
    path = flow.create_spool()
    assert os.path.dirname(path) == str(spool_root) and oct(os.stat(path).st_mode & 0o777) == "0o700"
    owner = json.load(open(os.path.join(path, "owner.json")))
    assert owner["pid"] == os.getpid() and owner["start"] == flow.own_token()


async def test_the_spool_is_removed_after_a_run_even_when_the_service_fails(spool_root) -> None:
    c = coordinator()
    adm = c.admit("rec", "req")
    spool_tables(adm.spool_dir, 2)
    await c.start(adm, "rec", "req")
    assert not os.path.exists(adm.spool_dir)


async def test_the_spool_is_removed_when_the_task_is_cancelled(spool_root) -> None:
    started = asyncio.Event()

    async def hang(request) -> None:
        started.set()
        await asyncio.sleep(60)

    transport = httpx.MockTransport(hang)
    c = flow.ShadowCoordinator(TableStructureClient("http://svc", transport))
    adm = c.admit("rec", "req")
    spool_tables(adm.spool_dir, 2)
    task = c.start(adm, "rec", "req")
    await asyncio.wait_for(started.wait(), 5)
    await c.shutdown()
    assert task.cancelled() or task.done()
    assert not os.path.exists(adm.spool_dir)


def test_a_parse_that_fails_frees_the_slot_and_removes_the_spool(spool_root) -> None:
    c = coordinator()
    adm = c.admit("rec", "req")
    assert os.path.isdir(adm.spool_dir)
    c.abandon(adm)
    assert not os.path.exists(adm.spool_dir)
    assert c.admit("rec2", "req2") is not None  # the slot is free again


def make_owned(root, name: str, pid: int, start: str | None) -> str:
    d = root / name
    d.mkdir(parents=True)
    (d / "owner.json").write_text(json.dumps({"pid": pid, "start": start}))
    (d / "x.png").write_bytes(b"x")
    return str(d)


def test_the_sweep_removes_a_directory_whose_owner_is_gone(spool_root) -> None:
    gone = make_owned(spool_root, "doc-1-a", 424242, "t1")
    assert flow.sweep_stale(str(spool_root), token_of=lambda pid: None) == 1
    assert not os.path.exists(gone)


def test_the_sweep_keeps_a_live_owner_whatever_the_age(spool_root) -> None:
    live = make_owned(spool_root, "doc-2-a", 777, "t-live")
    old = time.time() - 30 * 24 * 3600
    os.utime(live, (old, old))
    assert flow.sweep_stale(str(spool_root), token_of=lambda pid: "t-live") == 0
    assert os.path.isdir(live)


def test_the_sweep_removes_a_directory_whose_pid_was_reused_by_a_new_process(spool_root) -> None:
    """A restarted container gives a new process the dead owner's pid: the start token differs."""
    reused = make_owned(spool_root, "doc-3-a", 1234, "boot-1")
    assert flow.sweep_stale(str(spool_root), token_of=lambda pid: "boot-2") == 1
    assert not os.path.exists(reused)


def test_a_young_directory_with_no_owner_file_is_left_alone(spool_root) -> None:
    d = spool_root / "doc-4-a"
    d.mkdir(parents=True)
    assert flow.sweep_stale(str(spool_root), token_of=lambda pid: None) == 0
    assert d.is_dir()


def test_the_sweep_ignores_directories_it_does_not_own(spool_root) -> None:
    other = spool_root / "something-else"
    other.mkdir(parents=True)
    flow.sweep_stale(str(spool_root), token_of=lambda pid: None)
    assert other.is_dir()


def test_the_own_live_spool_survives_a_sweep_by_the_same_process(spool_root) -> None:
    path = flow.create_spool()
    assert flow.sweep_stale(str(spool_root)) == 0 and os.path.isdir(path)


def test_the_spool_root_is_container_local_by_default(monkeypatch) -> None:
    import tempfile

    monkeypatch.delenv("PIPESHUB_TABLE_SPOOL_DIR")
    assert flow.spool_root().startswith(tempfile.gettempdir())


def test_the_parent_removes_a_directory_a_worker_left_behind(spool_root) -> None:
    """The worker only writes files and never deletes; what it leaves, the parent removes (a pool-level kill is not exercised here)."""
    path = flow.create_spool()
    col = spool_tables(path, 2)
    assert col.sent == 2 and os.listdir(path)
    flow.remove_spool(path)
    assert not os.path.exists(path)
