"""The flow rule: a call that sends data outside the organization after a run has read private data waits for a
person or is refused.

The lethal trifecta is private data, untrusted content and a way out in one run. The way out is a call whose
arguments leave the organization: a ``write``/``execute`` with an ``external`` sink (every MCP tool, a Slack post, a
Jira create), and a read whose request carries what the model chose to an outside host (an open-world MCP read:
FG2), which egress rules cannot stop because they see the host, not the data in the URL. The platform's own web
search is never held (its query goes only to the configured provider), nor is its page fetch; but once a send would
be held, a fetch opens only an address the run was given, and one only the model wrote is refused (``VouchesAddress``). What the run has read is the session ledger P2.5a already keeps from the
journal (``SourceLedger``): the sources every run of the session recorded and this run's own tool
results, or unknown when anything reached it whose provenance nobody can name. So the rule needs no
store of its own and survives a resume: the journal is the evidence.

The rule is one more layer of the approval policy and, like every layer, it can only narrow.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, Protocol, cast, runtime_checkable

from harness_core.contracts import canonical_digest
from harness_core.effects.policy import Threshold, stricter
from harness_core.effects.redaction import either, redaction_of
from harness_core.effects.types import (
    POLICY_DENIAL,
    ExplainsAsk,
    ExplainsDenial,
    GrantAnswers,
    default_lifted,
    refresh_policy,
    runs_unasked,
)
from harness_core.logs import HarnessLog
from harness_core.security.display_safe import clip_display, display_safe
from harness_core.security.labels import (
    AskSource,
    DescribesSources,
    LabelsContext,
    ReadsUntrusted,
)
from harness_core.security.token_patterns import redact_tokens

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping, Sequence

    from pydantic import JsonValue

    from harness_core.effects.policy import LayerFlow, RuleLayer
    from harness_core.effects.redaction import Redaction
    from harness_core.effects.types import ApprovalDecision, ApprovalPolicy, ToolEffect
    from harness_core.journal.types import JournalEvent
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.security.labels import SourceLedger

Sensitivity = Literal["public", "private", "unknown"]
FlowLevel = Literal["ask", "deny"]

# Unknown ranks above private: content nobody can name may be anything (fail closed).
_RANK: Final[dict[Sensitivity, int]] = {"public": 0, "private": 1, "unknown": 2}
SENSITIVITIES: Final[frozenset[str]] = frozenset(_RANK)
_LEVEL_RANK: Final[dict[FlowLevel, int]] = {"ask": 1, "deny": 2}
FLOW_RULE: Final = "flow_external_write"
# The org's policy webhook asks (G-40): never answered by a conversation grant, never decided in a batch.
EXTERNAL_POLICY_REASON_RULE: Final = "external_policy"
# UX2/UX4: an engine run asks before a write that leaves the user's own space (``ENGINE_APPROVALS``).
ENGINE_WRITES_RULE: Final = "engine_writes"
# The rules ``interaction.explained`` listed before a reason named its layer (QA6-F6).
_UNLAYERED_RULES: Final[frozenset[str]] = frozenset({FLOW_RULE, ENGINE_WRITES_RULE})

# The rules an edition adds to ``interaction.explained`` (its schema overlay's ``HoldRule`` values), each with the
# journal feature a claim names when its control plane routes that rule (``register_edition_rule``). Empty here.
_EDITION_RULES: dict[str, str] = {}
# The edition rules whose reason names who approves (Node routes the decision by it): another ask never replaces it.
_ROUTING_RULES: set[str] = set()


def register_edition_rule(rule: str, feature: str, *, routes_approval: bool = True) -> None:
    """An edition's ask rule: its reason reaches only a journal whose claim names ``feature``. A rule that
    ``routes_approval`` (its reason names who approves) keeps its reason when a webhook's ask holds the same call, as
    another rule never replaces who decides it; one that does not yields to the webhook's, which is outermost. A rule
    this build has, or one registered again differently, is refused."""
    own = {*_UNLAYERED_RULES, EXTERNAL_POLICY_REASON_RULE}
    known = _EDITION_RULES.get(rule)
    if rule in own or (known is not None and (known != feature or (rule in _ROUTING_RULES) != routes_approval)):
        raise ValueError(f"ask rule {rule!r} is already defined")
    _EDITION_RULES[rule] = feature
    if routes_approval:
        _ROUTING_RULES.add(rule)


def edition_rule_features() -> frozenset[str]:
    """The journal features of the edition's ask rules: what a journal names to take every reason this build makes."""
    return frozenset(_EDITION_RULES.values())


def edition_features(accepts: Callable[[str], bool]) -> frozenset[str]:
    """The edition rules' features a claim names (``accepts``: its journal's), for ``reason_for_journal``."""
    return frozenset(feature for feature in _EDITION_RULES.values() if accepts(feature))


def is_edition_reason(reason: Mapping[str, JsonValue] | None) -> bool:
    """Whether ``reason`` is an edition rule's."""
    return reason is not None and reason.get("rule") in _EDITION_RULES


def routes_approval(reason: Mapping[str, JsonValue] | None) -> bool:
    """Whether ``reason`` is an edition rule's that names who approves, which another ask's reason never replaces."""
    return reason is not None and reason.get("rule") in _ROUTING_RULES
_OWN_SPACE: Final[frozenset[str]] = frozenset({"none", "self"})
FLOW_DENIAL: Final = (
    FLOW_RULE,
    "denied: this run has read private data, and this tool would send it outside the organization",
)
# A page fetch the flow rule would hold, to an address the run was not given (``unvouched_address``): refused, never
# asked, so the model reads why and goes on (a search, or a link it was given) and nobody gets a card for it.
WEB_ADDRESS_RULE: Final = "web_address"
WEB_ADDRESS_DENIAL: Final = (
    WEB_ADDRESS_RULE,
    "not_opened: this address did not come from a search result or from the user, so it was not opened after this "
    "conversation read private content. Search for the page, or open a link from a search result or one the user "
    "gave; do not retry this address.",
)


# The card names this many sources and counts the rest: enough to recognise, short enough to read.
MAX_ASK_SOURCES: Final = 10
EXPLAINED_EVENT: Final = "interaction.explained"
GRANTED_EVENT: Final = "interaction.granted"
# ADR-GRANT-01 D8: the conversation's owner took a grant back; from this event on it answers nothing.
REVOKED_EVENT: Final = "interaction.grant_revoked"
CONVERSATION_SCOPE: Final = "conversation"
MAX_TITLE_CHARS: Final = 512
MAX_URL_CHARS: Final = 2048
# The most grants a child's spec pins (``x.pipeshub.inheritedGrants``).
MAX_INHERITED_GRANTS: Final = 256
_ID: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
# A grant journaled or pinned before grants named the data they were given for held at any sensitivity; it still does,
# so an upgrade asks no one again and takes nothing away. That is the widest a grant reaches (``_GRANT_RANK``): private,
# not unknown, which ranks with public.
LEGACY_GRANT_CEILING: Final[Sensitivity] = "private"
# One grant: the tool, the destination's key, and the most sensitive data it was given for.
HeldGrant = tuple[str, str, Sensitivity]
# One ask on a stream: the run that asked it and its interaction.
_Ask = tuple[str | None, str]
# How far a grant reaches (FLOWGRANT): one given while the run had read nothing private never covers private data it
# has read since. Content nobody can name raises no grant: the classification extension point may hold it at its highest
# level, which no grant answers (OWD-15), and a write's own reply is such content, so ranking it would re-ask right after
# every grant.
_GRANT_RANK: Final[dict[Sensitivity, int]] = {"public": 0, "private": 1, "unknown": 0}


@dataclass(frozen=True, slots=True)
class AskDestination:
    """Where an external write goes, as a person names it (``#sales``, an address); ``key`` identifies it for a
    conversation-wide grant and is never shown."""

    label: str
    key: str


def destination_key(tool_id: str, value: str) -> str:
    return canonical_digest({"toolId": tool_id, "destination": value.strip().lower()})


@dataclass(frozen=True, slots=True)
class FlowAsk:
    """Why the flow rule holds a call for a person (UX1): what the run read, where the call would send it, and
    whether "allow for this conversation" may be offered (an ``ask`` over a call nothing else holds, to a
    destination the host can name)."""

    tool_id: str
    sensitivity: Sensitivity
    sources: tuple[AskSource, ...]
    more_sources: int
    destination: AskDestination | None
    conversation_grant: bool
    rule: str = FLOW_RULE
    layer: RuleLayer = "flow"

    def to_wire(self) -> dict[str, JsonValue]:
        """Journaled for whoever decides, who may not be the run's owner: text from the call or the sources is
        masked by the shared token vector before it is clipped, so a cut cannot leave half a secret."""
        wire: dict[str, JsonValue] = {
            "rule": self.rule,
            "layer": self.layer,
            "toolId": self.tool_id,
            "sensitivity": self.sensitivity,
            "sources": [_source_wire(s) for s in self.sources],
            "moreSources": self.more_sources,
            "conversationGrant": self.conversation_grant,
        }
        if self.destination is not None:
            wire["destination"] = destination_wire(self.destination)
        return wire


def destination_wire(where: AskDestination) -> dict[str, JsonValue]:
    """A destination as the card shows it (masked, display-safe, clipped) with the key a conversation grant names.
    Every destination source passes here, so none shows a bidi control or a homoglyph host (SEC-B-04)."""
    return {"label": clip_display(display_safe(redact_tokens(where.label)), MAX_TITLE_CHARS), "key": where.key}


def grant_answerable(policy: ApprovalPolicy, call: ToolCall, effect: ToolEffect) -> bool:
    """Whether ``policy`` lets the call run once a person allows it for the conversation: it allows it, or it asks
    only by a default such a grant answers (SYNC4: main's write default). An admin's own ask never is."""
    decision = policy(call, effect)
    return runs_unasked(decision) or (
        decision == "ask" and isinstance(policy, GrantAnswers) and policy.grant_answers(call, effect)
    )


def reason_for_journal(
    reason: Mapping[str, JsonValue],
    *,
    layers: bool,
    rule_grants: bool = False,
    edition_features: frozenset[str] = frozenset(),
    edits: bool = False,
    external_policy: bool = False,
    edit_order: bool = False,
    args_digest: bool = False,
) -> Mapping[str, JsonValue] | None:
    """``reason`` as the journal of a claim accepts it (QA6-F6). One that names rule layers takes it whole. An older
    one refuses a field or a rule its schema does not list, and the commit with it: it gets the two rules it knows
    without their layer, and nothing for any other rule (``None``: the card shows the call alone, as before).
    ``ruleGrant`` ("Don't ask again", BUGS-B) only reaches a journal that accepts it, and an edition's rule only one
    whose claim names its feature (``edition_features``): elsewhere the card shows the call alone. The edit
    offer (``editable``, AP-06) only reaches a journal that accepts an edited approval, and the tool's argument order
with it (``fieldOrder``, WP-56) only one that also names ``approval_edit_order``. The policy webhook's rule
    (G-40) only reaches a journal that names it; elsewhere the card shows the call alone, as before it was named. The call's ``argsDigest`` (R38-AUTHZ) only reaches a journal that names
    ``approval_args_digest``."""
    edition_feature = _EDITION_RULES.get(str(reason.get("rule")))
    if edition_feature is not None and edition_feature not in edition_features:
        return None
    if reason.get("rule") == EXTERNAL_POLICY_REASON_RULE and not external_policy:
        return None
    if not rule_grants and "ruleGrant" in reason:
        reason = {key: value for key, value in reason.items() if key != "ruleGrant"}
    if not edits and "editable" in reason:
        reason = {key: value for key, value in reason.items() if key != "editable"}
    # The tool's argument order (WP-56) rides on the edit offer, to a journal that names it.
    if not (edits and edit_order) and "fieldOrder" in reason:
        reason = {key: value for key, value in reason.items() if key != "fieldOrder"}
    if not args_digest and "argsDigest" in reason:
        reason = {key: value for key, value in reason.items() if key != "argsDigest"}
    if layers:
        return reason
    if reason.get("rule") not in _UNLAYERED_RULES:
        return None
    return {key: value for key, value in reason.items() if key != "layer"}


def _source_wire(source: AskSource) -> dict[str, JsonValue]:
    """Bounded to the event's schema: a value that would not fit is left out, never the whole commit refused."""
    wire: dict[str, JsonValue] = {}
    if source.title and source.title.strip():
        wire["title"] = clip_display(display_safe(redact_tokens(source.title.strip())), MAX_TITLE_CHARS)
    if source.record_id and _ID.match(source.record_id):
        wire["recordId"] = source.record_id
    if source.url and len(source.url) <= MAX_URL_CHARS and source.url.startswith(("https://", "http://")):
        wire["url"] = source.url
    return wire


class ConversationGrants:
    """The "allow for this conversation" decisions a run holds (FLOWGRANT, ADR-GRANT-01). A grant is one tool, one
    destination and the most sensitive data it was given for: it answers a later ask for the same tool and destination
    with the same or less sensitive data, and nothing else. It reaches a run three ways, all rebuilt from the journal
    so a resume keeps them:

    - on the run's own stream, the worker's offer (``interaction.explained``), a person's approval
      (``interaction.resolved``) and Node's scope (``interaction.granted``), paired by interaction;
    - a node ``interaction.granted`` that names its grant (``toolId``, ``key``, ``sensitivity``): Node writes one on
      every open run of the conversation when a person allows a call in any of them, from the worker's own offer;
    - what the child's admission pinned of its parent's (``inherited``).

    A node ``interaction.grant_revoked`` (ADR-GRANT-01 D8) takes the grant for its tool and destination away, however
    it reached the run; a later grant holds again.

    ``principal`` (R38-AUTHZ, SEC-B-01): the run's actor. A grant counts only for runs of the person who gave it: in a
    shared conversation the root runs share one stream, so the run of one participant reads every other's decisions.
    A grant or approval naming another ``by`` (or one that is not a principal ref) is ignored; one naming none was
    written before grants named their giver and stays the run's own. A revocation counts whoever gave the grant: the
    conversation's owner may take any back."""

    def __init__(
        self,
        inherited: Iterable[HeldGrant | tuple[str, str]] = (),
        *,
        enabled: bool = True,
        principal: str | None = None,
    ) -> None:
        self._seq = 0
        # C4.10 ``interactions.conversationGrants``: off, nothing is offered, held or inherited, so every call asks.
        self.enabled = enabled
        self.principal = principal
        self._held: dict[tuple[str, str], Sensitivity] = {}
        if enabled:
            for grant in inherited:
                tool_id, key = grant[0], grant[1]
                self._hold(tool_id, key, grant[2] if len(grant) == 3 else LEGACY_GRANT_CEILING)
        # Keyed by (run, interaction): two runs on a shared stream may name the same interaction, and one run's
        # approval must never pair with another's offer.
        self._offered: dict[_Ask, HeldGrant] = {}
        self._granted: set[_Ask] = set()
        self._approved: set[_Ask] = set()
        # Approved by anyone: an offer some participant answered is no longer awaited.
        self._answered: set[_Ask] = set()

    def _own(self, data: Mapping[str, JsonValue]) -> bool:
        """Whether a decision is this run's actor's: it names them, or names nobody (written before ``by``)."""
        if self.principal is None or "by" not in data:
            return True
        return data["by"] == self.principal

    def _hold(self, tool_id: str, key: str, ceiling: Sensitivity) -> None:
        held = self._held.get((tool_id, key))
        if held is None or _GRANT_RANK[ceiling] > _GRANT_RANK[held]:
            self._held[(tool_id, key)] = ceiling

    def _pair(self, ix: _Ask) -> None:
        offered = self._offered.get(ix)
        if offered is not None and ix in self._granted and ix in self._approved:
            self._hold(*offered)

    def apply(self, events: Sequence[JournalEvent]) -> None:
        for event in events:
            if event.seq <= self._seq:
                continue
            self._seq = event.seq
            if event.type == REVOKED_EVENT and event.writer == "node":
                self._revoke(event.data)
                continue
            interaction = event.data.get("interactionId")
            if not isinstance(interaction, str):
                continue
            ix = (event.run_id, interaction)
            if event.type == EXPLAINED_EVENT and event.writer == "worker":
                offered = _offer(event.data)
                if offered is not None:
                    self._offered[ix] = offered
            elif event.type == GRANTED_EVENT and event.writer == "node":
                if event.data.get("scope") != CONVERSATION_SCOPE or not self._own(event.data):
                    continue
                named = _named_grant(event.data)
                if named is not None and self.enabled:
                    self._hold(*named)
                # A copy of another run's grant answers none of this run's asks, whatever their ids.
                if not isinstance(event.data.get("fromRunId"), str):
                    self._granted.add(ix)
            elif event.type == "interaction.resolved" and event.data.get("decision") == "approved":
                self._answered.add(ix)
                if not self._own(event.data):
                    continue
                self._approved.add(ix)
            else:
                continue
            if self.enabled:
                self._pair(ix)

    def _revoke(self, data: Mapping[str, JsonValue]) -> None:
        tool_id, key = data.get("toolId"), data.get("key")
        if isinstance(tool_id, str) and isinstance(key, str):
            self._held.pop((tool_id, key), None)
            # An ask already paired stays answered, but its offer no longer re-pairs into a grant on a later event.
            for ix, offered in list(self._offered.items()):
                if offered[0] == tool_id and offered[1] == key and ix in self._granted:
                    self._granted.discard(ix)

    def allows(self, tool_id: str, key: str, sensitivity: Sensitivity = "public") -> bool:
        """Whether a person allowed ``tool_id`` to ``key`` for the conversation with data at least as sensitive as
        ``sensitivity``. The default suits an ask that does not depend on what the run read (an admin's Ask, a clone):
        any grant for the tool and destination answers it."""
        if not self.enabled:
            return False
        ceiling = self._held.get((tool_id, key))
        return ceiling is not None and _GRANT_RANK[sensitivity] <= _GRANT_RANK[ceiling]

    def held(self) -> frozenset[tuple[str, str]]:
        """Every (tool, destination key) the conversation allows, whatever the data."""
        return frozenset(self._held) if self.enabled else frozenset()

    def held_grants(self) -> frozenset[HeldGrant]:
        """Every grant with its ceiling: what a child this run asks for inherits."""
        if not self.enabled:
            return frozenset()
        return frozenset((tool_id, key, ceiling) for (tool_id, key), ceiling in self._held.items())

    def awaiting(self) -> bool:
        """An offer nobody has answered yet: a grant may arrive on the stream at any moment."""
        return any(ix not in self._answered for ix in self._offered)


def grants_offered(ledger: object) -> bool:
    """Whether a card may offer "allow for this conversation" on ``ledger``'s run: not when the run's policy turned
    conversation grants off (C4.10)."""
    grants = getattr(ledger, "grants", None)
    return not isinstance(grants, ConversationGrants) or grants.enabled


def grants_wire(held: Iterable[HeldGrant]) -> list[JsonValue]:
    """Grants as a child's spec pins them (``x.pipeshub.inheritedGrants``), sorted so the same set is the same
    document, and at most ``MAX_INHERITED_GRANTS``: a grant left out is asked about again, never assumed."""
    return [
        {"toolId": tool_id, "key": key, "sensitivity": ceiling}
        for tool_id, key, ceiling in sorted(held)[:MAX_INHERITED_GRANTS]
    ]


def grants_from_wire(value: object) -> frozenset[HeldGrant]:
    """A pinned grant list; anything unreadable in it grants nothing. A pin from before grants named their ceiling
    holds as it did then (``LEGACY_GRANT_CEILING``)."""
    if not isinstance(value, list):
        return frozenset()
    held: set[HeldGrant] = set()
    for item in cast("list[object]", value):
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        tool_id, key, ceiling = entry.get("toolId"), entry.get("key"), entry.get("sensitivity", LEGACY_GRANT_CEILING)
        if isinstance(tool_id, str) and isinstance(key, str) and tool_id and key and ceiling in _RANK:
            held.add((tool_id, key, cast("Sensitivity", ceiling)))
    return frozenset(held)


def _ceiling(value: object) -> Sensitivity:
    """The sensitivity a grant was given for; one written before grants named it holds as it did then."""
    return cast("Sensitivity", value) if value in _RANK else LEGACY_GRANT_CEILING


def _offer(data: Mapping[str, JsonValue]) -> HeldGrant | None:
    destination, tool_id = data.get("destination"), data.get("toolId")
    if data.get("conversationGrant") is not True or not isinstance(destination, dict) or not isinstance(tool_id, str):
        return None
    key = destination.get("key")
    return (tool_id, key, _ceiling(data.get("sensitivity"))) if isinstance(key, str) else None


def _named_grant(data: Mapping[str, JsonValue]) -> HeldGrant | None:
    """The grant a node ``interaction.granted`` names itself (FLOWGRANT); ``None`` for one that names only its ask."""
    tool_id, key = data.get("toolId"), data.get("key")
    if not isinstance(tool_id, str) or not isinstance(key, str) or not tool_id or not key:
        return None
    return (tool_id, key, _ceiling(data.get("sensitivity")))


@runtime_checkable
class GrantsContext(Protocol):
    """A ``SourceLedger`` facet: the conversation grants a child this run asks for inherits, in their wire form."""

    def context_grants(self) -> list[JsonValue]: ...


@dataclass
class GrantingLedger:
    """A ``SourceLedger`` that also keeps the session's conversation grants from the events the kernel feeds it."""

    inner: SourceLedger
    grants: ConversationGrants = field(default_factory=ConversationGrants)

    def apply(self, events: Sequence[JournalEvent]) -> None:
        self.inner.apply(events)
        self.grants.apply(events)

    def source_records(self, run_id: str) -> frozenset[str] | None:
        return self.inner.source_records(run_id)

    def describe_sources(self, run_id: str) -> tuple[AskSource, ...]:
        return self.inner.describe_sources(run_id) if isinstance(self.inner, DescribesSources) else ()

    def context_label(self, run_id: str) -> dict[str, JsonValue] | None:
        return self.inner.context_label(run_id) if isinstance(self.inner, LabelsContext) else None

    def context_grants(self) -> list[JsonValue]:
        return grants_wire(self.grants.held_grants())

    def untrusted_read(self, run_id: str) -> bool:
        return isinstance(self.inner, ReadsUntrusted) and self.inner.untrusted_read(run_id) is True


def sensitivity_of(records: frozenset[str] | None) -> Sensitivity:
    """The ledger's answer for a run: no sources is public, named sources private, ``None`` unknown."""
    if records is None:
        return "unknown"
    return "private" if records else "public"


def external_write(effect: ToolEffect) -> bool:
    return effect.needs_intent_commit and effect.sink.egress == "external"


def sends_out(effect: ToolEffect) -> bool:
    """The call's arguments leave the organization, whatever its effect: an external write, or a read whose request
    goes to an outside host (FG2). The flow rule holds these, the platform's own web reads aside (``flow_holds``)."""
    return effect.sink.egress == "external" and effect.effect != "workspace"


def flow_holds(effect: ToolEffect) -> bool:
    """What the flow rule holds after a private read: a call that sends out (``sends_out``), except the platform's own
    public-web read (``ToolEffect.platform_web``). Web search and page fetch are never put to a person: their safety is
    in the tools (secret redaction, private-address and internal-URL refusal, outsider-written labelling) and, for a
    fetch, in opening only an address the run was given (``unvouched_address``); what they bring back still marks the
    run as having read outsider-written content, which holds a later send."""
    return sends_out(effect) and not effect.platform_web


@runtime_checkable
class VouchesAddress(Protocol):
    """Whether the address a call opens is one the run was given: a search result, the person's own message or file,
    or a record's link; never one only the model wrote, which may carry what the run read."""

    def vouched(self, call: ToolCall, effect: ToolEffect) -> bool: ...


def unvouched_address(call: ToolCall, effect: ToolEffect, addresses: VouchesAddress | None) -> bool:
    """A platform page fetch (``ToolEffect.opens_address``) to an address the run was not given. With nothing to vouch
    for addresses, or one that fails, every address is unvouched (fail closed)."""
    if not effect.opens_address:
        return False
    if addresses is None:
        return True
    try:
        return not addresses.vouched(call, effect)
    except Exception:
        # An address nobody could check is not vouched for.
        return True


def leaves_own_space(effect: ToolEffect) -> bool:
    """A write whose arguments reach someone other than the acting user: other people in the organization
    (``internal_audience``) or anything outside it. An untagged or unreadable sink is ``external`` (UX1)."""
    return effect.needs_intent_commit and effect.sink.egress not in _OWN_SPACE


@dataclass(frozen=True, slots=True)
class FlowRule:
    """``level`` for a call that sends data out (``flow_holds``) once the run's context is above ``above``."""

    level: FlowLevel
    above: Threshold = "public"

    def decide(self, sensitivity: Sensitivity, effect: ToolEffect, *, untrusted: bool = False) -> ApprovalDecision:
        """``untrusted``: content an outsider could write reached the run (``untrusted_content_read``). With a context
        that is not public it is the Rule of Two: a person sees the call whatever the threshold, since that content
        may be what asked for the send."""
        if not flow_holds(effect):
            return "allow"
        if _RANK[sensitivity] > _RANK[self.above]:
            return self.level
        return "ask" if untrusted and sensitivity != "public" else "allow"

    def holds(self, sensitivity: Sensitivity, *, untrusted: bool = False) -> bool:
        """Whether this rule would hold a send now (ask or refuse it), as ``decide`` decides one."""
        return _RANK[sensitivity] > _RANK[self.above] or (untrusted and sensitivity != "public")


def holds_sends(rules: Iterable[FlowRule], ledger: SourceLedger, run_id: str) -> bool:
    """Whether any of ``rules`` would hold a call that sends data out of the run now (ask or refuse it): what a
    send through an allowed website is held to (WP-63), decided as ``FlowRule.decide`` decides a send."""
    sensitivity = sensitivity_of(ledger.source_records(run_id))
    untrusted = isinstance(ledger, ReadsUntrusted) and ledger.untrusted_read(run_id) is True
    return any(rule.holds(sensitivity, untrusted=untrusted) for rule in rules)


def layer_rule(flow: LayerFlow) -> FlowRule | None:
    """A configured layer's rule; ``off`` is no rule, never a rule that allows."""
    return None if flow.level == "off" else FlowRule(flow.level, flow.above)


def covers(a: FlowRule, b: FlowRule) -> bool:
    return _LEVEL_RANK[a.level] >= _LEVEL_RANK[b.level] and _RANK[a.above] <= _RANK[b.above]


def strictest_flows(rules: Iterable[FlowRule | None]) -> tuple[FlowRule, ...]:
    """The rules whose union decides as the strictest of all of them: each rule another one covers (as strict, from
    as low a threshold) is dropped. At most two remain, ``ask`` from the lower threshold and ``deny`` from the higher,
    which no single rule can say; applied one over another they narrow exactly as the set does."""
    given = {rule for rule in rules if rule is not None}
    kept = [rule for rule in given if not any(other != rule and covers(other, rule) for other in given)]
    return tuple(sorted(kept, key=lambda rule: (_RANK[rule.above], _LEVEL_RANK[rule.level])))


@dataclass
class FlowGuardedPolicy:
    """``base`` narrowed by ``rule`` over what ``ledger`` says ``run_id`` has read; an ``ask`` the session's
    ``grants`` already answered for the call's destination is allowed. The platform's own web reads are never held;
    once the rule would hold a send, a page fetch to an address ``addresses`` does not vouch for is refused."""

    base: ApprovalPolicy
    rule: FlowRule
    ledger: SourceLedger
    run_id: str
    log: HarnessLog = field(default_factory=lambda: HarnessLog.named("harness_core.effects.flow"))
    destination: Callable[[ToolCall, ToolEffect], AskDestination | None] | None = None
    grants: ConversationGrants | None = None
    addresses: VouchesAddress | None = None

    def _sensitivity(self) -> Sensitivity:
        return sensitivity_of(self.ledger.source_records(self.run_id))

    def _untrusted(self) -> bool:
        return isinstance(self.ledger, ReadsUntrusted) and self.ledger.untrusted_read(self.run_id) is True

    def _destination(self, call: ToolCall, effect: ToolEffect) -> AskDestination | None:
        return self.destination(call, effect) if self.destination is not None else None

    def _refuses_address(self, call: ToolCall, effect: ToolEffect) -> bool:
        return unvouched_address(call, effect, self.addresses) and self.rule.holds(
            self._sensitivity(), untrusted=self._untrusted()
        )

    def _flow(self, call: ToolCall, effect: ToolEffect, *, log: bool = True) -> ApprovalDecision:
        if not flow_holds(effect):
            if not self._refuses_address(call, effect):
                return "allow"
            if log:
                self.log.info("effects.web_address_refused", toolId=effect.tool_id, rule=WEB_ADDRESS_RULE)
            return "deny"
        sensitivity = self._sensitivity()
        untrusted = self._untrusted()
        decision = self.rule.decide(sensitivity, effect, untrusted=untrusted)
        if decision == "ask" and self.grants is not None:
            where = self._destination(call, effect)
            if where is not None and self.grants.allows(effect.tool_id, where.key, sensitivity):
                if log:
                    self.log.info("effects.flow_granted", toolId=effect.tool_id, sensitivity=sensitivity, rule=FLOW_RULE)
                return "allow"
        if decision != "allow" and log:
            self.log.info(
                "effects.flow_narrowed",
                toolId=effect.tool_id,
                sensitivity=sensitivity,
                untrusted=untrusted,
                decision=decision,
                rule=FLOW_RULE,
            )
        return decision

    async def refresh(self) -> None:
        await refresh_policy(self.base)

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return stricter(self.base(call, effect), self._flow(call, effect))

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None:
        """This rule's reason when it is what asks; otherwise the base policy's, if it gives one."""
        where = self._destination(call, effect)
        if self._flow(call, effect, log=False) != "ask":
            reason = self.base.ask_reason(call, effect) if isinstance(self.base, ExplainsAsk) else None
            return in_context(reason, effect, self.ledger, self.run_id, where) if reason else None
        return explained_ask(
            FLOW_RULE, effect, self.ledger, self.run_id, where, grantable=grant_answerable(self.base, call, effect)
        )

    def grant_answers(self, call: ToolCall, effect: ToolEffect) -> bool:
        return self._flow(call, effect, log=False) != "deny" and grant_answerable(self.base, call, effect)

    def lifts_default(self, call: ToolCall, effect: ToolEffect) -> bool:
        return default_lifted(self.base, call, effect)

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None:
        return redaction_of(self.base, call, effect)

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        if self.base(call, effect) == "deny":
            return self.base.denial(call, effect) if isinstance(self.base, ExplainsDenial) else POLICY_DENIAL
        return WEB_ADDRESS_DENIAL if self._refuses_address(call, effect) else FLOW_DENIAL


def explained_ask(
    rule: str,
    effect: ToolEffect,
    ledger: SourceLedger,
    run_id: str,
    where: AskDestination | None,
    *,
    grantable: bool,
    layer: RuleLayer = "flow",
) -> dict[str, JsonValue]:
    """The reason a person reads on the card (``interaction.explained``): the rule and whose it is, what the run
    read, where the call goes, and whether this tool and destination may be allowed for the conversation."""
    described = ledger.describe_sources(run_id) if isinstance(ledger, DescribesSources) else ()
    return FlowAsk(
        tool_id=effect.tool_id,
        sensitivity=sensitivity_of(ledger.source_records(run_id)),
        sources=described[:MAX_ASK_SOURCES],
        more_sources=max(0, len(described) - MAX_ASK_SOURCES),
        destination=where,
        conversation_grant=grantable and where is not None and grants_offered(ledger),
        rule=rule,
        layer=layer,
    ).to_wire()


def in_context(
    reason: Mapping[str, JsonValue],
    effect: ToolEffect,
    ledger: SourceLedger,
    run_id: str,
    where: AskDestination | None,
) -> Mapping[str, JsonValue]:
    """A base policy's reason with what a policy that reads the run adds for the person deciding: what the run
    read and where the call goes (QA6-F6). The rule, its layer and the grant stay the base's; a reason that already
    says what the run read is left as it is."""
    if "sensitivity" in reason:
        return reason
    context = explained_ask(str(reason["rule"]), effect, ledger, run_id, where, grantable=False)
    return {**context, **reason}


@dataclass
class DefaultAskPolicy:
    """``base`` narrowed by a host's safety default that asks (an engine run's ``ENGINE_APPROVALS`` and its profile),
    except for a call whose own specific rule in ``base`` allows it (``LiftsDefault``, D-ALLOW-1).
    The default asks only before a write that leaves the user's own space (UX4): a draft in their own mailbox needs no
    person's word, and asking there trains people to approve without reading. It is not an admin's rule: an org that
    wants every call reviewed says so in its own rules, which are in ``base``, apply to every sink and are never
    granted. A person may answer the default for the rest of the conversation, per tool and destination, with the
    same grants that answer the flow rule; a ``deny`` in the default stays a ``deny`` whatever the sink."""

    base: ApprovalPolicy
    default: ApprovalPolicy
    rule: str
    ledger: SourceLedger
    run_id: str
    destination: Callable[[ToolCall, ToolEffect], AskDestination | None] | None = None
    grants: ConversationGrants | None = None
    log: HarnessLog = field(default_factory=lambda: HarnessLog.named("harness_core.effects.flow"))
    layer: RuleLayer = "engine"

    def _where(self, call: ToolCall, effect: ToolEffect) -> AskDestination | None:
        return self.destination(call, effect) if self.destination is not None else None

    def _default(self, call: ToolCall, effect: ToolEffect, *, log: bool = True) -> ApprovalDecision:
        decision = self.default(call, effect)
        if decision == "ask" and not leaves_own_space(effect):
            if log:
                self.log.info("effects.default_own_space", toolId=effect.tool_id, egress=effect.sink.egress, rule=self.rule)
            return "allow"
        # D-ALLOW-1: the call's own specific Allow (a person's or an admin's, for this tool) lifts this class default.
        # Only an ask: the base's decision still holds everything stricter (a block, the flow guard, an org's ask).
        if decision == "ask" and default_lifted(self.base, call, effect):
            if log:
                self.log.info("effects.default_lifted", toolId=effect.tool_id, rule=self.rule)
            return "allow"
        if decision == "ask" and self.grants is not None:
            where = self._where(call, effect)
            # The default asks before a write whatever the run read, so any grant for the place answers it; what the run
            # read is the flow rule's to weigh, with the grant's own sensitivity (FLOWGRANT).
            if where is not None and self.grants.allows(effect.tool_id, where.key):
                if log:
                    self.log.info("effects.default_granted", toolId=effect.tool_id, rule=self.rule)
                return "allow"
        return decision

    async def refresh(self) -> None:
        await refresh_policy(self.base)
        await refresh_policy(self.default)

    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return stricter(self.base(call, effect), self._default(call, effect))

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None:
        """The base's reason when it asks and gives one (the flow rule names what the run read); otherwise this
        default's, whenever it is among what asks."""
        base = self.base(call, effect)
        where = self._where(call, effect)
        if base == "ask" and isinstance(self.base, ExplainsAsk):
            reason = self.base.ask_reason(call, effect)
            if reason:
                return in_context(reason, effect, self.ledger, self.run_id, where)
        if self._default(call, effect, log=False) != "ask":
            return None
        return explained_ask(
            self.rule, effect, self.ledger, self.run_id, where, grantable=grant_answerable(self.base, call, effect),
            layer=self.layer,
        )

    def grant_answers(self, call: ToolCall, effect: ToolEffect) -> bool:
        return self._default(call, effect, log=False) != "deny" and grant_answerable(self.base, call, effect)

    def redaction(self, call: ToolCall, effect: ToolEffect) -> Redaction | None:
        return either(redaction_of(self.base, call, effect), redaction_of(self.default, call, effect))

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]:
        if self.base(call, effect) == "deny" and isinstance(self.base, ExplainsDenial):
            return self.base.denial(call, effect)
        return POLICY_DENIAL


__all__ = [
    "destination_wire",
    "edition_features",
    "edition_rule_features",
    "is_edition_reason",
    "routes_approval",
    "register_edition_rule",
    "grant_answerable",
    "CONVERSATION_SCOPE",
    "ENGINE_WRITES_RULE",
    "EXPLAINED_EVENT",
    "FLOW_DENIAL",
    "FLOW_RULE",
    "WEB_ADDRESS_DENIAL",
    "WEB_ADDRESS_RULE",
    "VouchesAddress",
    "unvouched_address",
    "GRANTED_EVENT",
    "REVOKED_EVENT",
    "MAX_ASK_SOURCES",
    "AskDestination",
    "ConversationGrants",
    "DefaultAskPolicy",
    "FlowAsk",
    "FlowGuardedPolicy",
    "GrantingLedger",
    "HeldGrant",
    "LEGACY_GRANT_CEILING",
    "GrantsContext",
    "MAX_INHERITED_GRANTS",
    "destination_key",
    "grants_from_wire",
    "grants_wire",
    "holds_sends",
    "FlowLevel",
    "FlowRule",
    "SENSITIVITIES",
    "Sensitivity",
    "Threshold",
    "explained_ask",
    "external_write",
    "layer_rule",
    "leaves_own_space",
    "flow_holds",
    "sends_out",
    "sensitivity_of",
    "covers",
    "strictest_flows",
]
