"""Unit tests for `app.agents.chat_modes.prefetch.prefetch_retrieval`."""

import logging
from collections.abc import Iterator
from contextlib import ExitStack, contextmanager
from typing import Any
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


_BUILDER = "app.modules.retrieval.context.builder"
_RENDERER = "app.modules.retrieval.context.renderer"


@contextmanager
def _pipeline(flattened: list[dict], *, build: Any = None, graph: AsyncMock | None = None) -> Iterator[None]:  # noqa: ANN401
    """Stand in for flatten (loading a record for every unit), enrichment and,
    when given, the formatter."""
    async def flatten(_results, _blob, _org, _mm, vr_map, *_a, **_kw) -> list[dict]:
        for unit in flattened:
            vr_map.setdefault(unit["virtual_record_id"], {"id": unit["virtual_record_id"]})
        return [dict(unit) for unit in flattened]

    with ExitStack() as stack:
        stack.enter_context(patch(f"{_BUILDER}.get_flattened_results", side_effect=flatten))
        stack.enter_context(patch(f"{_BUILDER}.enrich_virtual_record_id_to_result_with_fk_children", new=AsyncMock()))
        stack.enter_context(patch(f"{_BUILDER}.enrich_records_with_graph_context", new=graph or AsyncMock()))
        if build is not None:
            stack.enter_context(patch(f"{_RENDERER}.build_message_content_array", side_effect=build))
        yield


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

        with _pipeline([]):
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
        with _pipeline(flattened, build=_fake_build_message_content_array, graph=graph_enrich):
            result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.is_empty is False
        assert "Relevant excerpt from r1" in result.formatted_context
        assert result.final_results == flattened
        assert result.citation_ref_mapper is captured_ref_mapper["mapper"]
        graph_enrich.assert_awaited_once()


    async def test_ranked_results_keep_the_search_order(self) -> None:
        """`final_results` is re-sorted by virtual record id for display; what the
        search ranked first is what a later id-recovery list must keep."""
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": 200, "searchResults": [{"metadata": {}}], "virtual_to_record_map": {},
        }
        flattened = [
            {"virtual_record_id": "vr-b", "block_index": 0},
            {"virtual_record_id": "vr-a", "block_index": 0},
        ]
        with _pipeline(
            flattened, build=lambda fr, vr, **kw: ([[{"type": "text", "text": "x"}]], kw["ref_mapper"]),
        ):
            result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert [r["virtual_record_id"] for r in result.final_results] == ["vr-a", "vr-b"]
        assert [r["virtual_record_id"] for r in result.ranked_results] == ["vr-b", "vr-a"]


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

        with _pipeline(flattened, build=_fake_build_message_content_array):
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

        with _pipeline(flattened, build=_fake_build_message_content_array):
            await prefetch_retrieval(
                **_make_kwargs(retrieval_service=retrieval_service, image_budget=shared_budget)
            )

        assert captured["image_budget"] is shared_budget


class TestRefusedFilter:
    async def test_a_refused_entity_filter_is_a_notice_not_an_empty_search(self) -> None:
        from app.agents.chat_modes.prefetch import filter_notice_constraint

        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": 422,
            "message": "This entity filter matches too many documents. Narrow it.",
        }

        result = await prefetch_retrieval(**_make_kwargs(
            retrieval_service=retrieval_service,
            filters={"kb": ["kb-1"], "entityFilters": {"kinds": ["date"]}},
        ))

        assert result.is_empty is True
        assert result.filter_notice == "This entity filter matches too many documents. Narrow it."
        assert retrieval_service.search_with_filters.await_args.kwargs["filter_groups"]["entityFilters"] == {"kinds": ["date"]}
        constraint = filter_notice_constraint(result.filter_notice)
        assert "Narrow it." in constraint and "Do not answer as if no documents matched" in constraint

    @pytest.mark.parametrize("status_code", [202, 404, 500, 503])
    async def test_other_failures_carry_no_filter_notice(self, status_code) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {"status_code": status_code, "message": "x"}
        result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))
        assert result.filter_notice is None


class TestSearchFailureNotice:
    """The model is told when its first retrieval failed, and only then."""

    @pytest.mark.parametrize(("status", "status_code", "message"), [
        ("search_index_unavailable", 503,
         "The search index took too long to respond, so no results are shown. Please try again in a minute."),
        ("error", 500, "Unexpected server error during search."),
        ("permission_check_unavailable", 503, "We couldn't check which documents you have access to just now."),
    ])
    async def test_a_failed_search_carries_its_reason(self, status, status_code, message) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status": status, "status_code": status_code, "message": message, "searchResults": [],
        }

        result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.is_empty is True
        assert result.failure_notice == message

    @pytest.mark.parametrize(("status", "status_code"), [
        ("accessible_records_not_found", 404),
        ("vector_db_empty", 503),
    ])
    async def test_an_answer_that_is_not_a_failure_carries_no_notice(self, status, status_code) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status": status, "status_code": status_code, "message": "No documents yet.",
        }

        result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.failure_notice is None

    async def test_nothing_readable_where_the_search_ran_with_part_of_the_index_down_is_a_failure(self) -> None:
        notice = "1 of 2 parts of the search index could not be searched, so this search may have missed documents."
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status": "accessible_records_not_found", "status_code": 404,
            "message": f"{notice} No documents are available.", "searchIncomplete": notice,
        }

        result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.failure_notice == notice

    async def test_a_raised_search_names_no_internals(self) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.side_effect = RuntimeError("qdrant at 10.0.0.4:6334 refused")

        result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.failure_notice == "The search could not run."
        assert "10.0.0.4" not in result.failure_notice

    async def test_a_successful_empty_search_is_not_a_failure(self) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status": "empty_response", "status_code": 200, "searchResults": [], "virtual_to_record_map": {},
        }

        with _pipeline([]):
            result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.is_empty is True
        assert result.failure_notice is None
        assert result.incomplete_notice is None

    async def test_empty_where_the_search_ran_with_part_of_the_index_down_is_a_failure(self) -> None:
        notice = "1 of 2 parts of the search index could not be searched, so this search may have missed documents."
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status": "empty_response", "status_code": 200, "searchResults": [],
            "virtual_to_record_map": {}, "searchIncomplete": notice,
        }

        with _pipeline([]):
            result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.is_empty is True
        assert result.failure_notice == notice

    async def test_results_from_part_of_the_index_carry_the_gap(self) -> None:
        notice = "1 of 2 parts of the search index could not be searched, so this search may have missed documents."
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status": "success", "status_code": 200, "searchResults": [{"metadata": {"recordId": "r1"}}],
            "virtual_to_record_map": {}, "searchIncomplete": notice,
        }

        def _build(final_results, vr_map, **kwargs) -> tuple[list, Any]:
            return [[{"type": "text", "text": "Relevant excerpt from r1"}]], kwargs["ref_mapper"]

        with _pipeline([{"virtual_record_id": "vr1", "recordId": "r1"}], build=_build):
            result = await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service))

        assert result.is_empty is False
        assert result.failure_notice is None
        assert result.incomplete_notice == notice


class TestSearchFailureConstraints:
    def test_the_failure_constraint_says_the_search_failed_and_what_to_do(self) -> None:
        from app.agents.chat_modes.prefetch import search_failure_constraint

        notice = "The search index took too long to respond, so no results are shown."
        with_tool = search_failure_constraint(notice, can_search=True)
        without_tool = search_failure_constraint(notice, can_search=False)

        for text in (with_tool, without_tool):
            assert notice in text
            assert "did not complete" in text
            assert "Do not answer as if no documents matched" in text
        assert "Search again" in with_tool
        assert "Search again" not in without_tool

    def test_the_incomplete_note_tells_the_model_a_missing_document_may_exist(self) -> None:
        from app.agents.chat_modes.prefetch import search_incomplete_constraint

        note = search_incomplete_constraint("1 of 2 parts did not respond.", can_search=True)

        assert note.startswith("Note: 1 of 2 parts did not respond.")
        assert "search again" in note


class TestAConnectedAppsLimit:
    @pytest.mark.parametrize(("filters", "sources"), [
        ({"apps": ["a"]}, None),
        ({"apps": ["a"], "records": ["f"], "connectionApps": [], "connectionRecordGroups": [],
          "connectionRecords": ["f"]}, {"apps": [], "recordGroups": [], "records": ["f"]}),
    ])
    async def test_the_turns_limit_reaches_the_graph_context(self, filters, sources) -> None:
        retrieval_service = AsyncMock()
        retrieval_service.search_with_filters.return_value = {
            "status_code": 200, "searchResults": [{"metadata": {"recordId": "r1"}}], "virtual_to_record_map": {},
        }
        graph_enrich = AsyncMock()
        with _pipeline([{"virtual_record_id": "vr1", "recordId": "r1"}], graph=graph_enrich):
            await prefetch_retrieval(**_make_kwargs(retrieval_service=retrieval_service, filters=filters))

        assert graph_enrich.await_args.kwargs["connection_sources"] == sources
