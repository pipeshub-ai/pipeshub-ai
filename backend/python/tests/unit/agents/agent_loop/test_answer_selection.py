"""ANSWER_DRAFT_SELECTION: the switch, per-run state, what reaches the client, the selector's input, and the choice."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import TYPE_CHECKING, Any

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from app.agent_loop_lib.core.context import CancellationToken
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.responses import TokenUsage
from app.agent_loop_lib.core.types import AgentResult, AgentTurn, Goal
from app.agents.agent_loop import answer_selection
from app.agents.agent_loop.answer_selection import (
    MAX_DRAFT_RUNS,
    MAX_RECORDS,
    RECORD_CHARS,
    TOTAL_RECORD_CHARS,
    Draft,
    DraftEventSink,
    ProgressGate,
    answer_draft_runs,
    assemble_records,
    candidate_text,
    cited_record_order,
    fork_chat_state,
    label_order,
    pick_label,
    select_among_drafts,
)
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.protocol.run_usage import (
    ANSWER_DRAFT_SOURCE_PREFIX,
    ANSWER_SELECTION_SOURCE,
)
from app.utils.chat_helpers import CitationRefMapper
from app.utils.conversation_tasks import pop_tasks, register_task

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

_QUESTION = "Which firmware build fixed the kiosk printer jam?"
_CONVERSATION = "conv-kiosk"


def _url(record_id: str, block: int = 0) -> str:
    return f"http://localhost:3000/record/{record_id}/preview#blockIndex={block}"


def _record(record_id: str, name: str, *blocks: str) -> dict[str, Any]:
    return {
        "id": record_id,
        "record_name": name,
        "block_containers": {"blocks": [{"index": i, "type": "text", "data": text} for i, text in enumerate(blocks)]},
    }


class _Sink:
    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def write(self, event: dict[str, Any]) -> bool:
        self.events.append(event)
        return True


class _Selector:
    """A chat model that replies with ``reply`` (or its result for the prompt), or raises it."""

    def __init__(self, reply: str | BaseException | Callable[[str], str]) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def ainvoke(self, messages: list[BaseMessage], config: object = None, **kwargs: object) -> AIMessage:
        prompt = str(messages[-1].content)
        self.prompts.append(prompt)
        if isinstance(self.reply, BaseException):
            raise self.reply
        text = self.reply(prompt) if callable(self.reply) else self.reply
        return AIMessage(content=text, usage_metadata={"input_tokens": 700, "output_tokens": 45, "total_tokens": 745})


def _answers_in(prompt: str) -> dict[str, str]:
    return dict(re.findall(r"=== Answer ([A-Z]) ===\n(.*?)(?=\n\n=== Answer |\n\nRECORDS THE ANSWERS CITE)", prompt, re.S))


def _picks(fragment: str) -> Callable[[str], str]:
    """A selector reply that picks the answer containing ``fragment``."""

    def _reply(prompt: str) -> str:
        label = next(label for label, text in _answers_in(prompt).items() if fragment in text)
        return f'Here is my verdict.\n```json\n{{"asks": "the build", "pick": "{label}"}}\n```'

    return _reply


def _draft(index: int, client: _Sink, gate: ProgressGate, *records: dict[str, Any], usage: bool = False) -> Draft:
    """A run that has read ``records``; its answer cites them as ref1, ref2, ..."""
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", query=_QUESTION,
        conversation_id=_CONVERSATION, include_retrieval_context=usage,
    )
    mapper = CitationRefMapper()
    for record in records:
        mapper.get_or_create_ref(_url(record["id"]))
    context.tool_state["citation_ref_mapper"] = mapper
    context.tool_state["virtual_record_id_to_result"] = {f"vr-{record['id']}": record for record in records}
    sink = DraftEventSink(client, gate, index)
    context.event_sink = sink
    return Draft(index, context, sink)


def _answers(*outcomes: str | Exception) -> Callable[[Draft], Awaitable[None]]:
    async def _run(draft: Draft) -> None:
        outcome = outcomes[draft.index]
        if isinstance(outcome, Exception):
            raise outcome
        draft.result = AgentResult(goal=Goal(description=_QUESTION), output=outcome)

    return _run


async def _select(
    drafts: list[Draft], run: Callable[[Draft], Awaitable[None]], selector: object, client: _Sink,
    token: CancellationToken | None = None,
) -> answer_selection.Choice:
    return await select_among_drafts(
        drafts, run, client_sink=client, question=_QUESTION, llm=selector,  # type: ignore[arg-type]
        cancellation_token=token,
    )


@pytest.fixture(autouse=True)
def _not_adaptive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANSWER_DRAFT_SELECTION_ADAPTIVE", raising=False)


@pytest.mark.parametrize(
    ("value", "knowledge_answer", "expected"),
    [
        (None, True, 0),
        ("", True, 0),
        ("1", True, 0),
        ("yes", True, 0),
        ("2", True, 2),
        ("4", True, 4),
        ("40", True, MAX_DRAFT_RUNS),
        ("2", False, 0),
    ],
)
def test_runs_only_for_two_or_more_and_only_for_knowledge_answers(
    monkeypatch: pytest.MonkeyPatch, value: str | None, knowledge_answer: bool, expected: int,
) -> None:
    if value is None:
        monkeypatch.delenv("ANSWER_DRAFT_SELECTION", raising=False)
    else:
        monkeypatch.setenv("ANSWER_DRAFT_SELECTION", value)
    assert answer_draft_runs(knowledge_answer=knowledge_answer) == expected


def test_a_request_that_resumes_an_approved_call_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION", "3")
    assert answer_draft_runs(knowledge_answer=True, resumes_a_call=True) == 0


def test_forked_states_share_services_but_no_results() -> None:
    service = object()
    mapper = CitationRefMapper()
    mapper.get_or_create_ref(_url("attached"))
    state: dict[str, Any] = {
        "graph_provider": service, "final_results": [], "virtual_record_id_to_result": {},
        "known_record_ids": set(), "filters": {"kb": ["kb-1"]}, "citation_ref_mapper": mapper, "query": "q",
    }

    first, second = fork_chat_state(state), fork_chat_state(state)
    first["final_results"].append({"virtual_record_id": "vr-1"})
    first["virtual_record_id_to_result"]["vr-1"] = {"id": "rec-1"}
    first["known_record_ids"].add("rec-1")
    first["filters"]["kb"].append("kb-2")
    first["citation_ref_mapper"].get_or_create_ref(_url("rec-1"))

    assert first["graph_provider"] is service and second["graph_provider"] is service
    for untouched in (state, second):
        assert untouched["final_results"] == []
        assert untouched["virtual_record_id_to_result"] == {}
        assert untouched["known_record_ids"] == set()
        assert untouched["filters"] == {"kb": ["kb-1"]}
        assert untouched["citation_ref_mapper"].ref_to_url == {"ref1": _url("attached")}
    assert first["citation_ref_mapper"].ref_to_url == {"ref1": _url("attached"), "ref2": _url("rec-1")}


async def test_only_the_run_that_reports_first_shows_progress_and_no_run_shows_answer_text() -> None:
    client, gate = _Sink(), ProgressGate()
    first, second = DraftEventSink(client, gate, 0), DraftEventSink(client, gate, 1)
    tool_start = {"event": "TOOL_CALL_START", "data": {"type": "TOOL_CALL_START", "toolCallId": "c1"}}
    status = {"event": "status", "data": {"status": "planning"}}
    answer_text = {"event": "TEXT_MESSAGE_CONTENT", "data": {"type": "TEXT_MESSAGE_CONTENT", "delta": "draft"}}
    answer_delta = {"event": "STATE_DELTA", "data": {"delta": [{"op": "replace", "path": "/normalizedAnswer"}]}}
    legacy_chunk = {"event": "answer_chunk", "data": {"chunk": "draft", "accumulated": "draft"}}
    reasoning = {"event": "REASONING_MESSAGE_CONTENT", "data": {"delta": "thinking"}}
    discarded = {"event": "CUSTOM", "data": {"name": "message_discarded", "value": {}}}
    retrieval = {"event": "CUSTOM", "data": {"name": "retrieval_context", "value": {"seq": 1}}}
    artifact = {"event": "STATE_DELTA", "data": {"delta": [{"op": "add", "path": "/artifacts/-", "value": {}}]}}
    question = {"event": "CUSTOM", "data": {"name": "ask_user_question", "value": {}}}
    root_finished = {"event": "RUN_FINISHED", "data": {"runId": "r"}}
    child_finished = {"event": "RUN_FINISHED", "data": {"runId": "c", "parentRunId": "r"}}

    await second.write(status)
    for event in (tool_start, answer_text, answer_delta, legacy_chunk, reasoning, discarded, retrieval, root_finished):
        await first.write(event)
        await second.write(event)
    await second.write(child_finished)
    await second.write(artifact)
    await second.write(question)

    assert client.events == [status, tool_start, child_finished]
    assert first.kept == [retrieval]
    assert second.kept == [retrieval, artifact, question]


def test_records_cited_by_more_answers_come_first() -> None:
    assert cited_record_order([["a", "b"], ["c", "b"], ["b", "d", "c"]]) == ["b", "c", "a", "d"]


def test_record_text_respects_the_caps() -> None:
    order = [f"rec-{i}" for i in range(MAX_RECORDS + 5)]
    texts = {record_id: (f"Record {record_id}", "x" * (RECORD_CHARS * 2)) for record_id in order}

    records = assemble_records(order, texts)

    assert 0 < len(records) <= MAX_RECORDS
    assert all(len(record.text) <= RECORD_CHARS for record in records)
    assert sum(len(record.text) for record in records) <= TOTAL_RECORD_CHARS
    assert all(record.text.endswith("[... record cut here]") for record in records)
    assert [record.label for record in records] == [f"R{i}" for i in range(1, len(records) + 1)]
    assert [record.record_id for record in records] == order[: len(records)]


def test_short_records_are_kept_whole_and_unreadable_ones_are_skipped() -> None:
    texts = {"a": ("Alpha", "alpha text"), "b": ("Beta", ""), "c": ("Gamma", "gamma text")}

    records = assemble_records(["a", "b", "c", "missing"], texts)

    assert [(r.label, r.name, r.text) for r in records] == [("R1", "Alpha", "alpha text"), ("R2", "Gamma", "gamma text")]


def test_more_records_than_the_limit_are_cut_at_the_limit() -> None:
    order = [f"rec-{i}" for i in range(MAX_RECORDS + 3)]
    records = assemble_records(order, {record_id: (record_id, "short") for record_id in order})
    assert len(records) == MAX_RECORDS


def test_answers_cite_records_by_their_label_and_lose_the_confidence_trailer() -> None:
    ref_to_url = {"ref1": _url("rec-a", 2), "ref2": _url("rec-b"), "ref3": _url("rec-unshown")}
    answer = "**Build 4.3** [source](ref1) shipped to the lobby kiosks [source](ref2) [ref3].\n\n---\nConfidence: High"

    text = candidate_text(answer, ref_to_url, {"rec-a": "R2", "rec-b": "R1"})

    assert text == "**Build 4.3** [R2] shipped to the lobby kiosks [R1] [source]."


@pytest.mark.parametrize(
    ("value", "expected"),
    [("B", "B"), ("b", "B"), (" Answer B ", "B"), ("B.", "B"), ("Z", None), ("AB", None), (2, None), (None, None)],
)
def test_the_pick_is_read_defensively(value: object, expected: str | None) -> None:
    assert pick_label(value, ["A", "B", "C"]) == expected


def test_the_label_order_is_seeded_by_the_conversation() -> None:
    orders = {tuple(label_order(4, f"conv-{i}")) for i in range(20)}
    assert label_order(4, "conv-7") == label_order(4, "conv-7")
    assert all(sorted(order) == [0, 1, 2, 3] for order in orders)
    assert len(orders) > 1


async def test_two_runs_the_selector_sees_both_answers_and_the_cited_records_and_its_pick_wins(
    caplog: pytest.LogCaptureFixture,
) -> None:
    client, gate = _Sink(), ProgressGate()
    draft_notes = _record("rec-draft", "Kiosk printer notes (draft)", "Draft: build 4.2 should clear the jam.")
    final_notes = _record("rec-final", "Kiosk printer notes (final)", "Final: build 4.3 cleared the jam on every kiosk.")
    drafts = [_draft(0, client, gate, draft_notes), _draft(1, client, gate, final_notes)]
    selector = _Selector(_picks("4.3"))

    with caplog.at_level(logging.INFO, logger=answer_selection.__name__):
        choice = await _select(
            drafts, _answers("Build 4.2 cleared it [source](ref1).", "Build 4.3 cleared it [source](ref1)."),
            selector, client,
        )

    assert choice.draft is drafts[1]
    assert choice.reason == "selected"
    [prompt] = selector.prompts
    labels = {name: label for label, name in re.findall(r"=== Record (R\d+): (.*?) ===", prompt)}
    assert sorted(_answers_in(prompt).values()) == sorted([
        f"Build 4.2 cleared it [{labels['Kiosk printer notes (draft)']}].",
        f"Build 4.3 cleared it [{labels['Kiosk printer notes (final)']}].",
    ])
    assert "Draft: build 4.2 should clear the jam." in prompt
    assert "Final: build 4.3 cleared the jam on every kiosk." in prompt
    assert _QUESTION in prompt
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("answer_selection: chose")]
    assert f"conversation={_CONVERSATION}" in line
    assert "runs=2" in line and f"pick={choice.label}" in line and "reason=selected" in line
    assert "input_tokens=700" in line and "output_tokens=45" in line and "select_ms=" in line


@pytest.mark.parametrize("reply", [RuntimeError("provider down"), "no verdict here", '{"pick": "Z"}', '{"asks": "x"}'])
async def test_a_failed_or_unreadable_selection_uses_the_first_run(reply: str | BaseException) -> None:
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(i, client, gate, _record(f"rec-{i}", f"Notes {i}", f"text {i}")) for i in range(3)]

    choice = await _select(drafts, _answers("first [source](ref1)", "second", "third"), _Selector(reply), client)

    assert choice.draft is drafts[0]
    assert choice.label is None
    assert choice.reason in {"selector_failed", "invalid_pick"}


async def test_a_selector_that_takes_too_long_uses_the_first_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(answer_selection, "SELECTION_TIMEOUT_SECONDS", 0.05)
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(0, client, gate), _draft(1, client, gate)]

    class _Stalled:
        async def ainvoke(self, messages: list[BaseMessage], config: object = None, **kwargs: object) -> AIMessage:
            await asyncio.sleep(3600)
            return AIMessage(content='{"pick": "B"}')

    choice = await asyncio.wait_for(_select(drafts, _answers("one", "two"), _Stalled(), client), timeout=5)

    assert choice.draft is drafts[0]
    assert choice.reason == "selector_failed"


async def test_a_failed_run_is_dropped_and_the_other_is_used_without_selection() -> None:
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(0, client, gate), _draft(1, client, gate)]
    selector = _Selector('{"pick": "A"}')

    choice = await _select(drafts, _answers(RuntimeError("search failed"), "the answer"), selector, client)

    assert choice.draft is drafts[1]
    assert choice.reason == "one_answer"
    assert isinstance(drafts[0].error, RuntimeError)
    assert selector.prompts == []


async def test_a_run_that_ended_without_an_answer_is_not_a_candidate() -> None:
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(0, client, gate), _draft(1, client, gate)]

    async def _run(draft: Draft) -> None:
        draft.result = AgentResult(
            goal=Goal(description=_QUESTION), output="" if draft.index == 0 else "an answer",
            success=draft.index == 1,
        )

    choice = await _select(drafts, _run, _Selector('{"pick": "A"}'), client)

    assert choice.draft is drafts[1]


async def test_when_every_run_fails_nothing_is_chosen() -> None:
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(0, client, gate), _draft(1, client, gate)]

    choice = await _select(drafts, _answers(RuntimeError("a"), RuntimeError("b")), _Selector("{}"), client)

    assert choice.draft is None
    assert choice.reason == "all_failed"
    assert all(isinstance(d.error, RuntimeError) for d in drafts)


async def test_when_no_run_answers_the_first_that_finished_is_finalized() -> None:
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(0, client, gate), _draft(1, client, gate)]

    async def _run(draft: Draft) -> None:
        if draft.index == 0:
            raise RuntimeError("boom")
        draft.result = AgentResult(goal=Goal(description=_QUESTION), success=False, error="max turns")

    choice = await _select(drafts, _run, _Selector("{}"), client)

    assert choice.draft is drafts[1]
    assert choice.reason == "no_answer"


class _Started:
    """Set once ``count`` runs have started."""

    def __init__(self, count: int) -> None:
        self._count = count
        self._seen = 0
        self.all = asyncio.Event()

    def note(self) -> None:
        self._seen += 1
        if self._seen == self._count:
            self.all.set()


async def test_cancellation_stops_every_run_and_skips_the_selector() -> None:
    client, gate, token = _Sink(), ProgressGate(), CancellationToken()
    drafts = [_draft(0, client, gate), _draft(1, client, gate)]
    started = _Started(2)
    stopped: list[int] = []
    selector = _Selector('{"pick": "A"}')

    async def _run(draft: Draft) -> None:
        started.note()
        await token.wait()
        stopped.append(draft.index)
        draft.result = AgentResult(goal=Goal(description=_QUESTION), success=False, error="Cancelled", cancelled=True)

    task = asyncio.ensure_future(_select(drafts, _run, selector, client, token))
    await asyncio.wait_for(started.all.wait(), timeout=5)
    token.cancel()
    choice = await task

    assert sorted(stopped) == [0, 1]
    assert choice.stopped
    assert choice.draft in drafts
    assert selector.prompts == []


async def test_a_stop_while_the_selector_runs_ends_the_request_as_stopped() -> None:
    client, gate, token = _Sink(), ProgressGate(), CancellationToken()
    drafts = [_draft(0, client, gate), _draft(1, client, gate)]
    asked = asyncio.Event()

    class _SlowSelector:
        async def ainvoke(self, messages: list[BaseMessage], config: object = None, **kwargs: object) -> AIMessage:
            asked.set()
            await asyncio.sleep(3600)
            return AIMessage(content='{"pick": "A"}')

    task = asyncio.ensure_future(_select(drafts, _answers("one", "two"), _SlowSelector(), client, token))
    await asyncio.wait_for(asked.wait(), timeout=5)
    token.cancel()
    choice = await asyncio.wait_for(task, timeout=5)

    assert choice.stopped
    assert choice.draft in drafts


async def test_cancelling_the_request_cancels_every_run() -> None:
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(0, client, gate), _draft(1, client, gate)]
    started = _Started(2)
    cancelled: list[int] = []

    async def _run(draft: Draft) -> None:
        started.note()
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            cancelled.append(draft.index)
            raise

    task = asyncio.ensure_future(_select(drafts, _run, _Selector("{}"), client))
    await asyncio.wait_for(started.all.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert sorted(cancelled) == [0, 1]


async def test_the_chosen_run_delivers_its_kept_frames_and_counts_every_runs_usage() -> None:
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(i, client, gate, _record(f"rec-{i}", f"Notes {i}", f"text {i}"), usage=True) for i in range(2)]
    for draft in drafts:
        draft.context.usage_ledger.add("main", TokenUsage(input_tokens=100 + draft.index, output_tokens=10))
        await draft.sink.write({"event": "CUSTOM", "data": {"name": "retrieval_context", "value": {"seq": draft.index}}})

    choice = await _select(
        drafts, _answers("zero [source](ref1)", "one [source](ref1)"), _Selector(_picks("one")), client,
    )

    chosen = choice.draft
    assert chosen is drafts[1]
    assert chosen.context.event_sink is client
    assert chosen.context.tool_state["event_sink"] is client
    assert client.events == [{"event": "CUSTOM", "data": {"name": "retrieval_context", "value": {"seq": 1}}}]
    assert chosen.context.usage_ledger.entries == [
        ("main", TokenUsage(input_tokens=101, output_tokens=10)),
        (f"{ANSWER_DRAFT_SOURCE_PREFIX}:main", TokenUsage(input_tokens=100, output_tokens=10)),
        (ANSWER_SELECTION_SOURCE, TokenUsage(input_tokens=700, output_tokens=45)),
    ]


async def test_usage_is_not_pooled_when_the_request_did_not_ask_for_it() -> None:
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(i, client, gate, _record(f"rec-{i}", f"Notes {i}", f"text {i}")) for i in range(2)]
    for draft in drafts:
        draft.context.usage_ledger.add("main", TokenUsage(input_tokens=5, output_tokens=1))

    choice = await _select(drafts, _answers("a [s](ref1)", "b [s](ref1)"), _Selector('{"pick": "A"}'), client)

    assert choice.draft is not None
    assert choice.draft.context.usage_ledger.entries == [("main", TokenUsage(input_tokens=5, output_tokens=1))]


async def test_only_the_chosen_runs_conversation_tasks_stay_registered() -> None:
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(i, client, gate, _record(f"rec-{i}", f"Notes {i}", f"text {i}")) for i in range(2)]
    started: dict[int, asyncio.Task[int]] = {}

    async def _export(index: int) -> int:
        return index

    async def _run(draft: Draft) -> None:
        # As a query tool does: its export runs on, registered for the conversation.
        started[draft.index] = asyncio.create_task(_export(draft.index))
        register_task(_CONVERSATION, started[draft.index])
        draft.result = AgentResult(goal=Goal(description=_QUESTION), output=f"answer {draft.index} [s](ref1)")

    try:
        choice = await _select(drafts, _run, _Selector(_picks("answer 1")), client)
        kept = pop_tasks(_CONVERSATION)
    finally:
        _ = pop_tasks(_CONVERSATION)

    assert choice.draft is drafts[1]
    assert kept == [started[1]]
    assert await started[0] == 0


def _searching(
    answers: dict[int, str | Exception], tool_calls: dict[int, int], started: list[int],
) -> Callable[[Draft], Awaitable[None]]:
    """A run that makes ``tool_calls[index]`` tool calls (one by default), then answers ``answers[index]`` (or raises
    it)."""

    async def _run(draft: Draft) -> None:
        started.append(draft.index)
        outcome = answers[draft.index]
        if isinstance(outcome, Exception):
            raise outcome
        calls = [ToolCall(id=f"call-{i}", name="knowledgegraph__search") for i in range(tool_calls.get(draft.index, 1))]
        draft.result = AgentResult(
            goal=Goal(description=_QUESTION), output=outcome,
            turns=[AgentTurn(tool_calls=calls), AgentTurn()],
        )

    return _run


async def test_adaptive_an_answer_from_one_record_after_a_short_search_stands_without_the_other_runs(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION_ADAPTIVE", "true")
    client, gate = _Sink(), ProgressGate()
    notes = _record("rec-1", "Kiosk printer notes", "Build 4.3 cleared the jam.")
    drafts = [_draft(0, client, gate, notes), _draft(1, client, gate, notes)]
    started: list[int] = []
    selector = _Selector('{"pick": "B"}')

    with caplog.at_level(logging.INFO, logger=answer_selection.__name__):
        choice = await _select(
            drafts, _searching({0: "Build 4.3 [source](ref1), per the notes [again](ref1).", 1: "other"}, {0: 3}, started),
            selector, client,
        )

    assert started == [0]
    assert choice.draft is drafts[0]
    assert choice.reason == "first_run_stands"
    assert selector.prompts == []
    [line] = [r.getMessage() for r in caplog.records if r.getMessage().startswith("answer_selection: chose")]
    assert "reason=first_run_stands" in line and "answered=1" in line


@pytest.mark.parametrize(("cited", "tool_calls"), [(2, 1), (1, 4)])
async def test_adaptive_an_answer_on_several_records_or_after_a_long_search_gets_the_other_runs_and_the_selector(
    monkeypatch: pytest.MonkeyPatch, cited: int, tool_calls: int,
) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION_ADAPTIVE", "true")
    client, gate = _Sink(), ProgressGate()
    records = [
        _record("rec-a", "Kiosk printer notes (draft)", "Build 4.2."),
        _record("rec-b", "Kiosk printer notes (final)", "Build 4.3."),
    ]
    drafts = [_draft(0, client, gate, *records), _draft(1, client, gate, *records)]
    first = " ".join(f"Build 4.{i + 2} [source](ref{i + 1})." for i in range(cited))
    started: list[int] = []
    selector = _Selector(_picks("final word"))

    choice = await _select(
        drafts, _searching({0: first, 1: "The final word: build 4.3 [source](ref2)."}, {0: tool_calls}, started),
        selector, client,
    )

    assert started == [0, 1]
    assert choice.draft is drafts[1]
    assert choice.reason == "selected"
    assert len(selector.prompts) == 1


async def test_adaptive_a_first_run_without_an_answer_gets_the_other_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION_ADAPTIVE", "true")
    client, gate = _Sink(), ProgressGate()
    drafts = [_draft(0, client, gate), _draft(1, client, gate)]
    started: list[int] = []

    choice = await _select(
        drafts, _searching({0: RuntimeError("search failed"), 1: "the answer"}, {}, started), _Selector("{}"), client,
    )

    assert started == [0, 1]
    assert choice.draft is drafts[1]
    assert choice.reason == "one_answer"


async def test_adaptive_a_stop_during_the_first_run_starts_no_other_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION_ADAPTIVE", "true")
    client, gate, token = _Sink(), ProgressGate(), CancellationToken()
    records = [_record("rec-a", "A", "a"), _record("rec-b", "B", "b")]
    drafts = [_draft(0, client, gate, *records), _draft(1, client, gate, *records)]
    started: list[int] = []

    async def _run(draft: Draft) -> None:
        started.append(draft.index)
        token.cancel()
        draft.result = AgentResult(goal=Goal(description=_QUESTION), output="a [s](ref1) b [s](ref2)")

    choice = await _select(drafts, _run, _Selector('{"pick": "A"}'), client, token)

    assert started == [0]
    assert choice.stopped
    assert choice.draft is drafts[0]


async def test_without_the_adaptive_switch_every_run_starts_at_once() -> None:
    client, gate = _Sink(), ProgressGate()
    notes = _record("rec-1", "Kiosk printer notes", "Build 4.3 cleared the jam.")
    drafts = [_draft(0, client, gate, notes), _draft(1, client, gate, notes)]
    started: list[int] = []

    choice = await _select(
        drafts, _searching({0: "4.3 [s](ref1).", 1: "4.3 [s](ref1)."}, {}, started), _Selector('{"pick": "A"}'), client,
    )

    assert sorted(started) == [0, 1]
    assert choice.reason == "selected"


def test_a_runs_tool_calls_are_counted_from_its_turns() -> None:
    client, gate = _Sink(), ProgressGate()
    draft = _draft(0, client, gate)
    assert draft.tool_calls == 0
    draft.result = AgentResult(
        goal=Goal(description=_QUESTION),
        turns=[AgentTurn(tool_calls=[ToolCall(id="a", name="x"), ToolCall(id="b", name="y")]), AgentTurn(),
               AgentTurn(tool_calls=[ToolCall(id="c", name="z")])],
    )
    assert draft.tool_calls == 3
