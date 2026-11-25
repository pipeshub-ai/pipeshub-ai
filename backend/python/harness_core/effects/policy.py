"""Approval policy: allow, ask or deny a call from its effect class and layered rules.

Each layer (platform default, org, agent, engine profile) may only narrow what
the layers before it allowed: the stricter decision wins, so no configuration
level can approve on behalf of another. Rules are plain data, parsed once from
configuration; a rule that cannot be parsed fails closed (``ask`` for writes).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, cast, get_args

from harness_core.contracts.generated.enums import (
    APPROVAL_LEVELS,
    FlowLevel,
    FlowThreshold,
    HoldRule,
    RuleLayer,
)
from harness_core.effects.types import ApprovalDecision, EffectKind
from harness_core.refusals import HarnessRefusal

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.messages import ToolCall

FlowSetting = FlowLevel
Threshold = FlowThreshold
# The effect classes a layer's ``writes`` switch covers.
_WRITES: Final[frozenset[str]] = frozenset({"write", "execute"})

# ``allow_with_redaction`` runs unasked like ``allow`` but narrows it: any layer's redaction applies.
_RANK: Final[dict[ApprovalDecision, int]] = {"allow": 0, "allow_with_redaction": 1, "ask": 2, "deny": 3}
# The rule that lets a call run without a person because its effect stays in the run's own sandbox (CODEAUTO).
OWN_SANDBOX_RULE: Final = "own_sandbox"
_KINDS: Final[frozenset[str]] = frozenset(get_args(EffectKind))
# What a configured rule may say: a redaction is a policy's own decision, named with its reason, never a bare level.
_LEVELS: Final[frozenset[str]] = frozenset(APPROVAL_LEVELS)
MAX_TOOL_RULES: Final = 500
MAX_TOOL_ID_LENGTH: Final = 256


def stricter(a: ApprovalDecision, b: ApprovalDecision) -> ApprovalDecision:
    return a if _RANK[a] >= _RANK[b] else b


def in_own_sandbox(effect: ToolEffect) -> bool:
    """Code that runs in the run's own sandbox with no network (``execute``, sink ``none``): nothing it does reaches
    anyone outside the run, so a class rule does not ask about it."""
    return effect.effect == "execute" and effect.sink.egress == "none"


class ApprovalConfigInvalid(HarnessRefusal):
    """An approval rule that cannot be read; the caller falls back to ``WRITES_ASK``."""

    def __init__(self, reason_code: str, detail: str) -> None:
        super().__init__(f"{reason_code}: {detail}")
        self.reason_code = reason_code


@dataclass(frozen=True, slots=True)
class LayerFlow:
    """A layer's flow rule as configured (``approval-rules.schema.json`` ``flow``); ``harness_core.effects.flow``
    gives it meaning. ``off`` says nothing: a layer can never widen another's rule."""

    level: FlowSetting
    above: Threshold = "public"


@dataclass(frozen=True)
class ApprovalRules:
    """One layer: a decision per effect class and per tool id (absent: ``allow``), and its flow rule."""

    classes: Mapping[EffectKind, ApprovalDecision] = field(
        default_factory=dict[EffectKind, ApprovalDecision]
    )
    tools: Mapping[str, ApprovalDecision] = field(default_factory=dict[str, ApprovalDecision])
    flow: LayerFlow | None = None

    def decide(self, effect: ToolEffect) -> ApprovalDecision:
        by_class = self.classes.get(effect.effect, "allow")
        # A class's ask yields to the own-sandbox default; its deny does not, and a tool's own rule always applies.
        if by_class == "ask" and in_own_sandbox(effect):
            by_class = "allow"
        return stricter(by_class, self.tools.get(effect.tool_id, "allow"))

    def asking(self, effect: ToolEffect) -> HoldRule | None:
        """Which of this layer's rules asks about the call (QA6-F6): the tool's own (``tool``) before its class's
        (``writes`` for a write or execute, ``effect_class`` for any other); ``None`` when none does."""
        if self.decide(effect) != "ask":
            return None
        if self.tools.get(effect.tool_id) == "ask":
            return "tool"
        return "writes" if effect.effect in _WRITES else "effect_class"


ALLOW_ALL: Final = ApprovalRules()
WRITES_ASK: Final = ApprovalRules(classes={"write": "ask", "execute": "ask"})


@dataclass(frozen=True)
class LayeredApprovalPolicy:
    """``ApprovalPolicy`` over layers that can only narrow one another. ``scopes`` names whose rule each layer is,
    in step with ``layers``; a layer nobody named is the deployment's."""

    layers: tuple[ApprovalRules, ...] = ()
    scopes: tuple[RuleLayer, ...] = ()

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        decision: ApprovalDecision = "allow"
        for layer in self.layers:
            decision = stricter(decision, layer.decide(effect))
        return decision

    def narrowed(self, rules: ApprovalRules, scope: RuleLayer = "deployment") -> LayeredApprovalPolicy:
        return LayeredApprovalPolicy((*self.layers, rules), (*self._scopes(), scope))

    def _scopes(self) -> tuple[RuleLayer, ...]:
        return (*self.scopes, *("deployment",) * (len(self.layers) - len(self.scopes)))[: len(self.layers)]

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None:
        """Why this policy asks (``ExplainsAsk``, QA6-F6): the first layer that asks, in precedence order, and which
        of its rules does. A layer's own rule is never answered for the conversation. ``None`` unless it asks."""
        if self(call, effect) != "ask":
            return None
        for scope, layer in zip(self._scopes(), self.layers, strict=True):
            rule = layer.asking(effect)
            if rule is not None:
                return {"rule": rule, "layer": scope, "toolId": effect.tool_id, "conversationGrant": False}
        return None

    @property
    def flows(self) -> tuple[LayerFlow, ...]:
        return tuple(layer.flow for layer in self.layers if layer.flow is not None)


ENGINE_APPROVALS: Final = LayeredApprovalPolicy((WRITES_ASK,), ("engine",))


def _level(value: JsonValue, where: str) -> ApprovalDecision:
    if not isinstance(value, str) or value not in _LEVELS:
        raise ApprovalConfigInvalid("approval_level_invalid", where)
    return cast("ApprovalDecision", value)


def _tool_rules(raw_tools: JsonValue) -> dict[str, ApprovalDecision]:
    if not isinstance(raw_tools, Mapping):
        raise ApprovalConfigInvalid("approval_rules_not_object", "tools")
    if len(raw_tools) > MAX_TOOL_RULES:
        raise ApprovalConfigInvalid("approval_rules_too_many_tools", str(len(raw_tools)))
    tools: dict[str, ApprovalDecision] = {}
    for tool, value in raw_tools.items():
        if not 1 <= len(tool) <= MAX_TOOL_ID_LENGTH or tool == "__proto__":
            raise ApprovalConfigInvalid("approval_tool_id_invalid", "tools")
        tools[tool] = _level(value, "tools")
    return tools


_FLOW_LEVELS: Final[frozenset[str]] = frozenset(get_args(FlowSetting))
_THRESHOLDS: Final[frozenset[str]] = frozenset(get_args(Threshold))


def _flow(raw: JsonValue) -> LayerFlow:
    if not isinstance(raw, Mapping):
        raise ApprovalConfigInvalid("approval_rules_not_object", "flow")
    if set(raw) - {"level", "above"}:
        raise ApprovalConfigInvalid("approval_flow_invalid", "flow")
    level, above = raw.get("level"), raw.get("above", "public")
    if not isinstance(level, str) or level not in _FLOW_LEVELS:
        raise ApprovalConfigInvalid("approval_flow_invalid", "flow.level")
    if not isinstance(above, str) or above not in _THRESHOLDS:
        raise ApprovalConfigInvalid("approval_flow_invalid", "flow.above")
    return LayerFlow(cast("FlowSetting", level), cast("Threshold", above))


def parse_rules(raw: JsonValue) -> ApprovalRules:
    """``{"writes": "ask"}`` (the one switch), plus optional ``classes`` and ``tools`` maps.

    ``None`` is an absent layer; inside a layer nothing may be null (``approval-rules.schema.json``,
    the schema Node validates the same document with).
    """
    if raw is None:
        return ALLOW_ALL
    if not isinstance(raw, Mapping):
        raise ApprovalConfigInvalid("approval_rules_not_object", type(raw).__name__)
    unknown = set(raw) - {"writes", "classes", "tools", "flow"}
    if unknown:
        raise ApprovalConfigInvalid("approval_rules_unknown_key", ",".join(sorted(unknown)))
    classes: dict[EffectKind, ApprovalDecision] = {}
    if "writes" in raw:
        level = _level(raw["writes"], "writes")
        classes["write"] = level
        classes["execute"] = level
    raw_classes = raw.get("classes", {})
    if not isinstance(raw_classes, Mapping):
        raise ApprovalConfigInvalid("approval_rules_not_object", "classes")
    for kind, value in raw_classes.items():
        if kind not in _KINDS:
            raise ApprovalConfigInvalid("approval_class_unknown", kind)
        effect_kind = cast("EffectKind", kind)
        classes[effect_kind] = stricter(classes.get(effect_kind, "allow"), _level(value, kind))
    flow = _flow(raw["flow"]) if "flow" in raw else None
    return ApprovalRules(classes, _tool_rules(raw.get("tools", {})), flow)


__all__ = [
    "ALLOW_ALL",
    "ENGINE_APPROVALS",
    "MAX_TOOL_ID_LENGTH",
    "MAX_TOOL_RULES",
    "OWN_SANDBOX_RULE",
    "WRITES_ASK",
    "ApprovalConfigInvalid",
    "ApprovalRules",
    "FlowSetting",
    "LayerFlow",
    "LayeredApprovalPolicy",
    "RuleLayer",
    "Threshold",
    "in_own_sandbox",
    "parse_rules",
    "stricter",
]
