"""Replayed attachments go through admission like every other image source.

Two things were wrong here. `_seed_conversation_history` passed
`image_admission=` to a function that had no such parameter — a TypeError on
any conversation whose history carried an attachment — and the image
extraction underneath checked only the raw `ImageBudget`. History seeding runs
before the turn's own tool calls, so on the budget alone an old image spent the
allowance ahead of the record the user was actually asking about, and skipped
dedup and the per-image downscale on the way.
"""

from __future__ import annotations

import base64
import inspect
import io
from unittest.mock import MagicMock

import pytest

from app.agents.agent_loop.hooks.attachment_resolver import (
    _extract_image_urls_from_record,
    resolve_history_attachments,
)
from app.utils.image_admission import ImageAdmission, ImageBudget
from app.utils.image_policy import resolve_image_policy


def _uri(width: int = 60, height: int = 60) -> str:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (width, height), (200, 30, 30)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def _record(*uris: str) -> dict:
    return {"block_containers": {"blocks": [
        {"index": i, "type": "image", "data": {"uri": u}} for i, u in enumerate(uris)
    ]}}


def _admission(provider: str = "openai") -> ImageAdmission:
    return ImageAdmission(
        resolve_image_policy(provider=provider, is_multimodal=True), budget=ImageBudget(),
    )


class TestTheCallSiteMatches:
    def test_resolve_history_attachments_takes_an_admission(self) -> None:
        """`factory._seed_conversation_history` passes this by keyword; without
        the parameter every history-with-attachments turn raised."""
        assert "image_admission" in inspect.signature(resolve_history_attachments).parameters

    async def test_passing_one_does_not_raise(self) -> None:
        text, images = await resolve_history_attachments(
            [], MagicMock(), "org-1", MagicMock(), {},
            is_multimodal_llm=True, image_budget=ImageBudget(), image_admission=_admission(),
        )

        assert (text, images) == ("", [])

    async def test_the_factory_call_site_still_matches_the_signature(self) -> None:
        """Binds the exact keywords `factory.py` uses."""
        inspect.signature(resolve_history_attachments).bind(
            [], MagicMock(), "org-1", MagicMock(), {},
            is_multimodal_llm=True,
            image_budget=ImageBudget(),
            image_admission=_admission(),
        )


class TestReplayedImagesAreAdmitted:
    def test_the_model_cap_applies_not_just_the_conversation_cap(self) -> None:
        """Ollama takes one image per request; the 50-image conversation
        budget would have let fifty through."""
        record = _record(*[_uri(60 + i, 60) for i in range(5)])

        blocks = _extract_image_urls_from_record(
            record, ImageBudget(), image_admission=_admission("ollama"),
        )

        assert len(blocks) == 1

    def test_the_same_image_twice_takes_one_slot(self) -> None:
        uri = _uri()
        blocks = _extract_image_urls_from_record(
            _record(uri, uri, uri), ImageBudget(), image_admission=_admission(),
        )

        assert len(blocks) == 1

    def test_an_image_admitted_earlier_is_not_replayed_a_second_time(self) -> None:
        """Two history records carrying the same picture, replayed one batch
        each. `admitted_uri` answers "did this win a slot in the request",
        which is still yes for the copy the first batch sent — so emitting on
        that alone attached the same image twice while the cap counted it
        once, and the transport guard then cut a different picture to fit."""
        uri = _uri()
        admission = _admission()

        first = _extract_image_urls_from_record(
            _record(uri), ImageBudget(), image_admission=admission,
        )
        second = _extract_image_urls_from_record(
            _record(uri), ImageBudget(), image_admission=admission,
        )

        assert len(first) == 1
        assert second == []

    def test_replayed_images_debit_the_shared_budget(self) -> None:
        """History and the turn's own tool calls share one ceiling."""
        budget = ImageBudget()
        admission = ImageAdmission(
            resolve_image_policy(provider="openai", is_multimodal=True), budget=budget,
        )

        _extract_image_urls_from_record(_record(_uri(61, 61), _uri(62, 62)), budget,
                                        image_admission=admission)

        assert budget.used == 2

    def test_an_oversized_replayed_image_is_downscaled(self) -> None:
        from app.utils.image_utils import read_image_dimensions

        blocks = _extract_image_urls_from_record(
            _record(_uri(4000, 3000)), ImageBudget(), image_admission=_admission("anthropic"),
        )

        assert read_image_dimensions(blocks[0]["image_url"]["url"]) == (1568, 1176)

    def test_a_text_only_model_replays_no_images(self) -> None:
        admission = ImageAdmission(
            resolve_image_policy(provider="openai", is_multimodal=False), budget=ImageBudget(),
        )

        assert _extract_image_urls_from_record(
            _record(_uri()), ImageBudget(), image_admission=admission,
        ) == []

    def test_without_an_admission_the_budget_still_bounds_it(self) -> None:
        """Callers that predate this keep working — the permissive fallback."""
        budget = ImageBudget(max_images=2)

        blocks = _extract_image_urls_from_record(
            _record(_uri(61, 61), _uri(62, 62), _uri(63, 63)), budget,
        )

        assert len(blocks) == 2


class TestHistoryDoesNotStarveTheCurrentTurn:
    async def test_history_images_do_not_consume_the_whole_cap_first(self) -> None:
        """The ordering that motivated this: seeding runs before any tool call,
        so replayed images used to take every slot before the record the user
        asked about was even fetched."""
        from app.utils.image_admission import ImageCandidate, ImageOrigin

        admission = _admission("openai")  # cap 8
        record = _record(*[_uri(60 + i, 60) for i in range(12)])

        _extract_image_urls_from_record(record, admission.budget, image_admission=admission)

        # The cap is what stops it, not the 50-image conversation budget.
        assert admission.remaining == 0
        fresh = admission.admit([ImageCandidate(
            ref="r", data_uri=_uri(500, 500), origin=ImageOrigin.FETCHED_RECORD,
        )])
        assert fresh.degraded, "a fetched record should be told it lost, not silently dropped"


@pytest.mark.parametrize("origin_name", ["HISTORY"])
def test_replay_uses_the_history_origin(origin_name: str) -> None:
    """Origin is a hard tier in ranking — replayed images must not outrank the
    user's own attachment or a record the model deliberately fetched."""
    from app.utils.image_admission import ImageOrigin

    captured: list = []
    admission = _admission()
    real_admit = admission.admit

    def spy(candidates: list) -> object:
        captured.extend(candidates)
        return real_admit(candidates)

    admission.admit = spy
    _extract_image_urls_from_record(_record(_uri()), ImageBudget(), image_admission=admission)

    assert captured and captured[0].origin is getattr(ImageOrigin, origin_name)


class TestBlocksWithoutAnIndex:
    """`Block.index` defaults to `None` (`models/blocks.py`), and records come
    from blob storage, so an image block with no usable index is representable.
    Keying the admitted lookup on `int(index or 0)` collapsed every such block
    to 0 — emitting one image twice and losing the other.
    """

    @staticmethod
    def _blocks(*uris: str) -> list[dict]:
        return [{"type": "image", "data": {"uri": u}} for u in uris]

    def test_two_unindexed_images_both_survive(self) -> None:
        a, b = _uri(61, 61), _uri(62, 62)

        urls = [
            block["image_url"]["url"]
            for block in _extract_image_urls_from_record(
                {"block_containers": {"blocks": self._blocks(a, b)}},
                ImageBudget(), image_admission=_admission(),
            )
        ]

        assert sorted(urls) == sorted([a, b])

    def test_an_explicitly_null_index_behaves_the_same(self) -> None:
        """What a serialized `Block()` actually carries."""
        a, b = _uri(61, 61), _uri(62, 62)
        blocks = [{"type": "image", "index": None, "data": {"uri": u}} for u in (a, b)]

        urls = [
            block["image_url"]["url"]
            for block in _extract_image_urls_from_record(
                {"block_containers": {"blocks": blocks}},
                ImageBudget(), image_admission=_admission(),
            )
        ]

        assert sorted(urls) == sorted([a, b])

    def test_duplicate_bytes_still_collapse_to_one(self) -> None:
        """Keying by hash must not resurrect the duplicate it replaced."""
        a = _uri(61, 61)

        blocks = _extract_image_urls_from_record(
            {"block_containers": {"blocks": self._blocks(a, a, a)}},
            ImageBudget(), image_admission=_admission(),
        )

        assert len(blocks) == 1

    def test_the_cap_still_applies_without_indices(self) -> None:
        blocks = _extract_image_urls_from_record(
            {"block_containers": {"blocks": self._blocks(*[_uri(60 + i, 60) for i in range(5)])}},
            ImageBudget(), image_admission=_admission("ollama"),
        )

        assert len(blocks) == 1

    def test_indexed_blocks_are_unaffected(self) -> None:
        a, b = _uri(61, 61), _uri(62, 62)
        blocks = [
            {"type": "image", "index": i, "data": {"uri": u}} for i, u in enumerate((a, b))
        ]

        urls = [
            block["image_url"]["url"]
            for block in _extract_image_urls_from_record(
                {"block_containers": {"blocks": blocks}},
                ImageBudget(), image_admission=_admission(),
            )
        ]

        assert urls == [a, b], "document order is preserved"
