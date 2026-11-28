"""Value types of a tool call's effect: its class, its context, its outcome and its key.

``ToolEffect``, ``ToolContext``, ``ToolExecution``, ``EffectUnknown`` and the
approval types moved here from ``harness_core.kernel.durable`` (F1), which
re-exports them: the effect layer sits below the kernel, so it must not import it.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Final, Literal, Protocol, runtime_checkable

from pydantic import JsonValue

from harness_core.contracts import canonical_digest
from harness_core.contracts.generated.enums import ApprovalLevel, Effect, ToolErrorCode
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.risk import risk_of

if TYPE_CHECKING:
    from collections.abc import Mapping

    from harness_core.effects.redaction import Redaction
    from harness_core.effects.risk import RiskClass, RiskMark
    from harness_core.journal.types import NewEvent
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.provenance.types import ResultProvenance

EffectKind = Effect

_SOURCE = re.compile(r"[^a-z0-9_-]")
_NAME = re.compile(r"[^A-Za-z0-9_.-]")


@dataclass(frozen=True)
class ToolEffect:
    tool_id: str
    effect: EffectKind
    idempotent: bool
    sink: SinkDescriptor
    # The tool cannot be undone or moves money, as its metadata says (``harness_core.effects.risk``).
    mark: RiskMark | None = None
    # The platform's own public-web read (``TAG_FLOW_PLATFORM_WEB``): the flow rule does not hold it.
    platform_web: bool = False
    # Of those, one that opens an address its call names (``TAG_FLOW_OPENS_ADDRESS``): only an address the run was
    # given runs after a private read.
    opens_address: bool = False

    @property
    def risk(self) -> RiskClass:
        """The risk class (OWD-7): ``read``, ``write_internal``, ``write_external``, ``destructive``, ``financial``."""
        return risk_of(self, self.mark)

    @property
    def needs_intent_commit(self) -> bool:
        """``write``/``execute`` intents are their own fenced commit before the effect."""
        return self.effect in ("write", "execute")


def tool_id_for(name: str, *, source: str = "local", instance: str = "default") -> str:
    source = _SOURCE.sub("-", source.lower())[:64] or "local"
    if not source[0].isalnum():
        source = "t" + source[:63]
    return f"{source}@{_NAME.sub('-', instance)[:128] or 'default'}/{_NAME.sub('-', name)[:128]}"


UNKNOWN_TOOL = ToolEffect(
    tool_id=tool_id_for("unknown"),
    effect="write",
    idempotent=False,
    sink=SinkDescriptor(egress="external", args_leave_boundary=True),
)


@dataclass(frozen=True)
class ToolContext:
    run_id: str
    org_id: str
    invocation_id: str
    attempt_id: str
    idempotency_key: str
    # The text of the model response that made the call (a terminal tool
    # answers with it when its own answer is empty).
    turn_text: str = ""
    # The approval a person gave this call, when it was asked: a tool whose host re-checks the decision in the run's
    # journal (ADR-SCH-01 D3) names it, since an edited call (AP-06) no longer hashes to its interaction.
    interaction_id: str | None = None
    # The digest of the arguments the model called with, which that interaction is keyed on: an approved edit runs
    # other arguments under the same identity.
    args_digest: str | None = None
    # The ``name`` the model's ``call_tool`` named, when the call came through it: ``args_digest`` is then the digest of
    # that wrapper ``{name, arguments}``, not of the arguments the tool runs with. None: called directly.
    called_as: str | None = None


@dataclass(frozen=True)
class ToolExecution:
    """A definite outcome. Raise ``EffectUnknown`` when the effect may have happened."""

    status: Literal["ok", "error", "timeout"]
    content: JsonValue
    receipt: dict[str, JsonValue] | None = None
    integrity: Literal["trusted", "internal", "external"] = "internal"
    # A question to the user: the run completes with ``needs_input`` after
    # this batch instead of calling the model again (REPORT 5.2).
    needs_input: bool = False
    # Non-critical extension events (how the result is shown) journaled with
    # the outcome; the fold ignores them.
    events: tuple[NewEvent, ...] = ()
    # Loop state the call changed (e.g. which tools are disclosed), journaled
    # as slots with the outcome. Values are snapshots: within one batch the
    # call that finished last wins.
    slots: Mapping[str, JsonValue] = field(default_factory=dict[str, JsonValue])
    # A terminal tool's answer: the run completes with it after this batch.
    answer: str | None = None
    # The confidence the terminal tool reported with its answer.
    confidence: str | None = None
    # Journaled with the result and restored on the tool message (``artifact_meta``).
    meta: dict[str, JsonValue] | None = None
    # ``content`` is the tool message's parts (text and images), as dicts.
    parts: bool = False
    # Why a failed call failed, as the person reads it; ``content`` stays the detail the model reads (ERR-UX).
    error_code: ToolErrorCode | None = None
    # A ``rate_limited`` failure: how long the service asked to wait. Not journaled: it is only true for a while.
    retry_after_s: float | None = None
    # A dry run's call that was never made (G-03): journaled as ``tool.outcome.shadow``.
    shadow: ShadowMark | None = None
    # The host tool's own word on what its result is (ADR-RUN-01 D3): a public repository fetched with no credential
    # from a public address (WP-83). Read only by a tool declared with a ``HostClaim`` rule; public readers are also
    # journaled as ``labels.confidentiality`` with public readers and no sources. Only host code sets it.
    provenance: ResultProvenance | None = None

    @property
    def public(self) -> bool:
        """The result carries only what anyone may read, by the host's own claim."""
        return self.provenance is not None and self.provenance.readers == "public"


@dataclass(frozen=True)
class ShadowMark:
    """What a dry run recorded instead of making a call: whether, outside a dry run, it would have asked a person."""

    would_ask: bool = False


class EffectUnknown(Exception):
    """The call may or may not have taken effect (lost ack, timeout after send)."""


# A policy's decision (OWD-9): a configured rule's level, or ``allow_with_redaction``, which only a policy that
# names its ``Redaction`` (``harness_core.effects.redaction``) decides; the call runs without a person and what it
# returns is redacted before the model reads it.
ApprovalDecision = ApprovalLevel | Literal["allow_with_redaction"]
ALLOW_WITH_REDACTION: Final = "allow_with_redaction"


def runs_unasked(decision: ApprovalDecision) -> bool:
    """The decision lets the call run without a person."""
    return decision in ("allow", ALLOW_WITH_REDACTION)


class ApprovalPolicy(Protocol):
    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision: ...


def allow_all(call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
    return "allow"


# The ``tool.denied`` rule and the model's text for a policy's ``deny``.
POLICY_DENIAL: Final = ("policy", "denied by policy")
# What the model reads when a person declines a held call: a refusal it can explain, not an error to retry.
APPROVAL_DENIAL: Final = (
    "interaction_denied",
    "approval_denied: the person asked to approve this call declined it, so it was not made. "
    "Tell the user it was not done; do not retry it unless they ask you to.",
)
# The most of a person's deny reason the model reads (Node bounds what it accepts the same way).
MAX_DENY_REASON_CHARS: Final = 1000


def approval_denial(reason: str | None = None) -> tuple[str, str]:
    """``APPROVAL_DENIAL`` with the person's reason for declining (AP-05): the model adapts to what they said
    instead of retrying. The reason is quoted as data, so text inside it never reads as the refusal's own words."""
    said = " ".join((reason or "").split())[:MAX_DENY_REASON_CHARS]
    if not said:
        return APPROVAL_DENIAL
    rule, _ = APPROVAL_DENIAL
    return rule, (
        "approval_denied: the person asked to approve this call declined it, so it was not made. "
        f"Their reason: {json.dumps(said, ensure_ascii=False)}. "
        "Do not call it again with the same arguments; change your approach to fit their reason, "
        "or tell the user it was not done."
    )



@runtime_checkable
class ExplainsDenial(Protocol):
    """An ``ApprovalPolicy`` that names why it denied a call (its ``tool.denied`` rule and the model's text)."""

    def denial(self, call: ToolCall, effect: ToolEffect) -> tuple[str, str]: ...


@runtime_checkable
class ExplainsAsk(Protocol):
    """An ``ApprovalPolicy`` that says why it asks a person about a call, for the person deciding; ``None`` when
    it has nothing to add to the call itself. The value is journaled as ``interaction.explained``'s data."""

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> Mapping[str, JsonValue] | None: ...


@runtime_checkable
class GrantAnswers(Protocol):
    """An ``ApprovalPolicy`` whose ask, when it asks, is one a person's "allow for this conversation" answers (a
    product default), never an admin's own rule."""

    def grant_answers(self, call: ToolCall, effect: ToolEffect) -> bool: ...


@runtime_checkable
class LiftsDefault(Protocol):
    """An ``ApprovalPolicy`` that knows a call's own specific rule allows it: a person's or an admin's Allow of that
    one tool (ADR-OWD-09 D-ALLOW-1). A host's class default (an engine run's writes ask) yields to it; a block, the
    flow guard and an org rule that asks are in the policy's own decision and still hold."""

    def lifts_default(self, call: ToolCall, effect: ToolEffect) -> bool: ...


def default_lifted(policy: object, call: ToolCall, effect: ToolEffect) -> bool:
    return isinstance(policy, LiftsDefault) and policy.lifts_default(call, effect)


@runtime_checkable
class RefreshingPolicy(Protocol):
    """An ``ApprovalPolicy`` over something that changes while a run executes (a host's live narrowing). ``refresh``
    brings what it reads up to date before it decides, so the decision itself stays in process; it never raises:
    a policy that cannot refresh decides from what it holds and fails closed by its own rule."""

    async def refresh(self) -> None: ...


async def refresh_policy(policy: object) -> None:
    if isinstance(policy, RefreshingPolicy):
        await policy.refresh()


@runtime_checkable
class ConsultingPolicy(Protocol):
    """An ``ApprovalPolicy`` that asks something outside the run about one call (an org's policy webhook, G-40).
    ``consult`` runs before that call's decision, so the decision itself stays in process; it never raises: a
    policy whose consultation failed decides by its own rule (GA-20: no answer in time denies)."""

    async def consult(self, call: ToolCall, effect: ToolEffect) -> None: ...


async def consult_policy(policy: object, call: ToolCall, effect: ToolEffect) -> None:
    if isinstance(policy, ConsultingPolicy):
        await policy.consult(call, effect)


@dataclass(frozen=True)
class EffectKey:
    """What makes two calls the same effect: the run, the tool and its arguments."""

    run_id: str
    tool_id: str
    args_digest: str


def args_digest(arguments: Mapping[str, JsonValue]) -> str:
    """The digest the kernel journals in ``model.response.toolCalls[].argsDigest``."""
    return canonical_digest(dict(arguments))


@dataclass(frozen=True)
class EffectCall:
    """One call of a tool with a known effect, as the effect log records it.

    ``run_id`` and ``invocation_id`` are opaque ids the caller allocates; the
    runner never makes ids, reads a clock or draws randomness.
    """

    run_id: str
    invocation_id: str
    call: ToolCall
    effect: ToolEffect
    args_digest: str
    # The rule that let the call run without a person, journaled with its intent (``tool.intent`` ``allowedBy``).
    allowed_by: str | None = None
    # What the policy redacts from the call's result before the model reads it (``allow_with_redaction``).
    redaction: Redaction | None = None

    @property
    def key(self) -> EffectKey:
        return EffectKey(self.run_id, self.effect.tool_id, self.args_digest)


RecordedState = Literal["intent", "completed", "unknown"]


@dataclass(frozen=True)
class RecordedEffect:
    """The last recorded invocation for a key. ``execution`` is set when ``completed``
    and carries the recorded status, content and receipt."""

    key: EffectKey
    invocation_id: str
    state: RecordedState
    execution: ToolExecution | None = None


EffectDisposition = Literal[
    "executed",  # ran now; the outcome is recorded
    "recorded",  # an identical call already completed: its outcome, not run again
    "denied",  # the approval policy refused it; the refusal is recorded
    "held",  # needs approval that is not decided yet: nothing recorded, not run
    "unknown",  # the effect may have happened: recorded as unknown, never retried
    "in_flight",  # another invocation of the same effect has an intent and no outcome
]


@dataclass(frozen=True)
class EffectResult:
    disposition: EffectDisposition
    execution: ToolExecution | None = None
    invocation_id: str | None = None
    # ``held``: the interaction the call waits on, when the hold names one.
    interaction_id: str | None = None


__all__ = [
    "ALLOW_WITH_REDACTION",
    "APPROVAL_DENIAL",
    "MAX_DENY_REASON_CHARS",
    "POLICY_DENIAL",
    "UNKNOWN_TOOL",
    "ApprovalDecision",
    "ApprovalPolicy",
    "approval_denial",
    "EffectCall",
    "EffectDisposition",
    "EffectKey",
    "EffectKind",
    "EffectResult",
    "EffectUnknown",
    "ExplainsAsk",
    "ExplainsDenial",
    "LiftsDefault",
    "RecordedEffect",
    "RecordedState",
    "RefreshingPolicy",
    "ShadowMark",
    "ConsultingPolicy",
    "ToolContext",
    "ToolEffect",
    "ToolExecution",
    "allow_all",
    "args_digest",
    "refresh_policy",
    "consult_policy",
    "default_lifted",
    "runs_unasked",
    "tool_id_for",
]
