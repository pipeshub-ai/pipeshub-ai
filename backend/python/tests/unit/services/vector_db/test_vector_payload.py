"""Tests for VectorChunkPayload round-trip."""

from app.services.vector_db.models import VectorChunkMetadata, VectorChunkPayload


def test_vector_payload_round_trip():
    payload = VectorChunkPayload(
        page_content="hello",
        metadata=VectorChunkMetadata(
            orgId="org-1",
            virtualRecordId="vr-1",
            blockId="blk-1",
            blockIndex=3,
        ),
    )
    raw = payload.to_dict()
    restored = VectorChunkPayload.from_dict(raw)
    assert restored.page_content == "hello"
    assert restored.metadata.orgId == "org-1"
    assert restored.metadata.blockId == "blk-1"
    assert restored.metadata.blockIndex == 3
