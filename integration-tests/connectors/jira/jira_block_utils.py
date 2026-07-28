"""Normalization + expected-snapshot loading for the Jira streamed-blocks integration test.

The Jira connector streams a ``BlocksContainer`` (``application/blocks`` JSON) built from a
ticket's ADF description + comments + inline images. That output carries per-run volatile
values (uuid ids, ``source_group_id`` with the issue id, ``weburl`` with the issue key,
``content_hash``, source timestamps, and giant base64 image data-URIs). This module strips /
placeholders those so the expected snapshot is stable across runs, and loads (or bootstraps)
the expected-snapshot file.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

_EXPECTED_PATH = Path(__file__).with_name("fixtures") / "jira_issue_blocks.expected.json"

# Per-run-varying keys on Block / BlockGroup dropped before comparison.
_VOLATILE_KEYS = frozenset({
    "id",
    "source_group_id",
    "weburl",
    "content_hash",
    "name",           # embeds "[KEY] summary" / "Comment by <author>" / "Comment Thread - <id>"
    "description",    # embeds author display name
    "source_creation_date",
    "source_update_date",
    "source_modified_date",
    "source_created_at",
    "source_updated_at",
    "source_id",
    "source_name",
})

# Inline image markdown carries a base64 data-URI whose bytes we do not pin.
_BASE64_DATA_URI = re.compile(r"data:[^;\s)]+;base64,[A-Za-z0-9+/=\s]+")


def _scrub_text(value: Any) -> Any:
    if isinstance(value, str):
        return _BASE64_DATA_URI.sub("data:PLACEHOLDER;base64,PLACEHOLDER", value)
    return value


def _normalize(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _VOLATILE_KEYS:
                continue
            if key == "children_records" and isinstance(value, list):
                # child_id / child_name are volatile; keep only the child_type shape.
                out[key] = [
                    {"child_type": (c or {}).get("child_type")}
                    for c in value if isinstance(c, dict)
                ]
                continue
            out[key] = _normalize(value)
        return out
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    return _scrub_text(node)


def normalize_blocks_container(container: dict) -> dict:
    """Return a run-stable normalization of a ``BlocksContainer`` dict."""
    return _normalize(container)


def _mock_record_dict() -> dict[str, Any]:
    """Minimal record document for ``process_blocks`` → ``convert_record_dict_to_record``."""
    return {
        "_key": "test-record-1",
        "orgId": "test-org-1",
        "recordName": "jira-blocks",
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "externalRecordId": "ext-1",
        "connectorId": "conn-1",
        "connectorName": "KB",
        "mimeType": "application/blocks",
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
        "version": 1,
        "origin": "CONNECTOR",
    }


async def parse_connector_blocks_via_processor(blocks_data: bytes | str | dict) -> dict:
    """Run the connector's ``application/blocks`` through the production ``Processor.process_blocks``
    and return the FINAL parsed ``BlocksContainer`` as a dict.

    Mirrors the parser IT (``process_md_document``) but for connector records: block-groups with
    ``requires_processing=True`` are parsed into fine-grained typed blocks via the real markdown
    parser. The graph DB, indexing pipeline and LLM table-enhancement are mocked so only the
    parsing path runs (no external services, deterministic output).
    """
    import logging as _logging
    from unittest.mock import AsyncMock, MagicMock, patch

    from app.config.constants.arangodb import ExtensionTypes
    from app.events.processor import Processor
    from app.modules.parsers.image_parser.image_parser import ImageParser
    from app.modules.parsers.markdown.markdown_parser import MarkdownParser

    logger = _logging.getLogger("jira-blocks-parser")
    logger.setLevel(_logging.CRITICAL)
    parsers = {
        ExtensionTypes.MD.value: MarkdownParser(),
        ExtensionTypes.PNG.value: ImageParser(logger),
    }
    with patch("app.events.processor.DoclingClient"):
        processor = Processor(
            logger=logger,
            config_service=MagicMock(),
            indexing_pipeline=MagicMock(),
            graph_provider=AsyncMock(),
            parsers=parsers,
            document_extractor=MagicMock(),
            sink_orchestrator=MagicMock(),
        )
    # Table enhancement calls an LLM (non-deterministic) — no-op it for a stable snapshot.
    processor._enhance_tables_with_llm = AsyncMock()
    processor.graph_provider.get_document = AsyncMock(return_value=_mock_record_dict())

    captured: dict[str, Any] = {}

    async def _capture(ctx: Any) -> None:
        captured["bc"] = ctx.record.block_containers

    with patch("app.events.processor.IndexingPipeline") as mock_pipeline_cls:
        mock_pipeline_cls.return_value.apply = AsyncMock(side_effect=_capture)
        async for _ in processor.process_blocks(
            recordName="jira-blocks", recordId="test-record-1", version=1,
            source="connector", orgId="test-org-1", blocks_data=blocks_data,
            virtual_record_id="test-vr-1",
        ):
            pass

    bc = captured.get("bc")
    assert bc is not None, "IndexingPipeline.apply was not called by process_blocks"
    return {
        "block_groups": [g.model_dump(mode="json") for g in bc.block_groups],
        "blocks": [b.model_dump(mode="json") for b in bc.blocks],
    }


def load_expected() -> dict:
    """Load the committed expected snapshot, failing if it is absent.

    Mirrors the parser snapshot IT: the expected file is committed and hand-reviewed,
    and a missing one is a hard failure — no silent bootstrap/skip. To (re)generate
    after an intentional output change, run TC-JIRA-BLOCKS-001 once with
    ``JIRA_BLOCKS_BOOTSTRAP=1`` (writes the file via ``bootstrap_expected``),
    hand-review the JSON (description markdown, comment threading, image
    placeholder), and commit it.
    """
    if not _EXPECTED_PATH.is_file():
        raise AssertionError(
            f"Blocks expected snapshot not found at {_EXPECTED_PATH}. Regenerate it by running "
            "TC-JIRA-BLOCKS-001 once with JIRA_BLOCKS_BOOTSTRAP=1, hand-review the "
            "JSON (description markdown, comment threading, image placeholder), and commit it."
        )
    return json.loads(_EXPECTED_PATH.read_text(encoding="utf-8"))


def bootstrap_expected(actual: dict) -> None:
    """Write ``actual`` (already normalized) as the expected snapshot — local regeneration only.

    Not part of the assertion path; the test invokes this solely when
    ``JIRA_BLOCKS_BOOTSTRAP=1`` so an operator can regenerate, hand-review, and commit.
    """
    _EXPECTED_PATH.parent.mkdir(parents=True, exist_ok=True)
    _EXPECTED_PATH.write_text(json.dumps(actual, indent=2, sort_keys=True), encoding="utf-8")
