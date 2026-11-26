"""Engine-neutral tool policy: which native tools a profile allows, grant limits, and the profile's
approval layer."""

from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from harness_core.effects.policy import (
    ENGINE_APPROVALS,
    ApprovalRules,
    LayeredApprovalPolicy,
)
from harness_core.engines.errors import (
    EgressUnavailable,
    GrantTooLarge,
    ModelApiUnavailable,
    SkillNotAllowed,
    ToolNameTooLong,
)
from harness_core.engines.types import (
    EGRESS_NONE,
    EngineCapabilities,
    EngineMode,
    EngineProfile,
    ProfileApprovals,
    ProfileConstraint,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Sequence

    from harness_core.access.types import ModelApi
    from harness_core.effects.types import EffectKind


class NativeTool(StrEnum):
    """Tools an engine brings itself, by what they do. Gateway (MCP) tools are separate."""

    READ = "read"
    SEARCH = "search"
    EDIT = "edit"
    SHELL = "shell"
    SKILL = "skill"
    SUBAGENT = "subagent"
    WEB = "web"
    ASK_USER = "ask_user"
    TODO = "todo"


_READ_ONLY = frozenset({NativeTool.READ, NativeTool.SEARCH})
_MODE_TOOLS: dict[EngineMode, frozenset[NativeTool]] = {
    EngineMode.PLAN: _READ_ONLY,
    EngineMode.REVIEW: _READ_ONLY,
    EngineMode.BUILD: _READ_ONLY | {NativeTool.EDIT, NativeTool.SHELL},
}


def expected_native_tools(profile: EngineProfile, *, skills: bool) -> frozenset[NativeTool]:
    """The native tools an engine may expose for ``profile``.

    Web access and asking the user never come from the engine: the network is the
    egress proxy's, and questions go through the interaction bridge.
    """
    tools = set(_MODE_TOOLS[profile.mode])
    if skills:
        tools.add(NativeTool.SKILL)
    if profile.sub_agents.enabled:
        tools.add(NativeTool.SUBAGENT)
    return frozenset(tools)


def check_grant_size(capabilities: EngineCapabilities, tools: int) -> None:
    if capabilities.max_tools is not None and tools > capabilities.max_tools:
        raise GrantTooLarge(tools, capabilities.max_tools)


def check_grant_names(capabilities: EngineCapabilities, names: Iterable[str]) -> None:
    """A grant whose longest tool name the engine cannot carry is refused up front (E8 FB-14)."""
    if capabilities.max_tool_name is None:
        return
    longest = max((len(name) for name in names), default=0)
    if longest > capabilities.max_tool_name:
        raise ToolNameTooLong(longest, capabilities.max_tool_name)


def model_api_for(capabilities: EngineCapabilities, served: Sequence[ModelApi]) -> ModelApi:
    """The first API the model gateway serves (in the deployment's order) that the engine speaks."""
    chosen = next((api for api in served if api in capabilities.model_apis), None)
    if chosen is None:
        raise ModelApiUnavailable(f"the engine speaks {sorted(capabilities.model_apis)}; the gateway serves {list(served)}")
    return chosen


def check_profile_admits(profile: EngineProfile, *, tools: int, skills: Iterable[str]) -> None:
    """What the controller can see of a run against its pinned profile, before anything is acquired."""
    if profile.egress != EGRESS_NONE:
        raise EgressUnavailable(f"egress {profile.egress} is beyond the gateways this sandbox reaches")
    if profile.tools.max_tools is not None and tools > profile.tools.max_tools:
        raise GrantTooLarge(tools, profile.tools.max_tools)
    if profile.skills is not None:
        outside = sorted(set(skills) - profile.skills)
        if outside:
            raise SkillNotAllowed(f"skills {outside} are outside the profile")


def tool_effect_allowed(profile: EngineProfile, effect: EffectKind) -> bool:
    """Whether a gateway tool of this effect class may be in the run's grant (the grant builder filters)."""
    return effect in profile.tools.effects


def approval_policy_for(profile: EngineProfile) -> LayeredApprovalPolicy:
    """The profile's approvals as the first layer; P2.6c's org and agent rules may only narrow it."""
    return LayeredApprovalPolicy((ApprovalRules(classes=profile.approvals.as_mapping()),), ("engine",))


def profile_rules(profile: EngineProfile, release: ProfileApprovals | None = None) -> ApprovalRules:
    """What the run's profile narrows below the release default, as an org rule (QA10-F3): an org's ceiling, an
    agent's or a task's narrowing is governance, so it asks whatever the call's sink and no conversation grant
    answers it. The release default itself is ``ENGINE_APPROVALS`` (the engineering default), which is not here."""
    baseline = release if release is not None else ProfileApprovals()
    return ApprovalRules(
        classes={kind: decision for kind, decision in profile.approvals.as_mapping().items() if decision != baseline.decision(kind)}
    )


def configured_rules(constraint: ProfileConstraint | None) -> ApprovalRules:
    """The approval classes a narrowing names, as an org rule even where it restates the release value."""
    return ApprovalRules(classes=dict(constraint.approvals)) if constraint is not None and constraint.approvals else ApprovalRules()


def engine_default_approvals() -> LayeredApprovalPolicy:
    """An engine run's engineering default (UX2, UX4): ``ENGINE_APPROVALS`` alone. It asks only before a write that
    leaves the user's own space and may be answered for the conversation; the profile's narrowing is the org's
    (``profile_rules``) and joins the org's rules instead."""
    return ENGINE_APPROVALS


__all__ = [
    "NativeTool",
    "approval_policy_for",
    "check_grant_names",
    "check_grant_size",
    "check_profile_admits",
    "configured_rules",
    "engine_default_approvals",
    "expected_native_tools",
    "model_api_for",
    "profile_rules",
    "tool_effect_allowed",
]
