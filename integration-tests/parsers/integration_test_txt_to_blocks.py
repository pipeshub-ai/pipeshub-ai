"""
Snapshot tests for plain-text (.txt) → BlocksContainer conversion.

Uses ``Processor.process_txt_document`` (production entry point), which decodes
TXT bytes and delegates to ``process_md_document``. Graph DB and indexing pipeline
dependencies are mocked so only the parsing path runs.

For each ``fixtures/<name>.txt``, compares serialized output against
``fixtures/<name>.expected.json``. Block ``id`` fields are stripped before
comparison because they are random UUIDs.

No external services required::

    cd integration-tests
    pytest -m integration parsers/integration_test_txt_to_blocks.py -v
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import ExtensionTypes
from app.events.processor import Processor
from app.models.blocks import Block, BlockGroup, BlocksContainer
from app.modules.parsers.image_parser.image_parser import ImageParser
from app.modules.parsers.markdown.markdown_parser import MarkdownParser

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _discover_txt_fixtures() -> list[Path]:
    return sorted(_FIXTURES_DIR.glob("*.txt"))


def _snapshot_path(txt_path: Path) -> Path:
    return txt_path.with_name(f"{txt_path.stem}.expected.json")


def _mock_record_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_key": "test-record-1",
        "orgId": "test-org-1",
        "recordName": "test.txt",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "externalRecordId": "ext-1",
        "connectorId": "conn-1",
        "connectorName": "KB",
        "mimeType": "text/plain",
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
        "version": 1,
        "origin": "UPLOAD",
    }
    base.update(overrides)
    return base


def _normalize_container(container: BlocksContainer) -> dict[str, Any]:
    """Serialize to JSON shape, stripping volatile block/group ids."""

    def _block_payload(block: Block) -> dict[str, Any]:
        payload = block.model_dump(mode="json")
        payload.pop("id", None)
        return payload

    def _group_payload(group: BlockGroup) -> dict[str, Any]:
        payload = group.model_dump(mode="json")
        payload.pop("id", None)
        return payload

    return {
        "block_groups": [_group_payload(g) for g in container.block_groups],
        "blocks": [_block_payload(b) for b in container.blocks],
    }


def _load_snapshot(path: Path) -> dict[str, Any]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    for block in raw["blocks"]:
        block.pop("id", None)
    for group in raw["block_groups"]:
        group.pop("id", None)
    return raw


def _make_processor() -> Processor:
    logger = logging.getLogger("integration-test-txt-parser")
    logger.setLevel(logging.CRITICAL)

    image_parser = ImageParser(logger)
    parsers = {
        ExtensionTypes.MD.value: MarkdownParser(),
        ExtensionTypes.PNG.value: image_parser,
    }

    with patch("app.events.processor.DoclingClient"):
        return Processor(
            logger=logger,
            config_service=MagicMock(),
            indexing_pipeline=MagicMock(),
            graph_provider=AsyncMock(),
            parsers=parsers,
            document_extractor=MagicMock(),
            sink_orchestrator=MagicMock(),
        )


async def _parse_txt_via_processor(
    processor: Processor,
    txt_path: Path,
) -> BlocksContainer:
    """Run ``process_txt_document`` and return the parsed ``BlocksContainer``."""
    captured: dict[str, BlocksContainer] = {}

    async def capture_apply(ctx: Any) -> None:
        captured["block_containers"] = ctx.record.block_containers

    processor.graph_provider.get_document = AsyncMock(
        return_value=_mock_record_dict(recordName=txt_path.name)
    )

    with patch("app.events.processor.IndexingPipeline") as mock_pipeline_cls:
        mock_pipeline_cls.return_value.apply = AsyncMock(side_effect=capture_apply)

        async for _ in processor.process_txt_document(
            recordName=txt_path.name,
            recordId="test-record-1",
            version=1,
            source="upload",
            orgId="test-org-1",
            txt_binary=txt_path.read_bytes(),
            virtual_record_id="test-vr-1",
            recordType="FILE",
            connectorName="KB",
            origin="UPLOAD",
        ):
            pass

    block_containers = captured.get("block_containers")
    assert block_containers is not None, "IndexingPipeline.apply was not called"
    return block_containers


@pytest.fixture(scope="module")
def processor() -> Processor:
    return _make_processor()


@pytest.mark.integration
@pytest.mark.parametrize(
    "txt_path",
    _discover_txt_fixtures(),
    ids=lambda path: path.stem,
)
@pytest.mark.asyncio
async def test_txt_to_blocks_snapshot(
    txt_path: Path,
    processor: Processor,
) -> None:
    snapshot_path = _snapshot_path(txt_path)
    assert txt_path.is_file(), f"Missing TXT fixture: {txt_path}"
    assert snapshot_path.is_file(), f"Missing expected JSON: {snapshot_path}"

    actual = _normalize_container(await _parse_txt_via_processor(processor, txt_path))
    expected = _load_snapshot(snapshot_path)
    assert actual == expected, f"Output mismatch for {txt_path.name}"
