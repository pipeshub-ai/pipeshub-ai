"""`app/utils/image_admission.py` — which images get pixels, and which degrade
to text.

The invariant every renderer depends on is that nothing vanishes: `admit()`
returns every candidate, either admitted or degraded with a reason.
"""

from __future__ import annotations

import base64
import io
from unittest.mock import patch

import pytest

from app.utils.image_admission import (
    MAX_IMAGES_IN_CONVERSATION,
    DegradeReason,
    ImageAdmission,
    ImageBudget,
    ImageCandidate,
    ImageOrigin,
    admission_from_state,
)
from app.utils.image_policy import permissive_policy, resolve_image_policy

_PNG_1PX = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk"
    "+A8AAQUBAScY42YAAAAASUVORK5CYII="
)


def _uri(seed: int) -> str:
    """A distinct data URI per seed, so candidates hash differently."""
    return f"{_PNG_1PX}#{seed}"


def _candidate(
    seed: int = 0,
    *,
    origin: ImageOrigin = ImageOrigin.SEARCH_HIT,
    width: int | None = 800,
    height: int | None = 600,
    relevance: float = 0.0,
    data_uri: str | None = None,
    block_index: int | None = None,
) -> ImageCandidate:
    return ImageCandidate(
        ref=f"ref{seed}",
        data_uri=data_uri if data_uri is not None else _uri(seed),
        origin=origin,
        relevance=relevance,
        block_index=seed if block_index is None else block_index,
        width=width,
        height=height,
    )


def _admission(max_images: int = 10, budget: ImageBudget | None = None) -> ImageAdmission:
    return ImageAdmission(permissive_policy(max_images), budget=budget)


class TestNothingVanishes:
    def test_every_candidate_comes_back_admitted_or_degraded(self) -> None:
        candidates = [_candidate(i) for i in range(25)]
        result = _admission(max_images=8).admit(candidates)
        assert result.total == len(candidates)
        assert len(result.admitted) == 8
        assert {c.ref for c in result.admitted} | {
            d.candidate.ref for d in result.degraded
        } == {c.ref for c in candidates}

    def test_text_only_model_degrades_everything_with_that_reason(self) -> None:
        admission = ImageAdmission(resolve_image_policy(provider="openAI", is_multimodal=False))
        result = admission.admit([_candidate(0), _candidate(1)])
        assert not result.admitted
        assert all(d.reason is DegradeReason.TEXT_ONLY_MODEL for d in result.degraded)

    def test_empty_input_is_not_an_error(self) -> None:
        assert _admission().admit([]).total == 0


class TestCaps:
    def test_admits_no_more_than_the_model_accepts(self) -> None:
        admission = ImageAdmission(resolve_image_policy(provider="ollama", is_multimodal=True))
        result = admission.admit([_candidate(i) for i in range(5)])
        assert len(result.admitted) == 1
        assert all(d.reason is DegradeReason.OVER_REQUEST_CAP for d in result.degraded)

    def test_conversation_ceiling_binds_when_it_is_tighter(self) -> None:
        budget = ImageBudget(max_images=2)
        admission = _admission(max_images=10, budget=budget)
        result = admission.admit([_candidate(i) for i in range(4)])
        assert len(result.admitted) == 2
        assert {d.reason for d in result.degraded} == {DegradeReason.OVER_CONVERSATION_CAP}

    def test_cap_applies_across_separate_calls(self) -> None:
        """Slots are per request, not per render: a second tool call cannot
        start the allowance over."""
        admission = _admission(max_images=3)
        first = admission.admit([_candidate(i) for i in range(2)])
        second = admission.admit([_candidate(i) for i in range(10, 14)])
        assert len(first.admitted) == 2
        assert len(second.admitted) == 1

    def test_exhausted_budget_reports_capacity_not_size(self) -> None:
        """With no slots left there is nothing to choose between, so the
        reason must name the ceiling rather than the image."""
        budget = ImageBudget(max_images=1)
        budget.try_consume(1)
        result = _admission(budget=budget).admit([_candidate(0, width=10, height=10)])
        assert [d.reason for d in result.degraded] == [DegradeReason.OVER_CONVERSATION_CAP]


class TestPrefilter:
    def test_icons_rules_and_thumbnails_lose_to_real_figures(self) -> None:
        figures = [_candidate(i, width=1200, height=900) for i in range(3)]
        junk = [
            _candidate(10, width=32, height=32),      # icon
            _candidate(11, width=1200, height=40),    # divider
            _candidate(12, width=60, height=200),     # narrow strip
        ]
        result = _admission(max_images=3).admit(junk + figures)
        assert {c.ref for c in result.admitted} == {c.ref for c in figures}
        assert {d.reason for d in result.degraded} <= {
            DegradeReason.TOO_SMALL, DegradeReason.DECORATIVE,
        }

    def test_prefilter_does_not_run_when_everything_fits(self) -> None:
        """A record whose one image is a small diagram must still be sent --
        the filter exists to stop crowding, and nothing is crowded here."""
        result = _admission(max_images=8).admit([_candidate(0, width=40, height=40)])
        assert len(result.admitted) == 1

    def test_unmeasured_images_are_never_filtered_out(self) -> None:
        """Most images arrive unmeasured; dropping them on a size rule would
        lose real figures with no trace."""
        unmeasured = [_candidate(i, width=None, height=None) for i in range(4)]
        result = _admission(max_images=2).admit(unmeasured)
        assert len(result.admitted) == 2
        assert all(d.reason is DegradeReason.OVER_REQUEST_CAP for d in result.degraded)

    def test_a_users_own_small_attachment_is_never_filtered(self) -> None:
        """Someone who uploads a 40x40 image is asking about that image."""
        attachment = _candidate(0, origin=ImageOrigin.ATTACHMENT, width=40, height=40)
        crowd = [_candidate(i, width=1200, height=900) for i in range(1, 6)]
        result = _admission(max_images=3).admit([*crowd, attachment])
        assert attachment.ref in {c.ref for c in result.admitted}


class TestDeduplication:
    def test_the_same_image_twice_costs_one_slot(self) -> None:
        same = _uri(1)
        result = _admission(max_images=8).admit([
            _candidate(0, data_uri=same), _candidate(1, data_uri=same),
        ])
        assert len(result.admitted) == 1
        assert [d.reason for d in result.degraded] == [DegradeReason.DUPLICATE]

    def test_a_repeated_logo_cannot_starve_the_figures(self) -> None:
        logo = _uri(99)
        candidates = [_candidate(i, data_uri=logo, width=300, height=300) for i in range(20)]
        candidates += [_candidate(50 + i, width=1600, height=1200) for i in range(3)]
        result = _admission(max_images=4).admit(candidates)
        assert sum(c.data_uri == logo for c in result.admitted) == 1
        assert len(result.admitted) == 4

    def test_re_admitting_an_image_is_a_duplicate_not_a_second_copy(self) -> None:
        """Re-fetching a record must not spend a second slot -- and must not
        hand back another copy to attach. Callers materialize whatever they
        are given, so a second admission put the same picture on the wire
        twice while the cap still counted it once."""
        admission = _admission(max_images=2)
        first = admission.admit([_candidate(0)])
        assert len(first.admitted) == 1
        assert admission.budget.used == 1

        again = admission.admit([_candidate(0)])
        assert again.admitted == []
        assert [d.reason for d in again.degraded] == [DegradeReason.DUPLICATE]
        assert admission.budget.used == 1, "no second charge for the same image"

    def test_a_duplicate_does_not_release_the_slot_it_reuses(self) -> None:
        """The first copy still holds the slot: a duplicate must not look like
        a free one and let a later image over the cap."""
        admission = _admission(max_images=1)
        admission.admit([_candidate(0)])
        admission.admit([_candidate(0)])

        later = admission.admit([_candidate(1)])

        assert later.admitted == []
        assert [d.reason for d in later.degraded] == [DegradeReason.OVER_REQUEST_CAP]


class TestRanking:
    def test_origin_outranks_relevance_and_size(self) -> None:
        attachment = _candidate(0, origin=ImageOrigin.ATTACHMENT, width=100, height=100)
        search_hit = _candidate(1, origin=ImageOrigin.SEARCH_HIT, width=4000, height=3000, relevance=0.99)
        result = _admission(max_images=1).admit([search_hit, attachment])
        assert [c.ref for c in result.admitted] == [attachment.ref]

    def test_relevance_decides_between_peers(self) -> None:
        low = _candidate(0, relevance=0.1, width=2000, height=2000)
        high = _candidate(1, relevance=0.9, width=800, height=600)
        result = _admission(max_images=1).admit([low, high])
        assert [c.ref for c in result.admitted] == [high.ref]

    def test_size_breaks_ties_between_equally_relevant_images(self) -> None:
        small = _candidate(0, width=200, height=200)
        large = _candidate(1, width=1600, height=1200)
        result = _admission(max_images=1).admit([small, large])
        assert [c.ref for c in result.admitted] == [large.ref]

    def test_selection_is_deterministic_for_identical_keys(self) -> None:
        candidates = [_candidate(i, width=800, height=600) for i in range(6)]
        first = _admission(max_images=3).admit(candidates)
        second = _admission(max_images=3).admit(candidates)
        assert [c.ref for c in first.admitted] == [c.ref for c in second.admitted]

    def test_admitted_images_come_back_in_document_order(self) -> None:
        """Ranking decides membership; presentation follows the record, so
        refs and reading order still line up."""
        candidates = [
            _candidate(5, relevance=0.9, block_index=5),
            _candidate(1, relevance=0.5, block_index=1),
            _candidate(3, relevance=0.7, block_index=3),
        ]
        result = _admission(max_images=3).admit(candidates)
        assert [c.block_index for c in result.admitted] == [1, 3, 5]


class TestNormalization:
    @staticmethod
    def _png(width: int, height: int) -> str:
        pillow = pytest.importorskip("PIL.Image")
        buffer = io.BytesIO()
        pillow.new("RGB", (width, height), (10, 90, 160)).save(buffer, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode()

    def test_an_oversized_admitted_image_is_downscaled(self) -> None:
        oversized = self._png(3000, 2000)
        admission = ImageAdmission(resolve_image_policy(provider="anthropic", is_multimodal=True))
        admitted = admission.admit([
            _candidate(0, data_uri=oversized, width=3000, height=2000),
        ]).admitted[0]
        assert admitted.data_uri != oversized
        assert len(admitted.data_uri) < len(oversized)

    def test_an_image_within_limits_is_left_alone(self) -> None:
        fine = self._png(600, 400)
        admission = ImageAdmission(resolve_image_policy(provider="anthropic", is_multimodal=True))
        admitted = admission.admit([
            _candidate(0, data_uri=fine, width=600, height=400),
        ]).admitted[0]
        assert admitted.data_uri == fine

    def test_a_degraded_image_is_never_re_encoded(self) -> None:
        """Normalization is for pixels that are actually being sent."""
        oversized = self._png(3000, 2000)
        result = _admission(max_images=0).admit([
            _candidate(0, data_uri=oversized, width=3000, height=2000),
        ])
        assert result.degraded[0].candidate.data_uri == oversized


class TestAdmissionFromState:
    def test_returns_the_seeded_arbiter(self) -> None:
        seeded = _admission(max_images=3)
        state = {"image_admission": seeded, "image_budget": seeded.budget}
        assert admission_from_state(state) is seeded

    def test_builds_a_permissive_one_for_state_that_predates_it(self) -> None:
        budget = ImageBudget()
        admission = admission_from_state({"image_budget": budget})
        assert admission.budget is budget
        assert admission.policy.max_images_per_request == MAX_IMAGES_IN_CONVERSATION

    def test_missing_state_still_yields_a_working_arbiter(self) -> None:
        assert admission_from_state(None).allows_images

    def test_a_replaced_budget_in_state_wins(self) -> None:
        """The state's budget is authoritative: an entry point that swaps it
        must not leave the arbiter counting against an orphan."""
        seeded = _admission(max_images=5)
        replacement = ImageBudget(max_images=1)
        replacement.try_consume(1)
        admission = admission_from_state(
            {"image_admission": seeded, "image_budget": replacement},
        )
        assert admission.budget is replacement
        assert not admission.admit([_candidate(0)]).admitted


class TestNormalizationReachesTheWire:
    """`_normalize` downscales an admitted image to the model's per-image
    limits. The renderers keep a decision, not a candidate, and re-read the
    source block's URI — so without somewhere to look up the admitted bytes
    the downscale was computed and thrown away, and an image over the
    provider's byte limit still went out at full size.
    """

    @staticmethod
    def _uri(width: int, height: int) -> str:
        import base64
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (width, height), (120, 60, 30)).save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    @staticmethod
    def _admission() -> ImageAdmission:
        from app.utils.image_policy import resolve_image_policy

        return ImageAdmission(
            resolve_image_policy(provider="anthropic", is_multimodal=True),
            budget=ImageBudget(),
        )

    @staticmethod
    def _candidate(uri: str, width: int, height: int) -> ImageCandidate:
        return ImageCandidate(
            ref="ref1", data_uri=uri, origin=ImageOrigin.FETCHED_RECORD,
            block_index=7, width=width, height=height,
        )

    def test_an_oversized_image_is_downscaled_on_admission(self) -> None:
        from app.utils.image_utils import read_image_dimensions

        uri = self._uri(4000, 3000)
        admission = self._admission()

        admitted = admission.admit([self._candidate(uri, 4000, 3000)]).admitted[0]

        assert read_image_dimensions(admitted.data_uri) == (1568, 1176)

    def test_the_renderer_can_look_the_admitted_bytes_up(self) -> None:
        """`rendered_uri` is what the renderers call: they hold the source URI
        and nothing else."""
        uri = self._uri(4000, 3000)
        admission = self._admission()
        admitted = admission.admit([self._candidate(uri, 4000, 3000)]).admitted[0]

        assert admission.rendered_uri(uri) == admitted.data_uri
        assert admission.rendered_uri(uri) != uri

    def test_a_repeat_fetch_still_resolves_to_the_normalized_bytes(self) -> None:
        """A repeat fetch in the same request is a duplicate, so it gets no
        second copy — but a renderer holding the source URI must still resolve
        it to the downscaled bytes the first admission paid for, since those
        are what the model is actually looking at."""
        uri = self._uri(4000, 3000)
        admission = self._admission()

        first = admission.admit([self._candidate(uri, 4000, 3000)]).admitted[0]
        second = admission.admit([self._candidate(uri, 4000, 3000)])

        assert second.admitted == []
        assert admission.rendered_uri(uri) == first.data_uri
        assert admission.admitted_uri(uri) == first.data_uri

    def test_an_image_within_limits_is_returned_unchanged(self) -> None:
        """No re-encode, and nothing cached: only images that actually needed
        rewriting cost anything."""
        uri = self._uri(100, 80)
        admission = self._admission()
        admission.admit([self._candidate(uri, 100, 80)])

        assert admission.rendered_uri(uri) == uri

    @pytest.mark.parametrize("uri", ["", "data:image/png;base64,ZZZ", "https://x/y.png"])
    def test_an_uri_that_was_never_admitted_passes_through(self, uri: str) -> None:
        assert self._admission().rendered_uri(uri) == uri

    def test_the_record_renderer_emits_the_downscaled_bytes(self) -> None:
        """End to end: what `collected_images` carries is what reaches the
        provider."""
        from app.models.blocks import BlockType
        from app.utils.chat_helpers import CitationRefMapper, record_to_message_content
        from app.utils.image_utils import read_image_dimensions

        uri = self._uri(4000, 3000)
        record = {
            "id": "rec-1",
            "virtual_record_id": "vr-1",
            "block_containers": {
                "blocks": [{
                    "index": 0,
                    "type": BlockType.IMAGE.value,
                    "parent_index": None,
                    "data": {"uri": uri},
                }],
                "block_groups": [],
            },
        }
        collected: list[dict] = []
        record_to_message_content(
            record,
            ref_mapper=CitationRefMapper(),
            is_multimodal_llm=True,
            collected_images=collected,
            image_admission=self._admission(),
        )

        assert collected, "the image was not admitted at all"
        assert read_image_dimensions(collected[0]["image_url"]["url"]) == (1568, 1176)


class TestNormalizationOffTheEventLoop:
    """`record_to_message_content` is synchronous and `execute_fetch_record`
    is not. An image that needs downscaling is a Pillow decode, resize and
    re-encode — ~600 ms for a 4000x3000 page scan — so doing it inline blocks
    every concurrent request on the loop, not just this one.
    """

    @staticmethod
    def _uri(width: int, height: int) -> str:
        import base64
        import io

        from PIL import Image

        buf = io.BytesIO()
        Image.new("RGB", (width, height), (30, 90, 140)).save(buf, format="PNG")
        return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()

    @staticmethod
    def _admission(provider: str = "anthropic", multimodal: bool = True) -> ImageAdmission:
        from app.utils.image_policy import resolve_image_policy

        return ImageAdmission(
            resolve_image_policy(provider=provider, is_multimodal=multimodal),
            budget=ImageBudget(),
        )

    async def test_warming_makes_the_render_a_cache_hit(self) -> None:
        from app.utils.image_utils import read_image_dimensions

        uri = self._uri(4000, 3000)
        admission = self._admission()

        await admission.warm([uri])
        # No Pillow work left for the synchronous path to do.
        assert read_image_dimensions(admission.rendered_uri(uri)) == (1568, 1176)

    async def test_the_admitted_bytes_are_the_same_either_way(self) -> None:
        """Warming is an optimisation, not a behaviour change."""
        uri = self._uri(4000, 3000)
        cold = self._admission()
        warmed = self._admission()

        await warmed.warm([uri])
        c = cold.admit([ImageCandidate(ref="r", data_uri=uri, origin=ImageOrigin.FETCHED_RECORD)])
        w = warmed.admit([ImageCandidate(ref="r", data_uri=uri, origin=ImageOrigin.FETCHED_RECORD)])

        assert c.admitted[0].data_uri == w.admitted[0].data_uri

    async def test_the_pillow_work_runs_on_a_worker_thread(self) -> None:
        """The whole point: the loop has to stay free while this happens."""
        import threading

        seen: list[str] = []
        main = threading.current_thread().name

        def spy(image_uri: str, **_kwargs) -> str:
            seen.append(threading.current_thread().name)
            return image_uri

        admission = self._admission()
        with patch("app.utils.image_utils.downscale_to_limits", side_effect=spy):
            await admission.warm([self._uri(4000, 3000)])

        assert seen and all(name != main for name in seen)

    async def test_an_image_needing_no_change_is_decided_once(self) -> None:
        """Without remembering the no-ops, every render re-decodes them."""
        calls = 0

        def spy(image_uri: str, **_kwargs) -> str:
            nonlocal calls
            calls += 1
            return image_uri

        uri = self._uri(100, 80)
        admission = self._admission()
        with patch("app.utils.image_utils.downscale_to_limits", side_effect=spy):
            await admission.warm([uri])
            await admission.warm([uri])
            admission.admit([ImageCandidate(ref="r", data_uri=uri, origin=ImageOrigin.ATTACHMENT)])

        assert calls == 1

    async def test_duplicates_in_one_batch_are_decoded_once(self) -> None:
        calls = 0

        def spy(image_uri: str, **_kwargs) -> str:
            nonlocal calls
            calls += 1
            return image_uri

        uri = self._uri(200, 200)
        with patch("app.utils.image_utils.downscale_to_limits", side_effect=spy):
            await self._admission().warm([uri, uri, uri])

        assert calls == 1

    async def test_a_text_only_model_does_no_image_work_at_all(self) -> None:
        with patch("app.utils.image_utils.downscale_to_limits") as spy:
            await self._admission(multimodal=False).warm([self._uri(4000, 3000)])

        spy.assert_not_called()

    @pytest.mark.parametrize("uris", [[], [""], None])
    async def test_nothing_to_warm_is_a_no_op(self, uris) -> None:
        await self._admission().warm(uris or [])


class TestRecordImageUris:
    def test_it_finds_images_nested_in_a_table(self) -> None:
        """Wider than the admission candidate filter on purpose: a figure
        inside a table is rendered too, and costs the same decode."""
        from app.models.blocks import BlockType
        from app.utils.chat_helpers import record_image_uris

        uri = TestNormalizationOffTheEventLoop._uri(50, 50)
        record = {"block_containers": {"blocks": [
            {"index": 0, "type": BlockType.IMAGE.value, "parent_block_index": None,
             "data": {"uri": uri}},
            {"index": 1, "type": BlockType.IMAGE.value, "parent_block_index": 0,
             "data": {"uri": uri}},
            {"index": 2, "type": BlockType.TEXT.value, "data": "not an image"},
        ]}}

        assert record_image_uris(record) == [uri, uri]

    def test_a_non_image_uri_is_skipped(self) -> None:
        from app.models.blocks import BlockType
        from app.utils.chat_helpers import record_image_uris

        record = {"block_containers": {"blocks": [
            {"index": 0, "type": BlockType.IMAGE.value, "data": {"uri": "https://x/y.png"}},
            {"index": 1, "type": BlockType.IMAGE.value, "data": {}},
        ]}}

        assert record_image_uris(record) == []

    def test_a_record_with_no_blocks_is_empty(self) -> None:
        from app.utils.chat_helpers import record_image_uris

        assert record_image_uris({}) == []
