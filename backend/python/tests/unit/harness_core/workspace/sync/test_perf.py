"""PF-2, PF-3: 10,000 small files in and back under 5 s on the fake broker; the writer streams."""

from __future__ import annotations

import asyncio
import os
import platform
import time

import pytest

from harness_core.testing.contracts.workspace_sync import acquire, commit
from harness_core.workspace import FILE_MODE, ObjectRef, WorkspaceFile
from harness_core.workspace.sync import TAR
from harness_core.workspace.sync.archive import BLOCK, CHUNK, Owner, write_tree
from tests.unit.harness_core.workspace.sync.support import FakeSyncHarness

FILES = 10_000
BUDGET_S = 5.0


@pytest.mark.slow
async def test_pf2_ten_thousand_files_materialize_and_collect_unchanged_in_under_five_seconds() -> None:
    harness = FakeSyncHarness(TAR)
    manifest = await commit(harness, {f"pkg{i % 100}/mod{i}.py": f"value = {i}\n".encode() for i in range(FILES)})
    sandbox = await acquire(harness)
    started = time.perf_counter()
    state = await harness.sync.materialize(sandbox, manifest, root=harness.root)
    materialized = time.perf_counter()
    collected = await harness.sync.collect(sandbox, state)
    finished = time.perf_counter()
    assert collected.changes.is_empty
    assert len(state.manifest.files) == FILES
    host = f"{platform.machine()}, {os.cpu_count()} cpus, Python {platform.python_version()}"
    print(
        f"PF-2 on {host}: materialize {materialized - started:.2f} s, "
        f"unchanged collect {finished - materialized:.2f} s, total {finished - started:.2f} s"
    )
    assert finished - started < BUDGET_S
    await harness.broker.release(sandbox)


async def test_pf3_the_writer_streams_through_a_bounded_window() -> None:
    sizes = [3 * CHUNK + 7, 10, 0, CHUNK]
    entries = [
        WorkspaceFile(f"d/f{i}", ObjectRef(f"o{i}", f"h{i}", size), "application/octet-stream", FILE_MODE)
        for i, size in enumerate(sizes)
    ]
    in_flight = 0
    peak = 0

    async def fetch(entry: WorkspaceFile) -> bytes:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0)
        in_flight -= 1
        return b"x" * entry.content.size_bytes

    chunks = [chunk async for chunk in write_tree(entries, fetch, owner=Owner(), mtime=0, window=2)]
    assert max(len(chunk) for chunk in chunks[:-1]) <= CHUNK + 3 * BLOCK
    assert peak <= 2
    assert sum(len(chunk) for chunk in chunks) % BLOCK == 0
