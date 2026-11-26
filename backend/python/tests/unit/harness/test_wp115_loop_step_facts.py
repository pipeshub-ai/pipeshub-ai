"""WP-115 (WP112-FU-1): the built-in agent's own steps carry the same facts as an engine's, from the one builder
(``app/harness/step_display.py``), on ``x.pipeshub.tool.call_presented`` and ``result_presented``.

Before, a loop step read "Read a page" with its page only inside the expanded summary, while an engine's step read
"Read a page · onyx.app"; the presented events had no room for the facts.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

from app.harness.citations import CitationJournal
from app.harness.presentation import PipesHubPresenter
from app.harness.step_display import MAX_LINK_CHARS, step_of
from harness_core.kernel.core.messages import AssistantMessage, ToolCall
from harness_core.kernel.core.types import Source, ToolResult
from harness_core.tools.registry import ToolRegistry

PAGE = "https://www.onyx.app/pricing"


def _presenter() -> PipesHubPresenter:
    context = SimpleNamespace(tool_state={}, root_agent_spec=None, conversation_id=None)
    return PipesHubPresenter(context, ToolRegistry(), citations=CitationJournal(context))


def _presented(presenter: PipesHubPresenter, *calls: ToolCall) -> list[dict[str, Any]]:
    events = presenter.response(AssistantMessage(tool_calls=list(calls)))
    return [e.data for e in events if e.type == "x.pipeshub.tool.call_presented"]


async def _result(presenter: PipesHubPresenter, call: ToolCall, result: ToolResult) -> dict[str, Any]:
    [shown] = [e.data for e in await presenter.result(call, result) if e.type == "x.pipeshub.tool.result_presented"]
    return shown


def test_ls1_a_page_read_names_its_host_and_link_when_called() -> None:
    [shown] = _presented(_presenter(), ToolCall(id="t1.0", name="dynamic__fetch_url", arguments={"url": PAGE}))
    assert shown["step"] == {"action": "readPage", "target": "onyx.app", "href": PAGE}


def test_ls2_a_search_names_its_query_and_a_tool_of_no_such_action_carries_none() -> None:
    search = ToolCall(id="t1.0", name="dynamic__web_search", arguments={"query": "PipesHub Reddit"})
    jira = ToolCall(id="t1.1", name="jira__search_issues", arguments={"query": "open bugs"})
    first, second = _presented(_presenter(), search, jira)
    assert first["step"] == {"action": "webSearch", "target": "PipesHub Reddit"}
    assert "step" not in second


def test_ls3_a_call_through_call_tool_is_named_by_the_tool_it_runs() -> None:
    wrapped = ToolCall(
        id="t1.0", name="call_tool", arguments={"name": "dynamic__fetch_url", "arguments": {"url": PAGE}}
    )
    [shown] = _presented(_presenter(), wrapped)
    assert shown["step"]["action"] == "readPage"


async def test_ls4_a_page_read_s_result_adds_the_title_and_the_links_it_returned() -> None:
    call = ToolCall(id="t1.0", name="dynamic__fetch_url", arguments={"url": PAGE})
    result = ToolResult(
        tool_call_id="t1.0", name=call.name, content="Onyx pricing ...",
        sources=[Source(url=PAGE + "#:~:text=Pricing", title="Onyx pricing")],
    )
    shown = await _result(_presenter(), call, result)
    assert shown["step"] == {
        "action": "readPage", "target": "onyx.app", "href": PAGE, "title": "Onyx pricing", "sources": [PAGE],
    }


async def test_ls5_a_search_s_result_counts_its_hits_and_none_is_no_results() -> None:
    call = ToolCall(id="t1.0", name="dynamic__web_search", arguments={"query": "onyx"})
    hits = [Source(url=f"https://example.com/{i}", title=f"Hit {i}") for i in range(3)]
    shown = await _result(_presenter(), call, ToolResult(tool_call_id="t1.0", name=call.name, content="...", sources=hits))
    assert shown["step"]["count"] == 3
    empty = "web_search_query: onyx\nBlocks retrieved from web search:\n"
    shown = await _result(_presenter(), call, ToolResult(tool_call_id="t1.0", name=call.name, content=empty))
    assert shown["step"]["count"] == 0


async def test_ls6_a_failed_step_keeps_what_it_asked_and_nothing_of_the_failure() -> None:
    call = ToolCall(id="t1.0", name="dynamic__fetch_url", arguments={"url": PAGE})
    failed = ToolResult(tool_call_id="t1.0", name=call.name, content="403 from https://evil.example", is_error=True)
    shown = await _result(_presenter(), call, failed)
    assert shown["status"] == "failed"
    assert shown["step"] == {"action": "readPage", "target": "onyx.app", "href": PAGE}


def test_ls7_a_link_too_long_for_the_event_is_left_out_never_the_event() -> None:
    long = "https://onyx.app/" + "a" * MAX_LINK_CHARS
    [shown] = _presented(_presenter(), ToolCall(id="t1.0", name="dynamic__fetch_url", arguments={"url": long}))
    assert shown["step"] == {"action": "readPage"}


def test_ls8_the_loop_and_the_gateway_describe_a_step_with_one_builder() -> None:
    result = ToolResult(tool_call_id="c", name="dynamic__fetch_url", content="x", sources=[Source(url=PAGE, title="T")])
    facts = step_of("dynamic__fetch_url", {"url": PAGE}, result)
    assert facts is not None and facts.title == "T" and facts.href == PAGE
    assert step_of("jira__search_issues", {"query": "q"}, result) is None
