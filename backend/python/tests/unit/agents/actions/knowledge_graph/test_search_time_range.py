"""Integration tests for time-aware `knowledgegraph__search` — verifies
`execute_search` parses ISO date parameters and forwards the resulting
`time_range` dict to `RetrievalService.search_with_filters`, in both the
single-call and per-source fan-out paths."""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.agents.actions.knowledge_graph.ops.search import execute_search


def _make_state(**overrides):
    """Create a ChatState-like dict with sensible defaults."""
    state = {
        "org_id": "org-1",
        "user_id": "user-1",
        "filters": {"apps": [], "kb": []},
        "retrieval_service": AsyncMock(),
        "graph_provider": AsyncMock(),
        "config_service": AsyncMock(),
        "logger": MagicMock(),
        "llm": None,
    }
    state.update(overrides)
    return state


def _empty_results_service() -> AsyncMock:
    """A `retrieval_service` mock whose `search_with_filters` reports no
    hits — `execute_search` short-circuits to "No results found" right
    after checking `search_results`, so tests that only care about what
    was forwarded to the service don't need to mock the downstream
    enrichment/formatting pipeline (BlobStorage, get_flattened_results,
    build_message_content_array, ...)."""
    service = AsyncMock()
    service.search_with_filters = AsyncMock(
        return_value={"status_code": 200, "searchResults": [], "virtual_to_record_map": {}}
    )
    return service


class TestSearchTimeRangeForwarding:
    @pytest.mark.asyncio
    async def test_time_params_forwarded_as_epoch_ms(self) -> None:
        retrieval_service = _empty_results_service()
        state = _make_state(retrieval_service=retrieval_service)

        await execute_search(state, query="test", created_after="2026-01-01")

        retrieval_service.search_with_filters.assert_awaited_once()
        kwargs = retrieval_service.search_with_filters.await_args.kwargs
        assert "time_range" in kwargs
        assert list(kwargs["time_range"].keys()) == ["source_created_after_ms"]

    @pytest.mark.asyncio
    async def test_modified_params_forwarded(self) -> None:
        retrieval_service = _empty_results_service()
        state = _make_state(retrieval_service=retrieval_service)

        await execute_search(state, query="test", modified_after="2026-06-01")

        kwargs = retrieval_service.search_with_filters.await_args.kwargs
        assert list(kwargs["time_range"].keys()) == ["source_updated_after_ms"]

    @pytest.mark.asyncio
    async def test_omitted_params_pass_none_time_range(self) -> None:
        retrieval_service = _empty_results_service()
        state = _make_state(retrieval_service=retrieval_service)

        await execute_search(state, query="test")

        kwargs = retrieval_service.search_with_filters.await_args.kwargs
        assert kwargs["time_range"] is None

    @pytest.mark.asyncio
    async def test_created_and_modified_both_forwarded(self) -> None:
        retrieval_service = _empty_results_service()
        state = _make_state(retrieval_service=retrieval_service)

        await execute_search(
            state,
            query="test",
            created_after="2026-01-01",
            created_before="2026-03-31",
            modified_after="2026-02-01",
        )

        kwargs = retrieval_service.search_with_filters.await_args.kwargs
        assert set(kwargs["time_range"].keys()) == {
            "source_created_after_ms",
            "source_created_before_ms",
            "source_updated_after_ms",
        }

    @pytest.mark.asyncio
    async def test_time_range_with_source_ids(self) -> None:
        """A single explicit source_id keeps the single-call path (no
        fan-out) and still forwards time_range alongside the narrowed
        filter_groups."""
        retrieval_service = _empty_results_service()
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["app-1"], "kb": []},
        )

        await execute_search(
            state, query="test", source_ids=["app-1"], created_after="2026-01-01"
        )

        retrieval_service.search_with_filters.assert_awaited_once()
        kwargs = retrieval_service.search_with_filters.await_args.kwargs
        assert kwargs["filter_groups"].get("apps") == ["app-1"]
        assert kwargs["time_range"] == {
            "source_created_after_ms": kwargs["time_range"]["source_created_after_ms"]
        }
        assert "source_created_after_ms" in kwargs["time_range"]


class TestSearchTimeRangeValidationErrors:
    @pytest.mark.asyncio
    async def test_inverted_range_returns_error_without_calling_service(self) -> None:
        retrieval_service = _empty_results_service()
        state = _make_state(retrieval_service=retrieval_service)

        result = await execute_search(
            state,
            query="test",
            created_after="2026-12-31",
            created_before="2026-01-01",
        )

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        retrieval_service.search_with_filters.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_future_created_after_returns_error(self) -> None:
        retrieval_service = _empty_results_service()
        state = _make_state(retrieval_service=retrieval_service)
        future = (datetime.now(timezone.utc) + timedelta(days=365)).strftime("%Y-%m-%d")

        result = await execute_search(state, query="test", created_after=future)

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        assert "future" in parsed["message"].lower()
        retrieval_service.search_with_filters.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_naive_datetime_returns_error(self) -> None:
        retrieval_service = _empty_results_service()
        state = _make_state(retrieval_service=retrieval_service)

        result = await execute_search(
            state, query="test", created_after="2026-01-15T08:00:00"
        )

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        retrieval_service.search_with_filters.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_malformed_date_returns_error(self) -> None:
        retrieval_service = _empty_results_service()
        state = _make_state(retrieval_service=retrieval_service)

        result = await execute_search(state, query="test", modified_before="not-a-date")

        parsed = json.loads(result)
        assert parsed["status"] == "error"
        retrieval_service.search_with_filters.assert_not_awaited()


class TestSearchTimeRangeFanOut:
    @pytest.mark.asyncio
    async def test_fan_out_forwards_time_range_to_every_call(self) -> None:
        """Multiple explicit source_ids trigger the per-source fan-out path
        (see `execute_search`'s `fan_out_sources` branch) — every parallel
        call must still carry the same time_range."""
        retrieval_service = AsyncMock()

        async def _search_side_effect(**kwargs):
            apps = kwargs["filter_groups"].get("apps") or []
            source_id = apps[0] if apps else "unknown"
            return {
                "status_code": 200,
                "searchResults": [
                    {"virtual_record_id": f"vr-{source_id}", "content": source_id}
                ],
                "virtual_to_record_map": {f"vr-{source_id}": {"id": source_id}},
            }

        retrieval_service.search_with_filters = AsyncMock(side_effect=_search_side_effect)
        state = _make_state(
            retrieval_service=retrieval_service,
            filters={"apps": ["app-1", "app-2"], "kb": []},
        )

        from unittest.mock import patch

        from app.utils.chat_helpers import CitationRefMapper

        with patch(
            "app.agents.actions.knowledge_graph.ops.search.get_flattened_results",
            new_callable=AsyncMock,
            return_value=[],
        ), patch(
            "app.agents.actions.knowledge_graph.ops.search.BlobStorage",
        ), patch(
            "app.agents.actions.knowledge_graph.ops.search.build_message_content_array",
            return_value=([], CitationRefMapper()),
        ):
            await execute_search(
                state,
                query="test",
                source_ids=["app-1", "app-2"],
                created_after="2026-01-01",
                created_before="2026-06-30",
            )

        assert retrieval_service.search_with_filters.await_count == 2
        for call in retrieval_service.search_with_filters.await_args_list:
            time_range = call.kwargs["time_range"]
            assert time_range == {
                "source_created_after_ms": time_range["source_created_after_ms"],
                "source_created_before_ms": time_range["source_created_before_ms"],
            }
            assert set(time_range.keys()) == {
                "source_created_after_ms",
                "source_created_before_ms",
            }
