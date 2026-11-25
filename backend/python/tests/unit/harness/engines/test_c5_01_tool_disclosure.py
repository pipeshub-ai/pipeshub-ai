"""C5.01 (TD-1..TD-5): the tool-disclosure mode is selectable per agent (``toolDisclosure.mode``: eager, lazy,
filesystem), pinned at admission and read by both executors.

The loop lists every tool when the agent says eager and goes lazy whatever the count when it says lazy (filesystem
has no files in the loop, so it reads as lazy); an engine lists every tool, defers the connector and MCP tools, or
lists only the meta tools and reads every tool from ``/pipeshub/tools``. Unset, each keeps its own threshold.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.lazy_tools_wiring import (
    META_TOOL_NAMES,
    make_lazy_tools_decider,
)
from app.control_plane.contracts.generated.resolved_run_config import ResolvedRunConfig
from app.control_plane.runtime.deployment import DeploymentLayer
from app.harness.engines.durable import engine_disclosure
from app.harness.run_policy.pinned import PinnedRunPolicy
from harness_core.contracts import validate_contract
from harness_core.logs import HarnessLog
from harness_core.policy import DimensionPolicyResolver, ScopedLayer
from tests.unit.agent_loop_lib.tools.test_registry import (
    _SimpleTool,  # pyright: ignore[reportPrivateUsage]
)

if TYPE_CHECKING:
    from pydantic import JsonValue

    from app.harness.assembly.context import RunContext

MCP = [f"jira_mcp_tool_{i}" for i in range(6)]
GRANT = ["search_knowledge", "create_artifact", "jira_create_issue", "skill_run", *MCP]


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in GRANT:
        registry.register_tool(_SimpleTool(name))
    registry.register_toolset("retrieval", "Knowledge.", ["search_knowledge"])
    registry.register_toolset("skills", "Skills.", ["skill_run"])
    registry.register_toolset("jira", "Jira.", ["jira_create_issue"])
    registry.register_toolset("mcp", "MCP servers.", [])
    registry.register_toolset("mcp_jira", "Jira MCP.", MCP, parent="mcp")
    return registry


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PIPESHUB_ENABLE_LAZY_TOOLS", "PIPESHUB_ENGINE_LAZY_TOOLS_THRESHOLD", "PIPESHUB_LAZY_TOOLS_THRESHOLD"):
        monkeypatch.delenv(name, raising=False)


def test_td_1_an_engine_lists_defers_or_files_its_tools_as_the_agent_chose() -> None:
    registry = _registry()
    # A small grant: by count every tool would be listed.
    assert engine_disclosure(registry, GRANT, ["retrieval"]) is None
    assert engine_disclosure(registry, GRANT, ["retrieval"], "eager") is None
    lazy = engine_disclosure(registry, GRANT, ["retrieval"], "lazy")
    assert lazy is not None
    assert lazy.deferred == frozenset({"jira_create_issue", *MCP})
    files = engine_disclosure(registry, GRANT, ["retrieval"], "filesystem")
    assert files is not None
    assert files.deferred == frozenset(set(GRANT) - {"skill_run"})


def test_td_1_eager_lists_every_tool_of_a_large_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENGINE_LAZY_TOOLS_THRESHOLD", "1")
    registry = _registry()
    assert engine_disclosure(registry, GRANT, ["retrieval"]) is not None
    assert engine_disclosure(registry, GRANT, ["retrieval"], "eager") is None


def test_td_2_the_operators_switch_still_turns_lazy_disclosure_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
    registry = _registry()
    assert engine_disclosure(registry, GRANT, ["retrieval"], "lazy") is None
    assert engine_disclosure(registry, GRANT, ["retrieval"], "filesystem") is None
    names, mode = make_lazy_tools_decider(apply=False, mode="lazy")(registry, list(GRANT))
    assert mode == "eager"
    assert names == GRANT


@pytest.mark.parametrize("chosen", ["lazy", "filesystem"])
def test_td_3_the_loop_goes_lazy_whatever_the_count_when_the_agent_says_so(chosen: str) -> None:
    grant = ["jira_create_issue", *MCP[:1]]
    by_count = make_lazy_tools_decider(apply=True)(_registry(), list(grant))
    assert by_count == (grant, "eager")
    names, mode = make_lazy_tools_decider(apply=True, mode=chosen)(_registry(), list(grant))
    assert mode == "lazy"
    assert set(META_TOOL_NAMES) <= set(names)


def test_td_3_the_loop_lists_every_tool_of_a_large_grant_when_the_agent_says_eager(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "1")
    assert make_lazy_tools_decider(apply=True)(_registry(), list(GRANT))[1] == "lazy"
    assert make_lazy_tools_decider(apply=True, mode="eager")(_registry(), list(GRANT)) == (GRANT, "eager")


def _pin(layers: list[ScopedLayer]) -> ResolvedRunConfig:
    resolved = DimensionPolicyResolver().resolve(layers)
    assert resolved.config is not None, resolved.refusal
    stored: dict[str, JsonValue] = {
        "schemaVersion": 1,
        "headRevision": 1,
        "routing": {"path": "durable", "reason": None},
        "scope": {"agentKey": "agent-1", "projectId": None},
        "effectiveSet": {"items": [], "digest": "sha256:" + "0" * 64},
        "effectiveConfig": resolved.config,
        "llm": None,
        "deploymentDigest": "sha256:" + "0" * 64,
        "digest": "sha256:" + "0" * 64,
    }
    return validate_contract(ResolvedRunConfig, stored)


def _policy(pin: ResolvedRunConfig) -> PinnedRunPolicy:
    run = cast("RunContext", SimpleNamespace(org_id="o1", agent=None))
    return PinnedRunPolicy(run, pin, deployment=DeploymentLayer(), live=None, log=HarnessLog.named("tests.c5_01"))


def test_td_4_a_pinned_run_reads_the_agents_mode_inside_the_orgs() -> None:
    pin = _pin(
        [
            ScopedLayer("org", {"toolDisclosure": {"modes": ["lazy", "filesystem"]}}),
            ScopedLayer("agent", {"toolDisclosure": {"mode": "filesystem"}}),
        ]
    )
    assert _policy(pin).tool_disclosure() == "filesystem"
    assert _policy(_pin([])).tool_disclosure() is None


def test_td_5_an_org_lock_on_the_mode_holds_against_the_agent() -> None:
    refused = DimensionPolicyResolver().resolve(
        [
            ScopedLayer("org", {"toolDisclosure": {"mode": "lazy"}, "locks": ["toolDisclosure.mode"]}),
            ScopedLayer("agent", {"toolDisclosure": {"mode": "eager"}}),
        ]
    ).refusal
    assert refused is not None
    assert refused.wire() == {"code": "locked", "dimension": "toolDisclosure.mode", "layer": "agent"}
