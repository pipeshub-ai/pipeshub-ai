"""WP-117 (WP108-FU-4): the durable presenter journals what main's legacy stream shows of a call and a question.

Legacy's saved tool part keeps this request's citation refs as the URLs they stand for (``TranscriptCollector``),
while its live frames keep the tool's own words; a question card carries the time its kept turn runs out
(``parked_turn.with_expiry``). The presenter builds each with the same function, so the two paths cannot drift.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.agents.agent_loop import parked_turn
from app.agents.agent_loop.protocol.transcript_collector import (
    TranscriptCollector,
    serialize_tool_args,
)
from app.harness.citations import CitationJournal
from app.harness.engines.presenter import PipesHubEngineRunPresenter
from app.harness.labels import unknown_label
from app.harness.presentation import PipesHubPresenter
from app.utils.chat_helpers import CitationRefMapper
from harness_core.engines.durable.ports import ProgressStamp
from harness_core.gateway.asking import AskedQuestion
from harness_core.kernel.core.messages import AssistantMessage, ToolCall
from harness_core.kernel.core.types import ToolResult
from harness_core.tools.registry import ToolRegistry

URL = "https://kb.example/record/r1/preview#blockIndex=0"
ASK = "internaltools__ask_user_question"


def _presenter(*, refs: bool = True) -> tuple[Any, PipesHubPresenter]:
    context = SimpleNamespace(tool_state={}, root_agent_spec=None, conversation_id=None, has_ui_client=True)
    if refs:
        mapper = CitationRefMapper()
        _ = mapper.get_or_create_ref(URL)
        context.tool_state["citation_ref_mapper"] = mapper
    return context, PipesHubPresenter(context, ToolRegistry(), citations=CitationJournal(context))


def _legacy_part(args: dict[str, Any], content: str, summary: str | None = None) -> dict[str, Any]:
    """The tool part main's collector saves for the same call, refs read from the same mapper."""
    mapper = CitationRefMapper()
    _ = mapper.get_or_create_ref(URL)
    collector = TranscriptCollector(ref_to_url=lambda: mapper.ref_to_url)
    part: dict[str, Any] = {"args": collector._durable_args(args, "c1")}  # pyright: ignore[reportPrivateUsage]
    part["resultPreview"] = collector._durable(content, "c1", 2000)  # pyright: ignore[reportPrivateUsage]
    if summary is not None:
        part["resultSummary"] = collector._durable(summary, "c1")  # pyright: ignore[reportPrivateUsage]
    return part


def _presented(presenter: PipesHubPresenter, args: dict[str, Any]) -> dict[str, Any]:
    message = AssistantMessage(text="", tool_calls=[ToolCall(id="c1", name="drive__read_file", arguments=args)])
    [shown] = [e for e in presenter.response(message) if e.type == "x.pipeshub.tool.call_presented"]
    return cast("dict[str, Any]", shown.data)


async def _result(presenter: PipesHubPresenter, content: str) -> dict[str, Any]:
    call = ToolCall(id="c1", name="drive__read_file", arguments={})
    events = await presenter.result(call, ToolResult(tool_call_id="c1", name="drive__read_file", content=content))
    [shown] = [e for e in events if e.type == "x.pipeshub.tool.result_presented"]
    return cast("dict[str, Any]", shown.data)


def test_wp117_1_saved_arguments_keep_refs_as_urls_as_legacy_saves_them() -> None:
    _, presenter = _presenter()
    data = _presented(presenter, {"block": "ref1"})
    assert data["argsJson"] == '{"block": "ref1"}'
    assert "args" not in data
    assert data["savedArgs"] == _legacy_part({"block": "ref1"}, "")["args"] == f'{{"block": "{URL}"}}'


def test_wp117_2_arguments_without_refs_journal_no_saved_copy() -> None:
    _, presenter = _presenter()
    data = _presented(presenter, {"query": "paid leave"})
    assert "savedArgs" not in data and "args" not in data


def test_wp117_3_long_arguments_keep_the_bounded_preview_the_live_frame_shows() -> None:
    _, presenter = _presenter()
    args = {"code": "x" * 1500}
    data = _presented(presenter, args)
    assert data["args"] == serialize_tool_args(args)
    assert "savedArgs" not in data


async def test_wp117_4_the_saved_preview_keeps_record_links_the_live_result_keeps_refs() -> None:
    _, presenter = _presenter()
    content = "[ref1] The Q3 launch moves to May."
    data = await _result(presenter, content)
    assert data["content"] == content
    assert data["savedContent"] == _legacy_part({}, content)["resultPreview"] == f"[{URL}] The Q3 launch moves to May."


async def test_wp117_5_a_result_without_refs_journals_no_saved_copy() -> None:
    _, presenter = _presenter()
    data = await _result(presenter, "25 days a year.")
    assert "savedContent" not in data and "savedSummary" not in data


async def test_wp117_6_a_ref_no_mapping_knows_is_saved_uncitable_as_legacy_saves_it() -> None:
    _, presenter = _presenter(refs=False)
    data = await _result(presenter, "see ref7")
    assert data["savedContent"] == "see earlier-turn source"


_NOW = datetime(2026, 3, 1, 9, 30, tzinfo=UTC)


class _FrozenClock(datetime):
    @classmethod
    def now(cls, tz: Any = None) -> datetime:  # noqa: ANN401
        return _NOW.astimezone(tz)


@pytest.fixture
def frozen(monkeypatch: pytest.MonkeyPatch) -> str:
    monkeypatch.setattr(parked_turn, "datetime", _FrozenClock)
    return (_NOW + timedelta(seconds=parked_turn.QUESTION_TTL_SECONDS)).strftime("%Y-%m-%dT%H:%M:%SZ")


async def test_wp117_7_the_question_card_carries_the_time_it_expires(frozen: str) -> None:
    _, presenter = _presenter()
    call = ToolCall(id="c-ask", name=ASK, arguments={})
    payload = '{"name": "ask_user_question", "questions": [{"question": "Which region?"}]}'
    events = await presenter.result(call, ToolResult(tool_call_id="c-ask", name=ASK, content=payload))
    [asked] = [e for e in events if e.type == "x.pipeshub.question.asked"]
    assert asked.data["toolData"]["expiresAt"] == frozen  # type: ignore[index]


async def test_wp117_8_a_pre_run_clarification_card_carries_it_too(frozen: str) -> None:
    _, presenter = _presenter()
    questions = [{"question": "Which team?", "options": [{"label": "Sales"}, {"label": "Support"}], "multiSelect": False}]
    events = await presenter._clarify("A report", questions, label=unknown_label(), integrity="first_party")  # pyright: ignore[reportPrivateUsage]
    [asked] = [e for e in events if e is not None and e.type == "x.pipeshub.question.asked"]
    assert asked.data["toolData"]["expiresAt"] == frozen  # type: ignore[index]


def test_wp117_9_an_engine_question_card_carries_it_too(frozen: str) -> None:
    presenter = PipesHubEngineRunPresenter.__new__(PipesHubEngineRunPresenter)
    presenter._log = SimpleNamespace(info=lambda *a, **k: None)  # type: ignore[attr-defined]  # pyright: ignore[reportPrivateUsage]
    stamp = ProgressStamp(task_id="task-1", turn_id="turn-1", allocate=lambda: 1)
    [asked] = presenter.question(stamp, AskedQuestion("Which region?", ("EU", "US")))
    assert asked.data["toolData"]["expiresAt"] == frozen  # type: ignore[index]
