"""Query-side code reads a record's content only through ``record_artifact_reader``."""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.modules.artifacts.reader import (
    LegacyRecordArtifactReader,
    record_artifact_reader,
)

APP = Path(__file__).resolve().parents[4] / "app"

# The indexing side reads, modifies and rewrites the stored record; it moves to
# the artifact writer together with the write path.
ALLOWED = frozenset({
    "app/modules/artifacts/reader.py",
    "app/modules/indexing/named_entity_completion.py",
    "app/modules/indexing/named_entity_persist_retry.py",
    "app/modules/indexing/record_label_repair.py",
    "app/modules/pipeline/stages/record_io.py",
    "app/modules/transformers/blob_storage.py",
    "app/modules/transformers/sink_orchestrator.py",
    "app/scripts/named_entity_reextract.py",
    "app/services/messaging/kafka/handlers/record.py",
})


def _direct_reads(path: Path) -> list[int]:
    source = path.read_text(encoding="utf-8-sig")
    # Parsing all of app/ takes longer than the test timeout.
    if "get_record_from_storage" not in source:
        return []
    return [
        node.lineno
        for node in ast.walk(ast.parse(source, filename=str(path)))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "get_record_from_storage"
    ]


def test_record_content_is_read_through_the_reader() -> None:
    offenders = [
        f"{rel}:{line}"
        for path in sorted(APP.rglob("*.py"))
        if (rel := path.relative_to(APP.parent).as_posix()) not in ALLOWED
        for line in _direct_reads(path)
    ]
    assert not offenders, (
        "Read a record's stored content through "
        "`record_artifact_reader(blob_store).read_record(...)`, not "
        f"`get_record_from_storage`: {offenders}"
    )


def test_allowlist_has_no_stale_entries() -> None:
    stale = sorted(rel for rel in ALLOWED if not _direct_reads(APP.parent / rel))
    assert not stale, f"No longer call get_record_from_storage; drop from ALLOWED: {stale}"


def test_factory_returns_the_legacy_reader() -> None:
    assert isinstance(record_artifact_reader(MagicMock()), LegacyRecordArtifactReader)


class TestLegacyRecordArtifactReader:
    async def test_read_record_passes_the_lookup_through(self) -> None:
        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock(return_value={"block_containers": {}})
        lookup = {"record_doc_id": "doc-1"}

        record = await LegacyRecordArtifactReader(blob).read_record("vr-1", "org-1", lookup=lookup)

        assert record == {"block_containers": {}}
        blob.get_record_from_storage.assert_awaited_once_with("vr-1", "org-1", lookup_result=lookup)

    async def test_read_record_without_lookup_lets_storage_resolve_it(self) -> None:
        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock(return_value=None)

        assert await LegacyRecordArtifactReader(blob).read_record("vr-1", "org-1") is None
        blob.get_record_from_storage.assert_awaited_once_with("vr-1", "org-1", lookup_result=None)

    async def test_read_record_errors_propagate(self) -> None:
        blob = MagicMock()
        blob.get_record_from_storage = AsyncMock(side_effect=RuntimeError("storage down"))

        with pytest.raises(RuntimeError, match="storage down"):
            await LegacyRecordArtifactReader(blob).read_record("vr-1", "org-1")

    async def test_lookup_many_is_the_batched_mapping_query(self) -> None:
        blob = MagicMock()
        blob.get_document_ids_by_virtual_record_ids = AsyncMock(return_value={"vr-1": {"record_doc_id": "d"}})

        result = await LegacyRecordArtifactReader(blob).lookup_many(["vr-1", "vr-2"])

        assert result == {"vr-1": {"record_doc_id": "d"}}
        blob.get_document_ids_by_virtual_record_ids.assert_awaited_once_with(["vr-1", "vr-2"])

    async def test_lookup_many_errors_propagate(self) -> None:
        blob = MagicMock()
        blob.get_document_ids_by_virtual_record_ids = AsyncMock(side_effect=RuntimeError("graph down"))

        with pytest.raises(RuntimeError, match="graph down"):
            await LegacyRecordArtifactReader(blob).lookup_many(["vr-1"])
