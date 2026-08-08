"""Unit tests for the POST_TOOL_USE `filtered_retrieval` middleware: it
must run PipesHub content search ONLY when `content_query` was stashed by
the PRE hook, scope it to exactly the tool's own `virtual_record_id`s, and
degrade to the plain listing (with a message) when retrieval can't run."""

import importlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
from app.agent_loop_lib.tools.base import ToolOutput

# See test_filter_value_resolution.py for why this must go through
# importlib rather than `import ... as fr_module` (package re-export
# shadowing).
fr_module = importlib.import_module("app.agents.agent_loop.hooks.filtered_retrieval")
from app.agents.agent_loop.hooks.filtered_retrieval import filtered_retrieval  # noqa: E402


def _context(retrieval_service=None, user_id: str = "u1", org_id: str = "o1") -> SimpleNamespace:
    return SimpleNamespace(retrieval_service=retrieval_service, user_id=user_id, org_id=org_id)


def _tool_result_ctx(payload: dict, metadata: dict | None = None) -> ToolResultContext:
    return ToolResultContext(
        tool_path="/tools/filtered_search/search_jira_issues",
        tool_use_id=__import__("uuid").uuid4(),
        tool_response=ToolOutput(success=True, data=json.dumps(payload)),
        metadata=metadata or {},
    )


async def _noop_next() -> None:
    return None


async def test_no_content_query_leaves_tool_response_untouched() -> None:
    middleware = filtered_retrieval(_context())
    payload = {"records": [{"record_id": "r1", "virtual_record_id": "vr1"}]}
    ctx = _tool_result_ctx(payload, metadata={"filtered_search_call": {"content_query": None, "limit": 50}})
    original = ctx.tool_response

    await middleware(ctx, _noop_next)

    assert ctx.tool_response is original


async def test_failed_tool_call_is_left_untouched() -> None:
    middleware = filtered_retrieval(_context())
    ctx = ToolResultContext(
        tool_path="/tools/filtered_search/search_jira_issues",
        tool_use_id=__import__("uuid").uuid4(),
        tool_response=ToolOutput(success=False, error="boom"),
        metadata={"filtered_search_call": {"content_query": "token refresh"}},
    )
    original = ctx.tool_response

    await middleware(ctx, _noop_next)

    assert ctx.tool_response is original


async def test_content_query_present_runs_scoped_retrieval(monkeypatch) -> None:
    search_mock = AsyncMock(return_value={"searchResults": [{"id": "hit-1"}, {"id": "hit-2"}]})
    monkeypatch.setattr(fr_module, "search_within_virtual_record_ids", search_mock)

    retrieval_service = object()
    middleware = filtered_retrieval(_context(retrieval_service=retrieval_service, user_id="u1", org_id="o1"))
    payload = {
        "records": [
            {"record_id": "r1", "virtual_record_id": "vr1"},
            {"record_id": "r2", "virtual_record_id": "vr2"},
            {"record_id": "r3", "virtual_record_id": None},
        ],
        "accessible_count": 3,
    }
    ctx = _tool_result_ctx(payload, metadata={"filtered_search_call": {"content_query": "token refresh", "limit": 20}})

    await middleware(ctx, _noop_next)

    search_mock.assert_awaited_once_with(
        retrieval_service, ["vr1", "vr2"], "token refresh", "u1", "o1", limit=20,
    )
    new_payload = json.loads(ctx.tool_response.data)
    assert new_payload["content_matches"] == 2
    assert new_payload["results"] == [{"id": "hit-1"}, {"id": "hit-2"}]
    assert "records" not in new_payload
    assert new_payload["accessible_count"] == 3  # other tool-output fields preserved


async def test_default_limit_used_when_not_stashed(monkeypatch) -> None:
    search_mock = AsyncMock(return_value={"searchResults": []})
    monkeypatch.setattr(fr_module, "search_within_virtual_record_ids", search_mock)

    middleware = filtered_retrieval(_context())
    payload = {"records": [{"record_id": "r1", "virtual_record_id": "vr1"}]}
    ctx = _tool_result_ctx(payload, metadata={"filtered_search_call": {"content_query": "q"}})

    await middleware(ctx, _noop_next)

    assert search_mock.call_args.kwargs["limit"] == fr_module._DEFAULT_LIMIT


async def test_retrieval_unavailable_falls_back_to_message(monkeypatch) -> None:
    """`search_within_virtual_record_ids` returning `None` (no retrieval
    service, or the call failed) must produce a friendly message, not an
    empty/broken payload."""
    monkeypatch.setattr(fr_module, "search_within_virtual_record_ids", AsyncMock(return_value=None))

    middleware = filtered_retrieval(_context())
    payload = {"records": [{"record_id": "r1", "virtual_record_id": "vr1"}]}
    ctx = _tool_result_ctx(payload, metadata={"filtered_search_call": {"content_query": "q"}})

    await middleware(ctx, _noop_next)

    new_payload = json.loads(ctx.tool_response.data)
    assert new_payload["records"] == payload["records"]  # plain listing preserved
    assert "message" in new_payload
    assert new_payload["content_query"] == "q"


async def test_non_json_tool_output_is_skipped_without_raising() -> None:
    middleware = filtered_retrieval(_context())
    ctx = ToolResultContext(
        tool_path="/tools/filtered_search/search_jira_issues",
        tool_use_id=__import__("uuid").uuid4(),
        tool_response=ToolOutput(success=True, data="not json"),
        metadata={"filtered_search_call": {"content_query": "q"}},
    )
    original = ctx.tool_response

    await middleware(ctx, _noop_next)

    assert ctx.tool_response is original


async def test_missing_metadata_is_treated_as_no_content_query() -> None:
    middleware = filtered_retrieval(_context())
    ctx = _tool_result_ctx({"records": []}, metadata={})
    original = ctx.tool_response

    await middleware(ctx, _noop_next)

    assert ctx.tool_response is original
