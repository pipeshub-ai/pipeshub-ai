"""Unit tests for `app.agents.chat_modes.prefetch.prefetch_retrieval`."""

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.agents.chat_modes.prefetch import PrefetchResult, prefetch_retrieval
from app.modules.retrieval.context.builder import KnowledgeContext
from app.modules.retrieval.context.renderer import RenderedKnowledge
from app.utils.chat_helpers import CitationRefMapper, ImageBudget

LOGGER = logging.getLogger("test")


def _make_kwargs(**overrides):
    kwargs = {
        "query": "what is our refund policy?",
        "org_id": "org-1",
        "user_id": "user-1",
        "retrieval_service": AsyncMock(),
        "graph_provider": object(),
        "blob_store": object(),
        "filters": {"apps": [], "kb": []},
        "limit": 10,
        "is_multimodal_llm": False,
        "previous_conversations": None,
        "logger": LOGGER,
    }
    kwargs.update(overrides)
    return kwargs


class TestFollowUpSkip:
    async def test_skips_retrieval_when_previous_conversations_present(self) -> None:
        retrieval_service = AsyncMock()
        result = await prefetch_retrieval(
            **_make_kwargs(
                retrieval_service=retrieval_service,
                previous_conversations=[{"role": "user", "content": "earlier turn"}],
            )
        )

        assert result is None
        retrieval_service.search_with_filters.assert_not_called()

    async def test_force_true_runs_retrieval_even_on_a_follow_up(self) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [],
            "virtual_to_record_map": {},
        }
        result = await prefetch_retrieval(
            **_make_kwargs(
                retrieval_service=retrieval_service,
                previous_conversations=[{"role": "user", "content": "earlier turn"}],
                force=True,
            )
        )

        retrieval_service.search_with_filters.assert_awaited_once()
        assert result is not None
        assert result.is_empty is True


class TestRetrievalFailureModes:
    async def test_exception_from_search_returns_empty_error_result(self) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.side_effect = RuntimeError("qdrant unavailable")

        result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert isinstance(result, PrefetchResult)
        assert result.is_empty is True
        assert result.final_results == []
        assert "qdrant unavailable" in (result.error_message or "")

    @pytest.mark.parametrize("status_code", [202, 404, 500, 503])
    async def test_backend_error_status_codes_return_empty_result(self, status_code) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": status_code,
            "message": "Search failed",
        }

        result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.is_empty is True
        assert result.error_message == "Search failed"

    async def test_empty_search_results_returns_empty_result_without_error(self) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [],
            "virtual_to_record_map": {},
        }

        builder, render = _pipeline(units=[])
        with builder, render:
            result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.is_empty is True
        assert result.error_message is None
        assert result.final_results == []


def _one_hit_retrieval() -> AsyncMock:
    retrieval_service = AsyncMock()
    retrieval_service.search_with_filters.return_value = {
        "status_code": 200,
        "searchResults": [{"metadata": {"recordId": "r1"}}],
        "virtual_to_record_map": {},
    }
    return retrieval_service


_UNITS = [{"virtual_record_id": "vr1", "block_index": 0}]


def _pipeline(*, units=_UNITS, text="Relevant excerpt from r1", images=None):
    """Replace the shared context pipeline at its two seams."""
    builder = MagicMock()
    builder.return_value.build = AsyncMock(return_value=KnowledgeContext(
        units=units, virtual_record_id_to_result={"vr1": {"id": "r1"}} if units else {},
    ))
    rendered = RenderedKnowledge(
        records=[text] if units else [], units=units, images=images or [],
    )
    return (
        patch("app.agents.chat_modes.prefetch.KnowledgeContextBuilder", builder),
        patch("app.agents.chat_modes.prefetch.render_knowledge", return_value=rendered),
    )


class TestSuccessfulPrefetch:
    async def test_renders_the_ranked_context_with_the_shared_ref_mapper(self) -> None:
        ref_mapper = CitationRefMapper()
        builder, render = _pipeline()
        with builder as mock_builder, render as mock_render:
            result = await prefetch_retrieval(
                **_make_kwargs(retrieval_service=_one_hit_retrieval(), ref_mapper=ref_mapper)
            )

        assert result.is_empty is False
        assert result.formatted_context == "Relevant excerpt from r1"
        assert result.final_results == _UNITS
        assert result.citation_ref_mapper is ref_mapper
        assert mock_render.call_args.kwargs["ref_mapper"] is ref_mapper
        build_kwargs = mock_builder.return_value.build.call_args.kwargs
        assert build_kwargs["include_fk_children"] is True
        # The system prompt is not subject to the tool-result cap: nothing is cut.
        assert build_kwargs.get("max_units") is None
        assert mock_render.call_args.kwargs.get("max_chars") is None


class TestPrefetchImageCollection:
    """`bridge.py` merges `PrefetchResult.collected_images` into
    `context.attachment_image_blocks` so `shape_image_injection` can
    deliver them."""

    async def test_collected_images_come_from_the_renderer(self) -> None:
        image = {
            "ref": "ref1", "block_index": 0,
            "image_url": {"url": "data:image/png;base64,xx"},
            "virtual_record_id": "vr1",
        }
        builder, render = _pipeline(images=[image])
        with builder, render:
            result = await prefetch_retrieval(
                **_make_kwargs(retrieval_service=_one_hit_retrieval(), is_multimodal_llm=True)
            )

        assert result.collected_images == [image]

    async def test_shared_image_budget_is_forwarded_to_the_renderer(self) -> None:
        """The per-request budget from `bridge.py` must reach rendering
        unchanged, so the 50-image cap holds across search, fetch, prefetch
        and attachments together."""
        shared_budget = ImageBudget(max_images=7)
        builder, render = _pipeline()
        with builder, render as mock_render:
            await prefetch_retrieval(
                **_make_kwargs(retrieval_service=_one_hit_retrieval(), image_budget=shared_budget)
            )

        assert mock_render.call_args.kwargs["image_budget"] is shared_budget
