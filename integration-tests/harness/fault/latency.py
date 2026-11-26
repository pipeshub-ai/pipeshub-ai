"""The fault worker as a latency rig (QA-22): a fixed token rate, scripts sized by the query, delegation, MCP.

Scenario knobs (``scenario.json``), all optional:

- ``tokenIntervalS``: seconds between two answer chunks (the time to first byte is ``modelDelayS``), so the model's
  own time is known exactly and every measured number is the harness's;
- ``deltaLog``: write each answer chunk's emission time to ``deltas.jsonl`` (SLO #5, matched by the chunk's text at
  the client; one host, one clock);
- ``mcpServers``: streamable-HTTP MCP server URLs every run discovers before its first model call, as
  ``MCPToolProvider`` does with the MCP hub off (SLO #12).

Scripts by the query's first words: ``answer <chunks>``, ``reads <turns> <calls> [<answer chunks>]`` (``code-mode-5k``),
``wave <children>`` (a deep wave: children run ``answer``).
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from harness_core.journal.children import ChildRequest
from harness_core.kernel.durable import ToolExecution
from harness_core.kernel.durable.delegation import DelegatedOutcome, delegate_effect, goal_tool, is_success

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable.delegation import Decision, Delegated, DelegationView
    from harness_core.testing.durable import FunctionToolPort
    from pydantic import JsonValue

Script = list[tuple[str, list[tuple[str, dict[str, Any]]]]]

DELEGATE = "delegate"
READ_SMALL = "read_small"
# capacity.py's CODE_MODE_RESULT_BYTES: what one code-mode gateway read returns.
SMALL_RESULT = "r" * 2048


def chunk_texts(run_id: str, chunks: int) -> list[str]:
    """Answer chunks the client can tell apart, so each frame is matched to its emission."""
    return [f"{run_id[-6:]}-{i:04d} " for i in range(chunks)]


def answer_script(chunks: int) -> Script:
    return [(f"answer:{chunks}", [])]


def reads_script(turns: int, calls: int, chunks: int = 4) -> Script:
    # Each turn's calls differ from the last turn's: the kernel ends a run whose turns repeat the same calls as stalled
    # (BU-06), which cut every such run at its fourth turn and measured code-mode-5k and reconnect's long case on a
    # fraction of their turns (WP88-FU-1).
    return [*[("", [(READ_SMALL, {"turn": t, "i": i}) for i in range(calls)]) for t in range(turns)],
            (f"answer:{chunks}", [])]


def wave_script(children: int) -> Script:
    calls = [(DELEGATE, {"goal": "answer 4", "task_id": f"c{i}"}) for i in range(children)]
    return [("", calls), ("answer:4", [])]


def latency_script(words: list[str]) -> Script | None:
    """The latency rig's scripts; ``None`` for any other query."""
    head, args = words[0], [int(w) for w in words[1:] if w.isdigit()]
    if head == "answer" and args:
        return answer_script(args[0])
    if head == "reads":
        return reads_script(*args[:3])
    if head == "wave":
        return wave_script(args[0])
    return None


def paced(
    on_delta: Callable[[Any, str], Awaitable[None]], interval_s: float, log: Path | None, run_id: str
) -> Callable[[Any, str], Awaitable[None]]:
    """``on_delta`` at a fixed token rate: the first chunk at once, each next one ``interval_s`` later."""
    first = True

    async def send(kind: Any, text: str) -> None:
        nonlocal first
        if not first and interval_s:
            await asyncio.sleep(interval_s)
        first = False
        if log is not None:
            with log.open("a", encoding="utf-8") as out:
                out.write(json.dumps({"runId": run_id, "text": text, "t": time.time()}) + "\n")
        await on_delta(kind, text)

    return send


def add_latency_tools(port: FunctionToolPort) -> None:
    async def read_small(args: Any, ctx: Any) -> ToolExecution:
        return ToolExecution(status="ok", content={"i": args.get("i"), "blob": SMALL_RESULT})

    port.add(READ_SMALL, read_small, tags=[("category", "read")])


class WaveDelegation:
    """``delegate{goal, task_id}``: every call of a batch is a child, all in one wave."""

    def delegates(self, call: ToolCall) -> bool:
        return call.name == DELEGATE

    def decide(self, call: Delegated, view: DelegationView) -> Decision:
        args = call.call.arguments
        return ChildRequest(invocation_id=call.invocation_id, goal=str(args.get("goal")), task_id=str(args.get("task_id")))

    async def outcome(self, call: ToolCall, document: JsonValue, *, workspace: Any = None) -> DelegatedOutcome:
        output = document.get("output") if isinstance(document, dict) else None
        return DelegatedOutcome(f"child: {output}", error=not is_success(document))

    def effect(self, name: str) -> Any:  # noqa: ANN401 - harness_core's ToolEffect
        return delegate_effect(name)

    def schemas(self) -> list[Any]:
        return [goal_tool(DELEGATE, "Hand one task to a sub-agent")]


async def add_mcp_tools(port: FunctionToolPort, urls: list[str]) -> int:
    """Discovers every server at once, as ``MCPToolProvider.load_into`` does; registers what each lists."""
    from app.agents.mcp.discovery import discover_tools
    from app.agents.mcp.models import MCPAuthMode, MCPServerConfig, MCPTransport

    configs = [
        MCPServerConfig.model_validate({
            "_id": f"bench-mcp-{i}", "orgId": "fault", "createdBy": "fault", "name": f"bench-mcp-{i}",
            "transport": MCPTransport.STREAMABLE_HTTP, "authMode": MCPAuthMode.NONE, "url": url,
            "createdAt": 0, "updatedAt": 0,
        })
        for i, url in enumerate(urls)
    ]
    found = await asyncio.gather(*(discover_tools(config, {}, timeout_seconds=30.0) for config in configs))

    async def call(args: Any, ctx: Any) -> ToolExecution:
        return ToolExecution(status="ok", content={"echoed": True})

    names = [info.namespaced_name for infos in found for info in infos]
    for name in names:
        port.add(name, call, tags=[("category", "read")])
    return len(names)


__all__ = [
    "DELEGATE",
    "READ_SMALL",
    "WaveDelegation",
    "add_latency_tools",
    "add_mcp_tools",
    "answer_script",
    "chunk_texts",
    "latency_script",
    "paced",
]
