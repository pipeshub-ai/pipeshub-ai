"""Unit tests for `app.agents.chat_modes.prefetch.prefetch_retrieval`."""

import logging
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.chat_modes.prefetch import (
    PrefetchResult,
    _select_prefetch_content,
    prefetch_retrieval,
)

LOGGER = logging.getLogger("test")


def test_select_prefetch_content_enforces_budget_and_marker_pairing() -> None:
    png = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
        "x8AAusB9Wl0bQAAAABJRU5ErkJggg=="
    )
    parts = [
        {"type": "text", "text": "[1|ref1]"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
        {"type": "text", "text": "[2|ref2]"},
        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}},
    ]

    with patch("app.agents.chat_modes.prefetch._MAX_PREFETCH_IMAGE_BYTES", 100):
        context, selected = _select_prefetch_content(parts)

    assert selected == [parts[1]]
    assert "[1|ref1]" in context
    assert "[2|ref2]" not in context


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
    async def test_malformed_search_response_returns_empty_error_result(self) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = []

        result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.is_empty is True
        assert result.error_message == "Invalid search response"

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

    async def test_preserves_only_four_images_for_multimodal_injection(self) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [{"metadata": {"recordId": "r1"}}],
            "virtual_to_record_map": {},
        }
        flattened = [{"virtual_record_id": "vr1", "recordId": "r1"}]
        captured_kwargs = {}

        def _fake_build_message_content_array(final_results, vr_map, **kwargs):
            captured_kwargs.update(kwargs)
            parts = [{"type": "text", "text": "[1|ref1] road image"}]
            png = (
                "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/"
                "x8AAusB9Wl0bQAAAABJRU5ErkJggg=="
            )
            parts.extend(
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{png}"}}
                for _ in range(6)
            )
            return [parts], kwargs["ref_mapper"]

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
            result = await prefetch_retrieval(**_make_kwargs(
                retrieval_service=retrieval_service,
                is_multimodal_llm=True,
            ))

        assert captured_kwargs["from_tool"] is False
        assert captured_kwargs["is_multimodal_llm"] is True
        assert len(result.image_blocks) == 4

    async def test_hydrates_ranked_image_record_summary(self) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": 200,
            "searchResults": [
                None,
                {"content": "malformed", "metadata": "not-a-mapping"},
                {
                    "content": "OCR text from the image",
                    "metadata": {
                        "virtualRecordId": "vr-image",
                        "recordId": "r-image",
                        "mimeType": "image/jpeg",
                        "blockIndex": 1,
                    },
                },
                {
                    "content": "Unclassified text result",
                    "metadata": {
                        "virtualRecordId": "vr-image",
                        "recordId": "r-image",
                        "mimeType": "image/jpeg",
                    },
                },
                {
                    "content": "A coastal photograph",
                    "metadata": {
                        "virtualRecordId": "vr-image",
                        "recordId": "r-image",
                        "mimeType": "image/jpeg",
                        "isRecordSummary": True,
                    },
                },
            ],
            "virtual_to_record_map": {"vr-image": {"mimeType": "image/jpeg"}},
        }
        flattened = [{"virtual_record_id": "vr-image", "recordId": "r-image"}]
        get_flattened = AsyncMock(return_value=flattened)

        with patch(
            "app.agents.chat_modes.prefetch.get_flattened_results", new=get_flattened,
        ), patch(
            "app.agents.chat_modes.prefetch.enrich_virtual_record_id_to_result_with_fk_children",
            new=AsyncMock(),
        ), patch(
            "app.agents.chat_modes.prefetch.enrich_records_with_graph_context",
            new=AsyncMock(),
        ), patch(
            "app.agents.chat_modes.prefetch.build_message_content_array",
            side_effect=lambda *args, **kwargs: (
                [[{"type": "text", "text": "image marker"}]],
                kwargs["ref_mapper"],
            ),
        ):
            await prefetch_retrieval(**_make_kwargs(
                retrieval_service=retrieval_service,
                is_multimodal_llm=True,
            ))

        hydrated = get_flattened.await_args.args[0][-1]
        passed_results = get_flattened.await_args.args[0]
        assert all(isinstance(item.get("metadata"), dict) for item in passed_results)
        assert sum(item["content"] == "Unclassified text result" for item in passed_results) == 1
        assert hydrated["content"] == "A coastal photograph"
        assert hydrated["metadata"]["virtualRecordId"] == "vr-image"
        assert hydrated["metadata"]["blockIndex"] == 0
        assert hydrated["metadata"]["isBlockGroup"] is False
        assert hydrated["metadata"]["isRecordSummary"] is False
