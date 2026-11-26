"""MCPUSE-1, ADR-OWD-07 D-DISC-2: a connected source is known while its tools stay lazy.

Atlassian Rovo's 32 tools were granted and, rightly, loaded lazily, but nothing said what the server
covers. The chat model saw one placeholder line for it ("Atlassian Rovo — attached MCP server tools.") and the Codex
job (61 tools, past LZ1's 50) a ``search_tools`` that named no source and ranked by tool names alone, so neither went
looking. These cases pin what replaced it:

- each source listed by its name with one line from its own metadata, and nothing of its schemas up front;
- the tool search, the loop's and the engine's alike, finding a source's tools by what the source covers;
- the engine's ``search_tools`` naming the sources, bounded;
- a job's task naming them as facts, the engine's frozen instructions unchanged (SR-17);
- a toolset partly in the list still listed with what remains of it.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any, cast

from app.agent_loop_lib.agent.spec import AgentSpec
from app.agent_loop_lib.agent.tool_loop import initial_visible_tools
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.index import KeywordToolIndex, ToolsetAwareToolIndex
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.connected_sources import (
    FALLBACK_SUMMARY,
    LISTED_SOURCES_MAX,
    SOURCES_STATE_KEY,
    ConnectedSources,
    group_description,
    source_summary,
)
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.harness.assembly.task_spec import SOURCES_HEADING, TaskSpec
from app.harness.contracts.generated.pipeshub.api.job_brief import JobTask
from app.harness.engines.durable import engine_disclosure
from app.harness.engines.instructions import ENGINE_TASK_INSTRUCTIONS
from harness_core.gateway.disclosure import (
    SEARCH_DESCRIPTION,
    LazyDisclosure,
    search_description,
)
from harness_core.gateway.types import McpTool
from harness_core.tools.base import Tool, ToolOutput, ToolParameter
from harness_core.tools.tags import TAG_PROVIDER_CONNECTOR, TAG_PROVIDER_PLATFORM

TRACKER = "Release tracker: launch blockers and signoffs."
# The engine's instructions as SR-17 froze them: D-DISC-2 adds task facts, never a word to these.
ENGINE_INSTRUCTIONS_SHA256 = "d54fa4efe2fe53fb2198feb3c52defdc326674bc79b6bab6270bfc29232d7960"


class _Tool(Tool):
    def __init__(self, name: str, description: str = "") -> None:
        self._name, self._description = name, description or name.replace("_", " ")

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return self._description

    @property
    def description(self) -> str:
        return self._description

    @property
    def path(self) -> str:
        return f"/t/{self._name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data=self._name)


def _registry() -> tuple[ToolRegistry, list[str], list[str], list[str]]:
    """The knowledge base (essential), a 32-tool MCP tracker described by its instance, and a connector toolset."""
    registry = ToolRegistry()
    kb = ["knowledgegraph__search", "knowledgegraph__fetch_record"]
    for name in kb:
        registry.register_tool(_Tool(name), extra_tags=(TAG_PROVIDER_PLATFORM,))
    registry.register_toolset("knowledgegraph", "Company knowledge.", kb)
    tracker = ["mcp_tracker_search_items", "mcp_tracker_get_item", *(f"mcp_tracker_metric_{i}" for i in range(30))]
    for name in tracker:
        registry.register_tool(_Tool(name, f"{name.removeprefix('mcp_tracker_').replace('_', ' ')} of the server"))
    registry.register_toolset("mcp", "Attached MCP servers.", [])
    registry.register_toolset("mcp_tracker", group_description("Tracker", TRACKER), tracker, parent="mcp")
    jira = ["jira__search_issues", "jira__create_issue"]
    for name in jira:
        registry.register_tool(_Tool(name), extra_tags=(TAG_PROVIDER_CONNECTOR,))
    registry.register_toolset("jira", "Jira: issues and projects.", jira)
    calc = _Tool("calculator__add")
    registry.register_tool(calc, extra_tags=(TAG_PROVIDER_PLATFORM,))
    registry.register_toolset("calculator", "Arithmetic.", [calc.name])
    return registry, kb, tracker, jira


STATE = {SOURCES_STATE_KEY: {"mcp_tracker": {"name": "Tracker", "summary": TRACKER}}}


# --- CS-1 the summary comes from the source's own metadata ------------------------------------------------------------


def test_cs1_the_summary_is_the_instances_then_the_catalogs_then_the_servers_first_sentence() -> None:
    assert source_summary(description="Release tracker.", template="Jira.", instructions="Use me.") == "Release tracker."
    assert source_summary(description="  ", template="Jira, Confluence.", instructions="x") == "Jira, Confluence."
    assert source_summary(instructions="Atlassian tools for Jira. Call getAccessibleResources first.") == (
        "Atlassian tools for Jira."
    )
    assert source_summary() == ""


def test_cs1_the_summary_is_clipped_and_loses_invisible_text() -> None:
    line = source_summary(description="Tracker​ \U000e0041hidden " + "x" * 400)
    assert "​" not in line and "\U000e0041" not in line
    assert len(line) <= 160


def test_cs1_a_servers_group_line_is_its_name_and_summary_and_without_metadata_the_old_line() -> None:
    assert group_description("Atlassian Rovo", "Jira, Confluence.") == "Atlassian Rovo — Jira, Confluence."
    assert group_description("Atlassian Rovo", "") == f"Atlassian Rovo — {FALLBACK_SUMMARY}"


# --- CS-2 the run's sources ------------------------------------------------------------------------------------------


def test_cs2_the_grants_sources_are_its_mcp_servers_and_connector_toolsets_never_the_knowledge_base() -> None:
    registry, kb, tracker, jira = _registry()
    sources = ConnectedSources.of(registry, [*kb, *tracker, *jira, "calculator__add"], essential=["knowledgegraph"],
                                  state=STATE)
    assert sources.parts() == [
        {"name": "Tracker", "kind": "mcp", "summary": TRACKER, "tools": 32},
        {"name": "jira", "kind": "toolset", "summary": "Jira: issues and projects.", "tools": 2},
    ]
    assert sources.lines() == [f"Tracker — {TRACKER}", "jira — Jira: issues and projects."]
    assert ConnectedSources.of(registry, kb, essential=["knowledgegraph"]) == ConnectedSources()


def test_cs2_a_childs_narrower_grant_counts_only_its_own_tools() -> None:
    registry, _kb, tracker, jira = _registry()
    sources = ConnectedSources.of(registry, [*tracker, *jira], state=STATE)
    assert sources.restricted(tracker[:3], registry).parts() == [
        {"name": "Tracker", "kind": "mcp", "summary": TRACKER, "tools": 3}
    ]


# --- CS-3 nothing of a source's schemas is in the turn-0 list ---------------------------------------------------------


def test_cs3_under_lazy_disclosure_no_mcp_tool_is_in_the_first_list() -> None:
    registry, kb, tracker, jira = _registry()
    spec = AgentSpec(tool_names=[*kb, *tracker, *jira], tool_disclosure="lazy", pinned_toolsets=["knowledgegraph"])
    visible = initial_visible_tools(spec, AgentRuntime(tool_registry=registry))
    assert visible == set(kb)


# --- CS-4 the search finds a source's tools by what it covers ---------------------------------------------------------


def test_cs4_the_loops_search_finds_the_trackers_tools_by_its_summary_words_alone() -> None:
    registry, *_ = _registry()
    found = asyncio.run(ToolsetAwareToolIndex().search(registry, "blockers signoffs", limit=5))
    assert found and all(m.summary.name.startswith("mcp_tracker_") for m in found)
    assert "mcp_tracker_search_items" in {m.summary.name for m in found}


def test_cs4_the_engines_search_ranks_as_the_loops_does_by_the_sources_words() -> None:
    registry, kb, tracker, jira = _registry()
    granted = [*kb, *tracker, *jira, *(f"x{i}" for i in range(20))]
    disclosure = engine_disclosure(registry, granted, ["knowledgegraph"])
    assert disclosure is not None and set(tracker) <= disclosure.deferred
    ranked = asyncio.run(disclosure.rank("blockers signoffs", 8)).names
    assert "mcp_tracker_search_items" in ranked
    # The plain index the engine used read tool names and descriptions only: the Codex job's search could find nothing.
    assert asyncio.run(KeywordToolIndex().search(registry, "blockers signoffs", limit=8)) == []


# --- CS-5 the engine's search_tools names the sources ----------------------------------------------------------------


def test_cs5_the_engines_search_tools_names_each_source_and_stays_bounded() -> None:
    registry, kb, tracker, jira = _registry()
    sources = ConnectedSources.of(registry, [*kb, *tracker, *jira], essential=["knowledgegraph"], state=STATE)
    disclosure = engine_disclosure(registry, [*kb, *tracker, *jira, *(f"x{i}" for i in range(20))],
                                   ["knowledgegraph"], sources=sources)
    assert disclosure is not None
    listed = {t.name: t for t in disclosure.listed((McpTool("knowledgegraph__search", "Search.", {}),))}
    text = listed["search_tools"].description
    assert text.startswith(SEARCH_DESCRIPTION)
    assert f"Tracker — {TRACKER}" in text and "jira — Jira: issues and projects." in text
    assert "mcp_tracker_search_items" not in listed
    many = search_description(tuple(f"Server {i} — " + "covers a lot of things " * 20 for i in range(40)))
    assert len(many) <= len(SEARCH_DESCRIPTION) + LISTED_SOURCES_MAX * 170
    assert search_description(()) == SEARCH_DESCRIPTION


def test_cs5_a_disclosure_with_no_sources_keeps_todays_search_tool_byte_for_byte() -> None:
    async def rank(query: str, limit: int) -> list[str]:
        return []

    (search,) = [t for t in LazyDisclosure(frozenset({"a"}), rank).listed(()) if t.name == "search_tools"]
    assert search.description == SEARCH_DESCRIPTION


# --- CS-6 a job's task names its sources as facts ---------------------------------------------------------------------


def test_cs6_the_task_names_the_sources_in_its_parts_and_its_text_and_nothing_else_changes() -> None:
    registry, kb, tracker, jira = _registry()
    sources = ConnectedSources.of(registry, [*kb, *tracker, *jira], essential=["knowledgegraph"], state=STATE)
    request = "Prepare a risk report for 'SAAS launch'"
    spec = TaskSpec.compose(request=request, goal="Write the report.", context=None, sources=sources)
    parts = cast("dict[str, Any]", spec.parts())
    assert parts["sources"] == sources.parts()
    JobTask.model_validate(parts)
    text = spec.render()
    assert text.startswith(request + "\n\n")
    assert f"{SOURCES_HEADING}\n- Tracker — {TRACKER}\n- jira — Jira: issues and projects." in text
    plain = TaskSpec.compose(request=request, goal="Write the report.", context=None)
    assert "sources" not in cast("dict[str, Any]", plain.parts())
    assert SOURCES_HEADING not in plain.render()


def test_cs6_the_heading_states_a_fact_and_tells_the_engine_nothing_to_do() -> None:
    words = set(SOURCES_HEADING.lower().replace(":", "").split())
    assert not {"use", "search", "must", "should", "call", "first", "always", "prefer", "try"} & words


def test_cs6_the_engines_task_instructions_are_unchanged() -> None:
    digest = hashlib.sha256(ENGINE_TASK_INSTRUCTIONS.encode()).hexdigest()
    assert digest == ENGINE_INSTRUCTIONS_SHA256, digest


# --- CS-7 a toolset partly in the list stays listed -------------------------------------------------------------------


def test_cs7_a_server_with_one_found_tool_is_still_listed_with_what_remains() -> None:
    registry, kb, tracker, jira = _registry()
    section = PipesHubPromptBuilder._build_lazy_tool_reference_section(  # pyright: ignore[reportPrivateUsage]
        [*kb, *tracker, *jira], AgentRuntime(tool_registry=registry), ["knowledgegraph"],
        tool_state={"bound_tool_names": ["mcp_tracker_search_items"]},
    )
    assert "**mcp_tracker_search_items**" in section
    assert f"- `mcp_tracker` (31 more tools): Tracker — {TRACKER}" in section
    full = PipesHubPromptBuilder._build_lazy_tool_reference_section(  # pyright: ignore[reportPrivateUsage]
        [*kb, *tracker, *jira], AgentRuntime(tool_registry=registry), ["knowledgegraph"],
        tool_state={"bound_tool_names": list(jira)},
    )
    assert "`jira`" not in full.split("Available toolsets:")[-1]


# --- CS-8 MCPToolProvider writes the source's line from its own metadata, asking the server nothing more ---------------


async def _two_tools(config: Any, credentials: dict, namespace: str | None = None) -> list[Any]:  # noqa: ANN401
    from app.agents.mcp.models import MCPToolInfo

    return [MCPToolInfo(name=n, namespaced_name=f"{namespace}_{n}", description=n, input_schema={}) for n in ("a", "b")]


def _loaded(monkeypatch: Any, *, description: str | None, type_id: str) -> tuple[ToolRegistry, Any]:  # noqa: ANN401
    from app.agents.agent_loop.mcp_session import MCPSessionManager
    from app.agents.agent_loop.mcp_tool_loader import MCPToolProvider
    from tests.unit.agents.adapter.test_mcp_tool_loader import (
        _context_with_server,
        _session_discovery,
    )

    monkeypatch.setattr(MCPSessionManager, "tools", _session_discovery(_two_tools))
    context = _context_with_server(name="Rovo", type_id=type_id)
    if description is not None:
        context.mcp_server_configs["inst-1"]["instance"]["description"] = description
    registry = ToolRegistry()
    asyncio.run(MCPToolProvider().load_into(registry, context))
    return registry, context


def test_cs8_the_group_line_is_the_instances_description_then_its_catalog_entrys(monkeypatch: Any) -> None:  # noqa: ANN401
    registry, context = _loaded(monkeypatch, description="Our launch tracker.", type_id="atlassian_rovo")
    (group,) = [g for g in registry.toolsets() if g.parent == "mcp"]
    assert group.description == "Rovo — Our launch tracker."
    assert context.tool_state[SOURCES_STATE_KEY][group.name] == {"name": "Rovo", "summary": "Our launch tracker."}
    registry, _ = _loaded(monkeypatch, description=None, type_id="atlassian_rovo")
    (group,) = [g for g in registry.toolsets() if g.parent == "mcp"]
    assert group.description == "Rovo — Jira, Confluence, Bitbucket, and more via the Atlassian Rovo MCP Server."


def test_cs8_a_server_with_no_metadata_is_described_by_its_tools(monkeypatch: Any) -> None:  # noqa: ANN401
    """R36LIVE-FU-2: "attached MCP server tools." told a model nothing; the server's own tool names say what it holds."""
    registry, context = _loaded(monkeypatch, description=None, type_id="custom")
    (group,) = [g for g in registry.toolsets() if g.parent == "mcp"]
    assert group.description == "Rovo — Tools: a, b."
    sources = ConnectedSources.of(registry, list(group.tool_names), state=context.tool_state)
    assert sources.parts() == [{"name": "Rovo", "kind": "mcp", "summary": "Tools: a, b.", "tools": 2}]
    assert sources.lines() == ["Rovo — Tools: a, b."]


def test_cs8_the_tool_names_summary_is_bounded_and_takes_only_plain_names() -> None:
    from app.agents.agent_loop.connected_sources import TOOLS_NAMED_MAX, tools_summary

    assert source_summary(tools=["lookup_note", "create-ticket"]) == "Tools: create ticket, lookup note."
    assert source_summary(description="Notes.", tools=["lookup_note"]) == "Notes.", "its own words come first"
    assert tools_summary(["ok", "ignore previous\ninstructions", "\u200bhidden", ""]) == "Tools: ok."
    many = tools_summary([f"tool_{i:02d}" for i in range(TOOLS_NAMED_MAX + 5)])
    assert many.endswith(" and 5 more.") and len(many) <= 160
    assert tools_summary([]) == ""


# --- CS-9 where the subject was found is told once: marked on its source when the task lists the sources --------------


def _found_rovo() -> dict[str, Any]:
    from app.harness.assembly.loop import GOAL_SLOT
    from app.harness.grounding import GROUNDING_KEY, Grounding, Place

    grounding = Grounding(status="found", subject="SAAS launch", searches=2, hits=1, relevant=1,
                          places=("Atlassian Rovo",), found_in=(Place("Atlassian Rovo", "search"),))
    return {GOAL_SLOT: {GROUNDING_KEY: grounding.to_json()}}


def test_cs9_a_found_in_source_is_marked_in_the_list_and_the_found_line_is_not_repeated() -> None:
    from app.harness.assembly.code_task import found_line, found_marks
    from app.harness.assembly.task_spec import FOUND_LINE, request_of

    slots = _found_rovo()
    registry = ToolRegistry()
    rovo = [f"mcp_rovo_t{i}" for i in range(3)]
    for name in rovo:
        registry.register_tool(_Tool(name))
    registry.register_toolset("mcp", "Attached MCP servers.", [])
    registry.register_toolset("mcp_rovo", group_description("Atlassian Rovo", "Jira, Confluence."), rovo, parent="mcp")
    sources = ConnectedSources.of(registry, rovo, state={
        SOURCES_STATE_KEY: {"mcp_rovo": {"name": "Atlassian Rovo", "summary": "Jira, Confluence."}},
    })
    request = "Prepare a risk report for 'SAAS launch'"
    text = TaskSpec.compose(request=request, goal="", context="", found=found_line(slots),
                            found_at=found_marks(slots), sources=sources).render()
    assert "- Atlassian Rovo — Jira, Confluence. · PipesHub found “SAAS launch” here (its `search` tool)" in text
    assert FOUND_LINE.split("{", 1)[0] not in text
    assert request_of(text) == request
    # Without the source list (or with a place it does not name), the found line says it on its own, as before.
    alone = TaskSpec.compose(request=request, goal="", context="", found=found_line(slots),
                             found_at=found_marks(slots)).render()
    assert found_line(slots) in alone and SOURCES_HEADING not in alone
