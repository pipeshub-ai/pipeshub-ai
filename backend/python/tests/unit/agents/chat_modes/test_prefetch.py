"""Unit tests for `app.agents.chat_modes.prefetch.prefetch_retrieval`."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.chat_modes.prefetch import PrefetchResult, prefetch_retrieval
from app.utils.chat_helpers import ImageBudget

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

        with patch(
            "app.agents.chat_modes.prefetch.get_flattened_results", new=AsyncMock(return_value=[]),
        ), patch(
            "app.agents.chat_modes.prefetch.enrich_virtual_record_id_to_result_with_fk_children",
            new=AsyncMock(),
        ):
            result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.is_empty is True
        assert result.error_message is None
        assert result.final_results == []


class TestSuccessfulPrefetch:
    async def test_builds_formatted_context_and_shares_ref_mapper(self) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [{"metadata": {"recordId": "r1"}}],
            "virtual_to_record_map": {},
        }
        flattened = [{"virtual_record_id": "vr1", "recordId": "r1"}]
        captured_ref_mapper = {}

        def _fake_build_message_content_array(final_results, vr_map, **kwargs):
            mapper = kwargs["ref_mapper"]
            captured_ref_mapper["mapper"] = mapper
            return [[{"type": "text", "text": "Relevant excerpt from r1"}]], mapper

        graph_enrich = AsyncMock()
        with patch(
            "app.agents.chat_modes.prefetch.get_flattened_results",
            new=AsyncMock(return_value=flattened),
        ), patch(
            "app.agents.chat_modes.prefetch.enrich_virtual_record_id_to_result_with_fk_children",
            new=AsyncMock(),
        ), patch(
            "app.agents.chat_modes.prefetch.enrich_records_with_graph_context",
            new=graph_enrich,
        ), patch(
            "app.agents.chat_modes.prefetch.build_message_content_array",
            side_effect=_fake_build_message_content_array,
        ):
            result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.is_empty is False
        assert "Relevant excerpt from r1" in result.formatted_context
        assert result.final_results == flattened
        assert result.citation_ref_mapper is captured_ref_mapper["mapper"]
        graph_enrich.assert_awaited_once()


class TestPrefetchImageCollection:
    """`bridge.py` merges `PrefetchResult.collected_images` into
    `context.attachment_image_blocks` so `shape_image_injection` can
    deliver them -- prefetch itself only needs to plumb `collected_images`/
    `image_budget` through to `build_message_content_array` correctly."""

    async def test_collected_images_populated_from_formatter(self) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [{"metadata": {"recordId": "r1"}}],
            "virtual_to_record_map": {},
        }
        flattened = [{"virtual_record_id": "vr1", "recordId": "r1"}]

        def _fake_build_message_content_array(final_results, vr_map, **kwargs):
            kwargs["collected_images"].append({
                "ref": "ref1", "block_index": 0,
                "image_url": {"url": "data:image/png;base64,xx"},
                "virtual_record_id": "vr1",
            })
            return [[{"type": "text", "text": "[ref1] (image)"}]], kwargs["ref_mapper"]

        with patch(
            "app.agents.chat_modes.prefetch.get_flattened_results",
            new=AsyncMock(return_value=flattened),
        ), patch(
            "app.agents.chat_modes.prefetch.enrich_virtual_record_id_to_result_with_fk_children",
            new=AsyncMock(),
        ), patch(
            "app.agents.chat_modes.prefetch.enrich_records_with_graph_context",
            new=AsyncMock(),
        ), patch(
            "app.agents.chat_modes.prefetch.build_message_content_array",
            side_effect=_fake_build_message_content_array,
        ):
            result = await prefetch_retrieval(
                **_make_kwargs(retrieval_service=retrieval_service, is_multimodal_llm=True)
            )

        assert result.collected_images == [{
            "ref": "ref1", "block_index": 0,
            "image_url": {"url": "data:image/png;base64,xx"},
            "virtual_record_id": "vr1",
        }]

    async def test_shared_image_budget_is_forwarded_to_formatter(self) -> None:
        """A shared `ImageBudget` passed in by the caller (`bridge.py`'s
        per-request budget) must reach `build_message_content_array`
        unchanged -- not a fresh per-call budget -- so the 50-image cap is
        enforced across search/fetch/prefetch/attachments together."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [{"metadata": {"recordId": "r1"}}],
            "virtual_to_record_map": {},
        }
        flattened = [{"virtual_record_id": "vr1", "recordId": "r1"}]
        shared_budget = ImageBudget(max_images=7)
        captured = {}

        def _fake_build_message_content_array(final_results, vr_map, **kwargs):
            captured["image_budget"] = kwargs["image_budget"]
            return [[{"type": "text", "text": "x"}]], kwargs["ref_mapper"]

        with patch(
            "app.agents.chat_modes.prefetch.get_flattened_results",
            new=AsyncMock(return_value=flattened),
        ), patch(
            "app.agents.chat_modes.prefetch.enrich_virtual_record_id_to_result_with_fk_children",
            new=AsyncMock(),
        ), patch(
            "app.agents.chat_modes.prefetch.enrich_records_with_graph_context",
            new=AsyncMock(),
        ), patch(
            "app.agents.chat_modes.prefetch.build_message_content_array",
            side_effect=_fake_build_message_content_array,
        ):
            await prefetch_retrieval(
                **_make_kwargs(retrieval_service=retrieval_service, image_budget=shared_budget)
            )

        assert captured["image_budget"] is shared_budget
