"""MCP-E2E LZ: what a large tool grant costs each executor's first model request (a measurement).

    python -m pytest test_tool_grant_size.py -m measure --attach=ST-1=RUN

For grants of 10, 50, 200 and 500 MCP tools whose names, descriptions and parameters are the product's own
connector tools (the release's toolset registry, served by the lane's MCP servers, approved and granted through
the product), each executor runs one turn. Read at the scripted model: the size of the tool definitions in the
run's first model request and how many of the granted tools it carries; from the turn: how long the first model
request took to arrive, and what failed. One line per (grant, executor) goes to ``mcpe2e-grant-size.json``.

It asserts only what must hold whatever the numbers are: an engine's first request carries a small grant whole and a
large one as ``search_tools`` and ``call_tool`` (LZ1), or the run ends with a reason, and the loop's first request
is the same size whatever the grant.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from mcp_rows import EXECUTORS, Chat, executors_here
from mcp_stack import McpAdmin, registry_tools
from plan import ProcessEnv

if TYPE_CHECKING:
    from conftest import Stacks
    from lane import Stack

Json = dict[str, Any]
STACK = "ST-1"
GRANTS = (10, 50, 200, 500)
# Node admits at most this many tools of one MCP server on an agent (es_validators).
PER_SERVER = 200
BYTES_PER_TOKEN = 4

pytestmark = [pytest.mark.mcp_hub, pytest.mark.measure]


def _agents(admin: McpAdmin, count: int, executors: list[str]) -> tuple[dict[str, str], list[str]]:
    """One agent per executor granted ``count`` approved tools, and the tools' exported names."""
    tools = list(registry_tools(count).items())
    servers = []
    for part in range(0, count, PER_SERVER):
        server = admin.http_server(f"g{count}x{part // PER_SERVER}", tools=dict(tools[part:part + PER_SERVER]))
        _ = admin.approve(server)
        servers.append(server)
    exported = [name for server in servers for name in server.tools.values()]
    return {executor: admin.agent(f"MCPE2E grant {count} {executor} {admin.suffix}", servers,
                                  engine=EXECUTORS[executor][0]) for executor in executors}, exported


def _measure(stack: Stack, executor: str, agent: str, exported: list[str]) -> Json:
    chat = Chat(stack, executor, agent)
    sent = time.time()
    turn = chat.send("Answer in one line.")
    requests = turn.model_requests
    first = requests[0] if requests else {}
    offered = [str(t) for t in first.get("tools") or []]
    carried = sum(1 for name in exported if any(t == name or t.endswith("_" + name) for t in offered))
    disclosure = sum(1 for t in offered if t.endswith(("search_tools", "call_tool")))
    return {
        "executor": executor, "granted": len(exported), "terminal": turn.obs.terminal, "failure": turn.failure(),
        "firstRequestToolCount": first.get("toolsCount"), "firstRequestToolsBytes": first.get("toolsBytes"),
        "firstRequestBodyBytes": first.get("bodyBytes"),
        "estimatedToolTokens": (first.get("toolsBytes") or 0) // BYTES_PER_TOKEN,
        "grantedToolsInFirstRequest": carried, "disclosureToolsInFirstRequest": disclosure,
        "secondsToFirstModelRequest": round(float(first["at"]) - sent, 1) if first else None,
        "modelRequests": len(requests),
    }


@pytest.fixture(scope="module")
def measured(stacks: Stacks) -> list[Json]:
    stack = stacks.get(STACK, mcp_hub=True)
    stack.set_process_env(ProcessEnv(engine_runs=True).env())
    admin = McpAdmin(stack)
    admin.enable()
    executors = executors_here()
    rows: list[Json] = []
    for count in GRANTS:
        agents, exported = _agents(admin, count, executors)
        rows += [_measure(stack, executor, agents[executor], exported) for executor in executors]
    out = Path(os.environ.get("QA6_RESULTS_DIR", str(stack.run_dir))) / "mcpe2e-grant-size.json"
    out.write_text(json.dumps(rows, indent=1))
    return rows


ENGINE_THRESHOLD = 50


def test_lz2_an_engine_gets_a_small_grant_whole_and_a_large_one_through_search(measured: list[Json]) -> None:
    """LZ1: the grant counts the agent's own platform tools too (about 20), so 10 MCP tools stay listed and 50 or
    more go behind ``search_tools`` and ``call_tool``, whose list does not grow with the grant."""
    engines = [r for r in measured if r["executor"] != "loop"]
    for row in engines:
        if row["terminal"] == "error":
            assert row["failure"], row
        elif row["granted"] < ENGINE_THRESHOLD - 20:
            assert row["grantedToolsInFirstRequest"] == row["granted"], row
        elif row["granted"] >= ENGINE_THRESHOLD:
            assert row["grantedToolsInFirstRequest"] == 0 and row["disclosureToolsInFirstRequest"] == 2, row
    for executor in {r["executor"] for r in engines}:
        large = {r["firstRequestToolsBytes"] for r in engines if r["executor"] == executor and r["granted"] >= 50}
        assert len(large) <= 1, engines


def test_lz2_the_loops_first_request_does_not_grow_with_the_grant(measured: list[Json]) -> None:
    """The loop's own tools already reach the lazy threshold, so a granted MCP tool is found by search, not sent."""
    loop = [r for r in measured if r["executor"] == "loop"]
    assert [r["granted"] for r in loop] == list(GRANTS) and all(r["terminal"] == "finished" for r in loop), loop
    assert len({r["firstRequestToolsBytes"] for r in loop}) == 1, loop
    assert all(r["grantedToolsInFirstRequest"] == 0 for r in loop), loop
