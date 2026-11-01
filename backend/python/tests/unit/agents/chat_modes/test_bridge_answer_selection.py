"""`run_chat_stream` with ANSWER_DRAFT_SELECTION: independent runs, what the client sees, and the chosen run's
answer and citations going out through the normal finalizer. Same patch points as `test_bridge.py`."""

from __future__ import annotations

import asyncio
import json
import re
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, BaseMessage

from app.agent_loop_lib.core.context import CancellationToken, RunContext
from app.agent_loop_lib.core.types import AgentResult, Goal
from app.agent_loop_lib.events.base import AgentEvent, EventType
from app.agents.agent_loop.answer_selection import DraftEventSink
from app.agents.agent_loop.stream_bridge import QueueEventSink
from app.agents.chat_modes.bridge import run_chat_stream
from app.agents.chat_modes.policy import (
    AGENT_POLICY,
    INTERNAL_SEARCH_POLICY,
    ChatModePolicy,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from app.agents.agent_loop.context import AgentContext

_QUESTION = "Which firmware build fixed the kiosk printer jam?"


def _url(record_id: str) -> str:
    return f"http://localhost:3000/record/{record_id}/preview#blockIndex=0"


def _event(event_type: EventType, **payload: object) -> AgentEvent:
    return AgentEvent(event_type=event_type, run_context=RunContext(role_name="pipeshub-agent", model="m"), payload=payload)


class _Agent:
    """Streams one answer turn the way `Agent.stream` does, after a progress frame and an optional wait."""

    def __init__(
        self, context: AgentContext, answer: str, *, wait_for: CancellationToken | None = None,
        on_wait: Callable[[], None] | None = None,
    ) -> None:
        self._context = context
        self._answer = answer
        self._wait_for = wait_for
        self._on_wait = on_wait
        self.last_stream_result: AgentResult | None = None
        self.task_cancelled = False

    async def stream(self, goal: Goal, **kwargs: object) -> AsyncIterator[AgentEvent]:
        await self._context.event_sink.write({"event": "status", "data": {"status": "executing", "message": "search"}})
        if self._wait_for is not None:
            if self._on_wait is not None:
                self._on_wait()
            try:
                await self._wait_for.wait()
            except asyncio.CancelledError:
                self.task_cancelled = True
                raise
            self.last_stream_result = AgentResult(goal=goal, success=False, error="Cancelled", cancelled=True)
            return
        yield _event(EventType.TEXT_MESSAGE_START)
        yield _event(EventType.TEXT_MESSAGE_CONTENT, delta=self._answer)
        yield _event(EventType.TEXT_MESSAGE_END)
        yield _event(EventType.AGENT_COMPLETE)
        self.last_stream_result = AgentResult(goal=goal, output=self._answer)


class _Factory:
    """`PipesHubAgentFactory.create` for run n: the run reads record rec-n and answers from it."""

    def __init__(self, *, fail: set[int] | None = None, waiting_runs: int = 0) -> None:
        self.contexts: list[AgentContext] = []
        self.agents: list[_Agent] = []
        self._fail = fail or set()
        self._waiting_runs = waiting_runs
        self._waiting = 0
        self.all_waiting = asyncio.Event()

    def _note_waiting(self) -> None:
        self._waiting += 1
        if self._waiting == self._waiting_runs:
            self.all_waiting.set()

    async def create(
        self, context: AgentContext, llm: object, chat_mode: str, *, query: str, model_name: str = "",
        model_key: str | None = None,
    ) -> tuple[Any, Any, Goal, list[Any]]:
        n = len(self.contexts)
        self.contexts.append(context)
        if n in self._fail:
            raise RuntimeError(f"run {n} could not start")
        record_id, vrid = f"rec-{n}", f"vr-{n}"
        ref = context.tool_state["citation_ref_mapper"].get_or_create_ref(_url(record_id))
        context.tool_state["final_results"] = [{
            "virtual_record_id": vrid, "block_index": 0, "block_type": "text", "content": f"build 4.{n}",
            "block_web_url": _url(record_id),
            "metadata": {"origin": "CONNECTOR", "recordName": f"Kiosk notes {n}", "recordId": record_id, "orgId": "org-1"},
        }]
        context.tool_state["virtual_record_id_to_result"] = {vrid: {
            "id": record_id, "record_name": f"Kiosk notes {n}",
            "block_containers": {"blocks": [{"index": 0, "type": "text", "data": f"Record {n} names build 4.{n}."}]},
        }}
        token = context.cancellation_token if self._waiting_runs else None
        agent = _Agent(
            context, f"Answer {n}: build 4.{n} [source]({ref}).", wait_for=token, on_wait=self._note_waiting,
        )
        self.agents.append(agent)
        return agent, MagicMock(), Goal(description=query), []


class _Selector:
    """The request's model; as the selector it picks the answer from run 1 unless given a fixed reply."""

    def __init__(self, reply: str = "") -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def ainvoke(self, messages: list[BaseMessage], config: object = None, **kwargs: object) -> AIMessage:
        prompt = str(messages[-1].content)
        self.prompts.append(prompt)
        if self.reply:
            return AIMessage(content=self.reply)
        answers = re.findall(r"=== Answer ([A-Z]) ===\n(.*?)(?=\n\n=== |\n\nRECORDS)", prompt, re.S)
        label = next(label for label, text in answers if text.startswith("Answer 1"))
        return AIMessage(content=json.dumps({"pick": label}))


class _Registry:
    def __init__(self) -> None:
        self.token: CancellationToken | None = None

    async def register(self, run_id: str, token: CancellationToken, owner: object) -> None:
        self.token = token

    async def unregister(self, run_id: str) -> None:
        return


def _parse(chunk: str) -> tuple[str, dict[str, Any]]:
    head, _, body = chunk.partition("\ndata: ")
    return head.removeprefix("event: "), json.loads(body.strip())


def _final(events: list[tuple[str, dict[str, Any]]]) -> dict[str, Any]:
    for name, data in reversed(events):
        if name == "complete":
            return data
        if name == "STATE_SNAPSHOT" and data["snapshot"].get("final"):
            return data["snapshot"]
    raise AssertionError(f"no final answer in {[name for name, _ in events]}")


async def _run(
    factory: _Factory, selector: _Selector, *, policy: ChatModePolicy = INTERNAL_SEARCH_POLICY,
    protocol: str = "legacy", registry: _Registry | None = None, query_info: dict[str, Any] | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    config_service = AsyncMock()
    config_service.get_config.return_value = {"providers": []}
    with (
        patch(
            "app.modules.agents.qna.chat_state.build_initial_state",
            return_value={"org_id": "org-1", "user_id": "user-1", "query": _QUESTION},
        ),
        patch("app.agents.chat_modes.bridge.fetch_user_connector_instances", new=AsyncMock(return_value=[])),
        patch("app.agents.chat_modes.bridge._fetch_available_connectors", new=AsyncMock(return_value=[])),
        patch("app.agents.chat_modes.bridge.unreadable_scope_error", new=AsyncMock(return_value=None)),
        patch("app.agents.chat_modes.bridge.prefetch_retrieval", new=AsyncMock(return_value=None)),
        patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=lambda _self, *a, **k: factory.create(*a, **k)),
    ):
        chunks = [chunk async for chunk in run_chat_stream(
            query_info={"query": _QUESTION, "chatMode": policy.name, "filters": {}, **(query_info or {})},
            user_info={"userId": "user-1", "orgId": "org-1"}, llm=selector, policy=policy, log=MagicMock(),  # type: ignore[arg-type]
            retrieval_service=AsyncMock(), graph_provider=MagicMock(), reranker_service=MagicMock(),
            config_service=config_service, model_name="chat-model", protocol=protocol,
            cancellation_registry=registry,  # type: ignore[arg-type]
        )]
    return [_parse(chunk) for chunk in chunks if not chunk.startswith("event: HEARTBEAT")]


@pytest.fixture(autouse=True)
def _selection_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANSWER_DRAFT_SELECTION_ADAPTIVE", raising=False)


@pytest.mark.parametrize("value", [None, "0", "1"])
async def test_off_runs_once_and_never_selects(monkeypatch: pytest.MonkeyPatch, value: str | None) -> None:
    if value is None:
        monkeypatch.delenv("ANSWER_DRAFT_SELECTION", raising=False)
    else:
        monkeypatch.setenv("ANSWER_DRAFT_SELECTION", value)
    factory, selector = _Factory(), _Selector()

    events = await _run(factory, selector)

    assert len(factory.contexts) == 1
    assert isinstance(factory.contexts[0].event_sink, QueueEventSink)
    assert selector.prompts == []
    assert _final(events)["answer"].startswith("Answer 0: build 4.0")
    assert [name for name, _ in events].count("answer_chunk") == 2


async def test_on_but_not_a_knowledge_answer_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION", "2")
    factory, selector = _Factory(), _Selector()

    await _run(factory, selector, policy=AGENT_POLICY)

    assert len(factory.contexts) == 1
    assert selector.prompts == []


async def test_on_but_resuming_an_approved_call_runs_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION", "2")
    factory, selector = _Factory(), _Selector()

    await _run(factory, selector, query_info={"toolApproval": {"callId": "c1", "decision": "approve"}})

    assert len(factory.contexts) == 1
    assert selector.prompts == []


@pytest.mark.parametrize("protocol", ["legacy", "agui"])
async def test_two_independent_runs_and_the_picked_runs_answer_and_citations_go_out(
    monkeypatch: pytest.MonkeyPatch, protocol: str,
) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION", "2")
    factory, selector = _Factory(), _Selector()

    events = await _run(factory, selector, protocol=protocol)

    first, second = factory.contexts
    assert first is not second
    assert first.tool_state is not second.tool_state
    assert first.tool_state["citation_ref_mapper"] is not second.tool_state["citation_ref_mapper"]
    assert first.tool_state["final_results"] is not second.tool_state["final_results"]

    [prompt] = selector.prompts
    assert "Answer 0: build 4.0" in prompt and "Answer 1: build 4.1" in prompt
    assert "Record 0 names build 4.0." in prompt and "Record 1 names build 4.1." in prompt

    final = _final(events)
    assert final["answer"].startswith("Answer 1: build 4.1")
    assert [c["metadata"]["recordId"] for c in final["citations"]] == ["rec-1"]
    assert isinstance(first.event_sink, DraftEventSink)
    assert isinstance(second.event_sink, QueueEventSink)

    names = [name for name, _ in events]
    assert names.count("status") == 1
    assert "TEXT_MESSAGE_CONTENT" not in names
    assert names.count("answer_chunk" if protocol == "legacy" else "STATE_DELTA") == 1


async def test_an_unreadable_selection_uses_the_first_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION", "2")
    factory, selector = _Factory(), _Selector(reply="Both look fine to me.")

    events = await _run(factory, selector)

    assert len(selector.prompts) == 1
    final = _final(events)
    assert final["answer"].startswith("Answer 0: build 4.0")
    assert [c["metadata"]["recordId"] for c in final["citations"]] == ["rec-0"]


async def test_a_failed_run_is_dropped_and_the_other_answers_without_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION", "2")
    factory, selector = _Factory(fail={0}), _Selector()

    events = await _run(factory, selector)

    assert len(factory.contexts) == 2
    assert selector.prompts == []
    assert _final(events)["answer"].startswith("Answer 1: build 4.1")


async def test_when_every_run_fails_the_request_answers_with_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION", "2")
    factory, selector = _Factory(fail={0, 1}), _Selector()

    events = await _run(factory, selector)

    assert [name for name, _ in events][-1] == "error"
    assert selector.prompts == []


async def test_a_stop_ends_every_run_and_the_request_is_stopped(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION", "2")
    factory, selector, registry = _Factory(waiting_runs=2), _Selector(), _Registry()

    request = asyncio.ensure_future(_run(factory, selector, registry=registry))
    await asyncio.wait_for(factory.all_waiting.wait(), timeout=5)
    assert registry.token is not None
    registry.token.cancel()
    events = await asyncio.wait_for(request, timeout=5)

    assert all(agent.last_stream_result is not None and agent.last_stream_result.cancelled for agent in factory.agents)
    assert selector.prompts == []
    final = _final(events)
    assert final["status"] == "stopped"
    assert final["answer"] == ""


async def test_the_client_leaving_cancels_every_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION", "2")
    factory, selector = _Factory(waiting_runs=2), _Selector()
    config_service = AsyncMock()
    config_service.get_config.return_value = {"providers": []}
    with (
        patch(
            "app.modules.agents.qna.chat_state.build_initial_state",
            return_value={"org_id": "org-1", "user_id": "user-1", "query": _QUESTION},
        ),
        patch("app.agents.chat_modes.bridge.fetch_user_connector_instances", new=AsyncMock(return_value=[])),
        patch("app.agents.chat_modes.bridge._fetch_available_connectors", new=AsyncMock(return_value=[])),
        patch("app.agents.chat_modes.bridge.unreadable_scope_error", new=AsyncMock(return_value=None)),
        patch("app.agents.chat_modes.bridge.prefetch_retrieval", new=AsyncMock(return_value=None)),
        patch("app.agents.chat_modes.bridge.PipesHubAgentFactory.create", new=lambda _self, *a, **k: factory.create(*a, **k)),
    ):
        stream = run_chat_stream(
            query_info={"query": _QUESTION, "chatMode": "internal_search", "filters": {}},
            user_info={"userId": "user-1", "orgId": "org-1"}, llm=selector, policy=INTERNAL_SEARCH_POLICY,  # type: ignore[arg-type]
            log=MagicMock(), retrieval_service=AsyncMock(), graph_provider=MagicMock(),
            reranker_service=MagicMock(), config_service=config_service, model_name="chat-model",
            cancellation_registry=_Registry(),  # type: ignore[arg-type]
        )
        first = await asyncio.wait_for(anext(stream), timeout=5)
        await asyncio.wait_for(factory.all_waiting.wait(), timeout=5)
        await stream.aclose()

    assert _parse(first)[0] == "status"
    assert len(factory.agents) == 2
    assert all(agent.task_cancelled for agent in factory.agents)
    assert selector.prompts == []


async def test_adaptive_a_short_answer_from_one_record_needs_no_other_run(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION", "2")
    monkeypatch.setenv("ANSWER_DRAFT_SELECTION_ADAPTIVE", "true")
    factory, selector = _Factory(), _Selector()

    events = await _run(factory, selector, protocol="agui")

    assert len(factory.contexts) == 1
    assert selector.prompts == []
    final = _final(events)
    assert final["answer"].startswith("Answer 0: build 4.0")
    assert [c["metadata"]["recordId"] for c in final["citations"]] == ["rec-0"]
    assert "TEXT_MESSAGE_CONTENT" not in [name for name, _ in events]
