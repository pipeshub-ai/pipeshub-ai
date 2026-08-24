"""Prompt size regression guard.

`test_prompt_invariants.py` already pins one fixture's char ceiling; this
file tracks ALL fixture configurations across both tiers so a future change
that re-inflates any of them fails loudly here rather than being noticed only
in production token costs.

Two sets of ceilings are tracked:

``_MID_CHAR_CEILINGS``
    Default (UNKNOWN_PROFILE → MID tier) — includes worked traces.  These
    grew by ~1 900 chars compared to the pre-Phase-9 values when Phase 9 added
    the ``worked_traces`` section for SMALL and MID tiers.

``_FRONTIER_CHAR_CEILINGS``
    Anthropic/200 k context → FRONTIER tier — no worked traces.  These are
    set from the post-Phase-9 measured sizes (which are *smaller* than the
    pre-Phase-9 MID sizes because earlier phases trimmed duplicate/dead prose).
    The FRONTIER assertion guards that Phase 9 did not inflate the terse prompt.
"""

from __future__ import annotations

import pytest

from tests.unit.agents.adapter.test_prompt_invariants import (
    _FIXTURES,
    _build_registry_for_fixture,
    _tool_names_for_fixture,
    build_prompt_for_fixture,
)
from app.agent_loop_lib.agent.spec import AgentSpec, ModelSpec
from app.agent_loop_lib.core.types import Goal
from app.agent_loop_lib.runtime.runtime import AgentRuntime
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.prompt_builder import PipesHubPromptBuilder
from unittest.mock import MagicMock


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_frontier_prompt(fixture_name: str) -> str:
    """Build a prompt using an anthropic/200k context (FRONTIER tier → no traces)."""
    fx = _FIXTURES[fixture_name]
    ctx = AgentContext(
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
        # Force FRONTIER: anthropic + 200k context window.
        llm_provider="anthropic",
        context_length=200_000,
    )
    ctx.tool_state.update({
        "agent_knowledge": fx.get("agent_knowledge") or [],
        "has_knowledge": fx.get("has_knowledge", False),
        "available_connectors": fx.get("available_connectors", []),
        "web_search_config": fx.get("web_search_config"),
        "agent_toolsets": fx.get("agent_toolsets") or [],
    })
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
    return PipesHubPromptBuilder(ctx).build(spec, runtime, Goal(description="test query"), [], {})


# ---------------------------------------------------------------------------
# MID-tier ceilings (default UNKNOWN_PROFILE → MID, includes worked traces)
#
# Re-measured after the entity tools were cut to search_entities +
# find_records_by_entity and the "Entities" paragraph in `source_catalog.py`
# shrank to ~500 chars (rendered only when search_entities is granted). Only
# fixtures with `has_knowledge=True` changed. Baseline sizes / ceilings with
# ~10% headroom:
#   no_sources:             7,520 →  7,900  (unaffected: no knowledge sources)
#   kb_only:               12,607 → 13,900
#   kb_plus_3_apps:        12,816 → 14,100  (also checked in test_prompt_invariants.py)
#   duplicate_apps:        12,853 → 14,150
#   web_search_mode:       10,189 → 10,850  (unchanged ceiling)
#   kb_plus_service_tools: 13,262 → 14,600
#   run_code_no_web:        8,720 →  9,200  (unaffected: no knowledge sources)
#   composed_agents:       13,541 → 14,900
#   service_only:           8,280 →  8,750  (unaffected: no knowledge sources)
#   lazy_with_pinned:      14,504 → 16,000
#   kb_with_full_record:   13,727 → 15,100
# ---------------------------------------------------------------------------
_MID_CHAR_CEILINGS: dict[str, int] = {
    "no_sources":            7_900,
    "kb_only":              13_900,
    "kb_plus_3_apps":       14_100,
    "duplicate_apps":       14_150,
    "web_search_mode":      10_850,
    "kb_plus_service_tools": 14_600,
    "run_code_no_web":       9_200,
    "composed_agents":      14_900,
    "service_only":          8_750,
    "lazy_with_pinned":     14_250,
    "kb_with_full_record":  13_600,
    # Code knowledge graph fixtures: these carry the gated "Navigating Code"
    # section, which only renders when a codegraph tool is callable, so they
    # sit above the rest. Measured 16,428 / 13,807 → +~10% headroom.
    "lazy_with_bound_codegraph":      18_100,
    "lazy_with_pinned_and_codegraph": 15_200,
}

# ---------------------------------------------------------------------------
# FRONTIER-tier ceilings (anthropic/200k → FRONTIER, no worked traces)
#
# Re-measured for the same reason as `_MID_CHAR_CEILINGS` above (the
# "Entities" paragraph is tier-independent). `no_sources`, `run_code_no_web`,
# `service_only` and `web_search_mode` keep their ceilings.
#
# Baseline sizes / ceilings with ~10% headroom:
#   no_sources:            4,926 →  5,050
#   kb_only:              10,013 → 11,050
#   kb_plus_3_apps:       10,222 → 11,250
#   duplicate_apps:       10,259 → 11,300
#   web_search_mode:       7,595 →  8,000
#   kb_plus_service_tools: 10,668 → 11,750
#   run_code_no_web:       6,126 →  6,350
#   composed_agents:      10,947 → 12,050
#   service_only:          5,686 →  5,900
#   lazy_with_pinned:     11,910 → 13,100
#   kb_with_full_record:  11,133 → 12,250
# ---------------------------------------------------------------------------
_FRONTIER_CHAR_CEILINGS: dict[str, int] = {
    "no_sources":            5_050,
    "kb_only":              11_050,
    "kb_plus_3_apps":       11_250,
    "duplicate_apps":       11_300,
    "web_search_mode":       8_000,
    "kb_plus_service_tools": 11_750,
    "run_code_no_web":       6_350,
    "composed_agents":      12_050,
    "service_only":          5_900,
    "lazy_with_pinned":     11_400,
    "kb_with_full_record":  10_750,
    # Both measured 11,213 (no worked traces, same gated section) → +~10%.
    "lazy_with_bound_codegraph":      12_350,
    "lazy_with_pinned_and_codegraph": 12_350,
}


# ---------------------------------------------------------------------------
# Structural guards
# ---------------------------------------------------------------------------

def test_mid_ceilings_cover_every_fixture() -> None:
    """Guard against a new fixture being added without a matching MID ceiling."""
    assert set(_MID_CHAR_CEILINGS) == set(_FIXTURES)


def test_frontier_ceilings_cover_every_fixture() -> None:
    """Guard against a new fixture being added without a matching FRONTIER ceiling."""
    assert set(_FRONTIER_CHAR_CEILINGS) == set(_FIXTURES)


# ---------------------------------------------------------------------------
# MID-tier size tests (default tier; includes worked traces)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", sorted(_FIXTURES))
def test_mid_prompt_stays_under_char_ceiling(fixture_name: str) -> None:
    prompt = build_prompt_for_fixture(fixture_name)
    ceiling = _MID_CHAR_CEILINGS[fixture_name]
    assert len(prompt) < ceiling, (
        f"[{fixture_name}/MID] prompt is {len(prompt)} chars; expected under {ceiling}. "
        "If this growth is intentional, trim elsewhere first or revisit the "
        "ceiling deliberately — don't just raise it to the new number."
    )


# ---------------------------------------------------------------------------
# FRONTIER-tier size tests (no traces; guards Phase 9 did not inflate terse path)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fixture_name", sorted(_FIXTURES))
def test_frontier_prompt_stays_under_char_ceiling(fixture_name: str) -> None:
    prompt = _build_frontier_prompt(fixture_name)
    ceiling = _FRONTIER_CHAR_CEILINGS[fixture_name]
    assert len(prompt) < ceiling, (
        f"[{fixture_name}/FRONTIER] prompt is {len(prompt)} chars; expected under {ceiling}. "
        "Phase 9 should not have grown the FRONTIER (no-traces) prompt path — "
        "check that worked_traces is gated on model_profile.inject_traces()."
    )


def test_frontier_prompt_has_no_worked_examples_header() -> None:
    """Phase 9 traces must not appear in any FRONTIER-tier prompt."""
    for fixture_name in _FIXTURES:
        prompt = _build_frontier_prompt(fixture_name)
        assert "## Worked Examples" not in prompt, (
            f"[{fixture_name}] FRONTIER prompt contains the worked traces section — "
            "inject_traces() must return False for the FRONTIER tier."
        )


def test_mid_prompt_has_worked_examples_header() -> None:
    """Phase 9 traces must appear in MID-tier prompts."""
    for fixture_name in _FIXTURES:
        prompt = build_prompt_for_fixture(fixture_name)
        assert "## Worked Examples" in prompt, (
            f"[{fixture_name}] MID prompt is missing the worked traces section — "
            "check prompt_builder.py's tier-gated injection."
        )
