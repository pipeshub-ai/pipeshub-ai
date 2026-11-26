"""DP-1: content over the inline threshold is sealed by Node; the worker holds refs, never keys.

The HTTP cases run ``NodeJournal`` against the real Node router, content
controller and attempt-token authority (``journal-http-server.ts``).
"""

from __future__ import annotations

import base64
import logging
from typing import TYPE_CHECKING

import pytest

from app.harness.adapters.content_store import (
    INLINE_MAX_BYTES,
    InMemorySealedContent,
    PipesHubContentStore,
)
from harness_core.contracts.generated.common import ContentRef
from harness_core.journal.types import ContentMissing, JournalError, OwnershipLost
from tests.unit.harness.test_node_journal_contract import (
    LEASE_MS,
    NodeHarness,
    node_server,
)

if TYPE_CHECKING:
    from app.harness.journal_node import NodeClaim
    from tests.unit.harness.test_node_journal_contract import _NodeServer

__all__ = ["node_server"]

SENTINEL = b"SENTINEL-py-content-2c7e"
BIG = SENTINEL + b"q" * (INLINE_MAX_BYTES + 1)


def _refusals(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [
        str(record.harness["reason"])  # type: ignore[attr-defined]
        for record in caplog.records
        if getattr(record, "harness_event", None) == "content.refused"
    ]


async def test_cs1_small_content_inline_the_rest_through_the_sealed_route() -> None:
    route = InMemorySealedContent()
    store = PipesHubContentStore(route)
    small = await store.put(b"hi")
    assert small.ref.startswith("inline:")
    big = await store.put(BIG)
    assert big.ref.startswith("sealed:")
    assert list(route.objects) == [big.ref]
    assert await store.get(big) == BIG
    route.objects[big.ref] = b"x" * len(BIG)
    with pytest.raises(ContentMissing):
        _ = await store.get(big)


async def test_cs7_plaintext_blob_refs_from_before_sealing_are_refused(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.WARNING)
    store = PipesHubContentStore(InMemorySealedContent())
    old = ContentRef(ref="blob:doc-1", hash="sha256:" + "0" * 64, size=len(BIG))
    with pytest.raises(ContentMissing, match="unsealed"):
        _ = await store.get(old)
    with pytest.raises(ContentMissing):
        _ = await store.get(ContentRef(ref="s3://bucket/x", hash=old.hash, size=1))
    assert _refusals(caplog) == ["plaintext_blob", "unknown_ref"]


async def test_no_sealed_route_fails_closed(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.WARNING)
    store = PipesHubContentStore(None)
    assert (await store.get(await store.put(b"small"))) == b"small"
    with pytest.raises(JournalError):
        _ = await store.put(BIG)
    assert _refusals(caplog) == ["no_sealed_route"]


class _Content:
    def __init__(self, server: _NodeServer) -> None:
        self.h = NodeHarness(server)
        self.control = server.control

    async def claim(self, run_id: str, session_id: str, worker_id: str = "w1") -> NodeClaim:
        self.h.admit(run_id, session_id=session_id)
        claim = await self.h.journal.claim(run_id, worker_id=worker_id)
        assert claim is not None
        assert claim.content is not None
        return claim  # type: ignore[return-value]

    def stored(self) -> list[bytes]:
        objects = self.control.get("/content-bytes").raise_for_status().json()["objects"]
        return [base64.b64decode(item) for item in objects]


@pytest.fixture
def content(node_server: _NodeServer) -> _Content:
    return _Content(node_server)


async def test_cs1_node_seals_what_the_worker_writes(
    content: _Content, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    claim = await content.claim("run-a", "ses-a")
    store = PipesHubContentStore(claim.content)
    ref = await store.put(BIG)
    assert ref.ref.startswith("sealed:run-a/")
    stored = content.stored()
    assert len(stored) == 1
    assert SENTINEL not in stored[0]
    assert await store.get(ref) == BIG
    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "content.sealed" in logged and "content.opened" in logged
    assert SENTINEL.decode() not in logged
    await content.h.node.aclose()


async def test_cs2_another_session_reads_nothing(content: _Content) -> None:
    writer = await content.claim("run-a", "ses-a")
    ref = await PipesHubContentStore(writer.content).put(BIG)
    other = await content.claim("run-b", "ses-b", worker_id="w2")
    with pytest.raises(ContentMissing):
        _ = await PipesHubContentStore(other.content).get(ref)
    await content.h.node.aclose()


async def test_cs3_a_superseded_attempt_neither_writes_nor_reads(content: _Content) -> None:
    first = await content.claim("run-a", "ses-a")
    ref = await PipesHubContentStore(first.content).put(BIG)
    content.h.advance(LEASE_MS / 1000 + 1)
    content.h.sweep()
    again = await content.h.journal.claim("run-a", worker_id="w2")
    assert again is not None and again.fence == first.fence + 1
    stale = PipesHubContentStore(first.content)
    with pytest.raises(OwnershipLost):
        _ = await stale.get(ref)
    with pytest.raises(OwnershipLost):
        _ = await stale.put(BIG)
    assert await PipesHubContentStore(again.content).get(ref) == BIG  # type: ignore[attr-defined]
    await content.h.node.aclose()


async def test_cs4_cs5_tampered_or_shredded_content_is_missing(content: _Content) -> None:
    claim = await content.claim("run-a", "ses-a")
    store = PipesHubContentStore(claim.content)
    tampered = await store.put(BIG)
    _ = content.control.post("/tamper-content").raise_for_status()
    with pytest.raises(ContentMissing):
        _ = await store.get(tampered)
    shredded = await store.put(BIG + b"2")
    _ = content.control.post("/shred").raise_for_status()
    with pytest.raises(ContentMissing):
        _ = await store.get(shredded)
    await content.h.node.aclose()
