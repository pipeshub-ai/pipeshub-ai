"""`SinkOrchestrator` image-description wiring.

The whole reason this step lives in the orchestrator is ordering: the record is
written to blob storage before the vector store runs, and the stored record is
what `fetch_record` serves at query time. A description generated any later
would never reach it.
"""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import Connectors
from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat, ImageMetadata
from app.models.entities import MimeTypes, OriginTypes, Record, RecordType
from app.modules.transformers.sink_orchestrator import SinkOrchestrator
from app.modules.transformers.transformer import TransformContext


def _make_orchestrator(*, previous_record=None):
    blob_storage = AsyncMock()
    blob_storage.get_record_from_storage = AsyncMock(return_value=previous_record)
    vector_store = AsyncMock()
    vector_store.apply = AsyncMock(return_value=True)
    graph_provider = AsyncMock()
    graph_provider.get_document = AsyncMock(return_value={"indexingStatus": "NOT_STARTED"})
    graph_provider.batch_upsert_nodes = AsyncMock(return_value=True)
    orchestrator = SinkOrchestrator(
        graphdb=AsyncMock(),
        blob_storage=blob_storage,
        vector_store=vector_store,
        graph_provider=graph_provider,
        logger=logging.getLogger("test-sink-images"),
        config_service=MagicMock(),
    )
    orchestrator._save_reconciliation_metadata = AsyncMock()
    orchestrator._update_indexing_status = AsyncMock()
    return orchestrator


def _image_block(index: int = 0, description: str | None = None) -> Block:
    return Block(
        index=index,
        type=BlockType.IMAGE,
        format=DataFormat.BASE64,
        data={"uri": f"data:image/png;base64,AAAA{index}"},
        image_metadata=ImageMetadata(description=description) if description else None,
    )


def _ctx(*blocks: Block) -> TransformContext:
    record = Record(
        id="rec-1",
        org_id="org-1",
        virtual_record_id="vr-1",
        record_name="report.pdf",
        record_type=RecordType.FILE,
        external_record_id="ext-1",
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id="conn-1",
        mime_type=MimeTypes.PDF.value,
        block_containers=BlocksContainer(blocks=list(blocks), block_groups=[]),
    )
    return TransformContext(record=record, settings={})


class TestDescribeBeforeStoring:
    async def test_images_are_described_before_the_record_is_stored(self) -> None:
        """The ordering the whole design depends on."""
        orchestrator = _make_orchestrator()
        order: list[str] = []
        orchestrator.image_describer.annotate = AsyncMock(
            side_effect=lambda *a, **kw: order.append("describe") or 1,
        )
        orchestrator.blob_storage.apply = AsyncMock(
            side_effect=lambda ctx: order.append("store"),
        )

        await orchestrator.index(_ctx(_image_block()))

        assert order == ["describe", "store"]

    async def test_a_record_without_images_skips_the_pass(self) -> None:
        orchestrator = _make_orchestrator()
        orchestrator.image_describer.annotate = AsyncMock(return_value=0)
        text_block = Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="hi")

        await orchestrator.index(_ctx(text_block))

        orchestrator.image_describer.annotate.assert_not_awaited()

    async def test_a_failing_description_pass_does_not_stop_indexing(self) -> None:
        """`ImageDescriber.annotate` swallows its own failures; this asserts the
        orchestrator does not reintroduce one."""
        orchestrator = _make_orchestrator()
        orchestrator.image_describer.annotate = AsyncMock(return_value=0)

        await orchestrator.index(_ctx(_image_block()))

        orchestrator.blob_storage.apply.assert_awaited_once()
        orchestrator.vector_store.apply.assert_awaited_once()


class TestInheritingPreviousDescriptions:
    async def test_a_small_record_does_not_fetch_its_previous_version(self) -> None:
        """The fetch pulls a whole record's base64 payload; below a handful of
        images, describing outright is cheaper."""
        orchestrator = _make_orchestrator()
        orchestrator.image_describer.annotate = AsyncMock(return_value=1)

        await orchestrator.index(_ctx(_image_block(0), _image_block(1)))

        orchestrator.blob_storage.get_record_from_storage.assert_not_awaited()

    async def test_an_image_heavy_record_inherits_what_it_can(self) -> None:
        previous = {
            "block_containers": {
                "blocks": [{
                    "type": BlockType.IMAGE.value,
                    "data": {"uri": "data:image/png;base64,AAAA0"},
                    "image_metadata": {"description": "carried forward"},
                }],
            },
        }
        orchestrator = _make_orchestrator(previous_record=previous)
        orchestrator.image_describer.annotate = AsyncMock(return_value=1)

        await orchestrator.index(_ctx(*(_image_block(i) for i in range(6))))

        orchestrator.blob_storage.get_record_from_storage.assert_awaited_once()
        inherited = orchestrator.image_describer.annotate.await_args.kwargs["inherited"]
        assert "carried forward" in inherited.values()

    async def test_an_unreadable_previous_version_is_not_fatal(self) -> None:
        orchestrator = _make_orchestrator()
        orchestrator.blob_storage.get_record_from_storage = AsyncMock(
            side_effect=RuntimeError("storage down"),
        )
        orchestrator.image_describer.annotate = AsyncMock(return_value=0)

        await orchestrator.index(_ctx(*(_image_block(i) for i in range(6))))

        assert orchestrator.image_describer.annotate.await_args.kwargs["inherited"] == {}
        orchestrator.blob_storage.apply.assert_awaited_once()


@pytest.mark.parametrize("skip_blob", [True, False])
async def test_skip_blob_skips_the_description_pass_too(skip_blob: bool) -> None:
    """Nothing is being stored, so there is nothing to describe for."""
    orchestrator = _make_orchestrator()
    orchestrator.image_describer.annotate = AsyncMock(return_value=1)
    ctx = _ctx(_image_block())
    ctx.settings = {"skip_blob": skip_blob}

    await orchestrator.index(ctx)

    assert orchestrator.image_describer.annotate.await_count == (0 if skip_blob else 1)
