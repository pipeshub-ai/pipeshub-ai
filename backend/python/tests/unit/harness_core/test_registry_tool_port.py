"""RegistryToolPort: only granted tools, executed through ToolExecutor."""

from __future__ import annotations

from contextvars import ContextVar

import pytest

from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable import ToolContext, classify_tool_effect
from harness_core.tools.base import ParameterType, Tag, ToolParameter
from harness_core.tools.decorators import tool
from harness_core.tools.durable_port import RegistryToolPort
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.toolset import ToolsetBuilder

_CTX = ToolContext("run1", "org1", "t1.0", "a1", "org1:run1:t1.0")


class _Docs:
    @tool(
        path="/tools/docs/search",
        short_description="Search",
        description="Search the docs.",
        parameters=[ToolParameter(name="q", type=ParameterType.STRING, description="Query", required=True)],
    )
    async def search(self, q: str) -> str:
        return f"found {q}"

    @tool(
        path="/tools/docs/ask",
        short_description="Ask",
        description="Ask the user.",
        parameters=[ToolParameter(name="question", type=ParameterType.STRING, description="Q", required=True)],
    )
    async def ask(self, question: str) -> dict[str, str]:
        return {"question": question}

    @tool(
        path="/tools/docs/boom",
        short_description="Boom",
        description="Always fails.",
        parameters=[],
    )
    async def boom(self) -> str:
        raise ValueError("boom")


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    ToolsetBuilder(
        _Docs(), name="docs", description="Docs", path_prefix="/tools/docs"
    ).register_into(registry)
    return registry


def _effects(registry: ToolRegistry, *names: str) -> dict[str, object]:
    return {n: classify_tool_effect(n, [Tag("category", "read")]) for n in names}


async def test_runs_granted_tools_and_maps_errors() -> None:
    registry = _registry()
    port = RegistryToolPort(registry, _effects(registry, "docs__search", "docs__boom", "docs__ask"),
                            asks_user=frozenset({"docs__ask"}))
    ok = await port.execute(ToolCall(id="c1", name="docs__search", arguments={"q": "x"}), _CTX)
    assert (ok.status, ok.content, ok.needs_input) == ("ok", "found x", False)
    failed = await port.execute(ToolCall(id="c2", name="docs__boom", arguments={}), _CTX)
    assert failed.status == "error" and "boom" in str(failed.content)
    invalid = await port.execute(ToolCall(id="c3", name="docs__search", arguments={}), _CTX)
    assert invalid.status == "error"
    asked = await port.execute(ToolCall(id="c4", name="docs__ask", arguments={"question": "?"}), _CTX)
    assert asked.needs_input and asked.status == "ok"


async def test_a_registered_but_ungranted_tool_is_not_available() -> None:
    registry = _registry()
    ran: list[str] = []
    port = RegistryToolPort(registry, _effects(registry, "docs__search"))
    assert port.describe("docs__boom") is None
    refused = await port.execute(ToolCall(id="c1", name="docs__boom", arguments={}), _CTX)
    assert (refused.status, refused.content) == ("error", "Unknown tool: docs__boom")
    assert port.unknown("docs__boom") == "Unknown tool: docs__boom"
    assert port.unknown("docs").startswith("Unknown tool: 'docs' — that's a toolset name")
    assert [s.name for s in port.schemas() or []] == ["docs__search"]
    assert ran == []


_STAGED: ContextVar[str | None] = ContextVar("dur1_staged", default=None)


class _Stash:
    @tool(
        path="/tools/stash/put",
        short_description="Put",
        description="Reports what an earlier call left in context, then leaves its own.",
        parameters=[ToolParameter(name="value", type=ParameterType.STRING, description="Value", required=True)],
    )
    async def put(self, value: str) -> str:
        seen = _STAGED.get()
        _ = _STAGED.set(value)
        return f"saw {seen}"


async def test_dur1_f6_a_call_never_sees_what_an_earlier_call_left_in_context() -> None:
    """Legacy runs every call in its own task (the turn loop's gather); a context variable a tool or its hooks
    set (the sandbox's staged input files) must not reach the next call, nor the next run on the same task."""
    registry = ToolRegistry()
    ToolsetBuilder(_Stash(), name="stash", description="Stash", path_prefix="/tools/stash").register_into(registry)
    port = RegistryToolPort(registry, _effects(registry, "stash__put"))
    first = await port.execute(ToolCall(id="c1", name="stash__put", arguments={"value": "a.csv"}), _CTX)
    second = await port.execute(ToolCall(id="c2", name="stash__put", arguments={"value": "b.csv"}), _CTX)
    assert (first.content, second.content) == ("saw None", "saw None")
    assert _STAGED.get() is None


def test_effects_must_name_registered_tools() -> None:
    with pytest.raises(ValueError, match="lacks"):
        _ = RegistryToolPort(ToolRegistry(), {"ghost": classify_tool_effect("ghost", [])})
