"""Normalization + expected-snapshot I/O for Drive Workspace blocks snapshot ITs.

Drive streams binary (Workspace export OOXML / download), not ``application/blocks``.
This module routes streamed bytes through the same format-specific ``Processor``
methods indexing uses, captures ``block_containers`` via a mocked
``IndexingPipeline.apply``, strips volatile fields, and loads / bootstraps
per-kind ``fixtures/<kind>.expected.json`` files.

LLM-generated table prose (``row_natural_language_text``, ``table_summary``,
``column_headers``) is replaced with placeholders so snapshots compare structure,
not non-deterministic wording.

Regenerate snapshots with ``GOOGLE_DRIVE_BLOCKS_BOOTSTRAP=1`` (missing expected
files are a hard failure — same pattern as Jira / parser ITs).
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from app.config.constants.arangodb import ExtensionTypes, MimeTypes

_FIXTURES_DIR = Path(__file__).with_name("fixtures")

# Per-run-varying keys on Block / BlockGroup dropped before comparison.
_VOLATILE_KEYS = frozenset({
    "id",
    "source_group_id",
    "weburl",
    "content_hash",
    "name",
    "description",
    "source_creation_date",
    "source_update_date",
    "source_modified_date",
    "source_created_at",
    "source_updated_at",
    "source_id",
    "source_name",
})

# LLM / prose fields — keep the key for structure, scrub the value.
_LLM_TEXT_KEYS = frozenset({
    "row_natural_language_text",
    "table_summary",
    "column_headers",
})

_LLM_PLACEHOLDER = "PLACEHOLDER"

_BASE64_DATA_URI = re.compile(r"data:[^;\s)]+;base64,[A-Za-z0-9+/=\s]+")

# kind → committed expected filename stem (fixtures/<stem>.expected.json)
EXPECTED_STEM_BY_KIND: dict[str, str] = {
    "newsletter_doc": "newsletter_doc",
    "science_fair_slides": "science_fair_slides",
    "gantt_chart_sheets": "gantt_chart_sheets",
    "contributing_md": "contributing_md",
}


def expected_path(kind: str) -> Path:
    stem = EXPECTED_STEM_BY_KIND.get(kind, kind)
    return _FIXTURES_DIR / f"{stem}.expected.json"


def _scrub_text(value: Any) -> Any:
    if isinstance(value, str):
        return _BASE64_DATA_URI.sub("data:PLACEHOLDER;base64,PLACEHOLDER", value)
    return value


def _round_floats(value: Any, ndigits: int = 4) -> Any:
    """Stabilize OCR/layout float noise in bounding boxes."""
    if isinstance(value, float):
        return round(value, ndigits)
    return value


def _normalize(node: Any) -> Any:
    if isinstance(node, dict):
        out: dict[str, Any] = {}
        for key, value in node.items():
            if key in _VOLATILE_KEYS:
                continue
            if key in _LLM_TEXT_KEYS:
                # Keep key presence / list-vs-str shape; drop LLM wording.
                if isinstance(value, list):
                    out[key] = [_LLM_PLACEHOLDER] * len(value)
                elif value is None:
                    out[key] = None
                else:
                    out[key] = _LLM_PLACEHOLDER
                continue
            if key == "children_records" and isinstance(value, list):
                out[key] = [
                    {"child_type": (c or {}).get("child_type")}
                    for c in value if isinstance(c, dict)
                ]
                continue
            out[key] = _normalize(value)
        return out
    if isinstance(node, list):
        return [_normalize(item) for item in node]
    return _round_floats(_scrub_text(node))


def normalize_blocks_container(container: dict) -> dict:
    """Return a run-stable normalization of a ``BlocksContainer`` dict.

    Drops volatile ids/urls/timestamps, placeholders LLM table prose, scrubs
    base64 blobs, and rounds float layout coords.
    """
    return _normalize(container)


def _mock_record_dict(*, record_name: str, mime_type: str) -> dict[str, Any]:
    return {
        "_key": "test-record-1",
        "orgId": "test-org-1",
        "recordName": record_name,
        "recordType": "FILE",
        "indexingStatus": "NOT_STARTED",
        "externalRecordId": "ext-1",
        "connectorId": "conn-1",
        "connectorName": "KB",
        "mimeType": mime_type,
        "createdAtTimestamp": 1000,
        "updatedAtTimestamp": 2000,
        "version": 1,
        "origin": "CONNECTOR",
    }


def _container_to_dict(bc: Any) -> dict[str, Any]:
    return {
        "block_groups": [g.model_dump(mode="json") for g in bc.block_groups],
        "blocks": [b.model_dump(mode="json") for b in bc.blocks],
    }


async def _stable_table_summary_n_headers(config: Any, table_data: Any) -> Any:
    """Deterministic stand-in for LLM table summary + headers."""
    headers: list[str] = []
    if isinstance(table_data, list) and table_data:
        first = table_data[0]
        if isinstance(first, list):
            headers = [str(c) if c else "" for c in first]
        elif isinstance(first, dict):
            headers = [str(k) for k in first.keys()]
    return MagicMock(summary=_LLM_PLACEHOLDER, headers=headers)


async def _stable_get_rows_text(
    config: Any,
    table_data: Any,
    table_summary: Any,
    column_headers: Any,
) -> tuple[list[str], list[Any]]:
    """Deterministic stand-in for LLM row natural-language text."""
    grid = (table_data or {}).get("grid") or []
    # Prefer body rows (skip header row when present).
    rows = grid[1:] if len(grid) > 1 else list(grid)
    texts = [_LLM_PLACEHOLDER] * len(rows)
    return texts, rows


async def _capture_via_indexing_pipeline(coro_agen: Any) -> dict[str, Any]:
    captured: dict[str, Any] = {}

    async def _capture(ctx: Any) -> None:
        captured["bc"] = ctx.record.block_containers

    with patch("app.events.processor.IndexingPipeline") as mock_pipeline_cls:
        mock_pipeline_cls.return_value.apply = AsyncMock(side_effect=_capture)
        async for _ in coro_agen:
            pass

    bc = captured.get("bc")
    assert bc is not None, "IndexingPipeline.apply was not called by Processor"
    return _container_to_dict(bc)


def _docling_llm_patches() -> tuple[Any, Any]:
    """Patch Docling table LLM helpers used by DoclingDocToBlocksConverter."""
    return (
        patch(
            "app.utils.converters.docling_doc_to_blocks.get_table_summary_n_headers",
            new=_stable_table_summary_n_headers,
        ),
        patch(
            "app.utils.converters.docling_doc_to_blocks.get_rows_text",
            new=_stable_get_rows_text,
        ),
    )


def _make_processor(*, mime_type: str) -> Any:
    from app.events.processor import Processor
    from app.modules.parsers.excel.excel_parser import ExcelParser
    from app.modules.parsers.image_parser.image_parser import ImageParser
    from app.modules.parsers.markdown.markdown_parser import MarkdownParser

    logger = logging.getLogger("drive-blocks-parser")
    logger.setLevel(logging.CRITICAL)
    config_service = MagicMock()
    parsers: dict[str, Any] = {
        ExtensionTypes.MD.value: MarkdownParser(),
        ExtensionTypes.PNG.value: ImageParser(logger),
        ExtensionTypes.XLSX.value: ExcelParser(logger, config_service),
    }
    # Do not mock DoclingClient — PDF path needs the real Docling service.
    return Processor(
        logger=logger,
        config_service=config_service,
        indexing_pipeline=MagicMock(),
        graph_provider=AsyncMock(),
        parsers=parsers,
        document_extractor=MagicMock(),
        sink_orchestrator=MagicMock(),
    )


async def parse_drive_stream_via_processor(
    content: bytes,
    record_mime: str,
    *,
    record_name: str = "drive-blocks",
) -> dict[str, Any]:
    """Run streamed Drive bytes through the production Processor path for *record_mime*.

    Dispatches like indexing (``events.py``): Google Docs/DOCX → docx, Slides/PPTX
    → pptx, Sheets/XLSX → excel, PDF → Docling client, MD → markdown parser.
    Excel and Docling table LLM calls are replaced with deterministic stubs;
    normalize still placeholders any residual LLM prose.
    """
    mime = (record_mime or "").lower()
    processor = _make_processor(mime_type=record_mime)
    processor.graph_provider.get_document = AsyncMock(
        return_value=_mock_record_dict(record_name=record_name, mime_type=record_mime)
    )
    # Table enhancement / Excel LLM summaries are non-deterministic.
    processor._enhance_tables_with_llm = AsyncMock()

    google_docs = MimeTypes.GOOGLE_DOCS.value.lower()
    google_slides = MimeTypes.GOOGLE_SLIDES.value.lower()
    google_sheets = MimeTypes.GOOGLE_SHEETS.value.lower()
    docx = MimeTypes.DOCX.value.lower()
    pptx = MimeTypes.PPTX.value.lower()
    xlsx = MimeTypes.XLSX.value.lower()
    pdf = MimeTypes.PDF.value.lower()
    markdown = MimeTypes.MARKDOWN.value.lower()

    async def _excel_stable_create_blocks(self: Any, llm: Any) -> Any:
        try:
            return self._build_basic_block_container()
        finally:
            if getattr(self, "workbook", None) is not None:
                self.workbook.close()

    summary_patch, rows_patch = _docling_llm_patches()

    if mime in (google_docs, docx):
        with summary_patch, rows_patch:
            agen = processor.process_docx_document(
                recordName=record_name,
                recordId="test-record-1",
                version=1,
                source="connector",
                orgId="test-org-1",
                docx_binary=content,
                virtual_record_id="test-vr-1",
            )
            return await _capture_via_indexing_pipeline(agen)

    if mime in (google_slides, pptx):
        with summary_patch, rows_patch:
            agen = processor.process_pptx_document(
                recordName=record_name,
                recordId="test-record-1",
                version=1,
                source="connector",
                orgId="test-org-1",
                pptx_binary=content,
                virtual_record_id="test-vr-1",
            )
            return await _capture_via_indexing_pipeline(agen)

    if mime in (google_sheets, xlsx):
        with patch(
            "app.modules.parsers.excel.excel_parser.ExcelParser.create_blocks",
            _excel_stable_create_blocks,
        ), patch(
            "app.events.processor.get_llm_for_role",
            new_callable=AsyncMock,
            return_value=(MagicMock(), {}),
        ):
            agen = processor.process_excel_document(
                recordName=record_name,
                recordId="test-record-1",
                version=1,
                source="connector",
                orgId="test-org-1",
                excel_binary=content,
                virtual_record_id="test-vr-1",
            )
            return await _capture_via_indexing_pipeline(agen)

    if mime == pdf or mime.endswith("/pdf"):
        # External Docling service may still embed LLM prose; normalize scrubs it.
        agen = processor.process_pdf_with_docling(
            recordName=record_name,
            recordId="test-record-1",
            pdf_binary=content,
            virtual_record_id="test-vr-1",
        )
        return await _capture_via_indexing_pipeline(agen)

    if mime in (markdown, "text/x-markdown") or record_name.lower().endswith(".md"):
        agen = processor.process_md_document(
            recordName=record_name,
            recordId="test-record-1",
            md_binary=content,
            virtual_record_id="test-vr-1",
        )
        return await _capture_via_indexing_pipeline(agen)

    raise AssertionError(
        f"Unsupported Drive record mime for blocks snapshot: {record_mime!r} "
        f"(record_name={record_name!r})"
    )


def load_expected(kind: str) -> dict:
    """Load the committed expected snapshot for *kind*, failing if absent.

    Re-normalizes on load so older fixtures with raw LLM prose still compare
    structurally against freshly normalized actuals.
    """
    path = expected_path(kind)
    if not path.is_file():
        raise AssertionError(
            f"Blocks expected snapshot not found at {path}. Regenerate it by running "
            "the Drive blocks IT once with GOOGLE_DRIVE_BLOCKS_BOOTSTRAP=1, hand-review "
            "the JSON, and commit it."
        )
    raw = json.loads(path.read_text(encoding="utf-8"))
    return normalize_blocks_container(raw)


def bootstrap_expected(kind: str, actual: dict) -> None:
    """Write ``actual`` (already normalized) as the expected snapshot for *kind*."""
    path = expected_path(kind)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(actual, indent=2, sort_keys=True), encoding="utf-8")
