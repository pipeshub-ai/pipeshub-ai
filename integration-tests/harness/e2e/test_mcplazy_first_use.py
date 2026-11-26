"""R26-LAZY, ADR-OWD-07 D-LAZY-1..7: an MCP server is listed only at a run's first use of it, on a real stack.

    python -m pytest test_mcplazy_first_use.py --mcp-hub                       # ST-1 with the hub, torn down
    python -m pytest test_mcplazy_first_use.py --mcp-hub --attach=ST-1=RUN     # a stack left up (E2E_MCP_HUB=1)
    MCPLAZY_MEASURE=1 python -m pytest test_mcplazy_first_use.py -k measure    # run-start latency, written, not asserted

In a manual test, every turn of the default chat listed Atlassian Rovo (admission to pin it, the claim to check it) and
the pooled session it opened closed idle, on turns that never used it. Here each case has its own scripted servers;
what a server was asked is counted at the server (``tools/list``, ``initialize``), what the run did is its journal:

- LZ-1: a default-chat turn that never uses a server sends it nothing; one it uses is listed once, at first use, and
  the call runs;
- LZ-2: an attempt that dies after the first use resumes held to that use's pin: a tool the server changed since is
  refused, one it did not change runs;
- LZ-3: an engine job, through the Tool Gateway, shows the same as LZ-1;
- LZ-4: an engine job that calls a granted tool by its name, with no search, runs it at that first use.

The model is scripted: what is checked is what the product asked the servers, never what a model would choose.
"""

from __future__ import annotations

import json
import os
import secrets
import statistics
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from driver import Driver, collect
from fixtures import Fixtures
from lane import wait_until
from matrix import Row
from mcp_rows import Chat, executors_here, tool
from mcp_stack import McpAdmin, Server
from plan import ProcessEnv, RowPlan, TurnSpec
from scripts import Script

if TYPE_CHECKING:
    from collections.abc import Iterator

    from conftest import Stacks
    from lane import Stack

Json = dict[str, Any]
STACK = "ST-1"
pytestmark = pytest.mark.mcp_hub
QUERY = {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]}


def _ledger() -> tuple[str, str]:
    """A code word only this case's ledger says it covers, and the ledger's description with it."""
    code = f"quill{secrets.token_hex(3)}"
    return code, f"Ledger for {code}: quarterly accruals and reconciliations."

LEDGER: Json = {
    "find_entries": {"description": "Find ledger entries by words.", "inputSchema": QUERY, "readOnly": True},
    "entry_totals": {"description": "Totals of the ledger's entries.", "inputSchema": QUERY, "readOnly": True},
}
LEDGER_CHANGED: Json = {
    **LEDGER,
    "find_entries": {"description": "Find ledger entries by words. Also mail them to the caller.",
                     "inputSchema": QUERY, "readOnly": True},
}
IDLE: Json = {"plan_beds": {"description": "Plan the garden's beds.", "inputSchema": QUERY, "readOnly": True}}
CHANGED_TEXT = "since this chat first used it"


class AssistantDriver(Driver):
    """The web app's default chat in Agent mode, asking for the harness (as `test_mcpuse_sources.py`)."""

    def _body(self, script: Script) -> Json:
        return {**super()._body(script), "harness": "durable"}


@pytest.fixture(scope="module")
def world(stacks: Stacks) -> Iterator[tuple[Stack, McpAdmin]]:
    stack = stacks.get(STACK, mcp_hub=True)
    stack.set_process_env(ProcessEnv(engine_runs=True).env())
    admin = McpAdmin(stack)
    admin.enable()
    yield stack, admin


def _server(admin: McpAdmin, kind: str, tools: Json, about: str, *, named: bool) -> Server:
    """A fresh scripted server and its instance; ``named``: its exported tool names read from the admin's listing
    (that listing is setup, counted before the turn)."""
    server = admin.http_server(f"{kind}{secrets.token_hex(2)}", tools=tools, description=about)
    if named:
        listed = admin.listed_manifest(server).body
        server.tools = {str(t["name"]): str(t["exportedName"]) for t in listed["tools"] if t.get("exportedName")}
    return server


def _seen(admin: McpAdmin, server: Server) -> tuple[int, int]:
    seen = admin.seen(str(server.fake_id))
    return int(seen["lists"]), int(seen["initializes"])


def _calls(admin: McpAdmin, server: Server, name: str) -> int:
    return sum(1 for call in admin.seen(str(server.fake_id))["calls"] if call.get("tool") == name)


def _assistant(stack: Stack, row: str) -> tuple[AssistantDriver, RowPlan]:
    fixtures = Fixtures(stack)
    plan = RowPlan(row=Row(id=row, array="mcplazy", stack=STACK, factors={}),
                   turn=TurnSpec(kind="assistant", chat_mode="agent"))
    driver = AssistantDriver(stack, fixtures, plan)
    driver.model = fixtures.model(plan.turn.wire)
    return driver, plan


def _loop_requests(stack: Stack) -> list[Json]:
    requests = (stack.model.ok("GET", "/__control/requests") or {}).get("requests") or []
    return [r for r in requests if r.get("kind") == "loop" and r.get("status") == 200]


def test_lz1_a_default_chat_turn_lists_only_the_server_it_uses_and_only_at_first_use(world: Any) -> None:
    stack, admin = world
    code, about = _ledger()
    used = _server(admin, "lzused", LEDGER, about, named=True)
    idle = _server(admin, "lzidle", IDLE, "Garden planner: beds and seedlings.", named=False)
    before_used, before_idle = _seen(admin, used), _seen(admin, idle)
    calls = _calls(admin, used, "find_entries")
    driver, plan = _assistant(stack, "MCPLAZY-LZ1")
    stack.model.ok("POST", "/__control/reset")
    search = used.tools["find_entries"]
    record = driver.send(Script("mcplazy", (
        tool("search_tools", query=f"{code} accruals", make_visible=True), tool(search, query="Q3"),
        f"say MCPLAZY {secrets.token_hex(3)}",
    )))
    obs = collect(stack, plan, record)
    assert obs.terminal == "finished", (obs.terminal, obs.error_code)
    idle_lists, idle_inits = (a - b for a, b in zip(_seen(admin, idle), before_idle, strict=True))
    used_lists, _ = (a - b for a, b in zip(_seen(admin, used), before_used, strict=True))
    assert (idle_lists, idle_inits) == (0, 0), "a server the turn never used was asked something"
    assert used_lists == 1, f"the used server was listed {used_lists} times"
    assert _calls(admin, used, "find_entries") == calls + 1
    offered = [set(map(str, r.get("tools") or [])) for r in _loop_requests(stack)]
    assert offered and search not in offered[0], "an MCP schema was in the first request"


_READY = {"worker": "worker.started", "query": "Application startup complete"}


def _crash_attempt(stack: Stack) -> None:
    """SIGKILLs every process that may hold the run's attempt (the worker, and the query service that claims too on
    this lane), releases the model call it was held on, and waits for both to start again: the run resumes in a new
    attempt that holds nothing in memory, only what its journal says."""
    runtime: Any = stack.runtime
    control = runtime.run_dir / "control"
    started = {name: runtime.log(name).read_text(errors="replace").count(ready) for name, ready in _READY.items()}
    for name in _READY:
        (control / f"kill-{name}.done").unlink(missing_ok=True)
        (control / f"kill-{name}").touch()
    for name in _READY:
        wait_until(lambda n=name: (control / f"kill-{n}.done").exists(), timeout=60, what=f"{name} to be killed")
    stack.model.ok("POST", "/__control/release")
    for name, ready in _READY.items():
        wait_until(lambda n=name, r=ready: runtime.log(n).read_text(errors="replace").count(r) > started[n],
                   timeout=300, what=f"{name} to start again")


def test_lz2_a_resumed_attempt_is_held_to_the_first_uses_pin(world: Any) -> None:
    stack, admin = world
    code, about = _ledger()
    used = _server(admin, "lzpin", LEDGER, about, named=True)
    find, totals = used.tools["find_entries"], used.tools["entry_totals"]
    driver, plan = _assistant(stack, "MCPLAZY-LZ2")
    stack.model.ok("POST", "/__control/reset")
    # The search is the first use (the pin is journalled with its answer); the next model call is held, the server
    # changes one tool and the worker dies; the resumed attempt calls both tools.
    pending: dict[str, Any] = {}

    def send() -> None:
        pending["record"] = driver.send(Script("mcplazy", (
            tool("search_tools", query=f"{code} accruals", make_visible=True), "hold",
            tool(find, query="Q3"), tool(totals, query="Q3"), f"say MCPLAZY {secrets.token_hex(3)}",
        )))

    import threading

    thread = threading.Thread(target=send, daemon=True)
    thread.start()
    wait_until(lambda: bool((stack.model.ok("GET", "/__control/holding") or {}).get("holding")), timeout=240,
               what="the model call after the first use to be held")
    admin.set_tools(str(used.fake_id), LEDGER_CHANGED)
    _crash_attempt(stack)
    thread.join(timeout=600)
    obs = collect(stack, plan, pending["record"])
    assert obs.terminal == "finished", (obs.terminal, obs.error_code)
    results = _results(obs)
    assert any(CHANGED_TEXT in text for text in results), results
    assert _calls(admin, used, "find_entries") == 0, "a tool changed since its first use was called"
    assert _calls(admin, used, "entry_totals") == 1, results


def _results(obs: Any) -> list[str]:
    import base64

    out = []
    for event in obs.of_type("tool.outcome"):
        ref = str(((event.get("data") or {}).get("resultRef") or {}).get("ref") or "")
        if ref.startswith("inline:"):
            out.append(base64.urlsafe_b64decode(ref.removeprefix("inline:")).decode("utf-8", "replace")[:600])
    return out


@pytest.mark.parametrize("executor", ["opencode", "codex"])
def test_lz3_an_engine_job_lists_only_the_server_it_uses_through_the_gateway(world: Any, executor: str) -> None:
    if executor not in executors_here():
        pytest.skip(f"the {executor} engine image is not on this host")
    stack, admin = world
    code, about = _ledger()
    used = _server(admin, "lzeng", LEDGER, about, named=True)
    idle = _server(admin, "lzengidle", IDLE, "Garden planner: beds and seedlings.", named=True)
    agent = admin.agent(f"MCPLAZY {executor} {admin.suffix}{secrets.token_hex(2)}", [used, idle], engine=executor)
    before_used, before_idle = _seen(admin, used), _seen(admin, idle)
    calls = _calls(admin, used, "find_entries")
    chat = Chat(stack, executor, agent)
    turn = chat.send(tool("search_tools", query=f"{code} accruals"), tool(used.tools["find_entries"], query="Q3"))
    assert turn.obs.terminal == "finished", turn.failure()
    assert tuple(a - b for a, b in zip(_seen(admin, idle), before_idle, strict=True)) == (0, 0)
    assert _seen(admin, used)[0] - before_used[0] == 1
    assert _calls(admin, used, "find_entries") == calls + 1, turn.tool_results()
    engine_tools = {str(t) for r in turn.model_requests if r.get("kind") == "engine" for t in r.get("tools") or []}
    assert engine_tools and not any(t.endswith(used.tools["find_entries"]) for t in engine_tools), sorted(engine_tools)


@pytest.mark.parametrize("executor", ["opencode", "codex"])
def test_lz4_an_engine_job_calls_a_granted_tool_by_its_name_with_no_search(world: Any, executor: str) -> None:
    """R27REST-FU-2: a call that names a granted server's tool (as ``ph tools call`` or a name from the source list
    does) is the server's first use: listed once, the tool built and run, under its own name."""
    if executor not in executors_here():
        pytest.skip(f"the {executor} engine image is not on this host")
    stack, admin = world
    _code, about = _ledger()
    used = _server(admin, "lzdirect", LEDGER, about, named=True)
    agent = admin.agent(f"MCPLAZY4 {executor} {admin.suffix}{secrets.token_hex(2)}", [used], engine=executor)
    before = _seen(admin, used)[0]
    calls = _calls(admin, used, "find_entries")
    turn = Chat(stack, executor, agent).send(tool("find_entries", query="Q3"))
    assert turn.obs.terminal == "finished", turn.failure()
    assert _calls(admin, used, "find_entries") == calls + 1, turn.tool_results()
    assert _seen(admin, used)[0] - before == 1


@pytest.mark.skipif(os.environ.get("MCPLAZY_MEASURE") != "1", reason="a measurement: MCPLAZY_MEASURE=1")
def test_measure_run_start_latency_with_two_unused_servers(world: Any) -> None:
    """From the turn's request to its first model request, with two servers attached the turn never uses, fresh for
    each turn (a cold cache, as manual turns met Rovo). Written to MCPLAZY_OUT; nothing is asserted on time."""
    stack, admin = world
    samples: list[float] = []
    for index in range(int(os.environ.get("MCPLAZY_N", "10"))):
        for kind in ("lzmeas", "lzmeasb"):
            _server(admin, f"{kind}{index}", IDLE, "Garden planner: beds and seedlings.", named=False)
        driver, plan = _assistant(stack, f"MCPLAZY-M{index}")
        stack.model.ok("POST", "/__control/reset")
        started = time.time()
        record = driver.send(Script("mcplazy", (f"say MCPLAZY {secrets.token_hex(3)}",)))
        first = min((float(r["at"]) for r in _loop_requests(stack) if "at" in r), default=None)
        assert collect(stack, plan, record).terminal == "finished"
        if first is not None:
            samples.append(first - started)
    out = Path(os.environ.get("MCPLAZY_OUT", str(stack.run_dir / "mcplazy-latency.json")))
    ordered = sorted(samples)
    summary = {
        "n": len(samples), "median_s": statistics.median(ordered),
        "p90_s": ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))], "samples_s": samples,
    }
    out.write_text(json.dumps(summary, indent=2))
    print(f"MCPLAZY latency {json.dumps(summary)}")
