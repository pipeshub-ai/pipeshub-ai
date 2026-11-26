"""The durable run's approval policy as its rules give it (P2.6c, P2.11e, SEC1, UX2): pure, nothing read here.

A pinned run's rules come from its pin (``app.harness.run_policy``); a run admitted without one reads the
document below per attempt (``app.harness.unpinned``, until the flip). The document
(``contracts/harness/ext/pipeshub/approvals-document.schema.json``; written by the operator)::

    {"default": {"writes": "ask", "flow": {"level": "ask"}},     # every run (operator)
     "agents": {"<agentKey>": {"tools": {"<toolId>": "deny"}}}}  # one agent's runs (operator)

An edition's schema overlay may add top-level maps (orgId -> entry) that the generated model names, so they are known
keys here; the edition's own rule layers (``edition_config.org_approval_rules``) read what they hold. Layers apply in
order (default, the edition's org layers, agents[agentKey], the edition's agent layers) and may only narrow
(``LayeredApprovalPolicy``). Absent, a run is ``allow`` for every class. A document whose top level the schema refuses
fails closed to ``WRITES_ASK`` as a whole; a layer that cannot be read fails closed alone. Both logged once; Node reads
the same document by the same rule (``contracts/harness/vectors/approvals.json``).
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, cast

from app.harness.contracts.generated.pipeshub.approvals_document import (
    PipeshubApprovalsDocument,
)
from app.harness.flow_destination import pipeshub_destination
from harness_core.effects.flow import (
    ENGINE_WRITES_RULE,
    DefaultAskPolicy,
    FlowGuardedPolicy,
    FlowRule,
    grant_answerable,
)
from harness_core.effects.policy import (
    WRITES_ASK,
    ApprovalConfigInvalid,
    ApprovalRules,
    LayeredApprovalPolicy,
    RuleLayer,
    parse_rules,
)
from harness_core.effects.redaction import redaction_of
from harness_core.effects.types import (
    POLICY_DENIAL,
    ExplainsAsk,
    ExplainsDenial,
    default_lifted,
    refresh_policy,
)
from harness_core.engines.policy import profile_rules

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

    from pydantic import JsonValue

    from app.agents.agent_loop.context import AgentContext
    from harness_core.effects.flow import (
        AskDestination,
        ConversationGrants,
        GrantingLedger,
        VouchesAddress,
    )
    from harness_core.effects.redaction import Redaction
    from harness_core.effects.types import ApprovalDecision, ApprovalPolicy, ToolEffect
    from harness_core.engines.types import EngineProfile
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.logs import HarnessLog
    from harness_core.security.labels import SourceLedger


# What a layer or document that cannot be read decides: writes ask, as ``WRITES_ASK``. Its own object, so today's loop,
# which applies only the flow layers, can tell that an org rule it cannot see may be on (FG2).
UNREADABLE_LAYER: Final = ApprovalRules(classes=dict(WRITES_ASK.classes))


def _layer(raw: JsonValue, layer: str, log: HarnessLog) -> ApprovalRules:
    try:
        return parse_rules(raw)
    except ApprovalConfigInvalid as refused:
        log.warning("approvals.config_invalid", reasonCode=refused.reason_code, layer=layer)
        return UNREADABLE_LAYER


# The schema's top-level keys, with those an edition's overlay adds (x-extensible); every one but `default` is a map.
_SECTIONS: Final = frozenset(field.alias or name for name, field in PipeshubApprovalsDocument.model_fields.items())
_MAPS: Final = _SECTIONS - {"default"}


def _document(document: JsonValue, log: HarnessLog) -> Mapping[str, JsonValue] | None:
    """The document when its top level is the schema's, else ``None`` (logged)."""
    if not isinstance(document, Mapping):
        log.warning("approvals.config_invalid", reasonCode="approval_rules_not_object", layer="document")
        return None
    doc = cast("Mapping[str, JsonValue]", document)
    if set(doc) - _SECTIONS:
        log.warning("approvals.config_invalid", reasonCode="approval_document_unknown_key", layer="document")
        return None
    if any(not isinstance(doc[section], Mapping) for section in _MAPS & set(doc)):
        log.warning("approvals.config_invalid", reasonCode="approval_rules_not_object", layer="document")
        return None
    return doc


def _entry(doc: Mapping[str, JsonValue], section: str, key: str) -> JsonValue:
    return cast("Mapping[str, JsonValue]", doc.get(section, {})).get(key)


class OrgApprovalRules(Protocol):
    """Rule layers an edition keeps in the approvals document beside the deployment's, each with whose rule it is on
    the approval card (QA6-F6). ``read`` parses one stored entry as a layer (unreadable: ``UNREADABLE_LAYER``)."""

    def org_layers(
        self, doc: Mapping[str, JsonValue], org_id: str, read: Callable[[JsonValue, str], ApprovalRules]
    ) -> Sequence[tuple[ApprovalRules, RuleLayer]]: ...

    def agent_layers(
        self,
        doc: Mapping[str, JsonValue],
        org_id: str,
        agent_key: str,
        read: Callable[[JsonValue, str], ApprovalRules],
    ) -> Sequence[tuple[ApprovalRules, RuleLayer]]: ...


class LayerAskAnswers(Protocol):
    """An edition's reading of the rule layers' asks: which of them hold the platform's web tools, and which a person
    may answer beyond one call, for the rest of the conversation (``grants``, per tool and destination) or for good (the
    person's own rule). ``governance`` is the run's layered policy; what comes back may lift what it asks, never refuse
    more."""

    def answering(
        self,
        governance: ApprovalPolicy,
        *,
        context: AgentContext,
        grants: ConversationGrants | None,
        destination: Callable[[ToolCall, ToolEffect], AskDestination | None],
        log: HarnessLog,
    ) -> ApprovalPolicy: ...


@dataclass(frozen=True)
class PlatformWebRuns:
    """``base`` with the platform's own web search and page fetch out of its reach: no rule layer asks about or refuses
    them here. Search always runs; a page's address is the flow rule's to check, outside this policy."""

    base: ApprovalPolicy

    async def refresh(self) -> None:
        await refresh_policy(self.base)

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return "allow" if effect.platform_web else self.base(call, effect)

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None:
        if effect.platform_web or not isinstance(self.base, ExplainsAsk):
            return None
        return self.base.ask_reason(call, effect)

    def grant_answers(self, call: ToolCall, effect: ToolEffect) -> bool:
        return grant_answerable(self.base, call, effect)

    def lifts_default(self, call: ToolCall, effect: ToolEffect) -> bool:
        return default_lifted(self.base, call, effect)

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None:
        return redaction_of(self.base, call, effect)

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        return self.base.denial(call, effect) if isinstance(self.base, ExplainsDenial) else POLICY_DENIAL


def layer_ask_answers(
    governance: ApprovalPolicy,
    *,
    context: AgentContext,
    grants: ConversationGrants | None,
    log: HarnessLog,
) -> ApprovalPolicy:
    """``governance`` with the edition's ``LayerAskAnswers``, read at call time. Without one, no rule layer governs the
    platform's web tools (``PlatformWebRuns``)."""
    from app import edition_config

    edition = cast("LayerAskAnswers | None", getattr(edition_config, "layer_ask_answers", None))
    if edition is None:
        return PlatformWebRuns(governance)
    return edition.answering(governance, context=context, grants=grants, destination=pipeshub_destination, log=log)


def org_approval_rules() -> OrgApprovalRules | None:
    """The edition's rule layers, read at call time so an edition extension that sets them is honoured; none here."""
    from app import edition_config

    return cast("OrgApprovalRules | None", getattr(edition_config, "org_approval_rules", None))


def approval_policy(
    document: JsonValue, *, org_id: str, agent_key: str | None, log: HarnessLog
) -> LayeredApprovalPolicy:
    if document is None:
        return LayeredApprovalPolicy()
    doc = _document(document, log)
    if doc is None:
        return LayeredApprovalPolicy((UNREADABLE_LAYER,), ("deployment",))
    edition = org_approval_rules()

    def read(raw: JsonValue, layer: str) -> ApprovalRules:
        return _layer(raw, layer, log)

    layers = [_layer(doc.get("default"), "default", log)]
    scopes: list[RuleLayer] = ["deployment"]
    for rules, scope in edition.org_layers(doc, org_id, read) if edition is not None else ():
        layers.append(rules)
        scopes.append(scope)
    if agent_key is not None:
        layers.append(_layer(_entry(doc, "agents", agent_key), "agents", log))
        scopes.append("agent")
        for rules, scope in edition.agent_layers(doc, org_id, agent_key, read) if edition is not None else ():
            layers.append(rules)
            scopes.append(scope)
    return LayeredApprovalPolicy(tuple(layers), tuple(scopes))


def flow_guarded(
    policy: ApprovalPolicy,
    *,
    rules: Sequence[FlowRule],
    ledger: SourceLedger | None,
    run_id: str,
    log: HarnessLog,
    destination: Callable[[ToolCall, ToolEffect], AskDestination | None] | None = None,
    grants: ConversationGrants | None = None,
    addresses: VouchesAddress | None = None,
) -> ApprovalPolicy:
    """``policy`` narrowed by the run's flow ``rules`` over what ``ledger`` says the run has read; unchanged when
    there is none. With ``destination`` and ``grants`` (the loop), a person may allow one destination for the
    conversation. ``addresses``: the page addresses the run was given; a fetch to any other is refused once a rule
    would hold a send (none: every address is refused then)."""
    if not rules or ledger is None:
        return policy
    for rule in rules:
        log.info("approvals.flow_guard", level=rule.level, above=rule.above)
        policy = FlowGuardedPolicy(
            policy, rule, ledger, run_id, log, destination=destination, grants=grants, addresses=addresses
        )
    return policy


def engine_guarded(
    default: ApprovalPolicy,
    governance: ApprovalPolicy,
    *,
    rules: Sequence[FlowRule],
    ledger: GrantingLedger,
    run_id: str,
    log: HarnessLog,
    addresses: VouchesAddress | None = None,
) -> ApprovalPolicy:
    """An engine run's approvals (UX2): the org's and agent's rules (``governance``) under the flow ``rules``, and
    the engine's safety default (``ENGINE_APPROVALS`` with its profile's). A person may answer the flow rule and
    the default for the conversation, per tool and destination; the org's rules are never granted."""
    guarded = flow_guarded(
        governance,
        rules=rules,
        ledger=ledger,
        run_id=run_id,
        log=log,
        destination=pipeshub_destination,
        grants=ledger.grants,
        addresses=addresses,
    )
    return DefaultAskPolicy(
        guarded,
        default,
        ENGINE_WRITES_RULE,
        ledger,
        run_id,
        destination=pipeshub_destination,
        grants=ledger.grants,
        log=log,
    )


def governed(org_policy: LayeredApprovalPolicy, profile: EngineProfile, ceiling: ApprovalRules) -> LayeredApprovalPolicy:
    """An engine run's governance (QA10-F3): the org's and agent's approval rules, narrowed by what the run's
    engine profile narrows below the release default and by the classes the org's profile ceiling names (the
    pinned profile cannot show a restated release value). Never granted; only ``ENGINE_APPROVALS`` is the
    default."""
    return org_policy.narrowed(profile_rules(profile), "engine").narrowed(ceiling, "org")


__all__ = [
    "UNREADABLE_LAYER",
    "LayerAskAnswers",
    "OrgApprovalRules",
    "PlatformWebRuns",
    "approval_policy",
    "engine_guarded",
    "flow_guarded",
    "governed",
    "layer_ask_answers",
    "org_approval_rules",
]
