"""R36LIVE-FU-2: the default chat's model is told where an attached MCP server's records are, and can reach them.

A live model (gpt-6-luna), asked for "our team's private tracker note on finance", the model searched knowledge
12 to 16 times and said it could not find it, 2 of 2, while the attached server's ``lookup_note`` held it. What the
model was shown, read off the request this world sends (the real assembler, the scripted model):

- "Finding Information", the section a model follows for any question that needs looking up, listed internal
  knowledge as the only place to look: an MCP server's tools carry no connector prefix, so no source line named it;
- a server attached without a description was "e2esaas — attached MCP server tools.", although its stored tools
  said what it holds;
- a server granted whole that this sign-in had never listed named no tool, so the prompt left it out altogether, and
  a search whose words its (empty) description could not match never listed it.

These cases pin the fix: the server is a named place to look, described by its tools when nothing else describes
it, and a search can always reach one nothing describes. The scripted model walks the path a model now has:
search for the capability, call the tool it answers.
"""

from __future__ import annotations

import hashlib
import json
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from app.agents.agent_loop.mcp_session import MCPSessionManager
from app.agents.mcp.models import MCPToolInfo
from app.agents.mcp.tool_cache import CachedCatalog, CachedTool
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.prompt_shown import PROMPT_SHOWN_SLOT
from harness_core.driver import RunDriver
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort

from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

SERVER = "e2esaas"
NOTE = "Private note on finance review: the finance review is due on Friday."
_SCHEMA = {"type": "object", "properties": {"topic": {"type": "string"}}, "required": ["topic"]}
TOOLS = [
    MCPToolInfo(name="lookup_note", namespaced_name="mcp_e2esaas_lookup_note",
                description="Look up the team's private note on a topic.", input_schema=_SCHEMA,
                annotations={"readOnlyHint": True, "openWorldHint": False}),
    MCPToolInfo(name="create_ticket", namespaced_name="mcp_e2esaas_create_ticket",
                description="File a ticket.", input_schema={"type": "object", "properties": {"title": {"type": "string"}}}),
]


@pytest.fixture(autouse=True)
def _deployment(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    with w.deployment():
        # The e2e lanes' server as the seed attaches it: no description, no catalog entry, no instructions.
        async def instance(instance_id: str, *_: object) -> dict[str, Any]:
            return {"_id": instance_id, "orgId": w.ORG, "createdBy": w.ACTOR, "name": SERVER,
                    "transport": "streamable_http", "authMode": "none", "createdAt": 0, "updatedAt": 0}

        monkeypatch.setitem(w.MCP_TOOLS, w.MCP, TOOLS)
        with patch("app.edition_config.get_mcp_instance_resolved", new=instance):
            yield


@pytest.fixture
def listings() -> Iterator[list[str]]:
    seen: list[str] = []
    real = MCPSessionManager.tools

    async def counted(self: MCPSessionManager, server: Any, namespace: str) -> Any:  # noqa: ANN401
        seen.append(server.instance_id)
        return await real(self, server, namespace)

    with patch.object(MCPSessionManager, "tools", counted):
        yield seen


def _stored() -> Any:  # noqa: ANN401 - a tool_cache hit
    tools = [CachedTool(name=t.name, description=t.description, input_schema=t.input_schema,
                        annotations=t.annotations) for t in TOOLS]
    return SimpleNamespace(catalog=CachedCatalog(tools=tools), pointer=SimpleNamespace(digest="d"))


async def _chat(*replies: Reply, stored: bool) -> ScriptedModelPort:
    """One default-chat turn (the server attached, granted whole) on the scripted model."""
    return (await _chat_journal(*replies, stored=stored))[0]


async def _chat_journal(*replies: Reply, stored: bool) -> tuple[ScriptedModelPort, Any]:
    agent = w.agent_doc(mcpServers=[{"instanceId": w.MCP, "name": SERVER, "displayName": SERVER}])
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=w.pinned_spec(tool_ids=[], whole=[w.MCP]),
                  input_text="What does our team's private tracker note on finance say?")
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=r) for r in replies])))
    assembler = PipesHubRuntimeAssembler(w.Services(agent=agent), model_factory=w.model_factory(port))
    driver = RunDriver(journal=journal, assembler=w.ClaimAssembler(assembler), clock=clock)
    with patch("app.agents.agent_loop.mcp_known.tool_cache.read", AsyncMock(return_value=_stored() if stored else None)):
        done = await driver.run_claimed(await w.claim_next(journal))
    assert done.status == "completed", done
    return port, journal


def _section(system: str, heading: str) -> str:
    return system.split(heading, 1)[1].split("\n## ", 1)[0]


def _tool_results(port: ScriptedModelPort) -> list[str]:
    last = port.calls[-1].messages
    return [str(getattr(m, "content", "")) for m in last if getattr(m, "role", "") == "tool"]


# --- AS-1 the server is a place to look, by name ------------------------------------------------------------------


async def test_as1_finding_information_names_the_attached_server_and_how_to_reach_its_tools() -> None:
    port = await _chat(Reply(text="ok"), stored=True)
    system = port.calls[0].system or ""
    finding = _section(system, "## Finding Information")
    assert f"Attached MCP servers ({SERVER})" in finding
    # R43MCP-FU-4: both ways in, as a real model given the server's name takes the shorter one.
    assert "`fetch_tools` on its toolset" in finding
    assert "`search_tools`" in finding and "`call_tool`" in finding
    # Two places to look: the rule to search them together applies.
    assert "IN PARALLEL" in finding


# --- AS-2 a server nothing describes is described by its tools -------------------------------------------------------


async def test_as2_a_server_without_a_description_is_described_by_its_stored_tools() -> None:
    port = await _chat(Reply(text="ok"), stored=True)
    listed = (port.calls[0].system or "").split("Available toolsets:", 1)[1]
    assert f"{SERVER} — Tools: create ticket, lookup note." in listed
    assert "attached MCP server tools." not in listed


# --- AS-3 the path a model now has: search for the capability, call what it answers ----------------------------------


async def test_as3_the_scripted_model_finds_lookup_note_by_search_and_reads_the_note(listings: list[str]) -> None:
    port = await _chat(
        Reply(tool_calls=[ToolCallSpec(name="search_tools", args={"query": "look up private note"})]),
        Reply(tool_calls=[ToolCallSpec(name="call_tool", args={
            "name": "mcp_e2esaas_lookup_note", "arguments": {"topic": "finance review"}})]),
        Reply(text="The finance review is due on Friday."),
        stored=True,
    )
    results = _tool_results(port)
    assert "mcp_e2esaas_lookup_note" in {m["name"] for m in json.loads(results[0])["matches"]}
    assert listings == [w.MCP], "the search's answer is the server's one first use"
    assert len(port.calls) == 3


async def test_as3_the_scripted_model_loads_the_named_servers_toolset_and_reads_the_note(listings: list[str]) -> None:
    """R43MCP-FU-4: the path gpt-6-luna took in a live run (2 of 2): ``fetch_tools`` on the server's toolset, then
    ``call_tool`` on what it answers. Its answer is the server's one first use, as a search's is."""
    port = await _chat(
        Reply(tool_calls=[ToolCallSpec(name="fetch_tools", args={"toolset": f"mcp_{SERVER}"})]),
        Reply(tool_calls=[ToolCallSpec(name="call_tool", args={
            "name": "mcp_e2esaas_lookup_note", "arguments": {"topic": "finance review"}})]),
        Reply(text="The finance review is due on Friday."),
        stored=True,
    )
    results = _tool_results(port)
    assert "mcp_e2esaas_lookup_note" in {t["name"] for t in json.loads(results[0])["tools"]}
    assert len(results) == 2, "the answered tool was called through call_tool"
    assert listings == [w.MCP], "the toolset's load is the server's one first use"
    assert len(port.calls) == 3


# --- AS-4 a server this sign-in never listed is still shown, and a search reaches it -------------------------------


async def test_as4_a_cold_server_is_listed_in_the_prompt_and_the_first_search_lists_it(listings: list[str]) -> None:
    port = await _chat(
        Reply(tool_calls=[ToolCallSpec(name="search_tools", args={"query": "private note"})]),
        Reply(text="done"),
        stored=False,
    )
    system = port.calls[0].system or ""
    assert f"(tools listed when searched): {SERVER} — attached MCP server tools." in system
    assert f"Attached MCP servers ({SERVER})" in _section(system, "## Finding Information")
    found = json.loads(_tool_results(port)[0])
    assert "mcp_e2esaas_lookup_note" in {m["name"] for m in found["matches"]}
    assert listings == [w.MCP]


async def test_as4_without_an_attached_server_nothing_is_added() -> None:
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit("run-1", org_id=w.ORG, session_id="ses-1", spec=w.pinned_spec(tool_ids=[]), input_text="hi")
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))
    _ = await RunDriver(journal=journal, assembler=w.ClaimAssembler(assembler), clock=clock).run_claimed(
        await w.claim_next(journal))
    system = port.calls[0].system or ""
    assert "Attached MCP servers" not in system and "tools listed when searched" not in system


# --- AS-5 what the model was shown is in the journal (R43MCP-FU-5) ---------------------------------------------------


def _shown(journal: Any) -> list[dict[str, Any]]:  # noqa: ANN401
    return [e.data["slots"][PROMPT_SHOWN_SLOT] for e in journal.events("ses-1")
            if e.type == "loop.state_saved" and PROMPT_SHOWN_SLOT in e.data["slots"]]


async def test_as5_the_journal_records_the_prompts_digest_and_its_named_sources() -> None:
    """A live run could not confirm the server was named: the journal held only the messages' digest and ``run.assembled``
    said ``systemChars=0``. Each request now journals, with its response, its stable block's digest and the source
    lines as the model read them: names and the tools a line points to, nothing a server holds."""
    port, journal = await _chat_journal(
        Reply(tool_calls=[ToolCallSpec(name="fetch_tools", args={"toolset": f"mcp_{SERVER}"})]),
        Reply(text="done"),
        stored=True,
    )
    (shown,) = _shown(journal)  # the second request showed the same stable block: nothing new to journal
    stable = (port.calls[0].system_blocks or [""])[0]
    assert shown["stableDigest"] == "sha256:" + hashlib.sha256(stable.encode()).hexdigest()
    assert shown["stableChars"] == len(stable) > 0
    labels = {s["label"]: s["via"] for s in shown["sources"]}
    assert labels[f"Attached MCP servers ({SERVER})"] == ["fetch_tools", "search_tools", "call_tool"]
    assert "lookup_note" not in json.dumps(shown) and "finance" not in json.dumps(shown)


async def test_as5_the_logs_count_the_prompt_the_loop_built(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    port = await _chat(Reply(text="ok"), stored=True)
    assembled = [r.getMessage() for r in caplog.records if "run.assembled" in r.getMessage()]
    shown = [r.getMessage() for r in caplog.records if "run.system_shown" in r.getMessage()]
    assert assembled and "systemChars=0" not in assembled[0] and "per_turn" in assembled[0]
    assert len(shown) == 1 and f"systemChars={len(port.calls[0].system or '')}" in shown[0]
    assert f"Attached MCP servers ({SERVER})" in shown[0]


async def test_as6_the_compiled_path_records_the_same_prompt_shown_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """R44MCP2-FU-3: with the context compiler on, the request is built from the shaper's ``bands``, never ``shape``,
    so nothing recorded what the prompt showed. The compiled path journals the same slot, in the compiler's own
    ``loop.state_saved`` (one entry, not a second one from the shaped path's diff)."""
    replies = (
        Reply(tool_calls=[ToolCallSpec(name="fetch_tools", args={"toolset": f"mcp_{SERVER}"})]),
        Reply(text="done"),
    )
    _, shaped = await _chat_journal(*replies, stored=True)
    monkeypatch.setenv("HARNESS_CONTEXT_COMPILER", "1")
    _, journal = await _chat_journal(*replies, stored=True)
    assert _shown(journal) == _shown(shaped)
    assert len(_shown(journal)) == 1
