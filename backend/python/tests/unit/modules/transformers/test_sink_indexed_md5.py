"""The write that completes a full index stores the md5 of the bytes that were indexed, and nothing else does."""
from __future__ import annotations

import pytest

from app.modules.indexing import indexed_bytes
from tests.unit.modules.transformers.test_sink_orchestrator import (
    _make_ctx,
    _make_orchestrator,
)

MD5 = "0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean() -> None:
    indexed_bytes.clear()
    yield
    indexed_bytes.clear()


async def _row() -> dict:
    orch = _make_orchestrator()
    await orch._update_indexing_status(_make_ctx())
    (row,) = orch.graph_provider.batch_update_nodes.await_args.args[0]
    return row


@pytest.mark.asyncio
async def test_the_completing_write_carries_the_md5_of_the_indexed_bytes() -> None:
    indexed_bytes.remember(MD5)
    assert (await _row())["indexedMd5"] == MD5


@pytest.mark.asyncio
async def test_a_write_with_no_remembered_bytes_leaves_the_field_alone() -> None:
    """A re-embed from stored blocks and every non-PDF event reach this write with nothing remembered."""
    assert "indexedMd5" not in await _row()


@pytest.mark.asyncio
async def test_clearing_forgets_the_bytes_so_the_next_event_cannot_inherit_them() -> None:
    indexed_bytes.remember(MD5)
    indexed_bytes.clear()
    assert "indexedMd5" not in await _row()
