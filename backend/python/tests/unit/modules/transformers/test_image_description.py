"""`app/modules/transformers/image_description.py` — the vision pass that gives
every image block words.

The contract: prose lands on `image_metadata.description` before the record is
stored, without ever failing the record and without paying twice for an image
that has not changed.
"""

from __future__ import annotations

import base64
import io
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat, ImageMetadata
from app.modules.transformers.image_description import (
    MAX_IMAGES_ENV_VAR,
    ImageDescriber,
    harvest_descriptions,
)


def _png(width: int = 800, height: int = 600, seed: int = 0) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (width, height), (seed * 11 % 256, 40, 90)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _image_block(index: int = 0, uri: str | None = None, description: str | None = None) -> Block:
    return Block(
        index=index,
        type=BlockType.IMAGE,
        format=DataFormat.BASE64,
        data={"uri": uri if uri is not None else _png(seed=index)},
        image_metadata=ImageMetadata(description=description) if description else None,
    )


def _container(*blocks: Block) -> BlocksContainer:
    return BlocksContainer(blocks=list(blocks), block_groups=[])


def _describer(response: str = "A bar chart of Q3 revenue", *, multimodal: bool = True) -> tuple:
    """Returns (describer, vlm_mock) with the indexing model stubbed."""
    vlm = MagicMock()
    vlm.ainvoke = AsyncMock(return_value=MagicMock(content=response))
    describer = ImageDescriber(logging.getLogger("test"), MagicMock())
    patcher = patch(
        "app.utils.llm.get_llm_for_role",
        new=AsyncMock(return_value=(vlm, {"isMultimodal": multimodal})),
    )
    return describer, vlm, patcher


class TestAnnotate:
    async def test_writes_a_description_onto_each_image_block(self) -> None:
        describer, vlm, patcher = _describer()
        container = _container(_image_block(0), _image_block(1))

        with patcher:
            written = await describer.annotate(container)

        assert written == 2
        assert all(b.image_metadata.description == "A bar chart of Q3 revenue" for b in container.blocks)
        assert vlm.ainvoke.await_count == 2

    async def test_blocks_that_already_have_prose_are_left_alone(self) -> None:
        """The record is written to blob twice (index, then enrichment); the
        second pass must be free."""
        describer, vlm, patcher = _describer()
        container = _container(_image_block(0, description="already described"))

        with patcher:
            written = await describer.annotate(container)

        assert written == 0
        assert vlm.ainvoke.await_count == 0
        assert container.blocks[0].image_metadata.description == "already described"

    async def test_page_furniture_is_not_described(self) -> None:
        """Most images in a report are logos and rules; paying a vision call
        for each is the cost this pass has to avoid."""
        describer, vlm, patcher = _describer()
        container = _container(
            _image_block(0, uri=_png(32, 32)),        # icon
            _image_block(1, uri=_png(1200, 40)),      # divider
            _image_block(2, uri=_png(900, 700)),      # a real figure
        )

        with patcher:
            written = await describer.annotate(container)

        assert written == 1
        assert vlm.ainvoke.await_count == 1
        assert container.blocks[0].image_metadata is None
        assert container.blocks[2].image_metadata.description

    async def test_a_text_only_indexing_model_describes_nothing(self) -> None:
        describer, vlm, patcher = _describer(multimodal=False)
        container = _container(_image_block(0))

        with patcher:
            written = await describer.annotate(container)

        assert written == 0
        assert vlm.ainvoke.await_count == 0

    async def test_one_failed_image_does_not_lose_the_others(self) -> None:
        describer, vlm, patcher = _describer()
        vlm.ainvoke = AsyncMock(side_effect=[RuntimeError("vision down"), MagicMock(content="ok")])
        container = _container(_image_block(0), _image_block(1))

        with patcher:
            written = await describer.annotate(container)

        assert written == 1

    async def test_a_broken_model_never_fails_the_record(self) -> None:
        """An undescribed record is still worth indexing."""
        describer = ImageDescriber(logging.getLogger("test"), MagicMock())
        container = _container(_image_block(0))

        with patch(
            "app.utils.llm.get_llm_for_role",
            new=AsyncMock(side_effect=RuntimeError("no model configured")),
        ):
            assert await describer.annotate(container) == 0

    async def test_records_without_images_do_no_work(self) -> None:
        describer, vlm, patcher = _describer()
        text_block = Block(index=0, type=BlockType.TEXT, format=DataFormat.TXT, data="hello")

        with patcher:
            assert await describer.annotate(_container(text_block)) == 0
        assert vlm.ainvoke.await_count == 0

    async def test_empty_container_is_not_an_error(self) -> None:
        describer, _vlm, patcher = _describer()
        with patcher:
            assert await describer.annotate(None) == 0
            assert await describer.annotate(_container()) == 0

    async def test_an_empty_response_is_not_written(self) -> None:
        describer, _vlm, patcher = _describer(response="   ")
        container = _container(_image_block(0))

        with patcher:
            assert await describer.annotate(container) == 0
        assert container.blocks[0].image_metadata is None


class TestPerRecordCap:
    async def test_the_cap_bounds_a_pathological_record(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv(MAX_IMAGES_ENV_VAR, "3")
        describer, vlm, patcher = _describer()
        container = _container(*(_image_block(i) for i in range(10)))

        with patcher:
            written = await describer.annotate(container)

        assert written == 3
        assert vlm.ainvoke.await_count == 3

    async def test_a_zero_cap_disables_the_pass(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(MAX_IMAGES_ENV_VAR, "0")
        describer, vlm, patcher = _describer()

        with patcher:
            assert await describer.annotate(_container(_image_block(0))) == 0
        assert vlm.ainvoke.await_count == 0


class TestInheritance:
    async def test_unchanged_images_reuse_the_previous_versions_prose(self) -> None:
        """Connector re-syncs re-parse documents that have not changed. Paying
        again for those descriptions is the largest avoidable cost here."""
        uri = _png(seed=7)
        previous = {
            "block_containers": {
                "blocks": [{
                    "type": BlockType.IMAGE.value,
                    "data": {"uri": uri},
                    "image_metadata": {"description": "carried forward"},
                }],
            },
        }
        describer, vlm, patcher = _describer()
        container = _container(_image_block(0, uri=uri))

        with patcher:
            written = await describer.annotate(container, inherited=harvest_descriptions(previous))

        assert written == 1
        assert vlm.ainvoke.await_count == 0, "no vision call for an unchanged image"
        assert container.blocks[0].image_metadata.description == "carried forward"

    async def test_changed_images_are_described_afresh(self) -> None:
        previous = {
            "block_containers": {
                "blocks": [{
                    "type": BlockType.IMAGE.value,
                    "data": {"uri": _png(seed=1)},
                    "image_metadata": {"description": "the old figure"},
                }],
            },
        }
        describer, vlm, patcher = _describer()
        container = _container(_image_block(0, uri=_png(seed=2)))

        with patcher:
            await describer.annotate(container, inherited=harvest_descriptions(previous))

        assert vlm.ainvoke.await_count == 1
        assert container.blocks[0].image_metadata.description == "A bar chart of Q3 revenue"

    @pytest.mark.parametrize(
        "record",
        [None, {}, {"block_containers": None}, {"block_containers": {"blocks": ["junk"]}}],
    )
    def test_harvest_tolerates_whatever_storage_returns(self, record) -> None:
        assert harvest_descriptions(record) == {}

    def test_harvest_ignores_blocks_with_no_description(self) -> None:
        record = {
            "block_containers": {
                "blocks": [
                    {"type": BlockType.IMAGE.value, "data": {"uri": "u1"}},
                    {"type": BlockType.IMAGE.value, "data": {"uri": "u2"},
                     "image_metadata": {"description": "  "}},
                    {"type": BlockType.TEXT.value, "data": "text"},
                ],
            },
        }
        assert harvest_descriptions(record) == {}
