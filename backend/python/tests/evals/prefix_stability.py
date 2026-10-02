"""Prompt-cache prefix stability on the golden cases (P0.14 benchmark).

Offline, no model. For each golden case it replays a short conversation of
follow-up messages through the production `PipesHubPromptBuilder` and the
production tool-schema ordering, then reports:

- ``stable_block_hits``: follow-ups whose cached system block (the one that
  carries the Anthropic breakpoint) is byte-identical to the previous one.
- ``system_prefix_share``: mean share of the joined system prompt that is a
  common byte prefix with the previous message (automatic prefix caching).
- ``tool_order_stable``: tool list identical across registration orders and
  across processes with different ``PYTHONHASHSEED`` values.
- ``disclosure_appends``: lazy disclosure kept every earlier tool in place.

Only uses APIs that predate P0.14, so it can be pointed at an older checkout
to get the "before" numbers:

    cd <checkout>/backend/python && python <this file>
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.getcwd())

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.agent.tool_loop import tool_schemas_for_turn
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from tests.evals.live_harness import GOLDEN_CASES
from tests.evals.live_runner import (
    StubTool,
    card_for_tool,
    eval_context,
    final_answer_tool,
)

_FOLLOW_UPS = [
    Goal(description="first question"),
    Goal(description="second", requirements=["Cover Q3 only"], success_criteria=["Names an owner"]),
    Goal(description="third", requirements=["Compare with Q2"], gaps=["Which region?"]),
    Goal(description="fourth", constraints=["<original_query>fourth, rephrased</original_query>"]),
    Goal(description="fifth", requirements=["Cite the source"], success_criteria=["Has a date"]),
]
_PRELOADED = [{}, {"preloaded_skills": "## Skill: pdf\nUse pdfplumber."}, {}, {"preloaded_tools": "jira"}, {}]


class _Named(Tool):
    def __init__(self, name: str) -> None:
        self._name = name

    name = property(lambda self: self._name)
    short_description = property(lambda self: self._name)
    description = property(lambda self: self._name)
    path = property(lambda self: f"/toolsets/bench/{self._name}")
    parameters = property(lambda self: list[ToolParameter]())

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data=None)


def _registry(tool_names: list[str], *, synthetic: bool = False) -> ToolRegistry:
    registry = ToolRegistry()
    if not synthetic:
        registry.register_tool(final_answer_tool())
    for name in tool_names:
        if name != "final_answer":
            registry.register_tool(_Named(name) if synthetic else StubTool(card_for_tool(name)))
    return registry


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _prompt_metrics(tool_names: list[str]) -> dict:
    from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder

    ctx = eval_context(provider="anthropic", model="claude-sonnet-4-5")
    spec = AgentSpec(
        name="bench", system_prompt="BASE_REACT_PROMPT", tool_names=list(tool_names),
        model=ModelSpec(provider="langchain", model="bench"),
    )
    runtime = AgentRuntime(tool_registry=_registry(tool_names))
    blocks: list[tuple[str, str]] = []
    for i, goal in enumerate(_FOLLOW_UPS):
        time_text = f"Current time: 2026-09-26T10:0{i}:00Z"
        with patch("app.agents.agent_loop.prompt_builder.build_llm_time_context",
                   MagicMock(return_value=time_text)):
            blocks.append(PipesHubPromptBuilder(ctx).build_blocks(spec, runtime, goal, [], dict(_PRELOADED[i])))
    hits = sum(1 for prev, cur in zip(blocks, blocks[1:]) if prev[0] == cur[0])
    joined = ["\n\n".join(p for p in b if p) for b in blocks]
    shares = [_common_prefix(p, c) / len(c) for p, c in zip(joined, joined[1:])]
    return {
        "stable_block_hits": f"{hits}/{len(blocks) - 1}",
        "system_prefix_share": round(sum(shares) / len(shares), 3),
    }


def _tool_order(tool_names: list[str]) -> list[str]:
    agent = SimpleNamespace(visible_tools=None, _scope=SimpleNamespace(tool_order=[]))
    spec = AgentSpec(name="bench", system_prompt="s", tool_names=[], model=ModelSpec(provider="x", model="y"))
    registry = _registry(tool_names)
    return [s.name for s in tool_schemas_for_turn(agent, spec, AgentRuntime(tool_registry=registry))]


def _lazy_disclosure_appends() -> bool:
    names = [f"core_{c}" for c in "qwertyuiop"] + [f"jira_{c}" for c in "abcd"] + [f"slack_{c}" for c in "efgh"]
    registry = _registry(names, synthetic=True)
    registry.register_toolset("jira", "Jira", [n for n in names if n.startswith("jira_")])
    registry.register_toolset("slack", "Slack", [n for n in names if n.startswith("slack_")])
    agent = SimpleNamespace(visible_tools=None, _scope=SimpleNamespace(tool_order=[]))
    spec = AgentSpec(name="bench", system_prompt="s", tool_names=[], model=ModelSpec(provider="x", model="y"))
    runtime = AgentRuntime(tool_registry=registry)
    seen: list[str] = []
    for group in (None, "jira", "slack"):
        if group:
            agent.visible_tools |= set(registry.tools_in_toolset(group))
        order = [s.name for s in tool_schemas_for_turn(agent, spec, runtime)]
        if order[: len(seen)] != seen:
            return False
        seen = order
    return True


def _order_in_subprocess(seed: str, tool_names: list[str]) -> list[str]:
    out = subprocess.run(
        [sys.executable, __file__, "--tool-order", json.dumps(tool_names)],
        env={**os.environ, "PYTHONHASHSEED": seed}, capture_output=True, text=True, check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--tool-order":
        names = json.loads(sys.argv[2])
        agent = SimpleNamespace(visible_tools=None, _scope=SimpleNamespace(tool_order=[]))
        registry = _registry(names)
        registry.register_toolset("all", "All", list(names))
        spec = AgentSpec(name="b", system_prompt="s", tool_names=[], model=ModelSpec(provider="x", model="y"))
        runtime = AgentRuntime(tool_registry=registry)
        agent.visible_tools = set(registry.names())
        print(json.dumps([s.name for s in tool_schemas_for_turn(agent, spec, runtime)]))
        return

    report = {"checkout": str(Path.cwd()), "cases": {}}
    for case in GOLDEN_CASES:
        names = [n for n in case.granted_tools if n != "final_answer"]
        orders = {tuple(_tool_order(names)), tuple(_tool_order(list(reversed(names))))}
        cross_process = {tuple(_order_in_subprocess(seed, names)) for seed in ("1", "2", "3", "4")}
        report["cases"][case.id] = {
            **_prompt_metrics(case.granted_tools),
            "tool_order_stable": len(orders) == 1 and len(cross_process) == 1,
        }
    report["disclosure_appends"] = _lazy_disclosure_appends()
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
