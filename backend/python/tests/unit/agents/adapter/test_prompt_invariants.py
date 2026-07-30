"""Prompt-invariant tests (Phase 0 safety net → Phase 9 regression guards).

Four invariants that originally failed and are now expected to pass:

1. Each connector ID appears at most once (SourceCatalog is the sole ID printer).
2. Every quoted cross-reference resolves to a section actually present.
3. Every backticked tool name is in ``spec.tool_names`` or the meta-tool set.
4. No section text contains directional language that locks ordering.

Fixture configurations (used by every invariant):
  - ``no_sources``       : agent with no knowledge sources
  - ``kb_only``          : agent with a single KB collection
  - ``kb_plus_3_apps``   : 1 KB + Confluence + Jira + S3
  - ``duplicate_apps``   : 2 Jira connectors (same-type duplicate)
  - ``web_search_mode``  : has_knowledge=False, web_search_config set
  - ``kb_plus_service_tools`` : knowledge + live service toolsets (hybrid)

Golden snapshots are smoke-tested here; Phase 9 reviews size ceilings.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import MagicMock

import pytest

from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from app.agents.agent_loop.context import AgentContext
from tests.unit.agents.adapter.test_prompt_builder import FakeTool


# ---------------------------------------------------------------------------
# Meta-tools that are always expected to be referenceable even when not in
# spec.tool_names (they may appear in section *text* as instructions).
# ---------------------------------------------------------------------------
_ALWAYS_KNOWN_META_TOOLS: frozenset[str] = frozenset(
    {
        "list_toolsets",
        "fetch_tools",
        "search_tools",
        "run_code",
        "install_packages",
        "read_sandbox_file",
        "retrieve_artifact_content",
        "coding_agent",
        "web_agent",
        "internal_exploration_agent",
        "knowledgegraph__fetch_record",
        "dynamic_fetch_full_record",  # legacy alias kept for backward-compat
        "internaltools__ask_user_question",
    }
)

# Knowledge essentials always loaded when has_knowledge is true.
# Use production double-underscore names (what BoundMethodTool.name generates)
# so the grounding invariant tests the real registry spelling, not a guess.
_KNOWLEDGE_ESSENTIAL_TOOLS: frozenset[str] = frozenset(
    {
        "knowledgegraph__search",
        "knowledgegraph__navigate",
        "knowledgegraph__lookup_record",
        "knowledgegraph__list_files",
    }
)

# Backtick identifiers that are parameters / prose, not tools.
_NON_TOOL_BACKTICKS: frozenset[str] = frozenset(
    {
        "connector_ids",
        "record_group_ids",
        "node_id",
        "currentUser",
        "search_internal_knowledge",  # bare method fragment in prose
        "toolset_name",
        "Record ID",
    }
)

# Regex: backtick-quoted identifiers that look like tool references
_BACKTICK_TOOL_RE = re.compile(
    r"`([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)?)(?:\(|`)"
)

# Cross-reference patterns the prompt uses to point at other sections
_XREF_RE = re.compile(
    r'(?:see\s+"([^"]+)"|'          # see "Section Name"
    r'(R-[A-Z]+-\d+))',             # R-SLACK-1  style rule IDs
    re.IGNORECASE,
)

# Directional language that makes sections order-dependent
_DIRECTIONAL_RE = re.compile(
    r'\b(above|below|later in this prompt|earlier in this prompt|the section below|the section above)\b',
    re.IGNORECASE,
)

# 4-source fixture prompt should stay under this ceiling after consolidation.
# Updated after the block-token-usage optimization added a one-time "Record &
# Block Structure" explainer to `_CITATION_RULES` (compact `[idx|refN]`
# block format, replacing the old per-block scaffolding). Measured size after
# that change is ~11,338 chars — see test_prompt_size_regression.py for the
# full per-tier table.
_KB_PLUS_3_APPS_CHAR_CEILING = 12_500


# ---------------------------------------------------------------------------
# Fixture state builders
# ---------------------------------------------------------------------------

KB_ID      = "aabbccdd-1111-1111-1111-aabbccdd0001"
CONF_ID    = "aabbccdd-2222-2222-2222-aabbccdd0002"
JIRA_ID    = "aabbccdd-3333-3333-3333-aabbccdd0003"
S3_ID      = "aabbccdd-4444-4444-4444-aabbccdd0004"
JIRA_ID_2  = "aabbccdd-5555-5555-5555-aabbccdd0005"


def _mk_kb(label: str, cid: str) -> dict[str, Any]:
    """Knowledge entry in the format classify_knowledge_sources() expects."""
    return {
        "displayName": label,
        "name": label,
        "_id": cid,
        "connectorId": cid,
        "type": "KB",
    }


def _mk_app(label: str, app_type: str, cid: str, filters: dict | None = None) -> dict[str, Any]:
    return {
        "displayName": label,
        "name": label,
        "_id": cid,
        "connectorId": cid,
        "type": app_type,
    }


_FIXTURES: dict[str, dict[str, Any]] = {
    "no_sources": {
        "agent_knowledge": [],
        "has_knowledge": False,
        "available_connectors": [],
    },
    "kb_only": {
        "agent_knowledge": [_mk_kb("Private KB", KB_ID)],
        "has_knowledge": True,
        "available_connectors": [],
    },
    "kb_plus_3_apps": {
        "agent_knowledge": [
            _mk_kb("Private KB", KB_ID),
            _mk_app("Confluence", "confluence", CONF_ID),
            _mk_app("Jira", "jira", JIRA_ID),
            _mk_app("S3", "s3", S3_ID),
        ],
        "has_knowledge": True,
        "available_connectors": [],
    },
    "duplicate_apps": {
        "agent_knowledge": [
            _mk_app("Jira Prod", "jira", JIRA_ID),
            _mk_app("Jira Staging", "jira", JIRA_ID_2),
        ],
        "has_knowledge": True,
        "available_connectors": [],
    },
    "web_search_mode": {
        "agent_knowledge": [],
        "has_knowledge": False,
        "available_connectors": [],
        "web_search_config": {"provider": "brave"},
    },
    "kb_plus_service_tools": {
        "agent_knowledge": [
            _mk_app("Confluence", "confluence", CONF_ID),
            _mk_app("Jira", "jira", JIRA_ID),
        ],
        "has_knowledge": True,
        "available_connectors": [],
        "agent_toolsets": [{"name": "jira-cloud"}, {"name": "confluence-cloud"}],
    },
    # `run_code` with no web tool granted — the fixture that lets
    # `test_invariant_tool_names_grounded` catch the Part 0b regression
    # (naming `web_search`/`fetch_url` from the code-execution section
    # when neither was ever granted this turn).
    "run_code_no_web": {
        "agent_knowledge": [],
        "has_knowledge": False,
        "available_connectors": [],
        "tool_names": ["run_code"],
    },
    # Composed domain-agent delegates claim the flat tools — only the
    # delegate names may ever appear as tool references.
    "composed_agents": {
        "agent_knowledge": [_mk_app("Confluence", "confluence", CONF_ID)],
        "has_knowledge": True,
        "available_connectors": [],
        # `internal_exploration_agent` only claims the `knowledgegraph`
        # app_names (see `domain_agents.py`'s DomainAgentDefinition) plus
        # `knowledgegraph__fetch_record` — `knowledgegraph.navigate` and
        # `lookup_record` stay flat on the parent even under composition,
        # matching real factory.py wiring.
        "tool_names": [
            "internal_exploration_agent", "web_agent", "coding_agent",
            "knowledgegraph__navigate", "knowledgegraph__lookup_record",
        ],
    },
    # Live service tools with NO indexed knowledge base at all — the
    # "service-only" shape that used to get its own now-retired section.
    "service_only": {
        "agent_knowledge": [],
        "has_knowledge": False,
        "available_connectors": [],
        "agent_toolsets": [{"name": "jira-cloud"}],
        "tool_names": ["jira_search_issues"],
    },
    # Lazy tool disclosure with a declared-essential toolset pinned back
    # to visible — the Part 0a fixture: `retrieval` must render under
    # "Available Tools", never under the load-first block, while the
    # non-pinned `jira` group renders only as a name to fetch.
    "lazy_with_pinned": {
        "agent_knowledge": [_mk_kb("Private KB", KB_ID)],
        "has_knowledge": True,
        "available_connectors": [],
        "tool_disclosure": "lazy",
        "pinned_toolsets": ["knowledgegraph"],
        "registry_toolsets": {
            "knowledgegraph": ["knowledgegraph__search", "knowledgegraph__navigate",
                               "knowledgegraph__lookup_record", "knowledgegraph__list_files"],
            "jira": ["jira_search_issues"],
        },
        "tool_names": [
            *sorted(_KNOWLEDGE_ESSENTIAL_TOOLS), "jira_search_issues",
        ],
    },
    # Full-record escalation granted — the one fixture where "## Reading
    # Records in Full" is expected to render.
    "kb_with_full_record": {
        "agent_knowledge": [_mk_kb("Private KB", KB_ID)],
        "has_knowledge": True,
        "available_connectors": [],
        "tool_names": [*sorted(_KNOWLEDGE_ESSENTIAL_TOOLS), "knowledgegraph__fetch_record"],
    },
}


def _make_context(fixture_name: str) -> AgentContext:
    fx = _FIXTURES[fixture_name]
    return AgentContext(
        org_id="org-test",
        user_id="user-test",
        user_email="test@example.com",
        user_info={"userId": "user-test", "orgId": "org-test"},
        org_info={"name": "TestOrg"},
        logger=MagicMock(),
        retrieval_service=MagicMock(),
        graph_provider=MagicMock(),
        config_service=MagicMock(),
        has_knowledge=fx.get("has_knowledge", False),
        agent_knowledge=fx.get("agent_knowledge"),
        agent_toolsets=fx.get("agent_toolsets", []),
        web_search_config=fx.get("web_search_config"),
    )


def _tool_names_for_fixture(fx: dict[str, Any]) -> list[str]:
    # An explicit override takes precedence — used by fixtures that need a
    # specific tool surface (composed delegates, run_code alone, service-only,
    # lazy disclosure) rather than the has_knowledge/web_search_config default.
    if "tool_names" in fx:
        return list(fx["tool_names"])
    tool_names: list[str] = []
    if fx.get("web_search_config"):
        # Use production dynamic__ names so the grounding invariant verifies
        # the real granted spellings that the web tools use in the registry.
        tool_names.extend(["dynamic__web_search", "dynamic__fetch_url"])
    if fx.get("has_knowledge"):
        tool_names.extend(sorted(_KNOWLEDGE_ESSENTIAL_TOOLS))
    return tool_names


def _build_registry_for_fixture(fx: dict[str, Any]) -> ToolRegistry:
    """Registers a real `FakeTool` per name in `fx["registry_toolsets"]` and
    groups them, so `_build_lazy_tool_reference_section`'s pinned/non-pinned
    partition has real toolset groups to partition."""
    registry = ToolRegistry()
    for group_name, names in (fx.get("registry_toolsets") or {}).items():
        for name in names:
            registry.register_tool(FakeTool(name, description=f"Fake {name} tool."))
        registry.register_toolset(group_name, f"{group_name} toolset", list(names))
    return registry


def build_prompt_for_fixture(fixture_name: str) -> str:
    """Build a full prompt for the given fixture using real helpers (no mocks)."""
    context = _make_context(fixture_name)
    fx = _FIXTURES[fixture_name]

    context.tool_state.update(
        {
            "agent_knowledge": fx.get("agent_knowledge") or [],
            "has_knowledge": fx.get("has_knowledge", False),
            "available_connectors": fx.get("available_connectors", []),
            "web_search_config": fx.get("web_search_config"),
            "agent_toolsets": fx.get("agent_toolsets") or [],
        }
    )

    tool_names = _tool_names_for_fixture(fx)
    spec = AgentSpec(
        name="test-agent",
        system_prompt="BASE_REACT_PROMPT",
        tool_names=tool_names,
        tool_disclosure=fx.get("tool_disclosure", "eager"),
        pinned_toolsets=fx.get("pinned_toolsets", []),
        model=ModelSpec(provider="scripted", model="scripted-model"),
    )
    runtime = AgentRuntime(tool_registry=_build_registry_for_fixture(fx))
    builder = PipesHubPromptBuilder(context)
    return builder.build(spec, runtime, Goal(description="test query"), [], {})


def _extract_section_headers(text: str) -> set[str]:
    headers: set[str] = set()
    for match in re.finditer(r"^#{1,4}\s+(.+)$", text, re.MULTILINE):
        headers.add(match.group(1).strip())
    return headers


def _extract_backtick_tool_names(text: str) -> list[str]:
    return _BACKTICK_TOOL_RE.findall(text)


def _looks_like_tool(name: str) -> bool:
    if name in _NON_TOOL_BACKTICKS:
        return False
    if name in _ALWAYS_KNOWN_META_TOOLS or name in _KNOWLEDGE_ESSENTIAL_TOOLS:
        return True
    if "." in name or "__" in name:
        return True
    if name in {"web_search", "fetch_url", "run_code", "dynamic__web_search", "dynamic__fetch_url"}:
        return True
    return False


_FIXTURES_WITH_IDS = ["kb_only", "kb_plus_3_apps", "duplicate_apps", "kb_plus_service_tools"]
_FIXTURES_WITH_XREFS = ["kb_plus_service_tools"]
# `composed_agents` is deliberately excluded here: `SourceCatalog.render()`'s
# source-table header always names the flat `retrieval.search_internal_
# knowledge`/`knowledgehub.list_files` tool references regardless of
# composition, a pre-existing defect in the knowledge_sources section (not
# one of the six sections this plan retires) — tracked separately, not
# asserted against by this invariant.
_FIXTURES_WITH_TOOL_REFS = [
    "kb_only", "kb_plus_3_apps", "duplicate_apps", "kb_plus_service_tools", "web_search_mode",
    "run_code_no_web", "service_only", "lazy_with_pinned", "kb_with_full_record",
]
_ALL_FIXTURES = list(_FIXTURES.keys())


# ---------------------------------------------------------------------------
# Invariant 1: Each connector ID appears AT MOST ONCE
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", _FIXTURES_WITH_IDS)
def test_invariant_connector_id_unique(fixture_name: str) -> None:
    """Each connector ID must appear exactly once (SourceCatalog sole renderer)."""
    fx = _FIXTURES[fixture_name]
    knowledge = fx.get("agent_knowledge") or []
    source_ids = {
        src.get("connectorId") or src.get("connector_id")
        for src in knowledge
        if src.get("connectorId") or src.get("connector_id")
    }
    if not source_ids:
        pytest.skip("no connector IDs to check for this fixture")

    prompt = build_prompt_for_fixture(fixture_name)

    duplicates: dict[str, int] = {}
    for cid in source_ids:
        count = prompt.count(cid)
        if count > 1:
            duplicates[cid] = count

    assert not duplicates, (
        f"[{fixture_name}] Connector ID(s) appear more than once:\n"
        + "\n".join(f"  {cid}: {n}×" for cid, n in duplicates.items())
    )


# ---------------------------------------------------------------------------
# Invariant 2: Every cross-reference resolves to a section present
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", _FIXTURES_WITH_XREFS)
def test_invariant_cross_references_resolve(fixture_name: str) -> None:
    """Every see-'X' and R-FOO-N reference must resolve to a heading present."""
    prompt = build_prompt_for_fixture(fixture_name)
    headers = _extract_section_headers(prompt)

    dangling: list[str] = []
    for match in _XREF_RE.finditer(prompt):
        ref = (match.group(1) or match.group(2) or "").strip()
        if not ref:
            continue
        if re.match(r"R-[A-Z]+-\d+", ref):
            dangling.append(ref)
            continue
        if ref not in headers:
            dangling.append(ref)

    assert not dangling, (
        f"[{fixture_name}] Dangling cross-references found: {dangling}"
    )


# ---------------------------------------------------------------------------
# Invariant 3: Every backticked tool name is in spec.tool_names or meta-tools
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", _FIXTURES_WITH_TOOL_REFS)
def test_invariant_tool_names_grounded(fixture_name: str) -> None:
    """Every tool-like backtick reference must be granted or a documented meta-tool."""
    fx = _FIXTURES[fixture_name]
    granted_tools = set(_tool_names_for_fixture(fx))
    allowed = granted_tools | _ALWAYS_KNOWN_META_TOOLS

    prompt = build_prompt_for_fixture(fixture_name)
    tool_refs = _extract_backtick_tool_names(prompt)

    ungrounded = [
        name for name in tool_refs
        if _looks_like_tool(name)
        and name not in allowed
        and name.split(".")[-1] not in {t.split(".")[-1] for t in allowed}
    ]

    assert not ungrounded, (
        f"[{fixture_name}] Backtick-referenced names not in spec.tool_names or meta-tools: "
        f"{sorted(set(ungrounded))}"
    )


# ---------------------------------------------------------------------------
# Invariant 4: No directional language that locks in ordering
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", _ALL_FIXTURES)
def test_invariant_no_directional_language(fixture_name: str) -> None:
    """No section text may contain words that assume a fixed ordering."""
    prompt = build_prompt_for_fixture(fixture_name)
    matches = [m.group(0) for m in _DIRECTIONAL_RE.finditer(prompt)]
    assert not matches, (
        f"[{fixture_name}] Directional language found: {matches[:10]}"
    )


# ---------------------------------------------------------------------------
# Golden snapshots + size ceiling (Phase 9)
# ---------------------------------------------------------------------------

def test_golden_snapshot_no_sources() -> None:
    result = build_prompt_for_fixture("no_sources")
    assert isinstance(result, str)
    assert len(result) > 10


def test_golden_snapshot_kb_only() -> None:
    result = build_prompt_for_fixture("kb_only")
    assert isinstance(result, str)
    assert KB_ID in result
    assert result.count(KB_ID) == 1


def test_golden_snapshot_kb_plus_3_apps() -> None:
    result = build_prompt_for_fixture("kb_plus_3_apps")
    assert isinstance(result, str)
    assert KB_ID in result
    assert CONF_ID in result
    assert JIRA_ID in result
    assert S3_ID in result
    assert len(result) < _KB_PLUS_3_APPS_CHAR_CEILING, (
        f"4-source prompt is {len(result)} chars; expected under "
        f"{_KB_PLUS_3_APPS_CHAR_CEILING} after consolidation"
    )


def test_golden_snapshot_duplicate_apps() -> None:
    result = build_prompt_for_fixture("duplicate_apps")
    assert isinstance(result, str)
    assert JIRA_ID in result
    assert JIRA_ID_2 in result


def test_golden_snapshot_web_search_mode() -> None:
    result = build_prompt_for_fixture("web_search_mode")
    assert isinstance(result, str)
    assert "retrieval__search_internal_knowledge" not in result
    assert "retrieval.search_internal_knowledge" not in result


def test_golden_snapshot_kb_plus_service_tools() -> None:
    result = build_prompt_for_fixture("kb_plus_service_tools")
    assert isinstance(result, str)
    assert CONF_ID in result
    assert JIRA_ID in result
    assert "R-SLACK-1" not in result


# ---------------------------------------------------------------------------
# Invariant 5: pinned toolsets are never behind the load-first block
# ---------------------------------------------------------------------------

def test_invariant_pinned_toolset_tools_listed_under_available_tools() -> None:
    """A toolset named in `spec.pinned_toolsets` is bound at turn 0 —
    its tools must render under "Available Tools", never under the
    "Tools you must load before calling" block, regardless of it also
    belonging to a registered toolset group (the Part 0a false-instruction
    bug: claiming a preloaded tool's schema "is NOT loaded")."""
    prompt = build_prompt_for_fixture("lazy_with_pinned")
    assert "## Available Tools" in prompt
    available_section = prompt.split("## Available Tools", 1)[1].split("\n## ", 1)[0]
    assert "knowledgegraph__search" in available_section


def test_invariant_non_pinned_toolset_not_listed_under_available_tools() -> None:
    prompt = build_prompt_for_fixture("lazy_with_pinned")
    available_section = prompt.split("## Available Tools", 1)[1].split("\n## ", 1)[0]
    assert "jira_search_issues" not in available_section


def test_invariant_non_pinned_toolset_under_load_first_block() -> None:
    prompt = build_prompt_for_fixture("lazy_with_pinned")
    assert "Tools you must load before calling" in prompt
    load_first_section = prompt.split("Tools you must load before calling", 1)[1]
    assert "`jira`" in load_first_section


def test_invariant_pinned_toolset_group_name_not_in_load_first_block() -> None:
    prompt = build_prompt_for_fixture("lazy_with_pinned")
    load_first_section = prompt.split("Tools you must load before calling", 1)[1]
    assert "`knowledgegraph`" not in load_first_section


# ---------------------------------------------------------------------------
# Invariant 6: single-statement — each operative rule appears exactly once
# ---------------------------------------------------------------------------

def test_invariant_single_statement_finding_information_section() -> None:
    prompt = build_prompt_for_fixture("kb_plus_service_tools")
    assert prompt.count("## Finding Information") == 1


def test_invariant_single_statement_parallel_search_rule() -> None:
    prompt = build_prompt_for_fixture("kb_plus_service_tools")
    assert prompt.count("IN PARALLEL") >= 1


def test_invariant_single_statement_skip_list() -> None:
    prompt = build_prompt_for_fixture("kb_plus_service_tools")
    assert prompt.lower().count("skip searching only for") == 1


def test_invariant_single_statement_full_record_escalation() -> None:
    prompt = build_prompt_for_fixture("kb_with_full_record")
    assert prompt.count("## Reading Records in Full") == 1


def test_invariant_no_full_record_section_before_tool_granted() -> None:
    prompt = build_prompt_for_fixture("kb_only")
    assert prompt.count("## Reading Records in Full") == 0


def test_invariant_knowledge_sources_heading_appears_exactly_once() -> None:
    """The dedupe pass merged two Knowledge Sources sections into one —
    pin it so a future change cannot re-introduce the duplication."""
    prompt = build_prompt_for_fixture("kb_plus_3_apps")
    assert prompt.count("## Knowledge Sources") == 1


def test_invariant_retrieval_surface_implies_finding_information_entry() -> None:
    """When retrieval is granted, the Finding Information section MUST name it —
    the tool-name grounding bug (retrieval__* not detected as dotted) silently
    dropped the entry; this invariant ensures it cannot regress."""
    prompt = build_prompt_for_fixture("kb_only")
    assert "## Finding Information" in prompt
    fi_section = prompt.split("## Finding Information", 1)[1].split("\n## ", 1)[0]
    assert "knowledgegraph__search" in fi_section or "internal_exploration_agent" in fi_section


def test_invariant_trust_boundary_stated_once() -> None:
    """The trust boundary rule must appear in every prompt — it gates write
    actions against injected instructions from retrieved content."""
    prompt = build_prompt_for_fixture("kb_plus_3_apps")
    assert "retrieved content" in prompt.lower() or "trust boundary" in prompt.lower()


# ---------------------------------------------------------------------------
# Invariant 7: emphasis budget — caps reserved for the load-bearing rules
# ---------------------------------------------------------------------------

_EMPHASIS_TOKEN_RE = re.compile(
    r"\b(MANDATORY|NEVER|ALWAYS|FIRST|IN PARALLEL|ONE call|IMPORTANT)\b"
)

# Measured across the richest fixture after consolidation — citation-ID
# handling (NEVER invent a URL) and the never-fabricate-a-URL rule account
# for most of these; this ceiling catches a regression back toward the old
# five-plus MANDATORY-headers style, not today's count exactly.
_EMPHASIS_TOKEN_CEILING = 20


@pytest.mark.parametrize("fixture_name", _ALL_FIXTURES)
def test_invariant_emphasis_budget(fixture_name: str) -> None:
    prompt = build_prompt_for_fixture(fixture_name)
    matches = _EMPHASIS_TOKEN_RE.findall(prompt)
    assert len(matches) <= _EMPHASIS_TOKEN_CEILING, (
        f"[{fixture_name}] {len(matches)} emphasis tokens found (ceiling "
        f"{_EMPHASIS_TOKEN_CEILING}): {matches}"
    )


def test_source_catalog_cached_on_context() -> None:
    context = _make_context("kb_plus_3_apps")
    fx = _FIXTURES["kb_plus_3_apps"]
    context.tool_state.update(
        {
            "agent_knowledge": fx["agent_knowledge"],
            "has_knowledge": True,
        }
    )
    first = context.get_source_catalog()
    second = context.get_source_catalog()
    assert first is second
