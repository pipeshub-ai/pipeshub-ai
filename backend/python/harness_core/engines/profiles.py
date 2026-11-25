"""Engine profiles (E4a): narrowing, the wire form, and resolution.

Release defaults → org ceiling → agent → request: each layer may only narrow what the
layer above left (ARCHITECTURE §3.1 principle 5), and a widening is refused naming the
dimension, never clipped (principle 6). The dimensions are the ``engineProfile.*`` rows of
``policy-dimensions.json``, folded by the one policy resolver (``harness_core.policy``) as
Node's ``domain/engine-profiles.ts`` does; ``contracts/harness/vectors/engine-profiles.json``
holds the two to one answer, digest included.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import cache
from typing import TYPE_CHECKING, Literal, cast

from pydantic import ValidationError

from harness_core.access.types import AccountKind
from harness_core.contracts import canonical_digest, dump_contract, validate_contract
from harness_core.contracts.generated.engine_profile import PinnedEngineProfile
from harness_core.contracts.generated.engine_profile_narrowing import (
    EngineProfileNarrowing,
)
from harness_core.engines.errors import (
    ProfileCeilingUnavailable,
    ProfileInvalid,
    ProfileWidening,
)
from harness_core.engines.types import (
    EFFECT_ORDER,
    EngineMode,
    EngineProfile,
    EngineVersion,
    ProfileApprovals,
    ProfileConstraint,
    RunLimits,
    SubAgentPolicy,
    ToolCeiling,
    is_acp_kind,
)
from harness_core.logs import HarnessLog
from harness_core.policy import (
    ENGINE_PROFILE,
    DimensionPolicyResolver,
    PolicyRefusal,
    ScopedLayer,
    policy_registry,
)
from harness_core.sandbox import RuntimeMode

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.access.types import AccessRouteRef
    from harness_core.effects.types import ApprovalDecision, EffectKind
    from harness_core.engines.ports import EngineCeilingSource
    from harness_core.engines.types import ReasoningEffort

LayerName = Literal["org", "agent", "request"]

_LOG = HarnessLog.named("harness_core.engines.profiles")


@dataclass(frozen=True, slots=True)
class ProfileLayer:
    name: LayerName
    constraint: ProfileConstraint


def engine_admits(selectors: frozenset[str], engine: EngineVersion) -> bool:
    """``kind`` admits every version of it; ``kind@version`` exactly one."""
    return engine.kind in selectors or f"{engine.kind}@{engine.version}" in selectors


def _state(profile: EngineProfile) -> dict[str, object]:
    """The profile as the resolver reads it: its wire form plus the host-only dimensions."""
    return {
        **profile_json(profile),
        "access": dataclasses.asdict(profile.access),
        "resultSchema": None if profile.result_schema is None else dict(profile.result_schema),
    }


def _layer(constraint: ProfileConstraint) -> dict[str, object]:
    layer: dict[str, object] = dict(_constraint_wire(constraint))
    if constraint.access is not None:
        layer["access"] = dataclasses.asdict(constraint.access)
    if constraint.result_schema is not None:
        layer["resultSchema"] = dict(constraint.result_schema)
    return layer


def _from_state(base: EngineProfile, doc: Mapping[str, object]) -> EngineProfile:
    """``base`` with the narrowed values; the engine and the access route never change by narrowing."""
    tools = cast("Mapping[str, object]", doc["tools"])
    limits = cast("Mapping[str, int | float | None]", doc["limits"])
    sub_agents = cast("Mapping[str, object]", doc["subAgents"])
    approvals = cast("Mapping[str, ApprovalDecision]", doc["approvals"])
    skills = cast("list[str] | None", doc["skills"])
    return dataclasses.replace(
        base,
        mode=EngineMode(cast("str", doc["mode"])),
        isolation_min=RuntimeMode(cast("str", doc["isolationMin"])),
        egress=cast("str", doc["egress"]),
        tools=ToolCeiling(
            max_tools=cast("int | None", tools["maxTools"]),
            effects=frozenset(cast("list[EffectKind]", tools["effects"])),
        ),
        limits=RunLimits(
            max_steps=cast("int | None", limits["maxSteps"]),
            max_wall_s=cast("int | None", limits["maxWallS"]),
            max_tokens=cast("int | None", limits["maxTokens"]),
            max_usd=limits["maxUsd"],
        ),
        sub_agents=SubAgentPolicy(
            enabled=cast("bool", sub_agents["enabled"]),
            max_depth=cast("int", sub_agents["maxDepth"]),
            max_concurrent=cast("int", sub_agents["maxConcurrent"]),
        ),
        reasoning_effort=cast("ReasoningEffort | None", doc["reasoningEffort"]),
        approvals=ProfileApprovals(**approvals),
        skills=None if skills is None else frozenset(skills),
        account_kinds=frozenset(AccountKind(k) for k in cast("list[str]", doc["accountKinds"])),
        result_schema=cast("Mapping[str, JsonValue] | None", doc["resultSchema"]),
    )


@cache
def _resolver() -> DimensionPolicyResolver:
    return DimensionPolicyResolver(policy_registry(ENGINE_PROFILE))


def narrow_profile(profile: EngineProfile, constraint: ProfileConstraint) -> EngineProfile:
    """Every dimension of ``constraint`` at or below ``profile``'s, applied; else the first widening, refused."""
    folded = _resolver().fold_prefix(
        (ScopedLayer("release", _state(profile)), ScopedLayer("request", _layer(constraint)))
    )
    if isinstance(folded, PolicyRefusal):
        if folded.code == "invalid":
            raise ProfileInvalid(f"{folded.dimension}: {folded.detail}")
        raise ProfileWidening(folded.dimension, folded.detail)
    return _from_state(profile, folded.document())


AcpOptIn = Literal["org", "release"]
"""Who opts an ACP engine in, as Node's ``AcpOptIn`` says: ``release``, every released one (no org sets a ceiling,
so none could name it); ``org``, only one the org's ceiling names (a long-tail agent is the org admin's choice)."""


def opted_in(
    releases: Sequence[EngineProfile], layers: Sequence[ProfileLayer], acp_opt_in: AcpOptIn = "release"
) -> list[EngineProfile]:
    """The releases an org may run: every native engine, and an ACP engine (``acp-<agent>``) as ``acp_opt_in``
    says: any released one, or only one the org's ceiling names by kind or ``kind@version`` (ACP-driver)."""
    if acp_opt_in == "release":
        return list(releases)
    org = next((layer.constraint.engines for layer in layers if layer.name == "org"), None)
    named = org or frozenset[str]()
    return [r for r in releases if not is_acp_kind(r.engine.kind) or engine_admits(named, r.engine)]


def resolve_profile(
    releases: Sequence[EngineProfile], layers: Sequence[ProfileLayer], acp_opt_in: AcpOptIn = "release"
) -> EngineProfile:
    """The first release profile every layer's ``engines`` admits among those ``acp_opt_in`` opts in, narrowed by
    each layer in order."""
    if not releases:
        raise ProfileWidening("engine", "no engine is released")
    candidates = opted_in(releases, layers, acp_opt_in)
    if not candidates:
        raise ProfileWidening("engine", "no released engine is admitted by every layer", layer="org")
    for layer in layers:
        if layer.constraint.engines is not None:
            engines = layer.constraint.engines
            candidates = [c for c in candidates if engine_admits(engines, c.engine)]
            if not candidates:
                raise ProfileWidening("engine", "no released engine is admitted by every layer", layer=layer.name)
    profile = candidates[0]
    for layer in layers:
        try:
            profile = narrow_profile(profile, layer.constraint)
        except ProfileWidening as refused:
            raise refused.at(layer.name) from None
    return profile


def profile_json(profile: EngineProfile) -> dict[str, JsonValue]:
    """The wire form (``PinnedEngineProfile``): every dimension explicit, lists sorted."""
    limits = profile.limits
    return {
        "engine": {
            "kind": profile.engine.kind,
            "version": profile.engine.version,
            "binaryDigest": profile.engine.binary_digest,
        },
        "mode": profile.mode.value,
        "isolationMin": profile.isolation_min.value,
        "egress": profile.egress,
        "tools": {"maxTools": profile.tools.max_tools, "effects": cast("list[JsonValue]", sorted(profile.tools.effects))},
        "limits": {
            "maxSteps": limits.max_steps,
            "maxWallS": limits.max_wall_s,
            "maxTokens": limits.max_tokens,
            "maxUsd": limits.max_usd,
        },
        "subAgents": {
            "enabled": profile.sub_agents.enabled,
            "maxDepth": profile.sub_agents.max_depth,
            "maxConcurrent": profile.sub_agents.max_concurrent,
        },
        "reasoningEffort": profile.reasoning_effort,
        "approvals": cast("dict[str, JsonValue]", profile.approvals.as_mapping()),
        "skills": None if profile.skills is None else cast("list[JsonValue]", sorted(profile.skills)),
        "accountKinds": cast("list[JsonValue]", sorted(k.value for k in profile.account_kinds)),
    }


def profile_digest(profile: EngineProfile) -> str:
    """``executor.engine.profileDigest``: the canonical digest of the pinned wire form."""
    return canonical_digest(profile_json(profile))


def _invalid(message: str) -> ProfileInvalid:
    return ProfileInvalid(message)


def _nulls(value: object, path: str) -> str | None:
    if value is None:
        return path or "the layer"
    if isinstance(value, Mapping):
        for key, item in cast("Mapping[str, object]", value).items():
            found = _nulls(item, f"{path}.{key}" if path else key)
            if found is not None:
                return found
    return None


def _unique[T](name: str, items: Sequence[T]) -> frozenset[T]:
    found = frozenset(items)
    if len(found) != len(items):
        raise _invalid(f"{name} repeats an item")
    return found


def profile_from_wire(
    raw: object, *, access: AccessRouteRef, result_schema: Mapping[str, JsonValue] | None = None
) -> EngineProfile:
    """A pinned profile back as an ``EngineProfile``; anything the schema refuses is ``profile_invalid``."""
    try:
        wire = raw if isinstance(raw, PinnedEngineProfile) else validate_contract(PinnedEngineProfile, raw)
        engine = EngineVersion(
            kind=wire.engine.kind,  # pyright: ignore[reportArgumentType] - checked by EngineVersion
            version=wire.engine.version,
            binary_digest=wire.engine.binary_digest,
        )
        limits = wire.limits
        return EngineProfile(
            engine=engine,
            mode=EngineMode(wire.mode),
            access=access,
            reasoning_effort=wire.reasoning_effort,
            sub_agents=SubAgentPolicy(
                enabled=wire.sub_agents.enabled,
                max_depth=wire.sub_agents.max_depth,
                max_concurrent=wire.sub_agents.max_concurrent,
            ),
            limits=RunLimits(
                max_steps=None if limits.max_steps is None else limits.max_steps.root,
                max_wall_s=None if limits.max_wall_s is None else limits.max_wall_s.root,
                max_tokens=None if limits.max_tokens is None else limits.max_tokens.root,
                max_usd=None if limits.max_usd is None else limits.max_usd.root,
            ),
            isolation_min=RuntimeMode(wire.isolation_min),
            result_schema=result_schema,
            egress=wire.egress,
            tools=ToolCeiling(
                max_tools=None if wire.tools.max_tools is None else wire.tools.max_tools.root,
                effects=_unique("tools.effects", list(wire.tools.effects)),
            ),
            approvals=ProfileApprovals(
                read=wire.approvals.read,
                workspace=wire.approvals.workspace,
                write=wire.approvals.write,
                execute=wire.approvals.execute,
            ),
            skills=None if wire.skills is None else _unique("skills", [s.root for s in wire.skills.root]),
            account_kinds=_unique("accountKinds", [AccountKind(k) for k in wire.account_kinds]),
        )
    except (ValidationError, ValueError) as exc:
        raise _invalid(f"not a pinned engine profile: {type(exc).__name__}") from None


def constraint_from_wire(raw: object) -> ProfileConstraint:
    """One layer (``EngineProfileNarrowing``) as a constraint. A null anywhere is refused, so a layer can
    never ask for "unlimited" or "any"; nothing invalid is read as absent."""
    null = _nulls(raw, "")
    if null is not None:
        raise _invalid(f"{null} is null")
    try:
        wire = validate_contract(EngineProfileNarrowing, raw)
    except ValidationError as exc:
        raise _invalid(f"not an engine profile narrowing: {exc.error_count()} error(s)") from None
    limits = wire.limits
    tools = wire.tools
    approvals = wire.approvals
    return ProfileConstraint(
        engines=None if wire.engines is None else _unique("engines", [e.root for e in wire.engines]),
        mode=None if wire.mode is None else EngineMode(wire.mode),
        isolation_min=None if wire.isolation_min is None else RuntimeMode(wire.isolation_min),
        egress=wire.egress,
        max_tools=None if tools is None else tools.max_tools,
        tool_effects=(
            None
            if tools is None or tools.effects is None
            else _unique("tools.effects", [e.root for e in tools.effects])
        ),
        limits=(
            None
            if limits is None
            else RunLimits(
                max_steps=limits.max_steps,
                max_wall_s=limits.max_wall_s,
                max_tokens=limits.max_tokens,
                max_usd=limits.max_usd,
            )
        ),
        sub_agents=(
            None
            if wire.sub_agents is None
            else SubAgentPolicy(
                enabled=wire.sub_agents.enabled,
                max_depth=wire.sub_agents.max_depth,
                max_concurrent=wire.sub_agents.max_concurrent,
            )
        ),
        reasoning_effort=wire.reasoning_effort,
        approvals=(
            None
            if approvals is None
            else {
                kind: cast("ApprovalDecision", value)
                for kind in EFFECT_ORDER
                if (value := getattr(approvals, kind)) is not None
            }
        ),
        skills=None if wire.skills is None else _unique("skills", [s.root for s in wire.skills]),
        account_kinds=(
            None
            if wire.account_kinds is None
            else _unique("accountKinds", [AccountKind(k.root) for k in wire.account_kinds])
        ),
    )


def constraint_json(constraint: ProfileConstraint) -> dict[str, JsonValue]:
    """The wire form of the shared dimensions of ``constraint`` (``access`` and ``result_schema`` are not on it)."""
    return cast("dict[str, JsonValue]", dump_contract(validate_contract(EngineProfileNarrowing, _constraint_wire(constraint))))


def _constraint_wire(constraint: ProfileConstraint) -> dict[str, JsonValue]:
    out: dict[str, JsonValue] = {}
    c = constraint
    if c.engines is not None:
        out["engines"] = cast("list[JsonValue]", sorted(c.engines))
    if c.mode is not None:
        out["mode"] = c.mode.value
    if c.isolation_min is not None:
        out["isolationMin"] = c.isolation_min.value
    if c.egress is not None:
        out["egress"] = c.egress
    tools: dict[str, JsonValue] = {}
    if c.max_tools is not None:
        tools["maxTools"] = c.max_tools
    if c.tool_effects is not None:
        tools["effects"] = cast("list[JsonValue]", sorted(c.tool_effects))
    if tools:
        out["tools"] = tools
    if c.limits is not None:
        limits = {
            "maxSteps": c.limits.max_steps,
            "maxWallS": c.limits.max_wall_s,
            "maxTokens": c.limits.max_tokens,
            "maxUsd": c.limits.max_usd,
        }
        out["limits"] = {key: value for key, value in limits.items() if value is not None}
    if c.sub_agents is not None:
        out["subAgents"] = {
            "enabled": c.sub_agents.enabled,
            "maxDepth": c.sub_agents.max_depth,
            "maxConcurrent": c.sub_agents.max_concurrent,
        }
    if c.reasoning_effort is not None:
        out["reasoningEffort"] = c.reasoning_effort
    if c.approvals is not None:
        out["approvals"] = cast("dict[str, JsonValue]", dict(c.approvals))
    if c.skills is not None:
        out["skills"] = cast("list[JsonValue]", sorted(c.skills))
    if c.account_kinds is not None:
        out["accountKinds"] = cast("list[JsonValue]", sorted(k.value for k in c.account_kinds))
    return out


class EngineProfileResolver:
    """The one place an engine run's profile is resolved outside Node's admission (E4a, AG1).

    ``releases`` gives the release profiles (allowlist order) for a caller's access route;
    ``ceilings`` the org's, and ``acp_opt_in`` who opts an ACP engine in. Each call reads the
    ceiling afresh, so a changed ceiling applies to the next resolution and never to a profile
    already returned.
    """

    def __init__(
        self,
        releases: Callable[[AccessRouteRef], Sequence[EngineProfile]],
        ceilings: EngineCeilingSource,
        *,
        acp_opt_in: AcpOptIn = "release",
        log: HarnessLog | None = None,
    ) -> None:
        self._releases = releases
        self._ceilings = ceilings
        self._acp_opt_in: AcpOptIn = acp_opt_in
        self._log = log or _LOG

    async def resolve_engine_profile(
        self,
        org_id: str,
        agent: ProfileConstraint | None,
        request: ProfileConstraint | None,
        *,
        access: AccessRouteRef,
        log: HarnessLog | None = None,
    ) -> EngineProfile:
        """Release → org ceiling → ``agent`` → ``request``. Raises ``ProfileWidening`` (naming the
        dimension and layer), ``ProfileInvalid`` or ``ProfileCeilingUnavailable``; logged once."""
        bound = (log or self._log).bind(orgId=org_id)
        layers: list[ProfileLayer] = []
        try:
            ceiling = await self._ceilings.ceiling(org_id)
            for name, constraint in (("org", ceiling), ("agent", agent), ("request", request)):
                if constraint is not None:
                    layers.append(ProfileLayer(cast("LayerName", name), constraint))
            profile = resolve_profile(self._releases(access), layers, self._acp_opt_in)
        except (ProfileWidening, ProfileInvalid, ProfileCeilingUnavailable) as refused:
            widening = refused if isinstance(refused, ProfileWidening) else None
            bound.warning(
                "engine.profile.refused",
                reasonCode=refused.reason_code,
                dimension=None if widening is None else widening.field,
                layer=None if widening is None else widening.layer,
            )
            raise
        bound.info(
            "engine.profile.resolved",
            engineKind=profile.engine.kind,
            engineVersion=profile.engine.version,
            profileDigest=profile_digest(profile),
            layers=[layer.name for layer in layers],
        )
        return profile


__all__ = [
    "AcpOptIn",
    "EngineProfileResolver",
    "opted_in",
    "LayerName",
    "ProfileLayer",
    "constraint_from_wire",
    "constraint_json",
    "engine_admits",
    "narrow_profile",
    "profile_digest",
    "profile_from_wire",
    "profile_json",
    "resolve_profile",
]
