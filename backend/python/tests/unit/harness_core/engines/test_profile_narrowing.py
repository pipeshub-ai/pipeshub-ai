"""EP-1..EP-6, EP-16 (E4a): the dimension registry, per-dimension narrowing and widening, and properties."""

from __future__ import annotations

import dataclasses
from typing import cast, get_args

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from harness_core.access.types import AccessRouteRef, AccountKind
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects import ToolEffect, tool_id_for
from harness_core.effects.types import ApprovalDecision, EffectKind
from harness_core.engines.errors import ProfileWidening
from harness_core.engines.policy import approval_policy_for, tool_effect_allowed
from harness_core.engines.profiles import narrow_profile
from harness_core.engines.types import (
    EngineMode,
    EngineProfile,
    EngineVersion,
    ProfileApprovals,
    ProfileConstraint,
    RunLimits,
    SubAgentPolicy,
    ToolCeiling,
)
from harness_core.kernel.core.messages import ToolCall
from harness_core.policy import ENGINE_PROFILE, policy_registry
from harness_core.sandbox import RuntimeMode

VERSION = EngineVersion("opencode", "1.18.34", "sha256:" + "a" * 64)
ACCESS = AccessRouteRef("route-1", "d1")
EFFECTS: tuple[EffectKind, ...] = get_args(EffectKind)
LEVELS: tuple[ApprovalDecision, ...] = ("allow", "ask", "deny")
RANK = {"allow": 0, "ask": 1, "deny": 2, "low": 0, "medium": 1, "high": 2}
ISOLATION = {
    RuntimeMode.DOCKER_HARDENED: 0,
    RuntimeMode.GVISOR: 1,
    RuntimeMode.K8S: 1,
    RuntimeMode.MANAGED: 1,
    RuntimeMode.MICROVM: 2,
}
MODES_WITHIN = {
    EngineMode.BUILD: set(EngineMode),
    EngineMode.PLAN: {EngineMode.PLAN},
    EngineMode.REVIEW: {EngineMode.REVIEW},
}
SKILLS = ("docx", "pdf", "pptx", "xlsx")

TOP = EngineProfile(
    engine=VERSION,
    mode=EngineMode.BUILD,
    access=ACCESS,
    reasoning_effort="high",
    sub_agents=SubAgentPolicy(enabled=True, max_depth=2, max_concurrent=3),
    limits=RunLimits(max_steps=50, max_wall_s=1800, max_tokens=1_000_000, max_usd=5.0),
    isolation_min=RuntimeMode.GVISOR,
    egress="allowlist:pypi",
    tools=ToolCeiling(max_tools=40, effects=frozenset(EFFECTS)),
    approvals=ProfileApprovals(read="allow", workspace="allow", write="ask", execute="ask"),
    skills=frozenset({"pdf", "xlsx"}),
)


_WIRE_FIELDS = {
    "engine": "engine", "access": "access", "mode": "mode", "isolationMin": "isolation_min", "egress": "egress",
    "tools": "tools", "limits": "limits", "subAgents": "sub_agents", "reasoningEffort": "reasoning_effort",
    "approvals": "approvals", "skills": "skills", "accountKinds": "account_kinds", "resultSchema": "result_schema",
}  # fmt: skip
PROFILE_ROWS = policy_registry(ENGINE_PROFILE).all()


def test_ep3_the_registry_covers_every_field_of_the_profile() -> None:
    governed = {_WIRE_FIELDS[d.path.split(".")[0]] for d in PROFILE_ROWS}
    assert governed == {f.name for f in dataclasses.fields(EngineProfile)}
    names = [d.path for d in PROFILE_ROWS]
    assert len(names) == len(set(names))
    constraint_fields = {f.name for f in dataclasses.fields(ProfileConstraint)}
    # every constraint field is read by some dimension: setting it to a widening value must refuse
    assert constraint_fields == {
        "engines", "mode", "access", "reasoning_effort", "sub_agents", "limits", "isolation_min",
        "egress", "max_tools", "tool_effects", "approvals", "skills", "account_kinds", "result_schema",
    }  # fmt: skip


@pytest.mark.parametrize(
    ("constraint", "expected"),
    [
        (ProfileConstraint(engines=frozenset({"opencode"})), {}),
        (ProfileConstraint(mode=EngineMode.REVIEW), {"mode": EngineMode.REVIEW}),
        (ProfileConstraint(isolation_min=RuntimeMode.MICROVM), {"isolation_min": RuntimeMode.MICROVM}),
        (ProfileConstraint(isolation_min=RuntimeMode.K8S), {"isolation_min": RuntimeMode.K8S}),
        (ProfileConstraint(egress="none"), {"egress": "none"}),
        (ProfileConstraint(egress="allowlist:pypi"), {}),
        (ProfileConstraint(max_tools=10), {"tools": dataclasses.replace(TOP.tools, max_tools=10)}),
        (
            ProfileConstraint(tool_effects=frozenset({"read"})),
            {"tools": dataclasses.replace(TOP.tools, effects=frozenset({"read"}))},
        ),
        (
            ProfileConstraint(limits=RunLimits(max_wall_s=60, max_usd=0.5)),
            {"limits": dataclasses.replace(TOP.limits, max_wall_s=60, max_usd=0.5)},
        ),
        (ProfileConstraint(reasoning_effort="medium"), {"reasoning_effort": "medium"}),
        (
            ProfileConstraint(approvals={"write": "deny", "read": "ask"}),
            {"approvals": dataclasses.replace(TOP.approvals, write="deny", read="ask")},
        ),
        (ProfileConstraint(skills=frozenset({"pdf"})), {"skills": frozenset({"pdf"})}),
        (ProfileConstraint(skills=frozenset()), {"skills": frozenset()}),
        (
            ProfileConstraint(account_kinds=frozenset({AccountKind.ORG_API_KEY})),
            {"account_kinds": frozenset({AccountKind.ORG_API_KEY})},
        ),
    ],
)
def test_ep1_every_dimension_narrows(constraint: ProfileConstraint, expected: dict[str, object]) -> None:
    assert narrow_profile(TOP, constraint) == dataclasses.replace(TOP, **expected)
    assert TOP.narrow(constraint) == narrow_profile(TOP, constraint)


@pytest.mark.parametrize(
    ("profile", "constraint", "dimension"),
    [
        (TOP, ProfileConstraint(engines=frozenset({"codex"})), "engine"),
        (dataclasses.replace(TOP, mode=EngineMode.REVIEW), ProfileConstraint(mode=EngineMode.PLAN), "mode"),
        (TOP, ProfileConstraint(isolation_min=RuntimeMode.DOCKER_HARDENED), "isolationMin"),
        (dataclasses.replace(TOP, egress="none"), ProfileConstraint(egress="allowlist:pypi"), "egress"),
        (TOP, ProfileConstraint(egress="allowlist:npm"), "egress"),
        (TOP, ProfileConstraint(max_tools=41), "tools.maxTools"),
        (
            dataclasses.replace(TOP, tools=ToolCeiling(effects=frozenset({"read"}))),
            ProfileConstraint(tool_effects=frozenset({"read", "write"})),
            "tools.effects",
        ),
        (TOP, ProfileConstraint(limits=RunLimits(max_tokens=1_000_001)), "limits.maxTokens"),
        (TOP, ProfileConstraint(limits=RunLimits(max_usd=5.01)), "limits.maxUsd"),
        (
            dataclasses.replace(TOP, sub_agents=SubAgentPolicy()),
            ProfileConstraint(sub_agents=SubAgentPolicy(enabled=True)),
            "subAgents.enabled",
        ),
        (TOP, ProfileConstraint(sub_agents=SubAgentPolicy(True, 3, 1)), "subAgents.maxDepth"),
        (dataclasses.replace(TOP, reasoning_effort="low"), ProfileConstraint(reasoning_effort="medium"), "reasoningEffort"),
        (TOP, ProfileConstraint(approvals={"write": "allow"}), "approvals.write"),
        (TOP, ProfileConstraint(approvals={"execute": "allow"}), "approvals.execute"),
        (
            dataclasses.replace(TOP, approvals=ProfileApprovals(read="ask")),
            ProfileConstraint(approvals={"read": "allow"}),
            "approvals.read",
        ),
        (
            dataclasses.replace(TOP, approvals=ProfileApprovals(workspace="deny")),
            ProfileConstraint(approvals={"workspace": "ask"}),
            "approvals.workspace",
        ),
        (TOP, ProfileConstraint(skills=frozenset({"docx"})), "skills"),
        (TOP, ProfileConstraint(account_kinds=frozenset({AccountKind.USER_PLAN_INJECTED})), "accountKinds"),
        (TOP, ProfileConstraint(access=AccessRouteRef("route-2", "d2")), "access"),
    ],
)
def test_ep2_every_widening_is_refused_naming_its_dimension(
    profile: EngineProfile, constraint: ProfileConstraint, dimension: str
) -> None:
    with pytest.raises(ProfileWidening) as refused:
        narrow_profile(profile, constraint)
    assert refused.value.field == dimension
    assert refused.value.reason_code == "profile_widening"


def test_ep2_unlimited_is_never_reached_by_narrowing() -> None:
    unlimited = dataclasses.replace(TOP, limits=RunLimits(), tools=ToolCeiling(), skills=None)
    assert narrow_profile(unlimited, ProfileConstraint(limits=RunLimits(max_steps=1))).limits.max_steps == 1
    assert narrow_profile(TOP, ProfileConstraint(limits=RunLimits())).limits == TOP.limits


def test_ep16_the_profile_is_the_first_approval_layer() -> None:
    def decide(profile: EngineProfile, kind: EffectKind) -> str:
        effect = ToolEffect(
            tool_id=tool_id_for("t"),
            effect=kind,
            idempotent=kind in ("read", "workspace"),
            sink=SinkDescriptor(egress="external", args_leave_boundary=True),
        )
        return approval_policy_for(profile)(ToolCall(id="c1", name="t", arguments={}), effect)

    assert [decide(TOP, k) for k in EFFECTS] == ["allow", "allow", "ask", "ask"]
    denied = narrow_profile(TOP, ProfileConstraint(approvals={"write": "deny"}))
    assert decide(denied, "write") == "deny"
    assert tool_effect_allowed(narrow_profile(TOP, ProfileConstraint(tool_effects=frozenset({"read"}))), "read")
    assert not tool_effect_allowed(narrow_profile(TOP, ProfileConstraint(tool_effects=frozenset({"read"}))), "write")


# --- properties -------------------------------------------------------------------------------

_opt_int = st.one_of(st.none(), st.integers(1, 100))
_limits = st.builds(
    RunLimits,
    max_steps=_opt_int,
    max_wall_s=_opt_int,
    max_tokens=_opt_int,
    max_usd=st.one_of(st.none(), st.integers(0, 20).map(lambda c: c / 4)),
)
_sub_agents = st.builds(SubAgentPolicy, enabled=st.booleans(), max_depth=st.integers(1, 3), max_concurrent=st.integers(1, 3))
_effects = st.frozensets(st.sampled_from(EFFECTS))
_skills = st.one_of(st.none(), st.frozensets(st.sampled_from(SKILLS)))
_effort = st.one_of(st.none(), st.sampled_from(("low", "medium", "high")))
_approvals = st.builds(
    ProfileApprovals,
    read=st.sampled_from(LEVELS),
    workspace=st.sampled_from(LEVELS),
    write=st.sampled_from(LEVELS),
    execute=st.sampled_from(LEVELS),
)
_egress = st.sampled_from(("none", "allowlist:pypi", "allowlist:npm"))
profiles = st.builds(
    EngineProfile,
    engine=st.just(VERSION),
    mode=st.sampled_from(list(EngineMode)),
    access=st.just(ACCESS),
    reasoning_effort=_effort,
    sub_agents=_sub_agents,
    limits=_limits,
    isolation_min=st.sampled_from(list(RuntimeMode)),
    result_schema=st.none(),
    egress=_egress,
    tools=st.builds(ToolCeiling, max_tools=_opt_int, effects=_effects),
    approvals=_approvals,
    skills=_skills,
    account_kinds=st.frozensets(st.sampled_from(list(AccountKind)), min_size=1),
)
constraints = st.builds(
    ProfileConstraint,
    engines=st.one_of(st.none(), st.just(frozenset({"opencode"})), st.just(frozenset({"codex"}))),
    mode=st.one_of(st.none(), st.sampled_from(list(EngineMode))),
    reasoning_effort=st.one_of(st.none(), st.sampled_from(("low", "medium", "high"))),
    sub_agents=st.one_of(st.none(), _sub_agents),
    limits=st.one_of(st.none(), _limits),
    isolation_min=st.one_of(st.none(), st.sampled_from(list(RuntimeMode))),
    egress=st.one_of(st.none(), _egress),
    max_tools=_opt_int,
    tool_effects=st.one_of(st.none(), _effects),
    approvals=st.one_of(
        st.none(), st.dictionaries(st.sampled_from(EFFECTS), st.sampled_from(LEVELS), max_size=4)
    ),
    skills=st.one_of(st.none(), st.frozensets(st.sampled_from(SKILLS))),
)


def _at_most(result: float | None, base: float | None) -> bool:
    return base is None or (result is not None and result <= base)


def _within(r: EngineProfile, p: EngineProfile) -> bool:
    """An order written apart from the registry: every dimension of ``r`` at or below ``p``'s."""
    sub = r.sub_agents
    return all(
        (
            r.engine == p.engine and r.access == p.access,
            r.mode in MODES_WITHIN[p.mode],
            ISOLATION[r.isolation_min] >= ISOLATION[p.isolation_min],
            r.egress in ("none", p.egress),
            _at_most(r.tools.max_tools, p.tools.max_tools),
            r.tools.effects <= p.tools.effects,
            _at_most(r.limits.max_steps, p.limits.max_steps),
            _at_most(r.limits.max_wall_s, p.limits.max_wall_s),
            _at_most(r.limits.max_tokens, p.limits.max_tokens),
            _at_most(r.limits.max_usd, p.limits.max_usd),
            not sub.enabled
            or (
                p.sub_agents.enabled
                and sub.max_depth <= p.sub_agents.max_depth
                and sub.max_concurrent <= p.sub_agents.max_concurrent
            ),
            p.reasoning_effort is None
            or (r.reasoning_effort is not None and RANK[r.reasoning_effort] <= RANK[p.reasoning_effort]),
            all(RANK[r.approvals.decision(k)] >= RANK[p.approvals.decision(k)] for k in EFFECTS),
            p.skills is None or (r.skills is not None and r.skills <= p.skills),
            p.result_schema is None or r.result_schema == p.result_schema,
        )
    )


def _attempt(p: EngineProfile, c: ProfileConstraint) -> EngineProfile | None:
    try:
        return narrow_profile(p, c)
    except ProfileWidening:
        return None


def _merge(a: ProfileConstraint, b: ProfileConstraint) -> ProfileConstraint:
    """``a ⊕ b``: b's dimensions over a's (limits and approvals per field)."""
    merged = {
        f.name: getattr(b, f.name) if getattr(b, f.name) is not None else getattr(a, f.name)
        for f in dataclasses.fields(ProfileConstraint)
    }
    if a.limits is not None and b.limits is not None:
        merged["limits"] = RunLimits(
            **{
                f.name: getattr(b.limits, f.name) if getattr(b.limits, f.name) is not None else getattr(a.limits, f.name)
                for f in dataclasses.fields(RunLimits)
            }
        )
    if a.approvals is not None and b.approvals is not None:
        merged["approvals"] = {**a.approvals, **b.approvals}
    return ProfileConstraint(**merged)  # type: ignore[arg-type]


@settings(max_examples=400, deadline=None)
@given(profiles, constraints)
def test_ep4_an_accepted_narrowing_is_within_its_base(p: EngineProfile, c: ProfileConstraint) -> None:
    result = _attempt(p, c)
    if result is not None:
        assert _within(result, p)


@settings(max_examples=400, deadline=None)
@given(profiles, constraints, constraints)
def test_ep5_composing_narrowings_equals_one_narrowing(
    p: EngineProfile, a: ProfileConstraint, b: ProfileConstraint
) -> None:
    once = _attempt(p, a)
    if once is None:
        return
    assert narrow_profile(once, a) == once
    twice = _attempt(once, b)
    if twice is not None:
        assert narrow_profile(p, _merge(a, b)) == twice


def _wideners(p: EngineProfile) -> list[tuple[str, ProfileConstraint]]:
    """For each dimension where ``p`` is not already at the top, one constraint strictly above it there."""
    out: list[tuple[str, ProfileConstraint]] = [("engine", ProfileConstraint(engines=frozenset({"codex"})))]
    if p.mode is not EngineMode.BUILD:
        out.append(("mode", ProfileConstraint(mode=EngineMode.BUILD)))
    weaker = [m for m in RuntimeMode if ISOLATION[m] < ISOLATION[p.isolation_min]]
    if weaker:
        out.append(("isolationMin", ProfileConstraint(isolation_min=weaker[0])))
    other = next(e for e in ("allowlist:pypi", "allowlist:npm") if e != p.egress)
    out.append(("egress", ProfileConstraint(egress=other)))
    if p.tools.max_tools is not None:
        out.append(("tools.maxTools", ProfileConstraint(max_tools=p.tools.max_tools + 1)))
    if p.tools.effects != frozenset(EFFECTS):
        out.append(("tools.effects", ProfileConstraint(tool_effects=frozenset(EFFECTS))))
    for name, attr in (("maxSteps", "max_steps"), ("maxWallS", "max_wall_s"), ("maxTokens", "max_tokens")):
        value = cast("int | None", getattr(p.limits, attr))
        if value is not None:
            out.append((f"limits.{name}", ProfileConstraint(limits=RunLimits(**{attr: value + 1}))))
    if p.limits.max_usd is not None:
        out.append(("limits.maxUsd", ProfileConstraint(limits=RunLimits(max_usd=p.limits.max_usd + 0.25))))
    if not p.sub_agents.enabled:
        out.append(("subAgents.enabled", ProfileConstraint(sub_agents=SubAgentPolicy(enabled=True))))
    else:
        deeper = SubAgentPolicy(True, p.sub_agents.max_depth + 1, p.sub_agents.max_concurrent)
        out.append(("subAgents.maxDepth", ProfileConstraint(sub_agents=deeper)))
    if p.reasoning_effort is not None and p.reasoning_effort != "high":
        out.append(("reasoningEffort", ProfileConstraint(reasoning_effort="high")))
    out.extend(
        (f"approvals.{kind}", ProfileConstraint(approvals={kind: "allow"}))
        for kind in EFFECTS
        if p.approvals.decision(kind) != "allow"
    )
    if p.skills is not None and p.skills != frozenset(SKILLS):
        out.append(("skills", ProfileConstraint(skills=frozenset(SKILLS))))
    if p.account_kinds != frozenset(AccountKind):
        out.append(("accountKinds", ProfileConstraint(account_kinds=frozenset(AccountKind))))
    return out


@settings(max_examples=300, deadline=None)
@given(profiles, st.data())
def test_ep6_a_widening_is_always_refused_naming_its_dimension(p: EngineProfile, data: st.DataObject) -> None:
    dimension, constraint = data.draw(st.sampled_from(_wideners(p)))
    with pytest.raises(ProfileWidening) as refused:
        narrow_profile(p, constraint)
    assert refused.value.field == dimension


def test_ep6_every_dimension_has_a_widener() -> None:
    bottom = dataclasses.replace(
        TOP,
        mode=EngineMode.PLAN,
        isolation_min=RuntimeMode.MICROVM,
        tools=ToolCeiling(max_tools=1, effects=frozenset()),
        sub_agents=SubAgentPolicy(),
        reasoning_effort="low",
        approvals=ProfileApprovals("deny", "deny", "deny", "deny"),
        skills=frozenset(),
        account_kinds=frozenset({AccountKind.ORG_API_KEY}),
    )
    named = {name.split(".")[0] for name, _ in _wideners(bottom)}
    shared = {d.path.split(".")[0] for d in PROFILE_ROWS} - {"access", "resultSchema"}
    assert named == shared

