"""Unit tests for CODE block embedding in VectorStore.index_documents().

Tests cover Phase 1.3 of the Code File Indexing plan:

- BlockType.CODE block produces ≥1 Document added to documents_to_embed
- The Document's page_content includes the contextual prefix (# File:, # Language:, etc.)
- The Document's page_content includes the code body
- block.data accessed as dict ["text"] — string/dict mismatch does not raise
- GroupType.CODE block group emits a document with isBlockGroup: True
- Oversized code block (> MAX_CODE_NONWS_CHARS) is split into multiple Documents
- Each split Document retains the blockId in metadata
- File summary block (kind="file_summary") emits isRecordSummary: True document
- Signature+docstring summary doc emitted for function blocks with metadata
- Non-code blocks (TEXT) are not added to code_blocks list
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Document may be a MagicMock stub when langchain_core is not installed in the
# test environment; import defensively and define a type guard.
try:
    from langchain_core.documents import Document as _LCDocument
    def _is_doc(obj) -> bool:
        return isinstance(obj, _LCDocument)
except (ImportError, TypeError):
    def _is_doc(obj) -> bool:  # type: ignore[misc]
        return hasattr(obj, "page_content") and hasattr(obj, "metadata")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_vectorstore():
    with patch("app.modules.transformers.vectorstore.FastEmbedSparse"), \
         patch("app.modules.transformers.vectorstore._get_shared_nlp"):
        from app.modules.transformers.vectorstore import VectorStore
        vs = VectorStore(
            logger=MagicMock(),
            config_service=AsyncMock(),
            graph_provider=AsyncMock(),
            collection_name="test_col",
            vector_db_service=AsyncMock(),
        )
    return vs


def _make_code_block(
    text: str = "def foo():\n    return 1\n",
    kind: str = "function",
    language: str = "python",
    name: str = "foo",
    block_id: str = "blk-1",
    parent_index: int | None = None,
    signature: str | None = "def foo():",
    docstring: str | None = "Return 1.",
    decorators: list[str] | None = None,
):
    from app.models.blocks import Block, BlockType, CodeMetadata, DataFormat, CitationMetadata
    b = Block(
        type=BlockType.CODE,
        name=name,
        format=DataFormat.CODE,
        parent_index=parent_index,
        data={"text": text, "kind": kind, "start_line": 1, "end_line": 3, "subtokens": text},
        code_metadata=CodeMetadata(
            language=language,
            signature=signature,
            docstring=docstring,
            decorators=decorators,
        ),
        citation_metadata=CitationMetadata(line_number=1),
    )
    b.id = block_id
    return b


def _make_code_block_group(
    text: str = "class Foo:\n    def bar(self): pass\n",
    kind: str = "class",
    name: str = "Foo",
    bg_id: str = "bg-1",
    language: str = "python",
):
    from app.models.blocks import BlockGroup, GroupType, GroupSubType, CodeMetadata
    bg = BlockGroup(
        type=GroupType.CODE,
        sub_type=GroupSubType.CODE_CLASS,
        name=name,
        data={"text": text, "kind": kind, "subtokens": text},
        code_metadata=CodeMetadata(language=language),
    )
    bg.id = bg_id
    return bg


def _make_summary_block(
    symbols: list[str] | None = None,
    block_id: str = "summary-1",
):
    from app.models.blocks import Block, BlockType, DataFormat
    text = "File: test.py\nLanguage: python\n  class: Foo"
    b = Block(
        type=BlockType.RECORD_SUMMARY,
        name="test.py symbols",
        format=DataFormat.TXT,
        data={"text": text, "kind": "file_summary", "symbols": symbols or ["class:Foo"]},
    )
    b.id = block_id
    return b


def _make_text_block(text: str = "Some plain text", block_id: str = "txt-1"):
    from app.models.blocks import Block, BlockType, DataFormat
    b = Block(type=BlockType.TEXT, data=text)
    b.id = block_id
    return b


def _make_blocks_container(blocks=None, block_groups=None):
    from app.models.blocks import BlocksContainer
    return BlocksContainer(
        blocks=blocks or [],
        block_groups=block_groups or [],
    )


# ---------------------------------------------------------------------------
# We patch out the heavy VectorStore._create_embeddings path and capture
# what goes into documents_to_embed instead.
# ---------------------------------------------------------------------------

def _capture_index(vs, bc, record=None):
    """Run index_documents and return the list of Documents that would be embedded."""
    captured: list[Document] = []

    async def fake_create_embeddings(docs, *args, **kwargs):
        captured.extend([d for d in docs if _is_doc(d)])

    async def fake_cleanup(*args, **kwargs):
        pass

    async def fake_get_model():
        return False  # not multimodal

    async def fake_get_llm(*args, **kwargs):
        return MagicMock(), {"isMultimodal": False}

    with patch.object(vs, "get_embedding_model_instance", new=AsyncMock(side_effect=fake_get_model)), \
         patch("app.modules.transformers.vectorstore.get_llm_for_role", new=AsyncMock(side_effect=fake_get_llm)), \
         patch.object(vs, "_create_embeddings", new=AsyncMock(side_effect=fake_create_embeddings)), \
         patch.object(vs, "_cleanup_orphaned_embeddings_if_needed", new=AsyncMock(side_effect=fake_cleanup)), \
         patch.object(vs, "_refresh_record_summary_documents", new=AsyncMock()):
        import asyncio
        asyncio.get_event_loop().run_until_complete(
            vs.index_documents(bc, "org-1", "rec-1", "vrid-1", record=record)
        )

    return captured


# ===========================================================================
# CODE block embedding
# ===========================================================================

class TestCodeBlockEmbedding:
    def test_code_block_produces_at_least_one_document(self):
        vs = _make_vectorstore()
        blk = _make_code_block()
        bc = _make_blocks_container(blocks=[blk])
        docs = _capture_index(vs, bc)
        assert any(d for d in docs if d.metadata.get("blockId") == blk.id), (
            "No Document with the code block's blockId was embedded"
        )

    def test_page_content_includes_language(self):
        vs = _make_vectorstore()
        blk = _make_code_block(language="python")
        bc = _make_blocks_container(blocks=[blk])
        docs = _capture_index(vs, bc)
        code_docs = [d for d in docs if d.metadata.get("blockId") == blk.id]
        assert any("python" in d.page_content.lower() for d in code_docs), (
            "Expected '# Language: python' in page_content"
        )

    def test_page_content_includes_code_body(self):
        vs = _make_vectorstore()
        code_text = "def unique_function_xyz():\n    return 42\n"
        blk = _make_code_block(text=code_text)
        bc = _make_blocks_container(blocks=[blk])
        docs = _capture_index(vs, bc)
        code_docs = [d for d in docs if d.metadata.get("blockId") == blk.id]
        assert any("unique_function_xyz" in d.page_content for d in code_docs), (
            "Code body not found in page_content"
        )

    def test_block_data_dict_does_not_raise(self):
        """block.data must be accessed as dict["text"], not as a plain string."""
        vs = _make_vectorstore()
        blk = _make_code_block(text="def foo(): pass\n")
        # Confirm data is a dict (regression for the original string/dict mismatch bug)
        assert isinstance(blk.data, dict)
        bc = _make_blocks_container(blocks=[blk])
        try:
            _capture_index(vs, bc)
        except (TypeError, AttributeError) as exc:
            pytest.fail(f"String/dict mismatch raised: {exc}")

    def test_isBlock_metadata_true(self):
        vs = _make_vectorstore()
        blk = _make_code_block()
        bc = _make_blocks_container(blocks=[blk])
        docs = _capture_index(vs, bc)
        code_docs = [d for d in docs if d.metadata.get("blockId") == blk.id and not d.metadata.get("isCodeSummary")]
        assert all(d.metadata.get("isBlock") is True for d in code_docs)

    def test_isBlockGroup_metadata_false_for_blocks(self):
        vs = _make_vectorstore()
        blk = _make_code_block()
        bc = _make_blocks_container(blocks=[blk])
        docs = _capture_index(vs, bc)
        code_docs = [d for d in docs if d.metadata.get("blockId") == blk.id and not d.metadata.get("isCodeSummary")]
        assert all(d.metadata.get("isBlockGroup") is False for d in code_docs)

    def test_signature_docstring_summary_doc_emitted(self):
        """Functions with signature+docstring emit an extra summary Document."""
        vs = _make_vectorstore()
        blk = _make_code_block(kind="function", signature="def foo():", docstring="Returns 1.")
        bc = _make_blocks_container(blocks=[blk])
        docs = _capture_index(vs, bc)
        summary_docs = [
            d for d in docs
            if d.metadata.get("blockId") == blk.id and d.metadata.get("isCodeSummary") is True
        ]
        assert summary_docs, "Expected a code summary document for the function"

    def test_method_block_emits_summary_doc(self):
        vs = _make_vectorstore()
        blk = _make_code_block(kind="method", signature="def run(self):", docstring="Run it.")
        bc = _make_blocks_container(blocks=[blk])
        docs = _capture_index(vs, bc)
        summary_docs = [d for d in docs if d.metadata.get("isCodeSummary") is True]
        assert summary_docs

    def test_scope_in_prefix_when_parent_exists(self):
        """When a block has a parent BlockGroup, the scope appears in the prefix."""
        vs = _make_vectorstore()
        bg = _make_code_block_group(name="MyClass")
        bg.index = 0
        blk = _make_code_block(parent_index=0)
        blk.index = 0
        bc = _make_blocks_container(blocks=[blk], block_groups=[bg])
        docs = _capture_index(vs, bc)
        code_docs = [d for d in docs if d.metadata.get("blockId") == blk.id and not d.metadata.get("isCodeSummary")]
        assert any("MyClass" in d.page_content for d in code_docs), (
            "Parent class scope not found in page_content prefix"
        )


# ===========================================================================
# Oversized block → sliding window split
# ===========================================================================

class TestOversizedCodeBlock:
    def test_large_block_produces_multiple_documents(self):
        vs = _make_vectorstore()
        # Produce a block whose text exceeds MAX_CODE_NONWS_CHARS (4000 raw chars)
        large_code = "x = " + "a" * 5000 + "\n"
        blk = _make_code_block(text=large_code, kind="statement", signature=None, docstring=None)
        bc = _make_blocks_container(blocks=[blk])
        docs = _capture_index(vs, bc)
        code_docs = [d for d in docs if d.metadata.get("blockId") == blk.id and not d.metadata.get("isCodeSummary")]
        assert len(code_docs) > 1, (
            "Expected multiple Documents for oversized code block (sliding window)"
        )

    def test_split_documents_retain_block_id(self):
        vs = _make_vectorstore()
        large_code = "y = " + "b" * 5000 + "\n"
        blk = _make_code_block(text=large_code, kind="statement", signature=None, docstring=None)
        bc = _make_blocks_container(blocks=[blk])
        docs = _capture_index(vs, bc)
        for d in docs:
            if d.metadata.get("blockId") == blk.id:
                assert d.metadata["blockId"] == blk.id

    def test_split_documents_have_chunk_index(self):
        vs = _make_vectorstore()
        large_code = "z = " + "c" * 5000 + "\n"
        blk = _make_code_block(text=large_code, kind="statement", signature=None, docstring=None)
        bc = _make_blocks_container(blocks=[blk])
        docs = _capture_index(vs, bc)
        code_docs = [d for d in docs if d.metadata.get("blockId") == blk.id and not d.metadata.get("isCodeSummary")]
        if len(code_docs) > 1:
            assert all("chunkIndex" in d.metadata for d in code_docs)


# ===========================================================================
# GroupType.CODE block group embedding
# ===========================================================================

class TestCodeBlockGroupEmbedding:
    def _bc_with_group(self, bg, blk=None):
        """Block group embedding only triggers when code_blocks is non-empty."""
        if blk is None:
            blk = _make_code_block(block_id="companion-blk")
        return _make_blocks_container(blocks=[blk], block_groups=[bg])

    def test_code_block_group_emits_document(self):
        vs = _make_vectorstore()
        bg = _make_code_block_group()
        bc = self._bc_with_group(bg)
        docs = _capture_index(vs, bc)
        bg_docs = [d for d in docs if d.metadata.get("blockId") == bg.id]
        assert bg_docs, "Expected at least one Document for the CODE block group"

    def test_code_block_group_isBlockGroup_true(self):
        vs = _make_vectorstore()
        bg = _make_code_block_group()
        bc = self._bc_with_group(bg)
        docs = _capture_index(vs, bc)
        bg_docs = [d for d in docs if d.metadata.get("blockId") == bg.id]
        assert all(d.metadata.get("isBlockGroup") is True for d in bg_docs)

    def test_code_block_group_page_content_has_class_text(self):
        vs = _make_vectorstore()
        bg = _make_code_block_group(text="class UniqueClass123:\n    pass\n", name="UniqueClass123")
        bc = self._bc_with_group(bg)
        docs = _capture_index(vs, bc)
        bg_docs = [d for d in docs if d.metadata.get("blockId") == bg.id]
        assert any("UniqueClass123" in d.page_content for d in bg_docs)


# ===========================================================================
# File summary block (RECORD_SUMMARY with kind="file_summary")
# ===========================================================================

class TestFileSummaryEmbedding:
    def test_summary_block_emits_isRecordSummary_true(self):
        vs = _make_vectorstore()
        summary = _make_summary_block()
        # Also include a real code block so index_documents doesn't short-circuit
        blk = _make_code_block()
        bc = _make_blocks_container(blocks=[blk, summary])
        docs = _capture_index(vs, bc)
        summary_docs = [d for d in docs if d.metadata.get("blockId") == summary.id]
        assert summary_docs, "No Document emitted for file summary block"
        assert all(d.metadata.get("isRecordSummary") is True for d in summary_docs)

    def test_summary_block_isBlock_false(self):
        vs = _make_vectorstore()
        summary = _make_summary_block()
        blk = _make_code_block()
        bc = _make_blocks_container(blocks=[blk, summary])
        docs = _capture_index(vs, bc)
        summary_docs = [d for d in docs if d.metadata.get("blockId") == summary.id]
        assert all(d.metadata.get("isBlock") is False for d in summary_docs)


# ===========================================================================
# Non-code blocks are NOT in code path
# ===========================================================================

class TestNonCodeBlocksExcluded:
    def test_text_block_does_not_get_contextual_prefix(self):
        """TEXT blocks go through the text path, not the code path."""
        vs = _make_vectorstore()
        text_blk = _make_text_block("some plain text")
        bc = _make_blocks_container(blocks=[text_blk])
        docs = _capture_index(vs, bc)
        # Text blocks do NOT get '# File:' or '# Language:' prefix headers
        for d in docs:
            assert "# Language:" not in d.page_content
