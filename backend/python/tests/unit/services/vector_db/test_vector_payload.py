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
        connectorIds=["conn-1"],
        recordGroupIds=["rg-1"],
    )
    raw = payload.to_dict()
    restored = VectorChunkPayload.from_dict(raw)
    assert restored.page_content == "hello"
    assert restored.metadata.orgId == "org-1"
    assert restored.metadata.blockId == "blk-1"
    assert restored.metadata.blockIndex == 3
    assert restored.connectorIds == ["conn-1"]
    assert restored.recordGroupIds == ["rg-1"]
    assert raw["connectorIds"] == ["conn-1"]
    assert raw["recordGroupIds"] == ["rg-1"]
    assert "connectorIds" not in raw["metadata"]


def test_from_dict_defaults_missing_membership_to_empty():
    restored = VectorChunkPayload.from_dict({"page_content": "x", "metadata": {}})
    assert restored.connectorIds == []
    assert restored.recordGroupIds == []


def test_from_dict_splits_comma_joined_string():
    restored = VectorChunkPayload.from_dict(
        {"page_content": "x", "connectorIds": "c1,c2", "recordGroupIds": "g1"}
    )
    assert restored.connectorIds == ["c1", "c2"]
    assert restored.recordGroupIds == ["g1"]


def test_from_dict_strips_membership_whitespace():
    restored = VectorChunkPayload.from_dict(
        {
            "connectorIds": "conn-1, conn-2",
            "recordGroupIds": [" rg-1 ", "  ", None],
        }
    )
    assert restored.connectorIds == ["conn-1", "conn-2"]
    assert restored.recordGroupIds == ["rg-1"]


def test_from_dict_drops_null_list_entries():
    restored = VectorChunkPayload.from_dict(
        {"connectorIds": ["c1", None, ""], "recordGroupIds": None}
    )
    assert restored.connectorIds == ["c1"]
    assert restored.recordGroupIds == []
