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
# Measured after Phase 9 added the worked_traces section for SMALL/MID tiers.
# Baseline sizes / ceilings with ~10% headroom:
#   no_sources:            6,148 →  6,800
#   kb_only:               9,253 → 10,200
#   kb_plus_3_apps:        9,462 → 10,450  (also checked in test_prompt_invariants.py)
#   duplicate_apps:        9,499 → 10,450
#   web_search_mode:       7,665 →  8,450
#   kb_plus_service_tools: 9,908 → 10,900
#   run_code_no_web:       7,167 →  7,900
#   composed_agents:      10,664 → 11,750
#   service_only:          6,933 →  7,650
#   lazy_with_pinned:     11,068 → 12,200
#   kb_with_full_record:  10,453 → 11,500
# ---------------------------------------------------------------------------
_MID_CHAR_CEILINGS: dict[str, int] = {
    "no_sources":            7_400,
    "kb_only":              10_850,
    "kb_plus_3_apps":       11_050,
    "duplicate_apps":       11_100,
    "web_search_mode":       9_100,
    "kb_plus_service_tools": 11_550,
    "run_code_no_web":       8_550,
    "composed_agents":      12_400,
    "service_only":          8_300,
    "lazy_with_pinned":     12_850,
    "kb_with_full_record":  12_150,
}

# ---------------------------------------------------------------------------
# FRONTIER-tier ceilings (anthropic/200k → FRONTIER, no worked traces)
#
# Re-measured after disabling final_answer by default — Response Format and
# Citation Rules sections are now back in the system prompt (they were
# previously omitted when final_answer was enabled and carried them in its
# tool parameter description instead).
#
# Baseline sizes / ceilings with ~10% headroom:
#   no_sources:            4,568 →  5,050
#   kb_only:               7,191 →  7,950
#   kb_plus_3_apps:        7,400 →  8,150
#   duplicate_apps:        7,437 →  8_200
#   web_search_mode:       5,603 →  6,200
#   kb_plus_service_tools: 7,846 →  8,650
#   run_code_no_web:       5,768 →  6,350
#   composed_agents:       8,602 →  9,500
#   service_only:          4,871 →  5,400
#   lazy_with_pinned:      9,006 →  9,950
#   kb_with_full_record:   8,391 →  9,250
# ---------------------------------------------------------------------------
_FRONTIER_CHAR_CEILINGS: dict[str, int] = {
    "no_sources":            5_050,
    "kb_only":               7_950,
    "kb_plus_3_apps":        8_150,
    "duplicate_apps":        8_200,
    "web_search_mode":       6_200,
    "kb_plus_service_tools": 8_650,
    "run_code_no_web":       6_350,
    "composed_agents":       9_500,
    "service_only":          5_400,
    "lazy_with_pinned":      9_950,
    "kb_with_full_record":   9_250,
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
