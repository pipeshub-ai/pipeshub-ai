"""The agent loop the fake engines run: model call, tool calls, repeat until a reply.

``FakeEngine`` and the recorded fakes of real engines share it so they behave
alike; each turns the hooks into its own wire format (neutral events, or the
real engine's SSE payloads).
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from harness_core.engines.jsonview import as_list, as_object, as_str
from harness_core.testing.engines.gateway import MODEL_PATH
from harness_core.testing.engines.script import (
    ChatMessage,
    ModelReply,
    ModelUsage,
    ToolCall,
)
from harness_core.testing.sandbox.egress import EgressDenied

if TYPE_CHECKING:
    import asyncio

    from harness_core.engines.types import JsonValue
    from harness_core.testing.sandbox.egress import FakeEgress

TIMED_OUT = "MCP error -32001: Request timed out"
_GATEWAY_CODE = re.compile(r"\[gateway:([a-z][a-z0-9_]{0,63})\]")


class ModelRefused(Exception):
    """The model endpoint refused the call (``ModelReply.error``): the provider failure's code, never its text."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True, slots=True)
class ToolOutcome:
    content: str
    is_error: bool = False


@dataclass(frozen=True, slots=True)
class LoopResult:
    text: str
    reason: Literal["completed", "limit"]


class LoopHooks(Protocol):
    async def step_started(self) -> None: ...

    async def text(self, text: str) -> None: ...

    async def tool_started(self, call: ToolCall) -> None: ...

    async def tool_finished(self, call: ToolCall, outcome: ToolOutcome) -> None: ...

    async def step_finished(self, reason: Literal["tool-calls", "stop"], usage: ModelUsage) -> None: ...


ToolRunner = Callable[[ToolCall], Awaitable[ToolOutcome]]


@dataclass(frozen=True, slots=True)
class ModelEndpoint:
    base_url: str
    model: str
    headers: Mapping[str, str]


async def call_model(
    egress: FakeEgress,
    endpoint: ModelEndpoint,
    messages: Sequence[ChatMessage],
    tools: Sequence[str],
) -> ModelReply:
    body: dict[str, JsonValue] = {
        "model": endpoint.model,
        "messages": [m.to_json() for m in messages],
        "tools": [{"name": t} for t in tools],
    }
    try:
        raw = await egress.request(endpoint.base_url.rstrip("/") + MODEL_PATH, headers=endpoint.headers, body=body)
    except EgressDenied as denied:
        # As a real engine reads the egress proxy's refusal of its model call: the code its words are tagged with
        # (``[gateway:<code>]``), else the HTTP status. Raised as anything else, it ended the engine's loop with no
        # turn end, and the run waited for one until its test's timeout (WP103-FU-3).
        tagged = _GATEWAY_CODE.search(denied.words or "")
        raise ModelRefused(tagged.group(1) if tagged else f"http_{denied.status or 403}") from denied
    reply = ModelReply.from_json(raw)
    if reply.error is not None:
        raise ModelRefused(reply.error)
    return reply


async def list_gateway_tools(
    egress: FakeEgress, url: str, headers: Mapping[str, str]
) -> list[tuple[str, str]]:
    reply = await egress.request(url, headers=headers, body={"method": "tools/list"})
    return [
        (as_str(as_object(t).get("name")), as_str(as_object(t).get("description")))
        for t in as_list(as_object(reply).get("tools"))
    ]


async def call_gateway_tool(
    egress: FakeEgress,
    url: str,
    headers: Mapping[str, str],
    name: str,
    arguments: JsonValue,
    *,
    timeout_s: float,
) -> ToolOutcome:
    """An MCP ``tools/call``. A call held longer than the engine's timeout fails, as it would live."""
    reply = as_object(
        await egress.request(
            url,
            headers=headers,
            body={"method": "tools/call", "params": {"name": name, "arguments": arguments}},
        )
    )
    held = reply.get("heldS")
    if isinstance(held, int | float) and not isinstance(held, bool) and held > timeout_s:
        return ToolOutcome(TIMED_OUT, is_error=True)
    return ToolOutcome(as_str(reply.get("content")), is_error=reply.get("isError") is True)


async def run_agent_loop(
    *,
    egress: FakeEgress,
    endpoint: ModelEndpoint,
    system: str,
    history: list[ChatMessage],
    tools: Sequence[str],
    run_tool: ToolRunner,
    hooks: LoopHooks,
    max_steps: int,
    steering: asyncio.Queue[str] | None = None,
) -> LoopResult:
    """Run until the model replies without tool calls and no steer waits; ``history`` is extended in place."""
    for _ in range(max_steps):
        while steering is not None and not steering.empty():
            history.append(ChatMessage("user", steering.get_nowait()))
        await hooks.step_started()
        messages = [ChatMessage("system", system), *history] if system else history
        reply = await call_model(egress, endpoint, messages, tools)
        if not reply.tool_calls:
            await hooks.text(reply.content)
            history.append(ChatMessage("assistant", reply.content))
            await hooks.step_finished("stop", reply.usage)
            if steering is None or steering.empty():
                return LoopResult(reply.content, "completed")
            # A steer that arrived during the last call is answered before the turn ends, not left behind.
            continue
        history.append(ChatMessage("assistant", reply.content, reply.tool_calls))
        for call in reply.tool_calls:
            await hooks.tool_started(call)
            outcome = await run_tool(call)
            history.append(ChatMessage("tool", outcome.content, tool_call_id=call.id))
            await hooks.tool_finished(call, outcome)
        await hooks.step_finished("tool-calls", reply.usage)
    return LoopResult("", "limit")


__all__ = [
    "TIMED_OUT",
    "LoopHooks",
    "LoopResult",
    "ModelEndpoint",
    "ModelRefused",
    "ToolOutcome",
    "ToolRunner",
    "call_gateway_tool",
    "call_model",
    "list_gateway_tools",
    "run_agent_loop",
]
