"""Deep-mode scenarios (K-L): one scripted model per agent of the run.

Legacy runs a deep run's sub-agents as concurrent tasks on one transport, so
their calls interleave; a durable run runs each child as its own run. A
single call-ordered script cannot serve both, so the scripted model routes
each request to its agent's own script: the intent call, the orchestrator,
the plan and result critics, and each child by its goal. Request parity is
then per agent, call by call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.agent_loop_lib.core.messages import ToolCall
from app.agent_loop_lib.core.responses import StructuredResponse, TokenUsage
from tests.contracts.golden_support import GoldenTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable

    from app.agent_loop_lib.core.messages import Message
    from app.agent_loop_lib.core.responses import ModelResponse
    from app.agent_loop_lib.core.streaming import StreamEvent
    from app.agent_loop_lib.core.tool_schema import ToolSchema

INTENT = "intent"
PLANNER = "planner"
ORCHESTRATOR = "orchestrator"
PLAN_CRITIC = "plan_critic"
RESULT_CRITIC = "result_critic"


def _text(message: Any) -> str:  # noqa: ANN401
    content = message.content
    if isinstance(content, str):
        return content
    return "".join(getattr(part, "text", "") for part in content or [])


class ScriptedAgent(GoldenTransport):
    """One agent's script, with structured replies for the critics."""

    def __init__(self) -> None:
        super().__init__()
        self.structured: list[dict[str, Any]] = []
        self.structured_calls: list[dict[str, Any]] = []

    def add_structured(self, data: dict[str, Any]) -> ScriptedAgent:
        self.structured.append(data)
        return self

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        self.structured_calls.append({"messages": [m.model_copy(deep=True) for m in messages], "system": system})
        data = self.structured.pop(0) if self.structured else {}
        return StructuredResponse(data=data, usage=TokenUsage(), model=self._model)


@dataclass
class DeepScript:
    """Every agent's script, and how a request is told apart: a child by a
    marker its goal contains."""

    agents: dict[str, ScriptedAgent] = field(default_factory=dict)
    children: dict[str, str] = field(default_factory=dict)

    def agent(self, key: str) -> ScriptedAgent:
        return self.agents.setdefault(key, ScriptedAgent())

    def child(self, key: str, goal_marker: str) -> ScriptedAgent:
        self.children[key] = goal_marker
        return self.agent(key)

    def route(self, messages: list[Message], system: str | None, *, structured: bool = False) -> str:
        users = [_text(m) for m in messages if getattr(m, "role", None) == "user"]
        if structured:
            first = users[0] if users else ""
            return PLAN_CRITIC if first.startswith("Evaluate the following execution plan") else RESULT_CRITIC
        if any(u.startswith("user query : ") for u in users):
            return INTENT
        if system is not None and system.startswith("You are a planning agent."):
            return PLANNER
        if system is not None and "## Execution Rules" in system and users:
            for key, marker in self.children.items():
                if marker in users[0]:
                    return key
            raise AssertionError(f"no child script for goal {users[0][:120]!r}")
        return ORCHESTRATOR


class DeepTransport(GoldenTransport):
    """The scripted model of a deep run: each request goes to its agent's script."""

    def __init__(self, script: DeepScript) -> None:
        super().__init__()
        self.script = script

    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: object = None,
    ) -> ModelResponse:
        agent = self.script.agents[self.script.route(messages, system)]
        return await agent.complete(
            messages, tools=tools, system=system, model=model, system_blocks=system_blocks,
            tool_choice=tool_choice,  # type: ignore[arg-type]
        )

    async def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        *,
        tool_choice: object = None,
    ) -> AsyncIterator[StreamEvent]:
        agent = self.script.agents[self.script.route(messages, system)]
        async for event in agent.stream(
            messages, tools=tools, system=system, model=model,
            thinking_budget=thinking_budget, effort=effort, system_blocks=system_blocks,
            tool_choice=tool_choice,  # type: ignore[arg-type]
        ):
            yield event

    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict,
        system: str | None = None,
        model: str | None = None,
    ) -> StructuredResponse:
        agent = self.script.agents[self.script.route(messages, system, structured=True)]
        return await agent.complete_structured(messages, output_schema, system=system, model=model)


@dataclass
class DeepScenario:
    name: str
    query: str
    write: Callable[[DeepScript], None]
    toolsets: dict[str, dict[str, Any]] = field(default_factory=dict)

    def script(self) -> DeepScript:
        script = DeepScript()
        self.write(script)
        return script


PASSED = {"passed": True, "confidence": "high", "summary": "Sound.", "issues": []}


def _intent(script: DeepScript, goal: str) -> None:
    script.agent(INTENT).add_text(goal)


def _single_child(s: DeepScript) -> None:
    _intent(s, "Report the status and owner of support ticket T-42.")
    o = s.agent(ORCHESTRATOR)
    o.add_tool_call(ToolCall(id="call-plan", name="create_plan", arguments={
        "steps": [{
            "id": "ticket",
            "description": "Look up support ticket T-42 and report its status and owner.",
            "domain": "goldendocs",
            "tool_names": ["goldendocs"],
        }],
        "confidence": "high",
    }))
    o.add_tool_call(ToolCall(id="call-critique", name="critique_plan", arguments={"plan": "1. ticket: look up T-42"}))
    s.agent(PLAN_CRITIC).add_structured(PASSED)
    child = s.child("ticket", "Look up support ticket T-42")
    child.add_tool_call(ToolCall(id="call-lookup", name="goldendocs__lookup_ticket", arguments={"ticket_id": "T-42"}))
    child.add_text_chunks(["Ticket T-42 is resolved; ", "Priya owns it."])
    o.add_tool_call(ToolCall(id="call-verify", name="verify_result", arguments={
        "output": "Ticket T-42 is resolved and owned by Priya.",
    }))
    s.agent(RESULT_CRITIC).add_structured(PASSED)
    o.add_text_chunks(["Ticket T-42 is resolved ", "and Priya owns it."])


def _dependent_steps(s: DeepScript) -> None:
    _intent(s, "Look up ticket T-42, then summarize it for the support lead.")
    o = s.agent(ORCHESTRATOR)
    o.add_tool_call(ToolCall(id="call-plan", name="create_plan", arguments={
        "steps": [
            {
                "id": "fetch",
                "description": "Look up support ticket T-42.",
                "domain": "goldendocs",
                "tool_names": ["goldendocs"],
                "output_format": "status and owner",
            },
            {
                "id": "summary",
                "description": "Summarize the ticket for the support lead.",
                "domain": "writer",
                "tool_names": [],
                "depends_on": ["fetch"],
                "boundaries": ["do not look anything up"],
            },
        ],
        "confidence": "high",
    }))
    o.add_tool_call(ToolCall(id="call-critique", name="critique_plan", arguments={"plan": "fetch, then summary"}))
    s.agent(PLAN_CRITIC).add_structured(PASSED)
    fetch = s.child("fetch", "Look up support ticket T-42.")
    fetch.add_tool_call(ToolCall(id="call-lookup", name="goldendocs__lookup_ticket", arguments={"ticket_id": "T-42"}))
    fetch.add_text("T-42: resolved, owner Priya.")
    summary = s.child("summary", "Summarize the ticket for the support lead.")
    summary.add_text("T-42 is closed out; Priya handled it.")
    o.add_tool_call(ToolCall(id="call-verify", name="verify_result", arguments={"output": "T-42 closed by Priya."}))
    s.agent(RESULT_CRITIC).add_structured(PASSED)
    o.add_text("T-42 is resolved; Priya owns it.")


def _llm_dispatch(s: DeepScript) -> None:
    _intent(s, "Find the leave policy and the status of ticket T-42.")
    o = s.agent(ORCHESTRATOR)
    # A free-text plan stores no structured plan: Phase 2 is the model's dispatch.
    o.add_tool_call(ToolCall(id="call-plan", name="create_plan", arguments={"plan": "1. leave policy 2. ticket"}))
    s.agent(PLANNER).add_text("1. Search the handbook for leave.\n2. Look up T-42.\nConfidence: high")
    o.add_tool_call(ToolCall(id="call-critique", name="critique_plan", arguments={"plan": "leave, ticket"}))
    s.agent(PLAN_CRITIC).add_structured(PASSED)
    o.add_tool_calls([
        ToolCall(id="call-leave", name="spawn_agent", arguments={
            "role": "handbook", "goal": "Find how many days of paid leave employees get.",
            "task_id": "leave", "tools": ["goldendocs"], "reasoning": "separate domain",
        }),
        ToolCall(id="call-ticket", name="spawn_agent", arguments={
            "role": "tickets", "goal": "Report the status of support ticket T-42.",
            "task_id": "ticket", "tools": ["goldendocs__lookup_ticket"], "reasoning": "separate domain",
        }),
    ])
    leave = s.child("leave", "days of paid leave")
    leave.add_tool_call(ToolCall(id="call-search", name="goldendocs__search_docs", arguments={"query": "paid leave"}))
    leave.add_text("Employees get 25 days of paid leave.")
    ticket = s.child("ticket", "status of support ticket T-42")
    ticket.add_tool_call(ToolCall(id="call-lookup", name="goldendocs__lookup_ticket", arguments={"ticket_id": "T-42"}))
    ticket.add_text("T-42 is resolved.")
    o.add_tool_call(ToolCall(id="call-verify", name="verify_result", arguments={"output": "25 days; T-42 resolved."}))
    s.agent(RESULT_CRITIC).add_structured(PASSED)
    o.add_text("You get 25 days of paid leave, and ticket T-42 is resolved.")


def _direct_answer(s: DeepScript) -> None:
    _intent(s, "Say hello.")
    s.agent(ORCHESTRATOR).add_text("Hello! No planning needed for that.")


def _child_fails(s: DeepScript) -> None:
    _intent(s, "Report the status of ticket T-42.")
    o = s.agent(ORCHESTRATOR)
    o.add_tool_call(ToolCall(id="call-plan", name="create_plan", arguments={
        "steps": [{
            "id": "ticket",
            "description": "Look up support ticket T-42.",
            "domain": "goldendocs",
            "tool_names": ["goldendocs"],
        }],
    }))
    o.add_tool_call(ToolCall(id="call-critique", name="critique_plan", arguments={"plan": "ticket"}))
    s.agent(PLAN_CRITIC).add_structured(PASSED)
    child = s.child("ticket", "Look up support ticket T-42.")
    child.add_error(RuntimeError("Error code: 401 - {'error': {'message': 'Incorrect API key provided'}}"))
    o.add_text("I could not look the ticket up: the sub-agent failed.")


DEEP_SCENARIOS: list[DeepScenario] = [
    DeepScenario("deep_single_child", "What is the status of ticket T-42?", _single_child),
    DeepScenario("deep_dependent_steps", "Summarize ticket T-42 for the support lead.", _dependent_steps),
    DeepScenario("deep_llm_dispatch", "Leave policy and ticket T-42 status?", _llm_dispatch),
    DeepScenario("deep_direct_answer", "hello", _direct_answer),
    DeepScenario("deep_child_fails", "What is the status of ticket T-42?", _child_fails),
]

__all__ = [
    "DEEP_SCENARIOS",
    "DeepScenario",
    "DeepScript",
    "DeepTransport",
    "ScriptedAgent",
]
