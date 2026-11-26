"""EU-4: the durable presenter shows a failed result by its code only; the detail and a summary made from it stay
off the person's stream (QA17-O1)."""

from __future__ import annotations

from types import SimpleNamespace

from app.harness.presentation import PipesHubPresenter
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.types import ToolResult
from harness_core.tools.registry import ToolRegistry

DETAIL = "ConnectError: All connection attempts failed (bolt://10.0.0.7:7687)"


def _presenter() -> PipesHubPresenter:
    context = SimpleNamespace(tool_state={}, root_agent_spec=None, conversation_id=None)
    return PipesHubPresenter(context, ToolRegistry())


async def test_eu_4_a_failed_result_is_presented_without_its_detail() -> None:
    call = ToolCall(id="t1.0", name="web_search", arguments={"query": "status"})
    failed = ToolResult(tool_call_id="t1.0", name="web_search", content=DETAIL, is_error=True, error_code="unavailable")

    events = await _presenter().result(call, failed)

    shown = next(e for e in events if e.type == "x.pipeshub.tool.result_presented")
    # The step keeps what the call asked (WP-115), never anything of the failure.
    assert shown.data == {
        "callId": "t1.0", "status": "failed", "content": "", "step": {"action": "webSearch", "target": "status"},
    }


async def test_eu_4_a_completed_result_keeps_its_preview() -> None:
    call = ToolCall(id="t1.0", name="web_search", arguments={"query": "status"})
    ok = ToolResult(tool_call_id="t1.0", name="web_search", content="3 results")

    events = await _presenter().result(call, ok)

    shown = next(e for e in events if e.type == "x.pipeshub.tool.result_presented")
    assert (shown.data["status"], shown.data["content"]) == ("completed", "3 results")
