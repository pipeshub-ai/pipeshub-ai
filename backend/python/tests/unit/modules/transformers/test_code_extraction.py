"""Unit tests for the code-file branch of DocumentExtraction.

Covers the discriminator (find_code_summary_block), the LLM input builder
(_prepare_code_content), the shared SemanticMetadata mapper, and the graph
write that consumes it.
"""

import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlockType, DataFormat

if TYPE_CHECKING:
    from app.models.blocks import BlocksContainer
    from app.modules.transformers.document_extraction import (
        CodeClassification,
        DocumentExtraction,
    )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

SAMPLE_SOURCE = b'''"""Module docstring."""
import os

from qdrant_client import QdrantClient


class Store(Base):
    """Persists vectors."""

    def upsert(self, points: list) -> None:
        """Write points to Qdrant."""
        QdrantClient(os.environ["QDRANT_URL"]).upsert(points)
'''


def _parse(source=SAMPLE_SOURCE, path="app/services/vector/store.py") -> "BlocksContainer | None":
    """Run the real CodeFileParser so the blocks match production shape."""
    from app.modules.parsers.code_parser import CodeFileParser

    return CodeFileParser().parse_to_blocks(
        source, path.rsplit("/", 1)[-1], path, "python"
    )


def _build_extractor() -> "DocumentExtraction":
    from app.modules.transformers.document_extraction import DocumentExtraction

    return DocumentExtraction(
        logging.getLogger("test-code-extraction"), MagicMock(), MagicMock()
    )


def _make_text_block(text, index=0) -> Block:
    return Block(type=BlockType.TEXT, format=DataFormat.TXT, data=text, index=index)


# =========================================================================
# find_code_summary_block
# =========================================================================
class TestFindCodeSummaryBlock:
    """The discriminator both pipelines use to pick the code prompt."""

    def test_detects_parsed_source_file(self) -> None:
        from app.modules.transformers.document_extraction import (
            find_code_summary_block,
        )

        block = find_code_summary_block(_parse().blocks)

        assert block is not None
        assert block.data["file_path"] == "app/services/vector/store.py"
        assert block.code_metadata.language == "python"

    def test_returns_none_for_a_document(self) -> None:
        from app.modules.transformers.document_extraction import (
            find_code_summary_block,
        )

        assert find_code_summary_block([_make_text_block("a policy document")]) is None


# =========================================================================
# _prepare_code_content
# =========================================================================
class TestPrepareCodeContent:
    """The LLM input built from parser output rather than raw source."""

    def _render(self, context_length=128000) -> str:
        from app.modules.transformers.document_extraction import (
            find_code_summary_block,
        )

        container = _parse()
        summary_block = find_code_summary_block(container.blocks)
        return _build_extractor()._prepare_code_content(
            container.blocks, summary_block, context_length, container.block_groups
        )

    def test_includes_import_statements(self) -> None:
        """external_dependencies is unanswerable without the import text."""
        content = self._render()

        assert "from qdrant_client import QdrantClient" in content
        assert "import os" in content

    def test_includes_cross_file_edges(self) -> None:
        content = self._render()

        assert "## References to other files" in content
        assert "INHERITS: Base" in content

    def test_includes_container_docstring_from_block_groups(self) -> None:
        """Top-level classes are BlockGroups, not Blocks."""
        content = self._render()

        assert "Persists vectors." in content

    def test_includes_symbol_signature_and_docstring(self) -> None:
        content = self._render()

        assert "Store.upsert" in content
        assert "Write points to Qdrant." in content

    def test_is_smaller_than_raw_source_for_a_large_file(self) -> None:
        """The point of sending the symbol table instead of the file."""
        import inspect

        import app.modules.transformers.graphdb as graphdb_module
        from app.modules.transformers.document_extraction import (
            find_code_summary_block,
        )

        source = inspect.getsource(graphdb_module).encode()
        container = _parse(source, "app/modules/transformers/graphdb.py")
        summary_block = find_code_summary_block(container.blocks)
        content = _build_extractor()._prepare_code_content(
            container.blocks, summary_block, 128000, container.block_groups
        )

        assert 0 < len(content) < len(source) / 2

    def test_respects_the_token_budget(self) -> None:
        """A tiny context window must not produce an oversized payload."""
        from app.modules.transformers.document_extraction import count_tokens

        content = self._render(context_length=100)

        assert count_tokens(content) <= int(100 * 0.85)


# =========================================================================
# to_semantic_metadata
# =========================================================================
class TestToSemanticMetadata:
    """Both classification shapes map onto the one persisted model."""

    def _code_classification(self, **overrides) -> "CodeClassification":
        from app.modules.transformers.document_extraction import (
            CodeClassification,
            SubCategories,
        )

        kwargs = {
            "architecture_role": "Repository / Data Access",
            "category": "Indexing",
            "subcategories": SubCategories(
                level1="Vector Store", level2="Qdrant", level3=""
            ),
            "topics": ["vector upsert"],
            "summary": "Writes embeddings to Qdrant.",
            "design_patterns": ["repository"],
            "external_dependencies": ["Qdrant"],
        }
        kwargs.update(overrides)
        return CodeClassification(**kwargs)

    def test_code_fields_survive_the_mapping(self) -> None:
        from app.modules.transformers.document_extraction import (
            semantic_metadata_from_code,
        )

        metadata = semantic_metadata_from_code(self._code_classification())

        assert metadata.architecture_role == "Repository / Data Access"
        assert metadata.design_patterns == ["repository"]
        assert metadata.external_dependencies == ["Qdrant"]
        assert metadata.categories == ["Indexing"]
        assert metadata.sub_category_level_1 == "Vector Store"

    def test_departments_and_languages_are_lists_not_none(self) -> None:
        """save_metadata_to_db iterates both; None raises TypeError there."""
        from app.modules.transformers.document_extraction import (
            semantic_metadata_from_code,
        )

        metadata = semantic_metadata_from_code(self._code_classification())

        assert metadata.departments == []
        assert metadata.languages == []

    def test_blank_category_yields_no_category(self) -> None:
        """A blank one would create a Categories node named ''."""
        from app.modules.transformers.document_extraction import (
            semantic_metadata_from_code,
        )

        metadata = semantic_metadata_from_code(self._code_classification(category=""))

        assert metadata.categories == []

    def test_document_classification_still_maps(self) -> None:
        from app.modules.transformers.document_extraction import (
            DocumentClassification,
            SubCategories,
            semantic_metadata_from_classification,
        )

        metadata = semantic_metadata_from_classification(
            DocumentClassification(
                departments=["HR"],
                category="Policy",
                subcategories=SubCategories(level1="Benefits", level2="", level3=""),
                languages=["English"],
                sentiment="Neutral",
                confidence_score=0.9,
                topics=["benefits"],
                summary="A benefits policy.",
            )
        )

        assert metadata.departments == ["HR"]
        assert metadata.languages == ["English"]
        assert metadata.architecture_role is None


# =========================================================================
# apply
# =========================================================================
class TestApplyRouting:
    """apply picks the branch from the record type, not from the blocks alone."""

    @staticmethod
    def _code_classification() -> "CodeClassification":
        from app.modules.transformers.document_extraction import (
            CodeClassification,
            SubCategories,
        )

        return CodeClassification(
            architecture_role="Service",
            category="Indexing",
            subcategories=SubCategories(level1="a", level2="", level3=""),
            topics=["t"],
            summary="S",
        )

    @pytest.mark.asyncio
    async def test_code_file_takes_the_code_branch(self) -> None:
        ext = _build_extractor()
        container = _parse()

        from app.modules.transformers.document_extraction import (
            semantic_metadata_from_code,
        )

        ext.process_code_document = AsyncMock(return_value=semantic_metadata_from_code(self._code_classification()))
        ext.extract_metadata = AsyncMock()

        record = MagicMock()
        record.block_containers = container
        record.record_type.value = "CODE_FILE"
        ctx = MagicMock()
        ctx.record = record

        # Routing only; which code files are worth a call is test_classification_policy's.
        with patch("app.modules.transformers.document_extraction.code_classification_skip_reason",
                   return_value=None):
            await ext.apply(ctx)

        ext.extract_metadata.assert_not_awaited()
        ext.process_code_document.assert_awaited_once()
        assert record.semantic_metadata.architecture_role == "Service"

    @pytest.mark.asyncio
    async def test_document_still_takes_the_document_branch(self) -> None:
        ext = _build_extractor()
        ext.process_code_document = AsyncMock()
        ext.process_document = AsyncMock(return_value=None)

        record = MagicMock()
        record.block_containers.blocks = [_make_text_block("a policy document")]
        record.org_id = "org-1"
        record.record_type.value = "FILE"
        ctx = MagicMock()
        ctx.record = record

        await ext.apply(ctx)

        ext.process_code_document.assert_not_awaited()
        ext.process_document.assert_awaited_once()
        assert record.semantic_metadata is None

    @pytest.mark.asyncio
    async def test_plain_file_record_never_reaches_the_code_prompt(self) -> None:
        """The code prompt is keyed on the record type: a source file that
        arrived as a plain FILE (a .py uploaded to a knowledge base) parses to
        a summary block but still takes the document prompt."""
        ext = _build_extractor()
        ext.process_code_document = AsyncMock(return_value=self._code_classification())
        ext.process_document = AsyncMock(return_value=None)

        record = MagicMock()
        record.block_containers = _parse()
        record.org_id = "org-1"
        record.record_type.value = "FILE"
        ctx = MagicMock()
        ctx.record = record

        await ext.apply(ctx)

        ext.process_code_document.assert_not_awaited()
        ext.process_document.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_code_file_without_summary_block_falls_back(self) -> None:
        """A CODE_FILE with no tree-sitter grammar (or a repo README) parses
        as prose, so there is no summary block for the code prompt to use."""
        ext = _build_extractor()
        ext.process_code_document = AsyncMock()
        ext.process_document = AsyncMock(return_value=None)

        record = MagicMock()
        record.block_containers.blocks = [_make_text_block("#!/bin/sh\necho hi")]
        record.org_id = "org-1"
        record.record_type.value = "CODE_FILE"
        ctx = MagicMock()
        ctx.record = record

        await ext.apply(ctx)

        ext.process_code_document.assert_not_awaited()
        ext.process_document.assert_awaited_once()


# =========================================================================
# graphdb consumption
# =========================================================================
class TestGraphWriteAcceptsCodeMetadata:
    """The graph write is what a code record's empty taxonomy reaches next."""

    def _transformer_with_store(self) -> tuple:
        from app.modules.transformers.graphdb import GraphDBTransformer

        transformer = GraphDBTransformer(
            graph_provider=AsyncMock(), logger=MagicMock()
        )
        tx_store = AsyncMock()
        tx_store.get_record_by_key = AsyncMock(return_value={"_key": "rec-1"})
        tx_store.get_nodes_by_filters = AsyncMock(return_value=[])
        tx_store.get_edge = AsyncMock(return_value=None)
        tx_store.get_edges_from_node_with_target_name = AsyncMock(return_value=[])
        tx_store.batch_upsert_nodes = AsyncMock()
        tx_store.batch_update_nodes = AsyncMock(return_value=True)
        tx_store.batch_create_edges = AsyncMock()
        tx_store.batch_delete_edges = AsyncMock(return_value=0)

        ctx_mgr = AsyncMock()
        ctx_mgr.__aenter__ = AsyncMock(return_value=tx_store)
        ctx_mgr.__aexit__ = AsyncMock(return_value=False)
        transformer.graph_data_store.transaction = MagicMock(return_value=ctx_mgr)
        return transformer, tx_store

    @pytest.mark.asyncio
    async def test_none_departments_do_not_raise(self) -> None:
        """Regression: unguarded iteration failed every code record here."""
        from app.models.blocks import SemanticMetadata

        transformer, tx_store = self._transformer_with_store()
        metadata = SemanticMetadata(
            summary="S", topics=["t"], categories=["Indexing"]
        )
        assert metadata.departments is None and metadata.languages is None

        await transformer.save_metadata_to_db("rec-1", metadata, "vr-1")

        status_doc = tx_store.batch_update_nodes.call_args[0][0][0]
        assert status_doc["extractionStatus"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_blank_category_creates_no_category_node(self) -> None:
        from app.config.constants.arangodb import CollectionNames
        from app.models.blocks import SemanticMetadata

        transformer, tx_store = self._transformer_with_store()

        await transformer.save_metadata_to_db(
            "rec-1", SemanticMetadata(summary="S", categories=[]), "vr-1"
        )

        created = [
            call.args[1]
            for call in tx_store.batch_upsert_nodes.await_args_list
        ]
        assert CollectionNames.CATEGORIES.value not in created
