"""A push body's ``externalRecordId`` is a logical key. It never becomes the storage document of the record."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .image_harness import did, image_body, make_harness

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


@pytest.fixture
async def h(tmp_path: Path) -> AsyncIterator[object]:
    harness = await make_harness(tmp_path)
    yield harness
    await harness.store.stop()


async def test_a_push_keyed_by_another_documents_id_still_gets_its_own_storage_document(h) -> None:
    victim = did(0xBAD)  # another user's storage document id, sent as the record's logical key
    await h.push(image_body([], key=victim))
    record_doc = h.graph.record["externalRecordId"]
    assert record_doc != victim
    assert record_doc in h.docs.created  # minted by this push, in this caller's org
    assert victim not in str(h.graph.record.get("fetchSignedUrl", ""))
