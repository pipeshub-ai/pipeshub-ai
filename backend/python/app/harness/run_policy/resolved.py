"""Approvals and engine governance as resolved dimensions give them (an ``EffectiveRunConfig``'s ``dimensions``).

One reading for both holders of resolved dimensions: a run's pin narrowed by the live layer
(``PinnedRunPolicy``), and the org's stored layers alone for a run that has no pin once the policy store is the
record (``StoredRunPolicy``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Final, TypedDict, cast, get_args

from app.harness.approvals import governed
from app.harness.run_policy.ports import RunApprovals
from harness_core.contracts.generated.enums import RESPONSE_FORMATS
from harness_core.contracts.generated.policy_data import RELEASE_DEFAULTS
from harness_core.effects.flow import FlowLevel, FlowRule, strictest_flows
from harness_core.effects.policy import (
    ApprovalConfigInvalid,
    ApprovalRules,
    LayeredApprovalPolicy,
    Threshold,
    parse_rules,
    stricter,
)
from harness_core.effects.types import ApprovalDecision, EffectKind
from harness_core.policy import LIVE
from harness_core.sandbox.types import ResourceCaps

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping

    from app.control_plane.runtime.deployment import DeploymentLayer
    from harness_core.contracts.generated.enums import RuleLayer
    from harness_core.engines.types import EngineProfile

APPROVALS: Final = "approvals"
SNAPSHOT_TTL: Final = "snapshots.ttlDays"
# OWD-12: what a run without a pin keeps a sandbox snapshot for.
RELEASE_SNAPSHOT_TTL_DAYS: Final = RELEASE_DEFAULTS.snapshots.ttl_days
# The layers whose engine-profile ceiling is the org's own rule (``configured_rules``): its stored ceiling, and
# what it tightened since the run was pinned.
_ORG_CEILING_LAYERS: Final = frozenset({"org", LIVE})
_EFFECTS: Final[tuple[EffectKind, ...]] = get_args(EffectKind)

Dimensions = dict[str, dict[str, Any]]


class _FlowRuleWire(TypedDict):
    level: FlowLevel
    above: Threshold


class _ApprovalCeiling(TypedDict, total=False):
    """The lifted form of ``approval-rules`` (``harness_core.policy.orders``), as resolved dimensions carry it."""

    classes: dict[EffectKind, ApprovalDecision]
    tools: dict[str, ApprovalDecision]
    flow: list[_FlowRuleWire]


class _ClassCeiling(TypedDict):
    layer: str
    value: ApprovalDecision | None


# Whose rule a layer's ceiling is on the approval card (QA6-F6). The live layer is what the org tightened since the
# pin (as ``_ORG_CEILING_LAYERS``). A ``parent`` ceiling only restates its parent's value, whose own ceilings a child's
# pin holds before it (``with_parent_ceilings``); a trigger's or a request's ceiling is no admin's rule.
_CARD_LAYERS: Final[Mapping[str, RuleLayer]] = {
    "release": "deployment",
    "deployment": "deployment",
    "org": "org",
    "team": "org",
    LIVE: "org",
    "agent": "agent",
}


def _ceiling_rules(raw: object) -> ApprovalRules | None:
    """A layer's approvals ceiling as rules, its flow left to the resolved value's; ``None`` if it cannot be read."""
    if not isinstance(raw, dict):
        return None
    try:
        return parse_rules({key: raw[key] for key in ("writes", "classes", "tools") if key in raw})
    except ApprovalConfigInvalid:
        return None


def resolved_approvals(dimensions: Dimensions, deployment: DeploymentLayer) -> RunApprovals:
    """The ``approvals`` ceiling as one layer of rules, and its flow rules met with this process's own.

    The resolved value decides. The ceilings it was met from go before it, so the card names the first layer whose
    rule asks (QA6-F6), not the layer that wrote the value last: an agent restating the org's ``writes: ask``, or a
    child's ``parent`` layer carrying it, does not make it the agent's or the deployment's rule (QAFINAL-FU-PW107).
    They are the same rules met, so they decide nothing the resolved value does not."""
    entry = dimensions.get(APPROVALS)
    ceiling: _ApprovalCeiling = cast("_ApprovalCeiling", entry["value"]) if entry is not None else {}
    rules = ApprovalRules(classes=dict(ceiling.get("classes", {})), tools=dict(ceiling.get("tools", {})))
    flows = [FlowRule(rule["level"], rule["above"]) for rule in ceiling.get("flow", [])]
    set_by = entry["setBy"] if entry is not None else "deployment"
    named: list[tuple[ApprovalRules, RuleLayer]] = []
    for held in cast("list[dict[str, Any]]", entry.get("ceilings", []) if entry is not None else []):
        layer = _CARD_LAYERS.get(str(held.get("layer")))
        held_rules = _ceiling_rules(held.get("value"))
        if layer is not None and held_rules is not None:
            named.append((held_rules, layer))
    policy = LayeredApprovalPolicy(
        (*(r for r, _ in named), rules), (*(layer for _, layer in named), _CARD_LAYERS.get(set_by, "deployment"))
    )
    return RunApprovals(policy, strictest_flows((*flows, deployment.flow_rule)))


def resolved_engine_governance(
    dimensions: Dimensions, approvals: RunApprovals, profile: EngineProfile
) -> LayeredApprovalPolicy:
    """``approvals`` narrowed by the profile and by the classes the org's engine ceiling names, found in the
    provenance of ``engineProfile.approvals.*`` (a class it names is its rule even where it restates the release)."""
    named: dict[EffectKind, ApprovalDecision] = {}
    for kind in _EFFECTS:
        entry = dimensions.get(f"engineProfile.approvals.{kind}")
        set_by = cast("list[_ClassCeiling]", entry["ceilings"]) if entry is not None else []
        for ceiling in set_by:
            if ceiling["layer"] in _ORG_CEILING_LAYERS and ceiling["value"] is not None:
                named[kind] = stricter(named.get(kind, "allow"), ceiling["value"])
    return governed(approvals.rules, profile, ApprovalRules(classes=named))


def resolved_snapshot_ttl_days(dimensions: Dimensions) -> int:
    """``snapshots.ttlDays`` (OWD-12); a config resolved before the row existed keeps the release's."""
    entry = dimensions.get(SNAPSHOT_TTL)
    value = entry["value"] if entry is not None else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else RELEASE_SNAPSHOT_TTL_DAYS


@dataclass(frozen=True, slots=True)
class RunTuning:
    """C4.10's run-scoped settings as a run was admitted on.

    ``result_cap_chars``: ``toolResults.maxChars``, the inline tool-result cap (None: each executor's own).
    ``response_format`` / ``response_formats``: ``toolResults.format`` and the formats ``toolResults.formats``
    allows (None: any). ``conversation_grants``: ``interactions.conversationGrants``. ``resources``: the
    ``sandbox.*`` caps on an engine sandbox (None each: the template's). ``input_max_mb``: ``sandbox.inputMaxMb``,
    the largest input in MiB (None: the deployment's ``HARNESS_INPUT_MAX_MB``), which only lowers it (J-26)."""

    result_cap_chars: int | None = None
    response_format: str | None = None
    response_formats: tuple[str, ...] | None = None
    conversation_grants: bool = True
    resources: ResourceCaps = ResourceCaps()
    input_max_mb: int | None = None


RELEASE_TUNING: Final = RunTuning()


def _value(dimensions: Dimensions, path: str) -> object:
    entry = dimensions.get(path)
    return entry["value"] if entry is not None else None


def _positive(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def resolved_tuning(dimensions: Dimensions) -> RunTuning:
    """C4.10's run-scoped rows; a config resolved before a row existed keeps the release's for it."""
    formats = _value(dimensions, "toolResults.formats")
    allowed = tuple(f for f in formats if f in RESPONSE_FORMATS) if isinstance(formats, list) else None
    chosen = _value(dimensions, "toolResults.format")
    grants = _value(dimensions, "interactions.conversationGrants")
    cpus = _value(dimensions, "sandbox.cpus")
    return RunTuning(
        result_cap_chars=_positive(_value(dimensions, "toolResults.maxChars")),
        response_format=chosen if isinstance(chosen, str) and chosen in RESPONSE_FORMATS else None,
        response_formats=allowed,
        conversation_grants=grants is not False,
        resources=ResourceCaps(
            cpus=float(cpus) if isinstance(cpus, int | float) and not isinstance(cpus, bool) and cpus > 0 else None,
            memory_mb=_positive(_value(dimensions, "sandbox.memoryMb")),
            pids=_positive(_value(dimensions, "sandbox.pids")),
            scratch_mb=_positive(_value(dimensions, "sandbox.scratchMb")),
        ),
        input_max_mb=_positive(_value(dimensions, "sandbox.inputMaxMb")),
    )


def bind_run_tuning(tool_state: MutableMapping[str, Any], tuning: RunTuning) -> None:
    """The run's tool-result settings where its tools and the loop's result cap read them: the response_format
    policy (``run_response_format``) and the inline cap (``loop_result_cap``); an engine's cap is its endpoint's."""
    from app.agents.actions.knowledge_graph.ops.search_budget import (
        RESPONSE_FORMAT_POLICY,
    )
    from harness_core.context.results import RESULT_CAP_STATE

    tool_state[RESPONSE_FORMAT_POLICY] = (tuning.response_format, tuning.response_formats)
    tool_state[RESULT_CAP_STATE] = tuning.result_cap_chars


__all__ = [
    "APPROVALS",
    "RELEASE_SNAPSHOT_TTL_DAYS",
    "RELEASE_TUNING",
    "SNAPSHOT_TTL",
    "Dimensions",
    "RunTuning",
    "bind_run_tuning",
    "resolved_approvals",
    "resolved_engine_governance",
    "resolved_snapshot_ttl_days",
    "resolved_tuning",
]
