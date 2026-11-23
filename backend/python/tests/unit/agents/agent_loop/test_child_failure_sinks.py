"""A child agent whose model call fails: the client's frames, the saved transcript, the parent model and the
orchestrator's dispatch carry only why it failed, never the provider's text (which can quote the prompt, an email or
a key). The provider's machine code (`content_filter`) still reaches the parent, so it can react. The no-tools stream's
error frame and the root answer are the user-facing messages they were."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import HumanMessage

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.loops import ReActLoop
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import CompositeEmitter
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.builtin.coordination.agent_tool import AgentTool
from app.agent_loop_lib.tools.builtin.planning.create_plan import _validate_steps
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from app.agents.agent_loop.error_classification import (
    classify_error,
    classify_exception,
)
from app.agents.agent_loop.protocol.agui_emitter import AGUIEventEmitter
from app.agents.agent_loop.protocol.transcript_collector import TranscriptCollector
from app.agents.agent_loop.sse_emitter import SSEEventEmitter
from tests.support.provider_errors import (
    MARK,
    SECRET,
    SENTINELS,
    openai_error,
    transport_error,
)
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport

pytestmark = pytest.mark.asyncio

_GOAL = Goal(description="What changed this week?")
_BODY = {"message": f"{MARK} key {SECRET}", "type": "invalid_request_error", "param": None, "code": "content_filter"}


def _leaks(value: Any) -> list[str]:  # noqa: ANN401
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return [s for s in (*SENTINELS, "F30SECRET") if s in text]


class _Sink:
    def __init__(self) -> None:
        self.frames: list[dict] = []

    async def write(self, frame: dict) -> None:
        self.frames.append(frame)


def _spec(name: str, provider: str, tools: list[str] | None = None) -> AgentSpec:
    return AgentSpec(name=name, system_prompt=f"You are {name}.", tool_names=tools or [],
                     model=ModelSpec(provider=provider, model="m"), loop=ReActLoop(), max_turns=3)


class TestADomainAgentWhoseModelCallFails:
    @pytest.fixture
    async def run(self) -> SimpleNamespace:
        parent_t = ScriptedTransport()
        parent_t.add_tool_call(ToolCall(id="call-1", name="research", arguments={"goal": "find it"}))
        parent_t.add_text("Done.")
        child_t = ScriptedTransport().add_error(transport_error(400, _BODY))
        transports = TransportRegistry()
        transports.register("parent", lambda: parent_t)
        transports.register("child", lambda: child_t)
        agui, sse, collector = _Sink(), _Sink(), TranscriptCollector()
        emitter = CompositeEmitter([AGUIEventEmitter(agui, thread_id="conv-1"), SSEEventEmitter(sse), collector])
        registry = ToolRegistry()
        runtime = AgentRuntime(transport_registry=transports, tool_registry=registry, event_emitter=emitter)
        registry.register_tool(AgentTool(_spec("research_agent", "child"), runtime, name="research",
                                         description="Researches."))
        await Agent(_spec("root", "parent", ["research"]), runtime).run(_GOAL)
        return SimpleNamespace(agui=agui.frames, sse=sse.frames, parts=collector.parts, parent=parent_t)

    async def test_the_agui_frames_carry_only_why(self, run: SimpleNamespace) -> None:
        (error,) = [f for f in run.agui if f["event"] == "RUN_ERROR"]
        assert error["data"]["message"] == "LLM call failed: bad_request: content_filter (BadRequestError, HTTP 400)"
        assert _leaks(run.agui) == []

    async def test_the_legacy_frames_carry_only_why(self, run: SimpleNamespace) -> None:
        assert any(f.get("data", {}).get("status") == "sub_agent_error" for f in run.sse)
        assert _leaks(run.sse) == []

    async def test_the_saved_transcript_carries_only_why(self, run: SimpleNamespace) -> None:
        assert run.parts and _leaks(run.parts) == []

    async def test_the_parent_model_is_told_why_with_the_providers_code(self, run: SimpleNamespace) -> None:
        request = json.dumps([m.model_dump(mode="json") for m in run.parent.calls[-1]["messages"]])
        assert _leaks(request) == []
        assert "content_filter" in request


async def test_the_orchestrators_dispatch_reports_a_raising_child_by_why() -> None:
    from app.agents.agent_loop.loops.orchestrator import _programmatic_dispatch

    steps, error = _validate_steps([{"id": "fetch", "description": "fetch tickets", "domain": "jira"}])
    assert error is None
    added: list[Any] = []

    async def failed() -> None:
        raise ConnectionError(f"config store at db.internal: {MARK} {SECRET}")

    async def schedule(agent, runtime, calls, scope, **kwargs) -> dict[str, asyncio.Task]:
        return {call.id: asyncio.ensure_future(failed()) for call in calls}

    async def add(message: Any) -> None:  # noqa: ANN401
        added.append(message)

    agent = SimpleNamespace(runtime=MagicMock(), scope=MagicMock(), started_at="t0", context=SimpleNamespace(add=add))
    with patch("app.agent_loop_lib.agent.spawn_scheduler.schedule_spawn_batch", new=schedule):
        ok, _ = await _programmatic_dispatch(agent, SimpleNamespace(steps=steps), _GOAL, 0)

    assert ok is False
    tool_message = added[-1]
    assert tool_message.content == "unavailable (ConnectionError)"
    assert _leaks([m.model_dump(mode="json") for m in added]) == []


@pytest.mark.parametrize("status", [401, 429])
async def test_the_no_tools_stream_error_frame_is_the_user_message(status: int) -> None:
    from app.utils.streaming import call_aiter_llm_stream_simple

    error = openai_error(status, _BODY)

    async def failing_stream(llm, messages, parts=None):  # noqa: ANN202
        raise error
        yield  # pragma: no cover

    with patch("app.utils.streaming.aiter_llm_stream", side_effect=failing_stream):
        events = [e async for e in call_aiter_llm_stream_simple(
            llm=MagicMock(), messages=[HumanMessage(content="q")], final_results=[], records=[],
            target_words_per_chunk=99, original_llm=MagicMock())]
    (frame,) = [e for e in events if e["event"] == "error"]
    assert frame["data"] == {"error": classify_exception(error)[1]}
    assert _leaks(frame) == []


def test_a_bad_request_detail_shown_to_the_user_has_its_key_masked() -> None:
    """#5122 shows a 400's own message to the user who sent the request; a key in it is masked."""
    # An Anthropic key: the account-id pattern doesn't cover it, only the masker does.
    key = "sk-ant-api03-F30SECRETabcdefghijklmnop"
    detail = classify_error(str(openai_error(400, {**_BODY, "code": None, "message": f"bad field, key {key}"})))
    assert detail[0] == "invalid_request"
    assert "bad field" in detail[1] and "F30SECRET" not in detail[1]
