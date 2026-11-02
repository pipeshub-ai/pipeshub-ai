"""A download that overlaps a storage move-tree should still find the stored record.

The move-tree route renames a record's files one by one, so a reader that lands between two renames
can see the document without its file. This holds on main without any indexing code, so the test is
an expected failure (not strict: no checkpoint forces a download into the window, so a run may pass; remove the
marker when the route is made atomic). Needs a running storage service; a graph store is used for the record's mapping.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from app.modules.transformers.blob_storage import get_shared_session
from app.utils.storage_move import post_move_tree
from tests.integration import stored_write_world
from tests.integration.stored_write_world import A, World, _rev

STORAGE_URL = os.environ.get("ARTIFACTS_IT_STORAGE_URL", "")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.timeout(600),
    pytest.mark.skipif(not STORAGE_URL, reason="set ARTIFACTS_IT_STORAGE_URL to a running storage service"),
]

world = stored_write_world.world


@pytest.mark.asyncio
@pytest.mark.xfail(
    strict=False,
    reason=(
        "the storage move-tree is not atomic for a concurrent download (pre-existing; fixed separately). "
        "Not strict: nothing forces a download into the window between two renames, so a run that misses it passes."
    ),
)
async def test_download_during_move_tree_finds_the_record(world: World) -> None:
    world.read_mode = "legacy"
    world.generation = None
    vrid = world.vrid("mv")
    await world.index(world.record(vrid, A), _rev(A))
    node = await world.mapping(vrid)
    raw = await world.blob._get_current_document_path(world.org, node["documentId"])
    current = world.blob._strip_org_prefix(world.org, raw)
    other = f"{current}-moved"
    headers, endpoint, _ = await world.blob._get_auth_and_config(world.org)

    stop = asyncio.Event()
    failures: list[str] = []

    async def download() -> None:
        while not stop.is_set():
            try:
                await world.blob.get_record_from_storage(vrid, world.org)
            except Exception as exc:  # any failure to read is the finding
                failures.append(type(exc).__name__)
            await asyncio.sleep(0)

    reader = asyncio.create_task(download())
    try:
        for _ in range(15):
            for source, target in ((current, other), (other, current)):
                result = await post_move_tree(
                    get_shared_session(), endpoint, headers, source, target, virtual_record_ids=[vrid],
                )
                assert result.get("moved") or result.get("failed") is not None, result
    finally:
        stop.set()
        await reader
    assert not failures, f"{len(failures)} downloads failed during the move: {sorted(set(failures))}"
