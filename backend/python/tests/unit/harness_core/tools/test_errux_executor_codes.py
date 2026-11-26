"""EU-3: every failure the executor catches carries its code, on the loop and through the durable port (QA17-O1)."""

from __future__ import annotations

import asyncio
from typing import Any, override

import pytest

from harness_core.effects.failures import ToolFailure
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext, classify_tool_effect
from harness_core.tools.base import ParameterType, Tag, Tool, ToolOutput, ToolParameter
from harness_core.tools.decorators import tool
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.executor import ToolExecutor
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.time_bound import ToolTimeBounds
from harness_core.tools.toolset import ToolsetBuilder

_CTX = ToolContext("run1", "org1", "t1.0", "a1", "org1:run1:t1.0")
_Q = [ToolParameter(name="q", type=ParameterType.STRING, description="Query", required=True)]


class _Slack:
    @tool(path="/tools/slack/refused", short_description="r", description="Refused.", parameters=[])
    async def refused(self) -> str:
        raise ConnectionRefusedError(111, "connect to slack.example:443 refused")

    @tool(path="/tools/slack/search", short_description="s", description="Search.", parameters=_Q)
    async def search(self, q: str) -> str:
        return f"found {q}"

    @tool(path="/tools/slack/said_no", short_description="n", description="Its own error.", parameters=[])
    async def said_no(self) -> ToolOutput:
        return ToolOutput(success=False, error="channel_not_found")

    @tool(path="/tools/slack/knows", short_description="k", description="Its own code.", parameters=[])
    async def knows(self) -> ToolOutput:
        return ToolOutput(success=False, error="channel_not_found", error_code="not_found")

    @tool(path="/tools/slack/raises_code", short_description="c", description="Raises its code.", parameters=[])
    async def raises_code(self) -> str:
        raise ToolFailure("rate_limited", "slack said ratelimited")

    @tool(path="/tools/slack/hangs", short_description="h", description="Never answers.", parameters=[])
    async def hangs(self) -> str:
        await asyncio.Event().wait()
        return "never"


class _Strict(Tool):
    """A tool the executor validates against its parameters (a decorated toolset method validates by its call)."""

    @property
    @override
    def name(self) -> str:
        return "slack__strict"

    @property
    @override
    def short_description(self) -> str:
        return "strict"

    @property
    @override
    def description(self) -> str:
        return "Needs q."

    @property
    @override
    def path(self) -> str:
        return "/tools/slack/strict"

    @property
    @override
    def parameters(self) -> list[ToolParameter]:
        return _Q

    @override
    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data="ran")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    ToolsetBuilder(_Slack(), name="slack", description="Slack", path_prefix="/tools/slack").register_into(registry)
    registry.register_tool(_Strict())
    return registry


CASES = [
    ("slack__refused", {}, "unavailable"),
    ("slack__strict", {}, "invalid_call"),
    ("slack__nope", {}, "invalid_call"),
    ("slack__said_no", {}, "failed"),
    ("slack__knows", {}, "not_found"),
    ("slack__raises_code", {}, "rate_limited"),
]


@pytest.mark.parametrize(("name", "args", "code"), CASES, ids=[c[0] for c in CASES])
async def test_eu_3_the_loop_s_executor_codes_each_failure(name: str, args: dict[str, str], code: str) -> None:
    result = await ToolExecutor(_registry()).call_tool(ToolCall(id="c1", name=name, arguments=args))
    assert result.is_error
    assert result.error_code == code
    assert result.content, "the model still reads the detail"


async def test_eu_3_the_wall_bound_is_a_timeout() -> None:
    executor = ToolExecutor(_registry(), bounds=ToolTimeBounds(default_wall_s=0.01, stall_s=0.01))
    result = await executor.call_tool(ToolCall(id="c1", name="slack__hangs", arguments={}))
    assert (result.is_error, result.error_code) == (True, "timeout")


async def test_eu_3_a_success_has_no_code() -> None:
    result = await ToolExecutor(_registry()).call_tool(ToolCall(id="c1", name="slack__search", arguments={"q": "x"}))
    assert (result.is_error, result.error_code) == (False, None)


@pytest.mark.parametrize(("name", "args", "code"), CASES, ids=[c[0] for c in CASES])
async def test_eu_3_the_durable_port_carries_the_code(name: str, args: dict[str, str], code: str) -> None:
    registry = _registry()
    granted = ("slack__refused", "slack__search", "slack__strict", "slack__said_no", "slack__knows", "slack__raises_code")
    effects = {n: classify_tool_effect(n, [Tag("category", "read")]) for n in granted}
    port = RegistryToolPort(registry, effects)
    execution = await port.execute(ToolCall(id="c1", name=name, arguments=args), _CTX)
    assert (execution.status, execution.error_code) == ("error", code)
    ok = await port.execute(ToolCall(id="c2", name="slack__search", arguments={"q": "x"}), _CTX)
    assert (ok.status, ok.error_code) == ("ok", None)
