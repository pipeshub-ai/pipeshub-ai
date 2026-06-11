"""
Snapshot tests for MarkdownIt → BlocksContainer conversion.

For each ``fixtures/<name>.md``, runs :class:`MarkdownToBlocksConverter` and
compares the serialized output against ``fixtures/<name>.expected.json``.
Block ``id`` fields are stripped before comparison because they are random UUIDs.

No external services required::

    cd integration-tests
    pytest parsers/integration_test_markdown_to_blocks.py -v -o addopts=
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.models.blocks import Block, BlockGroup, BlocksContainer
from app.modules.parsers.markdown.markdown_to_blocks import MarkdownToBlocksConverter

_FIXTURES_DIR = Path(__file__).resolve().parent / "fixtures"


def _discover_markdown_fixtures() -> list[Path]:
    return sorted(_FIXTURES_DIR.glob("*.md"))


def _snapshot_path(markdown_path: Path) -> Path:
    return markdown_path.with_name(f"{markdown_path.stem}.expected.json")


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


@pytest.fixture(scope="module")
def converter() -> MarkdownToBlocksConverter:
    return MarkdownToBlocksConverter()


@pytest.mark.parametrize(
    "markdown_path",
    _discover_markdown_fixtures(),
    ids=lambda path: path.stem,
)
def test_markdown_to_blocks_snapshot(
    markdown_path: Path,
    converter: MarkdownToBlocksConverter,
) -> None:
    snapshot_path = _snapshot_path(markdown_path)
    assert markdown_path.is_file(), f"Missing markdown fixture: {markdown_path}"
    assert snapshot_path.is_file(), f"Missing expected JSON: {snapshot_path}"

    markdown_content = markdown_path.read_text(encoding="utf-8")
    actual = _normalize_container(converter.convert(markdown_content))
    expected = _load_snapshot(snapshot_path)
    assert actual == expected, f"Output mismatch for {markdown_path.name}"
