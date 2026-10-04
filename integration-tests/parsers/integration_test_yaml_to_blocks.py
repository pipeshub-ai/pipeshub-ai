"""
Snapshot tests for YAML → BlocksContainer conversion.

Uses ``Processor.process_structured_document`` (production entry point for
``.yaml`` / ``.yml``). Graph DB and indexing pipeline dependencies are mocked
so only the parsing path runs.

Each fixture is a templated YAML dialect the parser detects and chunks
structurally (PyYAML cannot load bare ``{{ }}`` / ``{% %}`` / ``<% %>`` tags):

- ``deployment.yaml`` — Go ``text/template`` (copy of
  ``deployment/helm/pipeshub-ai/templates/deployment.yaml``)
- ``ansible_web_stack.yml`` — Jinja2 (Ansible, SaltStack)
- ``rails_app_config.yml`` — ERB (Rails, Chef)
- ``mustache_app_stack.yaml`` — Mustache / Handlebars

For each ``fixtures/<name>.yaml`` / ``.yml``, compares serialized output
against ``fixtures/<name>.expected.json``. Block ``id`` fields are stripped
before comparison because they are random UUIDs.

No external services required::

    cd integration-tests
    pytest -m integration parsers/integration_test_yaml_to_blocks.py -v
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

if TYPE_CHECKING:
    from app.events.processor import Processor
    from app.models.blocks import Block, BlockGroup, BlocksContainer

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _discover_yaml_fixtures() -> list[Path]:
    return sorted(
        [*_FIXTURES_DIR.glob("*.yaml"), *_FIXTURES_DIR.glob("*.yml")],
        key=lambda path: path.stem,
    )


def _snapshot_path(yaml_path: Path) -> Path:
    return yaml_path.with_name(f"{yaml_path.stem}.expected.json")


def _mock_record_dict(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "_key": "test-record-1",
        "orgId": "test-org-1",
        "recordName": "test.yaml",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "externalRecordId": "ext-1",
        "connectorId": "conn-1",
        "connectorName": "KB",
        "mimeType": "application/yaml",
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
    # Imported here rather than at module scope: Processor pulls in torch,
    # docling and onnxruntime, and under pytest-xdist every worker imports
    # every test module at collection time.
    from app.config.constants.arangodb import ExtensionTypes
    from app.events.processor import Processor
    from app.modules.parsers.yaml.yaml_parser import YAMLParser

    logger = logging.getLogger("integration-test-yaml-parser")
    logger.setLevel(logging.CRITICAL)

    parsers = {
        ExtensionTypes.YAML.value: YAMLParser(),
        ExtensionTypes.YML.value: YAMLParser(),
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


async def _parse_yaml_via_processor(
    processor: Processor,
    yaml_path: Path,
) -> BlocksContainer:
    """Run ``process_structured_document`` and return the parsed container."""
    captured: dict[str, BlocksContainer] = {}

    async def capture_apply(ctx: Any) -> None:
        captured["block_containers"] = ctx.record.block_containers

    processor.graph_provider.get_document = AsyncMock(
        return_value=_mock_record_dict(recordName=yaml_path.name)
    )

    with patch("app.events.processor.IndexingPipeline") as mock_pipeline_cls:
        mock_pipeline_cls.return_value.apply = AsyncMock(side_effect=capture_apply)

        async for _ in processor.process_structured_document(
            recordName=yaml_path.name,
            recordId="test-record-1",
            file_content=yaml_path.read_bytes(),
            virtual_record_id="test-vr-1",
            extension="yaml",
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
    "yaml_path",
    _discover_yaml_fixtures(),
    ids=lambda path: path.stem,
)
@pytest.mark.asyncio
async def test_yaml_to_blocks_snapshot(
    yaml_path: Path,
    processor: Processor,
) -> None:
    snapshot_path = _snapshot_path(yaml_path)
    assert yaml_path.is_file(), f"Missing YAML fixture: {yaml_path}"
    assert snapshot_path.is_file(), f"Missing expected JSON: {snapshot_path}"

    actual = _normalize_container(await _parse_yaml_via_processor(processor, yaml_path))
    expected = _load_snapshot(snapshot_path)
    assert actual == expected, f"Output mismatch for {yaml_path.name}"
