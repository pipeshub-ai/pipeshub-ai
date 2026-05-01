"""Unit tests for app.utils.chat_attachment_loader."""

import logging
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import (
    Block,
    BlocksContainer,
    BlockType,
    DataFormat,
)  # noqa: F401  -- DataFormat ensures the module loads cleanly under pytest collection
from app.utils.chat_attachment_loader import load_chat_attachments


def _install_fake_parse_file_module(supported: frozenset[str]):
    """Insert a stub `parse_file` module so the loader can import it lazily.

    The real module pulls in the entire document-parsing stack (bs4,
    html_to_markdown, docling, fitz, etc.), none of which are required for
    these unit tests. Tests provide their own `FileContentParser` substitute
    via :func:`unittest.mock.patch` against the stub.
    """
    module_name = "app.agents.actions.util.parse_file"
    if module_name in sys.modules:
        return
    fake = types.ModuleType(module_name)

    class _StubParser:
        SUPPORTED_EXTENSIONS = supported

        def __init__(self, *args, **kwargs) -> None:
            pass

        async def parse_to_block_container(self, *_args, **_kwargs) -> None:
            return None

    fake.FileContentParser = _StubParser
    sys.modules[module_name] = fake


_install_fake_parse_file_module(frozenset({"md", "pdf", "txt"}))


def _make_blocks_container(texts: list[str]) -> BlocksContainer:
    """Build a minimal BlocksContainer with one TEXT block per entry in `texts`."""
    blocks = [
        Block(
            index=i,
            type=BlockType.TEXT,
            data=t,
            format=DataFormat.TXT,
        )
        for i, t in enumerate(texts)
    ]
    return BlocksContainer(blocks=blocks)


def _make_list_blocks_container(items: list[str]) -> BlocksContainer:
    """Build a BlocksContainer whose TEXT blocks are LIST_ITEM children — i.e.
    each block has `parent_index` set to a list BlockGroup, just like the
    PyMuPDF/Docling PDF parser emits for bullet/numbered list documents.
    """
    blocks = [
        Block(
            index=i,
            type=BlockType.TEXT,
            data=t,
            format=DataFormat.TXT,
            parent_index=0,  # points at a (synthetic) list BlockGroup
        )
        for i, t in enumerate(items)
    ]
    return BlocksContainer(blocks=blocks)


@pytest.mark.asyncio
async def test_load_chat_attachments_returns_empty_for_no_ids() -> None:
    """No documentIds → no work done, no graph writes attempted."""
    blob_store = MagicMock()
    graph_provider = MagicMock()
    config_service = MagicMock()
    logger = logging.getLogger("test")

    flattened, virtual_map, appendix = await load_chat_attachments(
        document_ids=[],
        org_id="org-1",
        user_id="user-1",
        blob_store=blob_store,
        graph_provider=graph_provider,
        config_service=config_service,
        logger=logger,
    )

    assert flattened == []
    assert virtual_map == {}
    assert appendix == ""


@pytest.mark.asyncio
async def test_load_chat_attachments_prompt_only_when_graph_user_missing() -> None:
    """No graph user: still load from storage for prompt; skip FileRecord persistence."""
    blob_store = MagicMock()
    blob_store.get_raw_document_metadata = AsyncMock(
        return_value={
            "_id": "doc-1",
            "documentName": "note",
            "extension": ".md",
            "mimeType": "text/markdown",
            "sizeInBytes": 10,
        }
    )
    blob_store.get_raw_document_bytes = AsyncMock(return_value=b"# Hello\n")

    graph_provider = MagicMock()
    graph_provider.get_user_by_user_id = AsyncMock(return_value=None)
    graph_provider.batch_upsert_nodes = AsyncMock()
    graph_provider.batch_create_edges = AsyncMock()

    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={})
    logger = logging.getLogger("test")

    parsed = _make_blocks_container(["# Hello"])

    with patch(
        "app.agents.actions.util.parse_file.FileContentParser"
    ) as parser_cls:
        parser_cls.SUPPORTED_EXTENSIONS = frozenset({"md", "pdf", "txt"})
        parser_instance = parser_cls.return_value
        parser_instance.parse_to_block_container = AsyncMock(return_value=parsed)

        flattened, virtual_map, appendix = await load_chat_attachments(
            document_ids=["doc-1"],
            org_id="org-1",
            user_id="missing-user",
            blob_store=blob_store,
            graph_provider=graph_provider,
            config_service=config_service,
            logger=logger,
        )

    blob_store.get_raw_document_metadata.assert_awaited()
    blob_store.get_raw_document_bytes.assert_awaited()
    graph_provider.batch_upsert_nodes.assert_not_awaited()
    graph_provider.batch_create_edges.assert_not_awaited()

    assert flattened == []
    assert virtual_map == {}
    assert "# Hello" in appendix


@pytest.mark.asyncio
async def test_load_chat_attachments_happy_path_persists_record_and_prompt_markdown() -> None:
    """Successful path: graph writes happen; prompt appendix contains parsed text."""
    blob_store = MagicMock()
    blob_store.get_raw_document_metadata = AsyncMock(
        return_value={
            "_id": "abc",
            "documentName": "design",
            "extension": ".md",
            "mimeType": "text/markdown",
            "sizeInBytes": 42,
        }
    )
    blob_store.get_raw_document_bytes = AsyncMock(return_value=b"# Heading\n\nbody text")

    graph_provider = MagicMock()
    graph_provider.get_user_by_user_id = AsyncMock(
        return_value={"_key": "user_key_1"}
    )
    graph_provider.batch_upsert_nodes = AsyncMock()
    graph_provider.batch_create_edges = AsyncMock()

    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={})
    logger = logging.getLogger("test")

    parsed = _make_blocks_container(["# Heading", "body text"])

    with patch(
        "app.agents.actions.util.parse_file.FileContentParser"
    ) as parser_cls:
        parser_cls.SUPPORTED_EXTENSIONS = frozenset({"md", "pdf", "txt"})
        parser_instance = parser_cls.return_value
        parser_instance.parse_to_block_container = AsyncMock(return_value=parsed)

        flattened, virtual_map, appendix = await load_chat_attachments(
            document_ids=["doc-1", "doc-1"],  # duplicate to confirm dedup
            org_id="org-1",
            user_id="user-ext-1",
            blob_store=blob_store,
            graph_provider=graph_provider,
            config_service=config_service,
            logger=logger,
        )

    # Dedup: parser called once, not twice.
    assert parser_instance.parse_to_block_container.await_count == 1

    # Graph writes happened: records, files, permission, isOfType (4 calls total).
    assert graph_provider.batch_upsert_nodes.await_count == 2
    assert graph_provider.batch_create_edges.await_count == 2

    assert flattened == []
    assert virtual_map == {}
    assert "design.md" in appendix
    assert "# Heading" in appendix
    assert "body text" in appendix


@pytest.mark.asyncio
async def test_load_chat_attachments_includes_list_item_blocks() -> None:
    """PDFs of bullet/numbered lists emit TEXT blocks with `parent_index` set
    to the enclosing list BlockGroup. Those must still flow into the prompt
    appendix, otherwise list-heavy PDFs produce zero text."""
    blob_store = MagicMock()
    blob_store.get_raw_document_metadata = AsyncMock(
        return_value={
            "_id": "doc-list",
            "documentName": "10_jokes",
            "extension": ".pdf",
            "mimeType": "application/pdf",
            "sizeInBytes": 100,
        }
    )
    blob_store.get_raw_document_bytes = AsyncMock(return_value=b"%PDF-fakebytes")

    graph_provider = MagicMock()
    graph_provider.get_user_by_user_id = AsyncMock(return_value={"_key": "uk1"})
    graph_provider.batch_upsert_nodes = AsyncMock()
    graph_provider.batch_create_edges = AsyncMock()

    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={})
    logger = logging.getLogger("test")

    parsed = _make_list_blocks_container(
        ["Why did the chicken cross the road?", "To get to the other side."]
    )

    with patch(
        "app.agents.actions.util.parse_file.FileContentParser"
    ) as parser_cls:
        parser_cls.SUPPORTED_EXTENSIONS = frozenset({"md", "pdf", "txt"})
        parser_instance = parser_cls.return_value
        parser_instance.parse_to_block_container = AsyncMock(return_value=parsed)

        flattened, virtual_map, appendix = await load_chat_attachments(
            document_ids=["doc-list"],
            org_id="org-1",
            user_id="user-1",
            blob_store=blob_store,
            graph_provider=graph_provider,
            config_service=config_service,
            logger=logger,
        )

    assert flattened == []
    assert virtual_map == {}
    assert "Why did the chicken cross the road?" in appendix
    assert "To get to the other side." in appendix
    assert "10_jokes.pdf" in appendix


@pytest.mark.asyncio
async def test_load_chat_attachments_skips_oversized_file() -> None:
    """A file with `sizeInBytes` greater than the cap is dropped without download."""
    blob_store = MagicMock()
    blob_store.get_raw_document_metadata = AsyncMock(
        return_value={
            "_id": "huge",
            "documentName": "huge",
            "extension": ".pdf",
            "mimeType": "application/pdf",
            "sizeInBytes": 10 * 1024 * 1024 * 1024,  # 10 GB
        }
    )
    blob_store.get_raw_document_bytes = AsyncMock(return_value=b"x")

    graph_provider = MagicMock()
    graph_provider.get_user_by_user_id = AsyncMock(
        return_value={"_key": "user_key_1"}
    )
    graph_provider.batch_upsert_nodes = AsyncMock()
    graph_provider.batch_create_edges = AsyncMock()

    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={})
    logger = logging.getLogger("test")

    flattened, virtual_map, appendix = await load_chat_attachments(
        document_ids=["doc-huge"],
        org_id="org-1",
        user_id="user-ext-1",
        blob_store=blob_store,
        graph_provider=graph_provider,
        config_service=config_service,
        logger=logger,
    )

    assert flattened == []
    assert virtual_map == {}
    assert appendix == ""
    blob_store.get_raw_document_bytes.assert_not_called()
    graph_provider.batch_upsert_nodes.assert_not_called()


@pytest.mark.asyncio
async def test_load_chat_attachments_skips_unsupported_extension() -> None:
    """An unsupported extension never reaches the parser or the graph."""
    blob_store = MagicMock()
    blob_store.get_raw_document_metadata = AsyncMock(
        return_value={
            "_id": "weird",
            "documentName": "binary",
            "extension": ".bin",
            "mimeType": "application/octet-stream",
            "sizeInBytes": 10,
        }
    )
    blob_store.get_raw_document_bytes = AsyncMock(return_value=b"x")

    graph_provider = MagicMock()
    graph_provider.get_user_by_user_id = AsyncMock(
        return_value={"_key": "user_key_1"}
    )
    graph_provider.batch_upsert_nodes = AsyncMock()
    graph_provider.batch_create_edges = AsyncMock()

    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={})
    logger = logging.getLogger("test")

    flattened, virtual_map, appendix = await load_chat_attachments(
        document_ids=["doc-bin"],
        org_id="org-1",
        user_id="user-ext-1",
        blob_store=blob_store,
        graph_provider=graph_provider,
        config_service=config_service,
        logger=logger,
    )

    assert flattened == []
    assert virtual_map == {}
    assert appendix == ""
    graph_provider.batch_upsert_nodes.assert_not_called()
    blob_store.get_raw_document_bytes.assert_not_called()
