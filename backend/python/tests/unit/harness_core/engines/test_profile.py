"""EC-1..EC-4, EC-8, EC-9 (E0), EP-1/EP-2 (E4a): profile narrowing, endpoints, native tool policy, grant size."""

from __future__ import annotations

import dataclasses

import pytest

from harness_core.access.types import AccessRouteRef, ModelApi
from harness_core.engines.errors import GrantTooLarge, ProfileWidening
from harness_core.engines.policy import (
    NativeTool,
    check_grant_size,
    expected_native_tools,
)
from harness_core.engines.types import (
    EngineCapabilities,
    EngineMode,
    EngineProfile,
    EngineVersion,
    GatewayEndpoints,
    ProfileConstraint,
    RunLimits,
    SubAgentPolicy,
)
from harness_core.sandbox import RuntimeMode

VERSION = EngineVersion("opencode", "1.18.34", "sha256:" + "a" * 64)
ACCESS = AccessRouteRef("route-1", "d1")
BASE = EngineProfile(
    engine=VERSION,
    mode=EngineMode.BUILD,
    access=ACCESS,
    reasoning_effort="high",
    sub_agents=SubAgentPolicy(enabled=True, max_depth=2, max_concurrent=2),
    limits=RunLimits(max_steps=50, max_wall_s=600, max_tokens=None, max_usd=5.0),
    isolation_min=RuntimeMode.DOCKER_HARDENED,
)


@pytest.mark.parametrize(
    ("constraint", "expected"),
    [
        (ProfileConstraint(mode=EngineMode.PLAN), {"mode": EngineMode.PLAN}),
        (ProfileConstraint(mode=EngineMode.REVIEW), {"mode": EngineMode.REVIEW}),
        (ProfileConstraint(reasoning_effort="low"), {"reasoning_effort": "low"}),
        (ProfileConstraint(sub_agents=SubAgentPolicy()), {"sub_agents": SubAgentPolicy()}),
        (
            ProfileConstraint(sub_agents=SubAgentPolicy(enabled=True, max_depth=1, max_concurrent=2)),
            {"sub_agents": SubAgentPolicy(enabled=True, max_depth=1, max_concurrent=2)},
        ),
        (
            ProfileConstraint(sub_agents=SubAgentPolicy(enabled=True, max_depth=2, max_concurrent=1)),
            {"sub_agents": SubAgentPolicy(enabled=True, max_depth=2, max_concurrent=1)},
        ),
        (
            ProfileConstraint(limits=RunLimits(max_steps=10, max_tokens=1000, max_usd=1.0)),
            {"limits": RunLimits(max_steps=10, max_wall_s=600, max_tokens=1000, max_usd=1.0)},
        ),
        (ProfileConstraint(isolation_min=RuntimeMode.GVISOR), {"isolation_min": RuntimeMode.GVISOR}),
        (ProfileConstraint(isolation_min=RuntimeMode.MICROVM), {"isolation_min": RuntimeMode.MICROVM}),
        (ProfileConstraint(result_schema={"type": "object"}), {"result_schema": {"type": "object"}}),
        (ProfileConstraint(engines=frozenset({"opencode@1.18.34"}), access=ACCESS), {}),
        (ProfileConstraint(engines=frozenset({"opencode", "codex"})), {}),
    ],
)
def test_ec1_every_narrowing_is_applied(constraint: ProfileConstraint, expected: dict[str, object]) -> None:
    assert BASE.narrow(constraint) == dataclasses.replace(BASE, **expected)


PLAN = dataclasses.replace(BASE, mode=EngineMode.PLAN, reasoning_effort="low", sub_agents=SubAgentPolicy())


@pytest.mark.parametrize(
    ("profile", "constraint", "field"),
    [
        (PLAN, ProfileConstraint(mode=EngineMode.BUILD), "mode"),
        (PLAN, ProfileConstraint(mode=EngineMode.REVIEW), "mode"),
        (PLAN, ProfileConstraint(reasoning_effort="high"), "reasoningEffort"),
        (PLAN, ProfileConstraint(sub_agents=SubAgentPolicy(enabled=True)), "subAgents.enabled"),
        (BASE, ProfileConstraint(sub_agents=SubAgentPolicy(enabled=True, max_depth=3)), "subAgents.maxDepth"),
        (
            BASE,
            ProfileConstraint(sub_agents=SubAgentPolicy(enabled=True, max_concurrent=3)),
            "subAgents.maxConcurrent",
        ),
        (BASE, ProfileConstraint(limits=RunLimits(max_steps=51)), "limits.maxSteps"),
        (BASE, ProfileConstraint(limits=RunLimits(max_wall_s=601)), "limits.maxWallS"),
        (BASE, ProfileConstraint(limits=RunLimits(max_usd=5.01)), "limits.maxUsd"),
        (
            dataclasses.replace(BASE, isolation_min=RuntimeMode.MICROVM),
            ProfileConstraint(isolation_min=RuntimeMode.GVISOR),
            "isolationMin",
        ),
        (
            dataclasses.replace(BASE, result_schema={"type": "object"}),
            ProfileConstraint(result_schema={"type": "array"}),
            "resultSchema",
        ),
        (BASE, ProfileConstraint(engines=frozenset({"opencode@1.18.35"})), "engine"),
        (BASE, ProfileConstraint(engines=frozenset({"codex"})), "engine"),
        (BASE, ProfileConstraint(access=AccessRouteRef("route-2", "d2")), "access"),
    ],
)
def test_ec2_ec3_every_widening_is_refused(
    profile: EngineProfile, constraint: ProfileConstraint, field: str
) -> None:
    with pytest.raises(ProfileWidening) as refused:
        profile.narrow(constraint)
    assert refused.value.field == field
    assert refused.value.reason_code == "profile_widening"


def test_unset_effort_accepts_any_requested_effort() -> None:
    profile = dataclasses.replace(BASE, reasoning_effort=None)
    assert profile.narrow(ProfileConstraint(reasoning_effort="high")).reasoning_effort == "high"


def test_ec4_tool_timeout_below_the_approval_hold_margin_is_refused() -> None:
    with pytest.raises(ValueError, match="below 150s"):
        GatewayEndpoints("http://m/v1", ModelApi.CHAT_COMPLETIONS, "http://t/mcp", "ph", tool_timeout_s=149)
    assert GatewayEndpoints("http://m/v1", ModelApi.CHAT_COMPLETIONS, "http://t/mcp", "ph").tool_timeout_s == 150


@pytest.mark.parametrize(
    ("bad", "message"),
    [({"binary_digest": "abc"}, "digest"), ({"version": ""}, "empty"), ({"kind": "vim"}, "kind")],
)
def test_engine_version_validates(bad: dict[str, str], message: str) -> None:
    fields: dict[str, str] = {"kind": "opencode", "version": "1", "binary_digest": "sha256:" + "a" * 64}
    fields.update(bad)
    with pytest.raises(ValueError, match=message):
        EngineVersion(**fields)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("mode", "sub_agents", "skills", "expected"),
    [
        (EngineMode.PLAN, False, True, {NativeTool.READ, NativeTool.SEARCH, NativeTool.SKILL}),
        (EngineMode.REVIEW, False, False, {NativeTool.READ, NativeTool.SEARCH}),
        (
            EngineMode.BUILD,
            True,
            True,
            {
                NativeTool.READ,
                NativeTool.SEARCH,
                NativeTool.SKILL,
                NativeTool.EDIT,
                NativeTool.SHELL,
                NativeTool.SUBAGENT,
            },
        ),
    ],
)
def test_ec8_native_tools_by_mode(
    mode: EngineMode, sub_agents: bool, skills: bool, expected: set[NativeTool]
) -> None:
    profile = dataclasses.replace(BASE, mode=mode, sub_agents=SubAgentPolicy(enabled=sub_agents))
    tools = expected_native_tools(profile, skills=skills)
    assert tools == expected
    assert not tools & {NativeTool.WEB, NativeTool.ASK_USER, NativeTool.TODO}


def test_ec9_grant_size() -> None:
    limited = EngineCapabilities(False, False, False, frozenset(), 100, "stdio")
    check_grant_size(limited, 100)
    with pytest.raises(GrantTooLarge) as refused:
        check_grant_size(limited, 101)
    assert (refused.value.tools, refused.value.max_tools) == (101, 100)
    check_grant_size(dataclasses.replace(limited, max_tools=None), 10_000)
