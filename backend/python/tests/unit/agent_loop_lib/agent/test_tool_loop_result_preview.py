"""`_result_preview`: the bounded tool-result text every TOOL_RESULT event carries."""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.agent import Agent
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.agent.tool_loop import RESULT_PREVIEW_CHARS, _result_preview
from app.agent_loop_lib.core.messages import ImagePart, ImageSource, TextPart, ToolCall
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.events.base import AgentEvent, EventEmitter, EventType
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.builtin.planning.task_complete import TaskCompleteTool
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agent_loop_lib.transport.registry import TransportRegistry
from tests.unit.agents.adapter.support.scripted_transport import ScriptedTransport


class TestResultPreview:
    def test_structured_content_is_json_not_python_repr(self) -> None:
        preview = _result_preview({"active": True, "owner": None})

        assert preview == '{"active": true, "owner": null}'

    def test_short_string_is_unchanged(self) -> None:
        assert _result_preview("3 issues") == "3 issues"

    def test_long_preview_is_marked_and_stays_within_the_cap(self) -> None:
        preview = _result_preview("x" * 10_000)

        assert len(preview) <= RESULT_PREVIEW_CHARS
        assert preview.endswith("… [truncated: 10000 chars total]")

    def test_preview_exactly_at_the_cap_is_not_marked(self) -> None:
        text = "y" * RESULT_PREVIEW_CHARS

        assert _result_preview(text) == text

    def test_multipart_content_shows_text_and_an_image_placeholder(self) -> None:
        content = [TextPart(text="chart"), ImagePart(source=ImageSource(type="base64", data="AAAA"))]

        assert _result_preview(content) == "chart\n[image]"

    def test_unserializable_objects_fall_back_to_str(self) -> None:
        class _Opaque:
            def __str__(self) -> str:
                return "opaque-object"

        assert _result_preview({"value": _Opaque()}) == '{"value": "opaque-object"}'


class _DictTool(Tool):
    @property
    def name(self) -> str:
        return "dict_tool"

    @property
    def short_description(self) -> str:
        return "Returns a dict."

    @property
    def description(self) -> str:
        return "Returns a dict."

    @property
    def path(self) -> str:
        return "/test/dict_tool"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:  # noqa: ANN401
        return ToolOutput(success=True, data={"events": [{"id": 1, "active": True}]})


class _Recorder(EventEmitter):
    def __init__(self) -> None:
        self.events: list[AgentEvent] = []

    async def emit(self, event: AgentEvent) -> None:
        self.events.append(event)


async def test_tool_result_event_carries_the_json_preview() -> None:
    transport = ScriptedTransport()
    transport.add_tool_call(ToolCall(id="call-1", name="dict_tool", arguments={}))
    transport.add_tool_call(ToolCall(id="call-2", name="task_complete", arguments={"output": "done"}))
    registry = ToolRegistry()
    registry.register_tool(_DictTool())
    registry.register_tool(TaskCompleteTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    recorder = _Recorder()
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry, event_emitter=recorder)
    agent = Agent(
        AgentSpec(
            name="agent-under-test", system_prompt="You are a helpful assistant.",
            model=ModelSpec(provider="scripted", model="scripted-model"), max_turns=3,
        ),
        runtime,
    )

    await agent.run(Goal(description="do something"))

    results = [e.payload for e in recorder.events if e.event_type == EventType.TOOL_RESULT and e.payload.get("tool") == "dict_tool"]
    assert results[0]["content"] == '{"events": [{"id": 1, "active": true}]}'


class _ViewTool(_DictTool):
    """Offers a view of its result; `fail` makes building it raise."""

    def __init__(self, view: Any = None, *, fail: bool = False) -> None:  # noqa: ANN401
        self._view = view
        self._fail = fail

    def result_view(self, args: dict[str, Any], result: Any) -> Any:  # noqa: ANN401
        if self._fail:
            raise RuntimeError("view bug")
        return self._view


async def _dict_tool_result(tool: Tool) -> dict[str, Any]:
    transport = ScriptedTransport()
    transport.add_tool_call(ToolCall(id="call-1", name="dict_tool", arguments={}))
    transport.add_tool_call(ToolCall(id="call-2", name="task_complete", arguments={"output": "done"}))
    registry = ToolRegistry()
    registry.register_tool(tool)
    registry.register_tool(TaskCompleteTool())
    transport_registry = TransportRegistry()
    transport_registry.register("scripted", lambda: transport)
    recorder = _Recorder()
    runtime = AgentRuntime(transport_registry=transport_registry, tool_registry=registry, event_emitter=recorder)
    agent = Agent(
        AgentSpec(
            name="agent-under-test", system_prompt="You are a helpful assistant.",
            model=ModelSpec(provider="scripted", model="scripted-model"), max_turns=3,
        ),
        runtime,
    )
    await agent.run(Goal(description="do something"))
    (result,) = [e.payload for e in recorder.events if e.event_type == EventType.TOOL_RESULT and e.payload.get("tool") == "dict_tool"]
    return result


async def test_the_tools_view_of_its_result_goes_with_the_event() -> None:
    view = {"kind": "records", "columns": ["Id"], "rows": [{"cells": ["1"]}], "total": 1}
    assert (await _dict_tool_result(_ViewTool(view)))["result_view"] == view


async def test_no_view_or_a_failing_one_leaves_the_event_as_it_was() -> None:
    for tool in (_DictTool(), _ViewTool(None), _ViewTool("not a dict"), _ViewTool(fail=True)):
        result = await _dict_tool_result(tool)
        assert "result_view" not in result
        assert result["content"] == '{"events": [{"id": 1, "active": true}]}'
