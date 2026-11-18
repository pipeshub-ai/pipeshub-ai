"""An image of a record is described once: a retry, a replay or a re-update reuses the stored description."""

from __future__ import annotations

import base64
import io
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.blocks import Block, BlocksContainer, BlockType, DataFormat
from app.modules.transformers import image_description
from app.modules.transformers.image_description import ImageDescriber
from app.services.paid_cache.cache import PaidResultCache
from tests.support.paid_cache import CountingModel, MemoryResultStore, record_in_flight

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG, REC = "org-1", "rec-1"


def _png(seed: int) -> str:
    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", (800, 600), (seed * 11 % 256, 40, 90)).save(buffer, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()


def _record(n: int) -> BlocksContainer:
    blocks = [
        Block(index=i, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": _png(i)}) for i in range(n)
    ]
    return BlocksContainer(blocks=blocks, block_groups=[])


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> Iterator[MemoryResultStore]:
    memory = MemoryResultStore()
    cache = PaidResultCache(memory)
    monkeypatch.setattr("app.services.paid_cache.paid_cache_for", lambda *_a, **_k: cache)
    monkeypatch.setattr("app.services.paid_cache.access.paid_cache_for", lambda *_a, **_k: cache)
    yield memory


async def _annotate(model: CountingModel, container: BlocksContainer) -> int:
    describer = ImageDescriber(logging.getLogger("test"), MagicMock())
    with patch("app.utils.llm.get_llm_for_role", new=AsyncMock(return_value=(model, {"isMultimodal": True}))):
        return await describer.annotate(container)


@pytest.mark.parametrize("images", [1, 3, 4, 5, 12])
async def test_cached_images_are_not_described_again(store: MemoryResultStore, images: int) -> None:
    model = CountingModel("vision")
    with record_in_flight(ORG, REC):
        first = _record(images)
        await _annotate(model, first)
        # The same record is indexed again (a retry, a replay, a re-update): fresh blocks, no descriptions.
        second = _record(images)
        await _annotate(model, second)

    assert model.total == images
    assert model.duplicates == 0
    assert [b.image_metadata.description for b in second.blocks] == [b.image_metadata.description for b in first.blocks]


async def test_a_new_record_with_the_same_images_pays_for_its_own(store: MemoryResultStore) -> None:
    model = CountingModel("vision")
    with record_in_flight(ORG, "rec-1"):
        await _annotate(model, _record(3))
    with record_in_flight(ORG, "rec-2"):
        await _annotate(model, _record(3))
    with record_in_flight("org-2", "rec-1"):
        await _annotate(model, _record(3))

    assert model.total == 9


async def test_a_changed_image_is_described_and_the_unchanged_ones_are_not(store: MemoryResultStore) -> None:
    model = CountingModel("vision")
    with record_in_flight(ORG, REC):
        await _annotate(model, _record(3))
        edited = _record(3)
        edited.blocks[1].data = {"uri": _png(99)}
        await _annotate(model, edited)

    assert model.total == 4


async def test_an_empty_or_truncated_description_is_not_stored(store: MemoryResultStore) -> None:
    with record_in_flight(ORG, REC):
        await _annotate(CountingModel("vision", reply=""), _record(1))
        await _annotate(CountingModel("vision", metadata={"finish_reason": "length"}), _record(1))
        await _annotate(CountingModel("vision", metadata={"done_reason": "length"}), _record(1))

    assert store.results(ORG, REC) == []


async def test_without_a_record_in_flight_every_image_is_described_as_before(store: MemoryResultStore) -> None:
    model = CountingModel("vision")
    await _annotate(model, _record(2))
    await _annotate(model, _record(2))

    assert model.total == 4
    assert store.files == {}


async def test_a_storage_outage_never_fails_the_description(store: MemoryResultStore) -> None:
    store.failing = {"get", "put", "replace"}
    model = CountingModel("vision")
    with record_in_flight(ORG, REC):
        container = _record(2)
        written = await _annotate(model, container)

    assert written == 2
    assert all(b.image_metadata.description for b in container.blocks)


def test_the_module_still_exports_the_describer() -> None:
    assert image_description.ImageDescriber is ImageDescriber


async def test_a_remote_image_url_is_described_again_not_reused(store: MemoryResultStore) -> None:
    """The bytes behind a URL can change, so a stored description of the earlier image is never handed back."""
    model = CountingModel("vision")

    def remote() -> BlocksContainer:
        block = Block(index=0, type=BlockType.IMAGE, format=DataFormat.BASE64, data={"uri": "https://cdn.test/a.png"})
        return BlocksContainer(blocks=[block], block_groups=[])

    with record_in_flight(ORG, REC):
        await _annotate(model, remote())
        await _annotate(model, remote())

    assert model.total == 2
    assert store.paths(ORG, REC) == []
