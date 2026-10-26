"""`retrieval_context_emission` POST_TOOL_USE hook and the prefetch status
mapping used by the bridge's single prefetch frame."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.agent_loop_lib.hooks.middleware.context import ToolResultContext
from app.agent_loop_lib.tools.base import ToolOutput
from app.agents.agent_loop.hooks.retrieval_context import retrieval_context_emission
from app.agents.agent_loop.retrieval_ledger import prefetch_status
from tests.unit.agents.adapter.conftest import make_context


class _RecordingSink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def write(self, event: dict[str, Any]) -> None:
        await asyncio.sleep(0)
        self.events.append(event)


def _context() -> Any:  # noqa: ANN401
    return make_context(
        protocol="agui", run_id="run-1", include_retrieval_context=True,
        event_sink=_RecordingSink(),
    )


def _tool_ctx(output: ToolOutput) -> ToolResultContext:
    return ToolResultContext(
        tool_path="/toolsets/knowledgegraph/search", tool_use_id=uuid4(), tool_response=output,
    )


class TestPrefetchStatus:
    @pytest.mark.parametrize(
        ("scheduled", "result", "expected"),
        [
            (False, None, ("skipped", "mode_no_prefetch", None)),
            (True, None, ("skipped", "followup", None)),
            (True, SimpleNamespace(is_empty=True, error_message="503"), ("error", None, "503")),
            (True, SimpleNamespace(is_empty=True, error_message=None), ("empty", None, None)),
            (True, SimpleNamespace(is_empty=False, error_message=None), ("ok", None, None)),
        ],
    )
    def test_mapping(self, scheduled: bool, result: Any, expected: tuple[Any, ...]) -> None:  # noqa: ANN401
        assert prefetch_status(scheduled=scheduled, result=result) == expected


class TestRetrievalContextHook:
    async def test_emits_tool_frame_after_the_tool_ran(self) -> None:
        context = _context()
        ctx = _tool_ctx(ToolOutput(success=True, data="ok"))

        async def _next() -> None:
            context.tool_state["final_results"] = [
                {"virtual_record_id": "vr-1", "block_index": 3, "metadata": {"recordId": "r-1"}},
            ]

        await retrieval_context_emission(context)(ctx, _next)

        value = context.event_sink.events[0]["data"]["value"]
        assert value["source"] == "tool"
        assert value["status"] == "ok"
        assert value["toolCallId"] == str(ctx.tool_use_id)
        assert value["toolName"]
        assert value["records"][0]["blockIndices"] == [3]

    async def test_tool_that_added_nothing_emits_nothing(self) -> None:
        context = _context()

        async def _next() -> None:
            return None

        await retrieval_context_emission(context)(_tool_ctx(ToolOutput(success=True, data="ok")), _next)

        assert context.event_sink.events == []

    async def test_failed_tool_reports_error_status(self) -> None:
        context = _context()

        async def _next() -> None:
            return None

        await retrieval_context_emission(context)(
            _tool_ctx(ToolOutput(success=False, error="search backend down")), _next,
        )

        value = context.event_sink.events[0]["data"]["value"]
        assert value["status"] == "error"
        assert value["errorMessage"] == "search backend down"
