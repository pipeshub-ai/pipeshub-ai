"""WP-112 ACTDETAIL: a tool step says which page, query, pattern or file, and what came of it.

A recorded research run (a claude_code child): every engine step read "Read a page", "Searched the
web" or "Glob" with no URL, query or result. The engine mappers dropped the call's input, and the gateway's result
reached the chat as raw text with no summary. These tests pin the facts end to end: each engine's mapper keeps them,
the shared builder (``app/harness/step_display.py``) words them, and the engine presenter puts them on the step.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.harness.engines.presenter import PipesHubEngineRunPresenter
from app.harness.step_display import (
    GatewaySteps,
    ShownResult,
    StepFacts,
    call_facts,
    result_facts,
)
from harness_core.engines.events import EngineEventMeta, ToolCallSeen, ToolCallUpdated
from harness_core.gateway.types import Citation, McpResult
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.core.types import Source, ToolResult

FIXTURE = Path(__file__).parent / "fixtures" / "wp112_onyx_child_steps.json"


class _Stamp:
    task_id = "run_1"
    turn_id = "trn_0123456789abcdef0123456789abcdef"

    def __init__(self) -> None:
        self._n = 0

    def allocate(self) -> int:
        self._n += 1
        return self._n


def _frames(events: list[Any]) -> list[dict[str, Any]]:
    return [dict(e.data) for e in events if e.type == "x.pipeshub.engine.progress"]


def _seen(call_id: str, tool: str, status: str, n: int, **kw: Any) -> ToolCallSeen:  # noqa: ANN401
    return ToolCallSeen(EngineEventMeta("trn", n), call_id, tool, status, **kw)  # type: ignore[arg-type]


def _ok(text: str, invocation: str, citations: tuple[Citation, ...] = ()) -> McpResult:
    return McpResult(
        text=text, is_error=False, disposition="executed", retryable=False, invocation_id=invocation, citations=citations
    )


# ---- the shared builder ----


class TestCallFacts:
    @pytest.mark.parametrize(
        ("tool", "args", "expected"),
        [
            ("dynamic__fetch_url", {"url": "https://www.onyx.app/pricing"}, StepFacts("readPage", "onyx.app", "https://www.onyx.app/pricing")),
            ("fetch_url", {"url": "https://onyx.app"}, StepFacts("readPage", "onyx.app", "https://onyx.app")),
            ("WebFetch", {"url": "https://docs.onyx.app", "prompt": "x"}, StepFacts("readPage", "docs.onyx.app", "https://docs.onyx.app")),
            ("dynamic__web_search", {"query": "PipesHub Reddit"}, StepFacts("webSearch", "PipesHub Reddit")),
            ("google_web_search", {"query": "onyx"}, StepFacts("webSearch", "onyx")),
            ("Glob", {"pattern": "**/*.md", "path": "/workspace"}, StepFacts("findFiles", "**/*.md")),
            ("glob", {"pattern": "src/**"}, StepFacts("findFiles", "src/**")),
            ("Grep", {"pattern": "def main"}, StepFacts("searchFiles", "def main")),
            ("Read", {"file_path": "/workspace/notes/progress.md"}, StepFacts("readFile", "notes/progress.md")),
            ("read", {"filePath": "/workspace/out/report.md"}, StepFacts("readFile", "out/report.md")),
            ("read_file", {"absolute_path": "/workspace/a.txt"}, StepFacts("readFile", "a.txt")),
            ("developer__text_editor", {"command": "view", "path": "/workspace/b.py"}, StepFacts("readFile", "b.py")),
            ("list_directory", {"dir_path": "/workspace/src"}, StepFacts("listFiles", "src")),
        ],
    )
    def test_each_engine_names_its_target(self, tool: str, args: dict[str, Any], expected: StepFacts) -> None:
        assert call_facts(tool, args) == expected

    def test_a_connector_tool_is_never_an_engine_tool(self) -> None:
        assert call_facts("github__read_file", {"path": "README.md"}) is None
        assert call_facts("developer__text_editor", {"command": "write", "path": "x"}) is None
        assert call_facts("jira__search_issues", {"query": "x"}) is None

    def test_an_acp_call_with_no_input_is_named_by_its_kind_locations_or_title(self) -> None:
        assert call_facts("view", None, kind="read", paths=("/workspace/c.md",)) == StepFacts("readFile", "c.md")
        assert call_facts("lookup", None, kind="search", title="Search 'TODO' in src") == StepFacts(
            "searchFiles", "Search 'TODO' in src"
        )

    def test_a_link_is_only_http_and_never_a_citation_alias(self) -> None:
        assert call_facts("dynamic__fetch_url", {"url": "javascript:alert(1)"}) == StepFacts("readPage")
        assert call_facts("dynamic__fetch_url", {"url": "https://ref3.xyz"}).href is None  # type: ignore[union-attr]


class TestResultFacts:
    def test_a_page_read_gets_its_title_and_its_cited_links(self) -> None:
        facts = result_facts(
            StepFacts("readPage", "onyx.app", "https://onyx.app"),
            sources=[("https://onyx.app#:~:text=Complete", "Onyx | Complete context"), ("https://onyx.app#:~:text=b", None)],
        )
        assert facts.title == "Onyx | Complete context"
        assert facts.sources == ("https://onyx.app",)

    def test_a_search_counts_its_hits_from_its_sources_or_its_text(self) -> None:
        assert result_facts(StepFacts("webSearch", "q"), sources=[("https://a.example/1", "A"), ("https://b.example", "B")]).count == 2
        empty = "web_search_query: q\nBlocks retrieved from web search:\n<untrusted_web_content source=\"web_search\">\n"
        assert result_facts(StepFacts("webSearch", "q"), text=empty).count == 0

    @pytest.mark.parametrize(
        ("text", "count"),
        [("/workspace/a.md\n/workspace/b.md\n", 2), ("No files found", 0), ("Found 3 files\na\nb\nc", 3), ("", 0)],
    )
    def test_a_find_counts_the_files_the_engine_listed(self, text: str, count: int) -> None:
        assert result_facts(StepFacts("findFiles", "*.md"), text=text).count == count

    def test_a_failed_call_adds_nothing(self) -> None:
        assert result_facts(StepFacts("webSearch", "q"), text="boom", failed=True) == StepFacts("webSearch", "q")


# ---- the gateway's port keeps what the ToolResult said ----


class _Tool:
    def summarize_args(self, args: dict[str, Any]) -> str | None:
        return None

    def summarize_result(self, args: dict[str, Any], result: ToolResult) -> str | None:
        return None

    def result_view(self, args: dict[str, Any], result: ToolResult) -> dict[str, Any] | None:
        return {"kind": "table", "columns": ["Title"], "rows": [{"cells": ["A"], "url": "https://a.example"}], "total": 1}


class _Registry:
    def resolve_by_name(self, name: str) -> _Tool:
        return _Tool()


async def test_gateway_steps_keep_the_loops_summaries_by_invocation() -> None:
    steps = GatewaySteps(_Registry())
    call = ToolCall(id="inv_1", name="dynamic__web_search", arguments={"query": "PipesHub Reddit"})
    result = ToolResult(
        tool_call_id="inv_1", name=call.name, content="text", sources=[Source(url="https://reddit.com/r/x", title="A thread")]
    )
    assert await steps.present(call, result) == ()
    kept = steps.take("inv_1")
    assert kept is not None
    # The summarizer's words, the same as the loop's step says them.
    assert kept.args_summary == 'Searched the web for "PipesHub Reddit"'
    assert kept.result_summary is not None and kept.result_summary.startswith("Found 1 result")
    assert kept.result_view is not None
    assert kept.facts == StepFacts("webSearch", "PipesHub Reddit", count=1, sources=("https://reddit.com/r/x",))
    assert steps.take("inv_1") is None


async def test_a_step_that_cannot_be_described_never_fails_the_call() -> None:
    class Broken:
        def resolve_by_name(self, name: str) -> Any:  # noqa: ANN401
            raise RuntimeError

    steps = GatewaySteps(Broken())
    call = ToolCall(id="inv_2", name="dynamic__fetch_url", arguments={"url": 3})
    assert await steps.present(call, ToolResult(tool_call_id="inv_2", name=call.name, content="x")) == ()


# ---- the engine presenter puts them on the step ----


def test_an_engine_web_read_names_its_page_and_link_and_its_result_summary() -> None:
    steps = GatewaySteps(_Registry())
    presenter = PipesHubEngineRunPresenter(steps=steps)
    stamp = _Stamp()
    args = {"url": "https://onyx.app"}
    started = _frames(presenter.event(stamp, _seen("call_a", "dynamic__fetch_url", "started", 1, arguments=args)))
    assert started[0]["data"]["step"] == {"action": "readPage", "target": "onyx.app", "href": "https://onyx.app"}
    assert json.loads(started[0]["data"]["args"]) == args
    # What the gateway's port kept of this call's ToolResult (``GatewaySteps.present``).
    steps._kept["inv_3"] = ShownResult(
        "Reading https://onyx.app", "Read “Onyx” (onyx.app)", None, StepFacts("readPage", "onyx.app", "https://onyx.app", "Onyx")
    )
    shown = _frames(presenter.tool_result(stamp, "dynamic__fetch_url", args, _ok("page text", "inv_3")))
    data = shown[0]["data"]
    assert data["callId"] == "call_a" and data["invocationId"] == "inv_3"
    assert data["step"]["title"] == "Onyx" and data["step"]["href"] == "https://onyx.app"
    assert data["argsSummary"] == "Reading https://onyx.app"
    assert data["resultSummary"] == "Read “Onyx” (onyx.app)"
    ended = _frames(presenter.event(stamp, _seen("call_a", "dynamic__fetch_url", "completed", 2, arguments=args)))
    # The gateway's line carried the result: the engine's end does not repeat it.
    assert "result" not in ended[0]["data"] and "step" not in ended[0]["data"]


def test_two_reads_in_flight_get_their_own_results_whatever_returns_first() -> None:
    presenter = PipesHubEngineRunPresenter()
    stamp = _Stamp()
    a, b = {"url": "https://onyx.app"}, {"url": "https://pipeshub.com"}
    presenter.event(stamp, _seen("call_a", "dynamic__fetch_url", "started", 1, arguments=a))
    presenter.event(stamp, _seen("call_b", "dynamic__fetch_url", "started", 2, arguments=b))
    first = _frames(presenter.tool_result(stamp, "dynamic__fetch_url", b, _ok("pipeshub page", "inv_b")))
    second = _frames(presenter.tool_result(stamp, "dynamic__fetch_url", a, _ok("onyx page", "inv_a")))
    assert (first[0]["data"]["callId"], first[0]["data"]["result"]) == ("call_b", "pipeshub page")
    assert (second[0]["data"]["callId"], second[0]["data"]["result"]) == ("call_a", "onyx page")


def test_an_engine_find_shows_its_pattern_and_how_many_files() -> None:
    presenter = PipesHubEngineRunPresenter()
    stamp = _Stamp()
    args = {"pattern": "**/*.md"}
    started = _frames(presenter.event(stamp, _seen("call_g", "Glob", "started", 1, arguments=args)))
    assert started[0]["data"]["step"] == {"action": "findFiles", "target": "**/*.md"}
    ended = _frames(
        presenter.event(
            stamp, _seen("call_g", "Glob", "completed", 2, arguments=args, result="/workspace/a.md\n/workspace/b.md")
        )
    )
    assert ended[0]["data"]["step"] == {"action": "findFiles", "target": "**/*.md", "count": 2}
    assert ended[0]["data"]["result"] == "/workspace/a.md\n/workspace/b.md"


def test_late_input_is_one_steps_update_and_the_result_is_still_its_own() -> None:
    presenter = PipesHubEngineRunPresenter()
    stamp = _Stamp()
    first = _frames(presenter.event(stamp, _seen("call_r", "dynamic__fetch_url", "started", 1, arguments={})))
    update = ToolCallUpdated(EngineEventMeta("trn", 2), "call_r", "dynamic__fetch_url", arguments={"url": "https://onyx.app"})
    late = _frames(presenter.event(stamp, update))
    assert [f["kind"] for f in first + late] == ["tool", "tool_update"]
    assert late[0]["data"]["callId"] == "call_r" and late[0]["data"]["step"]["target"] == "onyx.app"
    # The waiting step asks with its new input: the gateway's result is still its own.
    shown = _frames(presenter.tool_result(stamp, "dynamic__fetch_url", {"url": "https://onyx.app"}, _ok("x", "inv_r")))
    assert shown[0]["data"]["callId"] == "call_r"


def test_a_start_sent_twice_is_still_one_step() -> None:
    presenter = PipesHubEngineRunPresenter()
    stamp = _Stamp()
    args = {"url": "https://onyx.app"}
    frames = _frames(presenter.event(stamp, _seen("c", "dynamic__fetch_url", "started", 1, arguments=args)))
    frames += _frames(presenter.event(stamp, _seen("c", "dynamic__fetch_url", "started", 2, arguments=args)))
    assert [f["kind"] for f in frames] == ["tool", "tool_update"]
    assert len(_frames(presenter.tool_result(stamp, "dynamic__fetch_url", args, _ok("x", "inv")))) == 1
    assert _frames(presenter.tool_result(stamp, "dynamic__fetch_url", args, _ok("y", "inv2"))) == []


# ---- the recorded run's steps, replayed ----


def test_the_onyx_runs_steps_say_which_page_and_which_query() -> None:
    """Every web read of the run names its host and link, every search its query and its hits (0: the searches
    found nothing), and each gateway result names its invocation (its tool.intent and its citations)."""
    fixture = json.loads(FIXTURE.read_text())
    presenter = PipesHubEngineRunPresenter()
    stamp = _Stamp()
    frames: list[dict[str, Any]] = []
    for n, item in enumerate(fixture["events"]):
        match item["kind"]:
            case "started":
                frames += _frames(presenter.event(stamp, _seen(item["callId"], item["tool"], "started", n, arguments=item["arguments"])))
            case "gateway":
                cited = tuple(Citation(c["ref"], c["url"]) for c in item["citations"])
                result = McpResult(
                    text=item["text"], is_error=item["failed"], disposition="executed", retryable=False,
                    invocation_id=item["invocationId"], citations=cited,
                )
                frames += _frames(presenter.tool_result(stamp, item["tool"], item["arguments"] or {}, result))
            case "ended":
                frames += _frames(presenter.event(stamp, _seen(item["callId"], item["tool"], item["status"], n)))
    # Each step as the chat folds its frames (``engine-steps.ts``): what it asked, then what came back.
    steps: dict[str, dict[str, Any]] = {}
    tools: dict[str, str] = {}
    for f in frames:
        tools.setdefault(f["data"]["callId"], f["text"])
        if "step" in f["data"]:
            known = steps.get(f["data"]["callId"], {})
            steps[f["data"]["callId"]] = {**f["data"]["step"], **{k: v for k, v in known.items() if k in ("target", "href")}}
    results = [f for f in frames if f["kind"] == "tool_result" and "invocationId" in f["data"]]
    assert len(results) == sum(1 for e in fixture["events"] if e["kind"] == "gateway")
    served = {f["data"]["callId"] for f in results}
    reads = {c: s for c, s in steps.items() if tools[c] == "dynamic__fetch_url" and c in served}
    searches = {c: s for c, s in steps.items() if tools[c] == "dynamic__web_search" and c in served}
    assert reads and all(s.get("target") and s["href"].startswith("https://") for s in reads.values())
    assert {s["target"] for s in reads.values()} >= {"onyx.app", "pipeshub.com", "docs.onyx.app"}
    onyx = next(s for s in reads.values() if s["target"] == "onyx.app")
    assert onyx["sources"] == ["https://onyx.app"]
    assert searches and all(s.get("target") and s["count"] == 0 for s in searches.values())
    assert {s["target"] for s in searches.values()} >= {"PipesHub Reddit", "Onyx Danswer Hacker News"}


def test_a_call_through_call_tool_is_described_by_the_tool_it_calls() -> None:
    """An engine that reaches a deferred tool through the gateway's ``call_tool`` reports the wrapper's arguments."""
    presenter = PipesHubEngineRunPresenter()
    stamp = _Stamp()
    wrapped = {"name": "dynamic__fetch_url", "arguments": {"url": "https://onyx.app"}}
    started = _frames(presenter.event(stamp, _seen("call_w", "dynamic__fetch_url", "started", 1, arguments=wrapped)))
    assert started[0]["data"]["step"]["target"] == "onyx.app"
    shown = _frames(presenter.tool_result(stamp, "call_tool", wrapped, _ok("x", "inv_w")))
    assert shown[0]["data"]["callId"] == "call_w"
