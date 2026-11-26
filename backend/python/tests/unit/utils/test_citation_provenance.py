"""A live record's citations say they are live (P1b C-18).

A citation built from a temporary record a tool fetched carries
`sourceKind: live`, the connector instance and the item's external id, and
is not linked to a record page (there is none). Citations of indexed records
are unchanged: no new field, so owner frames and goldens stay as they are.
"""

from __future__ import annotations

from typing import Any

from app.agents.actions.util.live_record import live_file_record
from app.config.constants.arangodb import Connectors
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.utils.chat_helpers import build_block_web_url, get_enhanced_metadata
from app.utils.citations import (
    normalize_citations_and_chunks,
    normalize_citations_and_chunks_for_agent,
)

FRONTEND = "https://app.test"


def _live_record() -> dict[str, Any]:
    record = live_file_record(
        {"org_id": "org-1", "tool_to_toolset_map": {"drive.get_file_content": "drive-toolset"}},
        tool="drive.get_file_content",
        connector=Connectors.GOOGLE_DRIVE,
        external_record_id="file-1",
        record_name="plan.txt",
        mime_type="text/plain",
        extension="txt",
    )
    record.block_containers = BlocksContainer(
        blocks=[Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="The plan ships in May.")],
        block_groups=[],
    )
    return record.model_dump(mode="json")


def _indexed_record() -> dict[str, Any]:
    return {
        "id": "0b6f4c1e-2d7a-4e39-9c55-0d1f6a7b8c9d",
        "org_id": "org-1",
        "record_name": "plan.pdf",
        "origin": "CONNECTOR",
        "connector_name": "DRIVE",
        "connector_id": "drive-connector",
        "external_record_id": "file-1",
        "mime_type": "application/pdf",
        "weburl": "https://drive.test/file-1",
        "block_containers": {"blocks": [{"index": 0, "type": "text", "data": "The plan ships in May."}]},
    }


def test_a_live_record_citation_carries_its_provenance() -> None:
    record = _live_record()
    meta = get_enhanced_metadata(record, record["block_containers"]["blocks"][0], {})
    assert meta["sourceKind"] == "live"
    assert meta["externalRecordId"] == "file-1"
    assert meta["connectorId"] == "drive-toolset"
    assert meta["connector"] == Connectors.GOOGLE_DRIVE.value
    assert meta["recordId"] == record["id"]
    # No record page exists for it, so the citation neither links nor previews one.
    assert "/record/" not in (meta["webUrl"] or "")
    assert meta["previewRenderable"] is False


def test_an_indexed_record_citation_is_unchanged() -> None:
    record = _indexed_record()
    meta = get_enhanced_metadata(record, record["block_containers"]["blocks"][0], {})
    assert "sourceKind" not in meta
    assert "externalRecordId" not in meta


def test_normalized_citations_of_a_live_record_are_live_in_both_pipelines() -> None:
    record = _live_record()
    url = build_block_web_url(FRONTEND, record["id"], 0)
    answer = f"It ships in May [1]({url})."
    for normalize in (normalize_citations_and_chunks, normalize_citations_and_chunks_for_agent):
        _, citations = normalize(answer, [], records=[record])
        [citation] = citations
        assert citation["metadata"]["sourceKind"] == "live"
        assert citation["metadata"]["externalRecordId"] == "file-1"
        assert citation["metadata"]["connectorId"] == "drive-toolset"
