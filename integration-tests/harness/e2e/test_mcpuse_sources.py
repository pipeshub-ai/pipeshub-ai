"""MCPUSE-1, ADR-OWD-07 D-DISC-2: a connected MCP server is known to the chat model and to an engine job while its
tools stay lazy, on a real stack.

    python -m pytest test_mcpuse_sources.py --mcp-hub                   # ST-1 with the hub, torn down
    python -m pytest test_mcpuse_sources.py --mcp-hub --attach=ST-1=RUN # a stack left up (E2E_MCP_HUB=1)

Found in a manual test: with Atlassian Rovo connected and signed in, a launch risk report request never called
it. Its 32 tools were granted and, rightly, loaded lazily, but nothing said what the server covers: the chat model got
one placeholder line ("Atlassian Rovo — attached MCP server tools."), the Codex job (61 tools, past the engine's 50)
got a ``search_tools`` that named no source and ranked tool names only. Here two servers whose grant is over both
thresholds stand in for it; the tracker's own description is what says it holds launch blockers and sign-offs:

- the default chat's scripted model searches its tools with those words only, then calls what the search answered
  (through ``call_tool``): no tracker tool is in its list before, and the server sees the one call;
- an engine job (a coder agent's ``code_task``) does the same through the Tool Gateway, and its brief's task names
  both servers with their own descriptions.

The model is scripted, so what is checked is what the product found and offered, not what a model would choose.
"""

from __future__ import annotations

import base64
import secrets
from typing import TYPE_CHECKING, Any

import pytest

from driver import Driver, collect
from fixtures import Fixtures
from matrix import Row
from mcp_rows import Chat, executors_here, tool
from mcp_stack import McpAdmin, Server
from plan import ProcessEnv, RowPlan, TurnSpec
from scripts import Script

if TYPE_CHECKING:
    from collections.abc import Iterator

    from conftest import Stacks
    from lane import Stack
    from mcp_rows import Turn

Json = dict[str, Any]
STACK = "ST-1"
pytestmark = pytest.mark.mcp_hub
QUERY = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}
BY_ID = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
# Over the loop's threshold (20) and the engine's (50), as a real Rovo's 32 tools plus the knowledge base were.
FILLER = {f"metric_{i}": {"description": f"Metric number {i} of the tracker.", "readOnly": True} for i in range(56)}
TRACKER_TOOLS: Json = {
    "search_items": {"description": "Search the tracker's items by words.", "inputSchema": QUERY, "readOnly": True},
    "get_item": {"description": "One tracker item in full.", "inputSchema": BY_ID, "readOnly": True},
    "create_item": {"description": "Create an item in the tracker.", "readOnly": False},
    **FILLER,
}
WIKI_TOOLS: Json = {
    "find_pages": {"description": "Find wiki pages by words.", "inputSchema": QUERY, "readOnly": True},
    "read_page": {"description": "One wiki page in full.", "inputSchema": BY_ID, "readOnly": True},
}
# A code word per run: the stack's default chat attaches every server the org has, an earlier run's tracker included.
CODE = f"orion{secrets.token_hex(3)}"
TRACKER_ABOUT = f"Release tracker for {CODE}: launch blockers and signoffs."
# Words only the tracker's own description has: none of its tools' names or descriptions, nor any other server's.
SEARCH = {"query": f"{CODE} blockers signoffs", "make_visible": True}
WIKI_ABOUT = "Team wiki: launch plans and runbooks."


class AssistantDriver(Driver):
    """The web app's default chat in Agent mode, asking for the harness (as `test_assist1_assistant_agent.py`)."""

    def _body(self, script: Script) -> Json:
        return {**super()._body(script), "harness": "durable"}


def _named(admin: McpAdmin, server: Server) -> Server:
    listed = admin.listed_manifest(server).body
    server.tools = {str(t["name"]): str(t["exportedName"]) for t in listed["tools"] if t.get("exportedName")}
    return server


@pytest.fixture(scope="module")
def world(stacks: Stacks) -> Iterator[tuple[Stack, McpAdmin, Server, Server]]:
    stack = stacks.get(STACK, mcp_hub=True)
    stack.set_process_env(ProcessEnv(engine_runs=True).env())
    admin = McpAdmin(stack)
    admin.enable()
    tracker = _named(admin, admin.http_server("utracker", tools=TRACKER_TOOLS, description=TRACKER_ABOUT))
    wiki = _named(admin, admin.http_server("uwiki", tools=WIKI_TOOLS, description=WIKI_ABOUT))
    yield stack, admin, tracker, wiki


def _lists(admin: McpAdmin, server: Server) -> int:
    return int(admin.seen(str(server.fake_id))["lists"])


def _calls(admin: McpAdmin, server: Server, name: str) -> int:
    return sum(1 for call in admin.seen(str(server.fake_id))["calls"] if call.get("tool") == name)


def test_l1_the_default_chat_finds_a_servers_tool_by_what_the_server_covers_and_calls_it(world: Any) -> None:
    stack, admin, tracker, _wiki = world
    search = tracker.tools["search_items"]
    before = _calls(admin, tracker, "search_items")
    lists = _lists(admin, tracker)
    fixtures = Fixtures(stack)
    plan = RowPlan(row=Row(id="MCPUSE-L1", array="mcpuse", stack=STACK, factors={}),
                   turn=TurnSpec(kind="assistant", chat_mode="agent"))
    driver = AssistantDriver(stack, fixtures, plan)
    driver.model = fixtures.model(plan.turn.wire)
    stack.model.ok("POST", "/__control/reset")
    marker = f"MCPUSE {secrets.token_hex(3)}"
    record = driver.send(Script("mcpuse", (tool("search_tools", **SEARCH), tool(search, query="SAAS launch"),
                                           f"say {marker}")))
    obs = collect(stack, plan, record)
    requests = (stack.model.ok("GET", "/__control/requests") or {}).get("requests") or []
    offered = [set(map(str, r.get("tools") or [])) for r in requests if r.get("kind") == "loop" and r.get("status") == 200]
    assert obs.terminal == "finished", (obs.terminal, obs.error_code)
    assert offered and not any(search in tools for tools in offered), "the tracker's tool was in the list up front"
    assert _calls(admin, tracker, "search_items") == before + 1, _results(obs)
    # D-LAZY-4: listed once, at the search that first used it; admission and the claim list nothing.
    assert _lists(admin, tracker) - lists == 1, _lists(admin, tracker) - lists


def _results(obs: Any) -> list[str]:  # noqa: ANN401
    """What each tool call of the turn answered, for a failure's message."""
    out = []
    for event in obs.of_type("tool.outcome"):
        ref = str(((event.get("data") or {}).get("resultRef") or {}).get("ref") or "")
        if ref.startswith("inline:"):
            out.append(base64.urlsafe_b64decode(ref.removeprefix("inline:")).decode("utf-8", "replace")[:600])
    return out


def _child_run(turn: Turn) -> str:
    for event in turn.obs.session_events:
        child = (event.get("data") or {}).get("childRunId")
        if isinstance(child, str) and child:
            return child
    raise AssertionError("the turn admitted no job")


@pytest.mark.parametrize("executor", ["opencode", "codex"])
def test_l2_an_engine_job_finds_a_servers_tool_by_what_the_server_covers_and_its_task_names_the_servers(world: Any, executor: str) -> None:
    if executor not in executors_here():
        pytest.skip(f"the {executor} engine image is not on this host")
    stack, admin, tracker, wiki = world
    search = tracker.tools["search_items"]
    agent = admin.agent(f"MCPUSE {executor} {admin.suffix}", [tracker, wiki], engine=executor)
    before = _calls(admin, tracker, "search_items")
    chat = Chat(stack, executor, agent)
    turn = chat.send(tool("search_tools", **SEARCH), tool(search, query="SAAS launch"))
    assert turn.obs.terminal == "finished", turn.failure()
    engine_tools = {str(t) for r in turn.model_requests if r.get("kind") == "engine" for t in r.get("tools") or []}
    assert engine_tools and not any(t == search or t.endswith("_" + search) for t in engine_tools), sorted(engine_tools)
    assert _calls(admin, tracker, "search_items") == before + 1, (turn.tool_results(), [t for t in sorted(engine_tools) if "search" in t or "call" in t])
    brief = stack.api.ok("GET", f"{chat._driver.paths.run(str(turn.record.session_id), _child_run(turn))}/brief")
    # By the name a person knows each server by (its display name).
    sources = {s["name"].lower(): s for s in (brief.get("task") or {}).get("sources") or []}
    assert {tracker.name, wiki.name} <= sources.keys(), (brief.get("task") or {})
    assert sources[tracker.name]["summary"] == TRACKER_ABOUT
    assert sources[tracker.name]["tools"] == len(TRACKER_TOOLS)
    assert sources[wiki.name]["summary"] == WIKI_ABOUT
