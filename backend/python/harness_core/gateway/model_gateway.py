"""The model gateway (E3, E3b, ARCHITECTURE §4.6, §5.6): model APIs for engines.

One stable service for every run: the gateway token names the run, the run's
pinned grant names what it may call, the resolver turns that route into a
transport, and the budget ledger (P6.5) holds what a call may cost before it is
made and is charged what it cost after. Each engine API is a wire module (``chat_wire``, ``responses_wire``, ``messages_wire``, ``gemini_wire``) over that one admission
path. Transport neutral: ``model_server`` serves it over HTTP. Nothing an engine sends in a
header reaches a provider: the transports build their own requests from the
parsed body.
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from harness_core.access.errors import ModelAccessRefused
from harness_core.access.ports import SpendKey
from harness_core.access.spend import (
    OutputAllowance,
    input_estimate,
    ledger_usage,
    output_estimate,
    priced,
    reported_nothing,
    reservation_for,
    usage_spend,
)
from harness_core.access.types import route_digest
from harness_core.budget.errors import BudgetRefused, BudgetUnavailable
from harness_core.budget.types import ZERO, Amount, Hold, RunAccountRef
from harness_core.gateway.chat_wire import CHAT_WIRE, models_body
from harness_core.gateway.cut_calls import CutCalls, OutputRates, best_output
from harness_core.gateway.errors import (
    BindingMismatch,
    CallerCredentialHeader,
    EgressVolumeExceeded,
    ForbiddenCredential,
    GatewayRefusal,
    ModelNotGranted,
)
from harness_core.gateway.priority_gate import provider_key
from harness_core.gateway.reasoning_state import (
    open_reasoning,
    reasoning_blocks,
    reasoning_tag,
)
from harness_core.gateway.refusal_reports import (
    ModelFallbackReport,
    RefusalReport,
    RefusalReporter,
    SecretHidden,
)
from harness_core.gateway.wire import WireCall, WireRequest
from harness_core.kernel.core.exceptions import TransportError
from harness_core.kernel.core.responses import TokenUsage
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.logs import HarnessLog
from harness_core.models.call_options import (
    NO_OPTIONS,
    PROMPT_CACHE_KEY,
    CallOptions,
    call_options,
)
from harness_core.models.output_bound import (
    OutputLimits,
    bound_response,
    bound_stream,
    output_limit,
)
from harness_core.models.policy import ModelPolicy, classify_failure
from harness_core.models.provider_receipt import (
    ProviderReceipt,
    UsageLookup,
    receipt_scope,
)
from harness_core.models.transports.failures import NETWORK_ERRORS
from harness_core.models.transports.prompt_cache import PromptCacheSettings, routing_key
from harness_core.refusals import HarnessRefusal
from harness_core.security.secrets import masking
from harness_core.security.token_patterns import find_forbidden, hide_forbidden_in
from harness_core.telemetry import genai
from harness_core.telemetry.memory import NOOP_TRACER
from harness_core.telemetry.ports import RunRef

if TYPE_CHECKING:
    from collections.abc import (
        AsyncGenerator,
        AsyncIterator,
        Awaitable,
        Callable,
        Iterable,
        Mapping,
    )
    from datetime import datetime

    from pydantic import JsonValue

    from harness_core.access.ports import (
        ConversationClassification,
        ModelAccessResolver,
        ResolvedRoute,
        RunModelGrants,
        SpendCounter,
    )
    from harness_core.access.types import GrantedModel, RunModelGrant
    from harness_core.budget.ports import BudgetAccounts
    from harness_core.budget.types import Reservation, Settlement
    from harness_core.clock import Clock
    from harness_core.gateway.priority_gate import AdmissionSlot, ProviderAdmission
    from harness_core.gateway.token import GatewayTokenVerifier
    from harness_core.gateway.types import GatewayClaims, RunAttemptBinding
    from harness_core.gateway.wire import GatewayWire, Json
    from harness_core.ids import IdSource
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.telemetry.ports import HarnessTracer, SpanHandle

# Headers that carry a provider credential: the gateway holds the credential, an engine never sends one.
CALLER_CREDENTIAL_HEADERS = frozenset({"api-key", "x-api-key", "x-goog-api-key", "anthropic-api-key"})
# What engines add to model calls (F4 [run]: opencode 1.18.34; the OpenAI and AI SDK clients; Codex). None of
# them, nor any other inbound header, reaches a provider: the transport builds its own request.
ENGINE_TRACKING_HEADERS = (
    "x-opencode-session-id",
    "x-session-id",
    "x-session-affinity",
    "user-agent",
    "x-stainless-*",
    "openai-organization",
    "openai-project",
    "http-referer",
    "x-title",
    # Codex rust-v0.160.0 to a custom Responses provider (E3b).
    "originator",
    "session-id",
    "thread-id",
    "x-client-request-id",
    "x-codex-*",
    "x-openai-subagent",
    # Claude Code 2.1.289 to a custom ANTHROPIC_BASE_URL (E3c); the Messages wire reads the version and betas.
    "anthropic-version",
    "anthropic-beta",
    "anthropic-dangerous-direct-browser-access",
    "x-app",
    "x-claude-code-session-id",
)
EGRESS_REQUEST_ID_HEADER = "x-egress-request-id"
# C6.12 (WP-61): the egress proxy's mark on a call of a run past its egress cap (``egress.policy.EGRESS_CAPPED_HEADER``).
EGRESS_CAPPED_HEADER = "x-egress-capped"

_OVERFLOW_HINTS = ("context_length_exceeded", "maximum context length", "prompt is too long", "too many tokens")
_FILTER_HINTS = ("content_filter", "content management policy", "content_policy", "responsibleaipolicyviolation")
_QUOTA_HINTS = ("insufficient_quota", "exceeded your current quota", "credit balance is too low")
# Longer than a provider call is allowed to take, so a live call's budget is never handed out twice;
# a gateway that dies mid-call holds it no longer than this.
RESERVATION_TTL_S = 900
_SETTLE_ATTEMPTS = 3
# Runs whose E3 spend this process already carried into the ledger (bounded).
_LEGACY_READ_LIMIT = 10_000
# How long a provider is taken to still hold a run's last prompt in its cache, when a call it cut short has no usage
# to say (WP-62): OpenAI and Azure keep an in-memory prefix cache 5 to 10 minutes, Anthropic's default is 5 minutes.
PROMPT_CACHE_CREDIT_S = 300
_PROMPTS_SEEN_LIMIT = 10_000
# Runs whose calls this process moved to an alternate (G-48), remembered so a provider found down is not asked again.
_MOVED_LIMIT = 10_000


class ProviderCallFailed(Exception):
    """The provider did not answer. ``code`` is ours; the provider's text never leaves the gateway."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code


def classify_provider_failure(exc: BaseException) -> ProviderCallFailed:
    """What the engine is told about a provider failure: retryable statuses only where retrying helps.

    Every transport hands a provider's answer back as a ``TransportError`` and a dropped connection surfaces as a
    network error (MGW-20). Anything else was raised by our own code putting the request on the provider's wire
    (FIX-P3-B1: a ``TypeError`` formatting an image result): resending the same history fails the same way, so it
    is a 400 the engine does not retry, never ``provider_unavailable``.
    """
    if not isinstance(exc, (TransportError, *NETWORK_ERRORS)):
        return ProviderCallFailed(
            400, "request_untranslatable", "The gateway could not translate this request for the provider"
        )
    status = exc.status_code if isinstance(exc, TransportError) else None
    text = str(exc).lower()
    if any(h in text for h in _OVERFLOW_HINTS):
        return ProviderCallFailed(400, "context_length_exceeded", "This model's maximum context length is exceeded")
    if any(h in text for h in _FILTER_HINTS):
        return ProviderCallFailed(400, "content_filter", "The provider's content filter refused this request")
    if any(h in text for h in _QUOTA_HINTS):
        return ProviderCallFailed(402, "provider_quota", "The organization's model account has no quota left")
    if status == 429:
        return ProviderCallFailed(429, "rate_limited", "The provider is limiting requests; retry later")
    if status in (401, 403):
        return ProviderCallFailed(502, "provider_auth_failed", "The provider refused the organization's credential")
    if status is not None and 400 <= status < 500:
        return ProviderCallFailed(400, "provider_rejected", "The provider rejected the request")
    return ProviderCallFailed(502, "provider_unavailable", "The provider is unavailable")


def upstream_fields(exc: BaseException) -> dict[str, str | int]:
    """What a log says of a provider failure (WP-77: failed calls said only
    ``provider_unavailable``): the upstream HTTP status when one was answered, the provider's own error code when it
    gave a short one, and the exception's type. Never the provider's message or body."""
    fields: dict[str, str | int] = {"errorType": type(exc).__name__}
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and not isinstance(status, bool):
        fields["upstreamStatus"] = status
    code = getattr(exc, "provider_code", None)
    if isinstance(code, str) and code:
        fields["upstreamCode"] = code
    return fields


def _chat_frame(event: Json) -> bytes:
    return CHAT_WIRE.frame(event)


@dataclass(frozen=True, slots=True)
class ChatCompletion:
    """A whole answer on the request's wire (chat completion or Responses object)."""

    body: Json


@dataclass(frozen=True, slots=True)
class ChatStream:
    """A streamed answer: ``chunks`` framed by ``frame``, then ``terminal``. Whoever serves it calls ``aclose``
    however serving ends: closing ``chunks`` alone does nothing to a generator never started, and the call's provider
    slot and reservation are given back only by the generator's own end or by ``aclose``."""

    chunks: AsyncGenerator[Json]
    frame: Callable[[Json], bytes] = field(default=_chat_frame)
    terminal: bytes = CHAT_WIRE.terminal
    unread: Callable[[], Awaitable[None]] | None = None
    keepalive: bytes | None = CHAT_WIRE.keepalive

    async def aclose(self) -> None:
        """Idempotent: ends the stream, and gives back what a stream never read holds (G-45)."""
        with contextlib.suppress(Exception):
            await self.chunks.aclose()
        if self.unread is not None:
            await self.unread()


@dataclass(slots=True)
class _Trace:
    """The request's log, bound to more correlation as the request is admitted, and its span (P9), opened once
    the token names the run (so the span joins the run's trace)."""

    log: HarnessLog
    span: SpanHandle | None = None
    # Set once the token verified as a run's: only then can a refusal be attributed and reported.
    run: RunAttemptBinding | None = None
    token: str | None = field(default=None, repr=False)
    claims: GatewayClaims | None = None


@dataclass(frozen=True, slots=True)
class _Subject:
    """Whom a call is charged to: a run."""

    tenant: str
    run_key: str


def _subject(binding: RunAttemptBinding) -> _Subject:
    return _Subject(binding.tenant, binding.run_id)


@dataclass(slots=True)
class _Progress:
    """When a call's request went out and its last token came back, and the provider's receipt (WP-66)."""

    opened_at: datetime | None = None
    last_at: datetime | None = None
    receipt: ProviderReceipt = field(default_factory=ProviderReceipt)


@dataclass(frozen=True, slots=True)
class _Call[R: WireRequest]:
    log: HarnessLog
    subject: _Subject
    grant: RunModelGrant
    granted: GrantedModel
    resolved: ResolvedRoute
    wire: GatewayWire[R]
    request: R
    account: RunAccountRef
    reservation: Reservation
    input_estimate: int
    completion_id: str
    created: int
    credential: str = field(repr=False)
    span: SpanHandle = field(repr=False, default_factory=lambda: NOOP_TRACER.start("chat", kind="client"))
    # What the gateway sent the provider as this call's output limit (P4.17); ``None``: no limit on the wire.
    output_limit: int | None = None
    # The call's place in its provider's limit (G-45), given back once the answer settles.
    slot: AdmissionSlot | None = field(default=None, repr=False)
    # What the engine asked of the provider beyond the messages, as the transport takes it (WP-62).
    options: CallOptions = NO_OPTIONS
    # What the provider's encrypted reasoning is sealed to on its way back (``reasoning_state``); empty: not sent.
    state_tag: str = ""
    progress: _Progress = field(default_factory=_Progress, repr=False)

    @property
    def named(self) -> WireCall:
        return WireCall(self.granted.model_id, self.completion_id, self.created, self.state_tag)

    @property
    def charges_usd(self) -> bool:
        return self.resolved.access.charges_usd


class ModelGateway:
    def __init__(
        self,
        *,
        verifier: GatewayTokenVerifier,
        grants: RunModelGrants,
        resolver: ModelAccessResolver,
        budget: BudgetAccounts,
        clock: Clock,
        ids: IdSource,
        allowance: OutputAllowance | None = None,
        limits: OutputLimits | None = None,
        legacy_spend: SpendCounter | None = None,
        reservation_ttl_s: int = RESERVATION_TTL_S,
        log: HarnessLog | None = None,
        tracer: HarnessTracer = NOOP_TRACER,
        refusals: RefusalReporter | None = None,
        classification: ConversationClassification | None = None,
        gate: ProviderAdmission | None = None,
        cut_calls: CutCalls | None = None,
        fallback: bool = False,
    ) -> None:
        """``fallback``: a call whose provider is down (a 5xx, a timeout, no connection, before any output) moves to the
        next route the run's pin names for that failure (G-48, WP-100), by the kernel's own ``ModelPolicy``; the run's
        later calls go there at once and the move is reported for the run's journal (``model.fallback``). Off, as
        E3 shipped: a call is served on its route or fails.
        ``refusals``: where a refusal of a run's call is reported for the run's journal (SEC2 O-2).
        ``classification``: whether the conversation's data may reach the call's model (OWD-15, GA-16), asked
        before anything is reserved or sent.
        ``legacy_spend`` is E3's counter, read once per
        run so a run admitted before the ledger starts its account at what it already spent (P6.5).
        ``gate``: each provider's calls in flight, served by the run's priority class (G-45), the same admission a
        loop run's own calls take; unset, no limit.
        ``cut_calls``: how long a cut call's bound stays held and the output rate a model is estimated at before
        one of its calls finished here (WP-66, ``cut_calls``)."""
        self._verifier = verifier
        self._grants = grants
        self._resolver = resolver
        self._budget = budget
        self._clock = clock
        self._ids = ids
        self._allowance = allowance or OutputAllowance()
        self._limits = limits or OutputLimits()
        self._legacy = legacy_spend
        self._legacy_read: dict[RunAccountRef, None] = {}
        self._ttl_s = reservation_ttl_s
        self._log = log or HarnessLog.named("harness_core.gateway.model")
        self._tracer = tracer
        self._refusals = refusals
        self._classification = classification
        self._gate = gate
        self._unpriced: set[str] = set()
        self._cache_shards = PromptCacheSettings.from_env().routing_shards
        # Per run and route: the last prompt a provider reported serving from its cache, and when (WP-62).
        self._prompts_seen: dict[tuple[RunAccountRef, str], tuple[int, datetime]] = {}
        self._cut = cut_calls or CutCalls()
        self._rates = OutputRates(self._cut.default_tps)
        self._reconciling: set[asyncio.Task[None]] = set()
        self._fallback = fallback
        # Per run: the index of the grant's route its calls were moved to.
        self._moved: dict[tuple[str, str], int] = {}

    async def models(self, token: str | None, headers: Mapping[str, str]) -> Json:
        trace = _Trace(self._log.bind(proxyRequestId=headers.get(EGRESS_REQUEST_ID_HEADER)))
        try:
            _, grant = await self._authorize(token, headers, trace)
        except HarnessRefusal as refused:
            self._refusal(trace, refused)
            raise
        return models_body([m.model_id for m in grant.models])

    async def chat(
        self, token: str | None, headers: Mapping[str, str], body: Callable[[], Awaitable[object]]
    ) -> ChatCompletion | ChatStream:
        """OpenAI chat completions; see ``serve``."""
        return await self.serve(CHAT_WIRE, token, headers, body)

    async def serve[R: WireRequest](
        self,
        wire: GatewayWire[R],
        token: str | None,
        headers: Mapping[str, str],
        body: Callable[[], Awaitable[object]],
    ) -> ChatCompletion | ChatStream:
        """Raises a ``HarnessRefusal`` (logged here, once) or ``ProviderCallFailed`` before any output.
        ``body`` is read only once the token and headers are accepted."""
        trace = _Trace(self._log.bind(proxyRequestId=headers.get(EGRESS_REQUEST_ID_HEADER), api=wire.api.value))
        try:
            call = await self._admit(wire, token, headers, body, trace)
        except HarnessRefusal as refused:
            self._refusal(trace, refused)
            self._end_refused(trace, refused)
            raise
        except BaseException as exc:
            self._end_refused(trace, None, error_type=type(exc).__name__)
            raise
        while True:
            try:
                if call.request.stream:
                    chunks, unread = await self._open_stream(call)
                    return ChatStream(
                        chunks, frame=wire.frame, terminal=wire.terminal, unread=unread, keepalive=wire.keepalive
                    )
                return ChatCompletion(await self._complete(call))
            except ProviderCallFailed as failed:
                moved = await self._fall_back(call, failed, trace)
                if moved is None:
                    raise
                call = moved

    async def refuse_stateful(self, token: str | None, headers: Mapping[str, str], refusal: HarnessRefusal) -> None:
        """A request this gateway never serves (it keeps no responses), refused after the token
        and headers are checked so an unauthenticated caller learns nothing more than 401."""
        trace = _Trace(self._log.bind(proxyRequestId=headers.get(EGRESS_REQUEST_ID_HEADER)))
        try:
            _ = await self._authorize(token, headers, trace)
            raise refusal
        except HarnessRefusal as refused:
            self._refusal(trace, refused)
            raise

    async def _authorize(
        self, token: str | None, headers: Mapping[str, str], trace: _Trace
    ) -> tuple[_Subject, RunModelGrant]:
        claims = await self._verifier.verify(token)
        binding = claims.binding
        trace.log = trace.log.bind(
            orgId=binding.tenant,
            sessionId=binding.scope,
            runId=binding.run_id,
            attemptId=binding.attempt_id,
            fence=binding.fence,
        )
        self._open_span(trace, RunRef(binding.tenant, binding.run_id, binding.scope, binding.attempt_id, binding.fence))
        assert token is not None
        trace.run, trace.token, trace.claims = binding, token, claims
        _check_headers(headers)
        if headers.get(EGRESS_CAPPED_HEADER):
            raise EgressVolumeExceeded
        grant = await self._grants.grant(claims, token)
        if grant.tenant != binding.tenant or grant.run_id != binding.run_id:
            raise BindingMismatch("the model grant is another run's")
        if route_digest(grant.routes) != binding.routes_digest:
            raise BindingMismatch("the token was not issued for this run's model routes")
        return _subject(binding), grant

    async def _admit[R: WireRequest](
        self,
        wire: GatewayWire[R],
        token: str | None,
        headers: Mapping[str, str],
        read: Callable[[], Awaitable[object]],
        trace: _Trace,
    ) -> _Call[R]:
        subject, grant = await self._authorize(token, headers, trace)
        request, granted = await self._parse(wire, grant, headers, read, trace)
        return await self._place(wire, subject, grant, request, self._routed(subject, grant, granted), trace)

    async def _place[R: WireRequest](
        self,
        wire: GatewayWire[R],
        subject: _Subject,
        grant: RunModelGrant,
        request: R,
        granted: GrantedModel,
        trace: _Trace,
    ) -> _Call[R]:
        """The call on ``granted``'s route: resolved, checked against the conversation's level, reserved and given
        its provider slot; no provider is reached yet."""
        resolved = await self._resolver.resolve(
            subject.tenant, granted.route, run_id=subject.run_key, need=grant.need(wire.api)
        )
        trace.log = log = trace.log.bind(modelId=granted.model_id, provider=resolved.access.provider)
        tag = reasoning_tag(subject.tenant, granted.route.route_id)
        request, carries_state = self._carry_reasoning(request, wire.provider_state, resolved.transport, tag, log)
        options = self._call_options(request, subject, grant, resolved.transport, log)
        if self._classification is not None and trace.run is not None:
            refused = await self._classification.model_refusal(
                trace.run, lambda: self._fresh_grant(trace), granted.route.model, resolved.access.provider
            )
            if refused is not None:
                raise refused
        span = trace.span or self._tracer.start(genai.CHAT, kind="client")
        span.rename(f"{genai.CHAT} {granted.route.model}")
        span.set(
            {
                genai.PROVIDER: genai.provider_name(resolved.access.provider),
                genai.REQUEST_MODEL: granted.route.model,
                genai.REQUEST_STREAM: request.stream,
                "pipeshub.model_alias": request.model,
                "pipeshub.api": wire.api.value,
            }
        )
        if self._tracer.capture_content:
            with masking(resolved.secrets):
                span.set({genai.INPUT_MESSAGES: genai.text_messages(_content_pairs(request.system, request.messages))})
        self._note_pricing(resolved, log)
        account = RunAccountRef(subject.tenant, subject.run_key)
        # Off the event loop: a long history is counted by the model's tokenizer, which leaves the GIL.
        estimate = await asyncio.to_thread(
            input_estimate, request.messages, request.system, request.tools, resolved.pricing_model
        )
        # An engine's own limit is kept when smaller; a transport that cannot send one is reserved as before.
        limit = (
            self._limits.for_engine(request.output_limit, resolved.pricing_model)
            if getattr(resolved.transport, "accepts_output_limit", False) is True
            else None
        )
        requested, minimum = reservation_for(
            estimate,
            resolved.pricing_model,
            self._allowance,
            charges_usd=resolved.access.charges_usd,
            output_limit=limit,
        )
        completion_id = self._ids.new_id(wire.id_prefix)
        token = trace.token
        assert token is not None
        slot = await self._slot(resolved.admission_key or provider_key(resolved.access.provider), grant, subject.tenant, log)
        try:
            reservation = await self._reserve(account, completion_id, requested, minimum, token, log)
        except BaseException:
            if slot is not None:
                slot.release()
            raise
        return _Call(
            log=log,
            subject=subject,
            grant=grant,
            granted=granted,
            resolved=resolved,
            wire=wire,
            request=request,
            account=account,
            reservation=reservation,
            input_estimate=estimate,
            completion_id=completion_id,
            created=int(self._clock.now().timestamp()),
            credential=token,
            span=span,
            output_limit=limit,
            slot=slot,
            options=options,
            state_tag=tag if carries_state else "",
        )

    def _routed(self, subject: _Subject, grant: RunModelGrant, granted: GrantedModel) -> GrantedModel:
        """``granted``, or the alternate this run's calls were moved to when it names the run's first route."""
        moved = self._moved.get((subject.tenant, subject.run_key))
        if moved is None or not grant.routes or granted.route != grant.routes[0] or moved >= len(grant.routes):
            return granted
        return dataclasses.replace(granted, route=grant.routes[moved])

    async def _fall_back[R: WireRequest](
        self, call: _Call[R], failed: ProviderCallFailed, trace: _Trace
    ) -> _Call[R] | None:
        """The call placed again on the next route the run's pin names for this failure, or ``None``: the fallback
        is off, the failure is not an outage (a filter, a limit, a rejection), the route is not the run's own model
        (a role's), it is a person's own key (A1: it pays for nothing but its own model), or no alternate is left.
        An alternate the org no longer serves, or the conversation's level does not allow, is passed over; a budget
        refusal is the run's limit and is raised."""
        routes = call.grant.routes
        if not self._fallback or call.granted.route not in routes or routes[0].account is not None:
            return None
        trigger = classify_failure(failed.__cause__ or failed)
        if trigger is None:
            return None
        policy = ModelPolicy.from_pinned(routes)
        current = policy.routes[routes.index(call.granted.route)]
        candidate = policy.next_route(current, trigger)
        while candidate is not None:
            route = routes[candidate.index]
            trace.span = None
            if trace.run is not None:
                b = trace.run
                self._open_span(trace, RunRef(b.tenant, b.run_id, b.scope, b.attempt_id, b.fence))
            granted = dataclasses.replace(call.granted, route=route)
            try:
                moved = await self._place(call.wire, call.subject, call.grant, call.request, granted, trace)
            except ModelAccessRefused as refused:
                trace.log.warning("model_gateway.route_unavailable", routeId=route.route_id, reason=refused.reason_code)
                self._end_refused(trace, refused)
                candidate = policy.next_route(candidate, trigger)
                continue
            self._remember_move(call.subject, candidate.index)
            report = ModelFallbackReport.of(current.provider, current.model, candidate.provider, candidate.model, trigger)
            if report is not None and self._refusals is not None and trace.token is not None:
                self._refusals.fallback(call.subject.run_key, trace.token, report, log=trace.log)
            trace.log.warning(
                "model_gateway.fallback", reason=trigger, fromRoute=current.route_id, toRoute=candidate.route_id
            )
            return moved
        return None

    def _remember_move(self, subject: _Subject, index: int) -> None:
        key = (subject.tenant, subject.run_key)
        _ = self._moved.pop(key, None)
        self._moved[key] = index
        while len(self._moved) > _MOVED_LIMIT:
            _ = self._moved.pop(next(iter(self._moved)))

    def _carry_reasoning[R: WireRequest](
        self, request: R, state_wire: str | None, transport: object, tag: str, log: HarnessLog
    ) -> tuple[R, bool]:
        """The request with the provider's reasoning it can take back, and whether the transport carries any (WP-62,
        WP56-WIRE).

        Reasoning reaches the provider only through a transport that replays the engine wire's own kind of it:
        Responses reasoning items, or Anthropic's signed thinking on the Messages wire. An item the caller holds only
        to display (no encrypted content, no signature) has nothing to replay; anything else not carried is named in
        one ``reasoning_dropped`` line, never its content."""
        carries = state_wire is not None and getattr(transport, "provider_state_wire", None) == state_wire
        attached = reasoning_blocks(request.messages)
        display = getattr(request, "display_reasoning", 0)
        unattached = max(0, request.reasoning_items - display - attached)
        if carries and attached:
            assert state_wire is not None
            messages, _, other = open_reasoning(request.messages, tag, state_wire)
            # Every wire's request is a frozen dataclass; only its messages change.
            request = cast("R", dataclasses.replace(cast("Any", request), messages=messages))
        else:
            other = 0 if carries else attached
        dropped = unattached + other
        if dropped or (request.encrypted_reasoning and not carries):
            reason = (
                "transport_has_no_reasoning_state"
                if not carries
                else "sealed_for_another_route" if other else "not_replayable"
            )
            log.info(
                "model_gateway.reasoning_dropped",
                reason=reason,
                items=dropped,
                encryptedRequested=request.encrypted_reasoning,
            )
        return request, carries

    def _call_options(
        self, request: WireRequest, subject: _Subject, grant: RunModelGrant, transport: object, log: HarnessLog
    ) -> CallOptions:
        """What the engine asked about its tools and its prompt cache, for the transport (WP-62).

        The engine's cache key never leaves the gateway: the provider gets ``routing_key`` of the run's org, the
        run's actor and that key, so neither another org's prompts nor another person's in the same org share a
        cache route with this run's, and a key an engine invents names nothing at the vendor. A call without one is
        keyed by its run.
        Whatever the transport cannot send is named in the one ``fields_dropped`` line with the wire's own drops."""
        parallel: bool | None = getattr(request, "parallel_tool_calls", None)
        engine_key: str | None = getattr(request, "cache_key", None)
        options = CallOptions(
            parallel_tool_calls=parallel,
            cache_key=routing_key(
                subject.tenant, engine_key or subject.run_key, self._cache_shards, principal_id=grant.actor or "",
            ),
        )
        asked = CallOptions(parallel_tool_calls=parallel, cache_key=engine_key).asked
        honoured = getattr(transport, "honoured_call_options", frozenset[str]())
        dropped = sorted({*request.dropped, *(asked - honoured)})
        if dropped:
            log.info("model_gateway.fields_dropped", fields=tuple(dropped))
        if PROMPT_CACHE_KEY not in honoured:
            options = dataclasses.replace(options, cache_key=None)
        return options

    async def _slot(self, key: str, grant: RunModelGrant, org: str, log: HarnessLog) -> AdmissionSlot | None:
        """The call's slot in the limit of the upstream it reaches (``ResolvedRoute.admission_key``), counted for
        the run's org inside that limit (WP56-FAIR)."""
        if self._gate is None:
            return None
        started = self._clock.now()
        slot = await self._gate.acquire(key, grant.priority_class, org=org)
        if slot.waited:
            log.info(
                "model_gateway.slot_waited",
                priorityClass=grant.priority_class,
                ms=round((self._clock.now() - started).total_seconds() * 1000),
                limit=self._gate.limit,
            )
        return slot

    async def _fresh_grant(self, trace: _Trace) -> RunModelGrant:
        """The run's grant past the grant cache: what the run recorded may have changed with a read the engine just
        made (GA-16)."""
        assert trace.claims is not None and trace.token is not None
        return await self._grants.grant(trace.claims, trace.token, fresh=True)

    async def _parse[R: WireRequest](
        self,
        wire: GatewayWire[R],
        grant: RunModelGrant,
        headers: Mapping[str, str],
        read: Callable[[], Awaitable[object]],
        trace: _Trace,
    ) -> tuple[R, GrantedModel]:
        # WP-103: the body is the run's own context going to the org's model; a credential shape there (a public
        # page's documentation example) is hidden and the call goes on. Headers are refused.
        body, hidden = hide_forbidden_in(_json(await read()))
        if hidden:
            self._hidden(trace, hidden)
        request = wire.parse(body, headers)
        granted = grant.model(request.model)
        if granted is None:
            raise ModelNotGranted("the run's grant has no such model")
        return request, granted

    async def count_tokens[R: WireRequest](
        self,
        wire: GatewayWire[R],
        token: str | None,
        headers: Mapping[str, str],
        body: Callable[[], Awaitable[object]],
    ) -> int:
        """The input estimate the ledger reserves on (Messages ``count_tokens``): admitted as a call is,
        but no provider is reached and nothing is reserved or charged."""
        trace = _Trace(self._log.bind(proxyRequestId=headers.get(EGRESS_REQUEST_ID_HEADER), api=wire.api.value))
        try:
            _, grant = await self._authorize(token, headers, trace)
            request, granted = await self._parse(wire, grant, headers, body, trace)
        except HarnessRefusal as refused:
            self._refusal(trace, refused)
            raise
        # The pinned route's model, which a route resolves to, so the count is the reservation's without its key.
        model = granted.route.config_model or granted.route.model
        tokens = await asyncio.to_thread(input_estimate, request.messages, request.system, request.tools, model)
        trace.log.info("model_gateway.count_tokens", modelId=granted.model_id, inputTokens=tokens)
        return tokens

    async def _reserve(
        self, account: RunAccountRef, key: str, requested: Amount, minimum: Amount, token: str, log: HarnessLog
    ) -> Reservation:
        opening = await self._opening(account, log)
        try:
            reservation = await self._budget.reserve(
                account, key, requested, minimum=minimum, ttl_s=self._ttl_s, opening=opening, credential=token
            )
        except BudgetRefused:
            raise
        except Exception as exc:
            raise BudgetUnavailable("the budget ledger cannot be reached", detail=type(exc).__name__) from exc
        self._legacy_read[account] = None
        return reservation

    async def _opening(self, account: RunAccountRef, log: HarnessLog) -> Amount | None:
        """E3's recorded spend of a run this process has not reserved for yet."""
        if self._legacy is None or account in self._legacy_read:
            return None
        if len(self._legacy_read) >= _LEGACY_READ_LIMIT:
            _ = self._legacy_read.pop(next(iter(self._legacy_read)))
        try:
            spent = await self._legacy.spent(SpendKey(account.tenant, account.run_id))
        except Exception as exc:
            log.warning("model_gateway.legacy_spend_unreadable", errorType=type(exc).__name__)
            return None
        opening = Amount(spent.usd_micros, spent.tokens)
        return opening if opening != ZERO else None

    def _effort[R: WireRequest](self, call: _Call[R]) -> str | None:
        return call.grant.reasoning_effort or call.request.reasoning_effort

    async def _complete[R: WireRequest](self, call: _Call[R]) -> Json:
        try:
            return await self._completed(call)
        finally:
            _give_back(call)

    async def _completed[R: WireRequest](self, call: _Call[R]) -> Json:
        call.progress.opened_at = self._clock.now()
        try:
            with call_options(call.options), receipt_scope(call.progress.receipt):
                response = await call.resolved.transport.complete(
                    list(call.request.messages),
                    tools=list(call.request.tools) or None,
                    system=call.request.system,
                    effort=self._effort(call),
                    **output_limit(call.output_limit),
                )
            if self._limits.repetition:
                response = bound_response(response, log=call.log)
        except asyncio.CancelledError:
            # ADR-GW-01 D2: the caller left (the server cancels its handler): a cut call, charged its estimate.
            with contextlib.suppress(Exception):
                await asyncio.shield(self._settle(call, None, "", status="interrupted"))
            raise
        except Exception as exc:
            failed = classify_provider_failure(exc)
            await self._release(call, status=failed.code, upstream={**upstream_fields(exc), "phase": "open"})
            raise failed from exc
        text = response.message.text
        await asyncio.shield(
            self._settle(
                call, response.usage, text, status="ok", answer=text, model=response.model,
                cut=response.message.cut is not None,
            )
        )
        return call.wire.body(call.request, response, call.named)

    async def _open_stream[R: WireRequest](
        self, call: _Call[R]
    ) -> tuple[AsyncGenerator[Json], Callable[[], Awaitable[None]]]:
        """The stream's chunks, and what ends the call if they are never read: a client gone before the first
        chunk (a Stop, a lost lease, a sandbox torn down) would otherwise hold the slot until its hold ran out."""
        events = call.resolved.transport.stream(
            list(call.request.messages),
            tools=list(call.request.tools) or None,
            system=call.request.system,
            effort=self._effort(call),
            **output_limit(call.output_limit),
        )
        if self._limits.repetition:
            events = bound_stream(events, model=call.granted.route.model, log=call.log)
        call.progress.opened_at = self._clock.now()
        try:
            # The transport builds its request on the first read, so the call's options and receipt are in scope.
            with call_options(call.options), receipt_scope(call.progress.receipt):
                first = await anext(aiter(events))
        except StopAsyncIteration:
            first = None
        except asyncio.CancelledError:
            # ADR-GW-01 D2, before the provider's first event (FIX-EGD4): the request may already be at the provider
            # (a model still thinking, the e2e model's hold), which bills it, so it is a cut call like one cut later.
            _give_back(call)
            with contextlib.suppress(Exception):
                await asyncio.shield(self._settle(call, None, "", status="interrupted"))
            raise
        except BaseException as exc:
            _give_back(call)
            if not isinstance(exc, Exception):
                raise
            failed = classify_provider_failure(exc)
            await self._release(call, status=failed.code, upstream={**upstream_fields(exc), "phase": "open"})
            raise failed from exc
        end = _StreamEnd()

        async def unread() -> None:
            if end.started or end.ended:
                return
            end.ended = True
            _give_back(call)
            aclose = getattr(events, "aclose", None)
            if aclose is not None:
                with contextlib.suppress(Exception):
                    await aclose()
            with contextlib.suppress(Exception):
                await asyncio.shield(self._settle(call, None, "", status="interrupted"))

        return self._chunks(call, events, first, end), unread

    async def _chunks[R: WireRequest](
        self, call: _Call[R], events: AsyncIterator[StreamEvent], first: StreamEvent | None, end: _StreamEnd
    ) -> AsyncGenerator[Json]:
        if end.ended:
            return
        end.started = True
        encoder = call.wire.encoder(call.request, call.named)
        streamed: list[str] = []
        usage: TokenUsage | None = None
        status = "interrupted"
        settled = False
        try:
            for chunk in encoder.start():
                yield chunk
            pending = [first] if first is not None else []
            async for event in _chain(pending, events):
                if isinstance(event, StreamCompleteEvent):
                    usage = event.response.usage
                    status = "ok"
                    # Settled before the engine sees the call end: a turn's usage is read from the ledger as soon as
                    # its stream ends (OWD-8), so the turn's last call must already be there.
                    settled = True
                    # The gateway's own output bound ended it: the provider was cut off, not finished.
                    cut = event.response.message.cut is not None
                    with contextlib.suppress(Exception):
                        await asyncio.shield(self._settle(call, usage, "".join(streamed), status=status, cut=cut))
                elif isinstance(event, (TextDeltaEvent, ThinkingDeltaEvent)):
                    streamed.append(event.delta)
                    call.progress.last_at = self._clock.now()
                elif isinstance(event, ToolCallDeltaEvent):
                    streamed.append(event.arguments_delta)
                    call.progress.last_at = self._clock.now()
                for chunk in encoder.on_event(event):
                    yield chunk
        except Exception as exc:
            failed = classify_provider_failure(exc)
            status = failed.code
            call.log.warning(
                "model_gateway.provider_failed", status=status, phase="stream", **upstream_fields(exc),
                routeId=call.granted.route.route_id,
            )
            for chunk in encoder.failed(failed.code, str(failed)):
                yield chunk
        finally:
            _give_back(call)
            if not settled:
                with contextlib.suppress(Exception):
                    await asyncio.shield(self._settle(call, usage, "".join(streamed), status=status))

    async def _settle[R: WireRequest](
        self,
        call: _Call[R],
        usage: TokenUsage | None,
        streamed: str,
        *,
        status: str,
        answer: str | None = None,
        model: str = "",
        cut: bool = False,
    ) -> None:
        """Commits the call once: the provider's usage, or an estimate when it never reported one. ``cut``: the
        gateway's output bound ended an answer the provider was still making (P4.17), a cut call however it ended."""
        call.span.set(
            {
                genai.RESPONSE_ID: call.completion_id,
                genai.RESPONSE_MODEL: model or call.granted.route.model,
                **genai.usage_attributes(usage),
                "pipeshub.usage_estimated": usage is None,
            }
        )
        if self._tracer.capture_content:
            # What the call resolved is masked exactly in what the span keeps of it (SEC4).
            with masking(call.resolved.secrets):
                call.span.set(
                    {genai.OUTPUT_MESSAGES: genai.text_messages([("assistant", answer if answer is not None else streamed)])}
                )
        try:
            await self._commit(call, usage, streamed, status=status, cut=cut or status != "ok")
        finally:
            call.span.end(error_type=None if status == "ok" else status)

    async def _commit[R: WireRequest](
        self, call: _Call[R], usage: TokenUsage | None, streamed: str, *, status: str, cut: bool
    ) -> None:
        if usage is not None and reported_nothing(usage):
            usage = None
        estimated = usage is None
        bound: TokenUsage | None = None
        # What streamed is counted off the event loop: a long answer takes the tokenizer milliseconds (R14-LOOP).
        produced = await asyncio.to_thread(output_estimate, streamed, call.resolved.pricing_model) if estimated else None
        if usage is None and cut:
            usage, bound = self._cut_usage(call, produced or 0)
        elif usage is None:
            usage = self._ended_usage(call, produced or 0)
        else:
            self._saw_prompt(call, usage)
            if not cut:
                self._observe_rate(call, usage)
        pricing = call.resolved.pricing_model
        amount = usage_spend(usage, pricing, charges_usd=call.charges_usd)
        hold = None
        if bound is not None:
            upper = usage_spend(bound, pricing, charges_usd=call.charges_usd)
            hold = Hold(
                Amount(max(0, upper.usd_micros - amount.usd_micros), max(0, upper.tokens - amount.tokens)),
                self._cut.hold_ttl_s,
            )
        used = ledger_usage(
            usage, input_estimate=call.input_estimate, streamed=streamed, model=pricing, streamed_tokens=produced
        )
        settled = await self._settled(
            call,
            amount,
            lambda: self._budget.commit(
                call.reservation,
                amount,
                usage=used,
                credential=call.credential,
                hold=hold,
                model_role=call.granted.role,
            ),
        )
        if settled is None:
            return
        if hold is not None:
            self._reconcile_later(call)
        call.log.info(
            "model_gateway.call",
            routeId=call.granted.route.route_id,
            modelRole=call.granted.role,
            status=status,
            stream=call.request.stream,
            inputTokens=usage.input_tokens,
            outputTokens=usage.output_tokens,
            # ``tokens`` is the budget's weighted charge; what the cache served is logged apart (FIX-P3-T1).
            cacheReadTokens=usage.cache_read_tokens,
            estimated=estimated,
            # A cut call (WP-66): the bound its budget keeps held beside the estimate it is charged.
            boundOutputTokens=bound.output_tokens if bound is not None else None,
            heldMicroUsd=hold.amount.usd_micros if hold is not None else None,
            reservedMicroUsd=call.reservation.granted.usd_micros,
            partial=call.reservation.partial,
            costMicroUsd=amount.usd_micros,
            tokens=amount.tokens,
            spentMicroUsd=settled.remaining.committed.usd_micros,
        )
        if settled.crossed:
            call.log.warning(
                "model_gateway.budget_crossed",
                reason="budget_exhausted",
                spentMicroUsd=settled.remaining.committed.usd_micros,
                tokens=settled.remaining.committed.tokens,
                overshootMicroUsd=max(0, amount.usd_micros - call.reservation.granted.usd_micros),
            )

    def _saw_prompt[R: WireRequest](self, call: _Call[R], usage: TokenUsage) -> None:
        if usage.cache_read_tokens <= 0:
            return
        key = (call.account, call.granted.route.route_id)
        if key not in self._prompts_seen and len(self._prompts_seen) >= _PROMPTS_SEEN_LIMIT:
            _ = self._prompts_seen.pop(next(iter(self._prompts_seen)))
        self._prompts_seen[key] = (usage.input_tokens, self._clock.now())

    def _ended_usage[R: WireRequest](self, call: _Call[R], produced: int) -> TokenUsage:
        """A call that ended with its whole answer but whose transport reports no usage (WP-62, 9a).

        The prompt was sent and answered, so its input is charged at the gateway's count of it, and its output is
        what streamed. As much of its input as this run's last prompt on this route is charged as a cache read, when
        the provider served that prompt from its cache within ``PROMPT_CACHE_CREDIT_S`` (an engine resends its
        history with one turn appended). Usage the provider reports always replaces all of this, and the ledger
        records the same counts the charge was made from."""
        prompt = call.input_estimate
        return TokenUsage(input_tokens=prompt, output_tokens=produced, cache_read_tokens=self._cached_prompt(call, prompt))

    def _cached_prompt[R: WireRequest](self, call: _Call[R], prompt: int) -> int:
        """How much of ``prompt`` the provider most likely served from its cache: as much as this run's last prompt on
        this route that it reported cached, when that was within ``PROMPT_CACHE_CREDIT_S``; else none."""
        seen = self._prompts_seen.get((call.account, call.granted.route.route_id))
        if seen is not None and (self._clock.now() - seen[1]).total_seconds() <= PROMPT_CACHE_CREDIT_S:
            return min(prompt, seen[0])
        return 0

    def _cut_usage[R: WireRequest](self, call: _Call[R], produced: int) -> tuple[TokenUsage, TokenUsage]:
        """A call cut short (the engine went away, the run was stopped, the stream failed): its best estimate, which
        it is charged, and its bound, which its budget keeps held (R13-EST, WP-66; ``cut_calls``).

        It is not drained (WP-65), so the provider may bill more than streamed: reasoning it never streamed and what it
        generated after the last event reached the gateway. The bound is the output limit the call ran under (the
        limit it sent on the wire, else the model's published maximum, P4.17; ``cap_tokens`` only shrinks
        reservations), never less than what streamed. The estimate adds to what streamed the model's observed rate
        times the time since its last streamed token (since the request, when none streamed), up to the bound. Both
        count the input at the gateway's count. The bound takes no cache credit: a prompt cache is the provider's to
        keep. The estimate takes the credit an ended call does (``_cached_prompt``, WP-81): one run's last Codex
        step resent a 150,000-token history the provider had served from its cache every step before, and its prompt
        was charged as if none of it were cached: USD 0.38, where the same history cost USD 0.016 a step earlier."""
        prompt = call.input_estimate
        pricing = call.resolved.pricing_model
        bound = max(produced, OutputAllowance(default_tokens=self._allowance.default_tokens).tokens(pricing, call.output_limit))
        since = call.progress.last_at or call.progress.opened_at
        unseen = (self._clock.now() - since).total_seconds() if since is not None else 0.0
        best = best_output(produced, bound, self._rates.rate(pricing), unseen)
        estimate = TokenUsage(input_tokens=prompt, output_tokens=best, cache_read_tokens=self._cached_prompt(call, prompt))
        return estimate, TokenUsage(input_tokens=prompt, output_tokens=bound)

    def _observe_rate[R: WireRequest](self, call: _Call[R], usage: TokenUsage) -> None:
        if call.progress.opened_at is not None:
            took = (self._clock.now() - call.progress.opened_at).total_seconds()
            self._rates.observe(call.resolved.pricing_model, usage.output_tokens, took)

    def _reconcile_later[R: WireRequest](self, call: _Call[R]) -> None:
        """Asks the provider once, shortly after the cut, what it billed, when it kept the response and the transport
        that made the call can look it up; otherwise the hold goes after its time and the estimate stands."""
        receipt, transport = call.progress.receipt, call.resolved.transport
        if receipt.response_id is None or not receipt.stored or not isinstance(transport, UsageLookup):
            return
        task = asyncio.get_running_loop().create_task(self._reconcile(call, transport, receipt.response_id))
        self._reconciling.add(task)
        task.add_done_callback(self._reconciling.discard)

    async def _reconcile[R: WireRequest](self, call: _Call[R], lookup: UsageLookup, response_id: str) -> None:
        # Only the transport that made the call is asked: its credentials, account and region are the call's own.
        await asyncio.sleep(self._cut.reconcile_delay_s)
        try:
            usage = await asyncio.wait_for(lookup.reported_usage(response_id), self._cut.lookup_timeout_s)
        except Exception as exc:
            call.log.warning("model_gateway.reconcile_failed", errorType=type(exc).__name__)
            return
        if usage is None or reported_nothing(usage):
            call.log.info("model_gateway.reconcile_unavailable", routeId=call.granted.route.route_id)
            return
        pricing = call.resolved.pricing_model
        amount = usage_spend(usage, pricing, charges_usd=call.charges_usd)
        used = ledger_usage(usage, input_estimate=call.input_estimate, streamed="", model=pricing)
        settled = await self._settled(
            call, amount, lambda: self._budget.reconcile(call.reservation, amount, usage=used, credential=call.credential)
        )
        if settled is not None:
            call.log.info(
                "model_gateway.reconciled",
                routeId=call.granted.route.route_id,
                inputTokens=usage.input_tokens,
                outputTokens=usage.output_tokens,
                cacheReadTokens=usage.cache_read_tokens,
                costMicroUsd=amount.usd_micros,
                spentMicroUsd=settled.remaining.committed.usd_micros,
            )

    async def reconciled(self) -> None:
        """Waits for the usage lookups under way (tests, and a graceful stop)."""
        while pending := [task for task in self._reconciling if not task.done()]:
            _ = await asyncio.gather(*pending, return_exceptions=True)

    async def _release[R: WireRequest](
        self, call: _Call[R], *, status: str, upstream: Mapping[str, str | int] | None = None
    ) -> None:
        """A provider failure before any output: nothing was billed, the reservation goes back. ``upstream``: what
        the provider answered (``upstream_fields``), on the call's line and on a warning of its own."""
        if upstream is not None:
            call.log.warning("model_gateway.provider_failed", status=status, routeId=call.granted.route.route_id, **upstream)
        try:
            settled = await self._settled(
                call, ZERO, lambda: self._budget.release(call.reservation, credential=call.credential)
            )
            if settled is not None:
                call.log.info(
                    "model_gateway.call",
                    status=status,
                    stream=call.request.stream,
                    released=True,
                    costMicroUsd=0,
                    routeId=call.granted.route.route_id,
                    **(upstream or {}),
                )
        finally:
            call.span.end(error_type=status)

    def _hidden(self, trace: _Trace, kinds: tuple[str, ...]) -> None:
        trace.log.warning("model_gateway.secret_hidden", kinds=kinds)
        if self._refusals is None or trace.run is None or trace.token is None:
            return
        for kind in kinds:
            report = SecretHidden.of("model", kind)
            if report is not None:
                self._refusals.hidden(trace.run.run_id, trace.token, report, log=trace.log)

    def _refusal(self, trace: _Trace, refused: HarnessRefusal) -> None:
        _refused(trace.log, refused)
        if self._refusals is None or trace.run is None or trace.token is None:
            return
        param = getattr(refused, "param", None)
        report = RefusalReport.of(
            "model", refused.reason_code, refusal_status(refused), param=param if isinstance(param, str) else None
        )
        if report is not None:
            self._refusals.report(trace.run.run_id, trace.token, report, log=trace.log)

    def _open_span(self, trace: _Trace, run: RunRef) -> None:
        trace.span = self._tracer.start(genai.CHAT, kind="client", run=run, attributes={genai.OPERATION: genai.CHAT})

    def _end_refused(self, trace: _Trace, refused: HarnessRefusal | None, *, error_type: str | None = None) -> None:
        span = trace.span or self._tracer.start(genai.CHAT, kind="client", attributes={genai.OPERATION: genai.CHAT})
        span.end(error_type=refused.reason_code if refused is not None else error_type)

    async def _settled[R: WireRequest](
        self, call: _Call[R], amount: Amount, settle: Callable[[], Awaitable[Settlement]]
    ) -> Settlement | None:
        # Settling is idempotent per reservation, so an answer lost on the way is asked for again.
        failure: BaseException | None = None
        for _ in range(_SETTLE_ATTEMPTS):
            try:
                return await settle()
            except BudgetRefused as refused:
                failure = refused
                break
            except Exception as exc:
                failure = exc
        call.log.error(
            "model_gateway.charge_failed",
            errorType=type(failure).__name__,
            reason=getattr(failure, "reason_code", None),
            costMicroUsd=amount.usd_micros,
        )
        return None

    def _note_pricing(self, resolved: ResolvedRoute, log: HarnessLog) -> None:
        if resolved.access.charges_usd and not priced(resolved.pricing_model) and resolved.pricing_model not in self._unpriced:
            self._unpriced.add(resolved.pricing_model)
            log.info("model_gateway.default_pricing", pricingModel=resolved.pricing_model)


@dataclass(slots=True)
class _StreamEnd:
    """Whether a stream's chunks were started (their generator then ends the call) or it was ended unread."""

    started: bool = False
    ended: bool = False


def _give_back[R: WireRequest](call: _Call[R]) -> None:
    if call.slot is not None:
        call.slot.release()


def _check_headers(headers: Mapping[str, str]) -> None:
    # An empty one carries nothing: Gemini CLI on a gateway sends ``x-goog-api-key: ""`` beside its bearer token.
    if CALLER_CREDENTIAL_HEADERS & {name.lower() for name, value in headers.items() if value.strip()}:
        raise CallerCredentialHeader("a provider credential header is not accepted")
    found: set[str] = set()
    for value in headers.values():
        found.update(find_forbidden(value))
    if found:
        raise ForbiddenCredential(tuple(sorted(found)))


def _json(body: object) -> JsonValue:
    return body  # pyright: ignore[reportReturnType]


async def _chain(first: list[StreamEvent], rest: AsyncIterator[StreamEvent]) -> AsyncIterator[StreamEvent]:
    for event in first:
        yield event
    async for event in rest:
        yield event


def _content_pairs(system: str | None, messages: Iterable[object]) -> list[tuple[str, str]]:
    """(role, text) per message for ``gen_ai.input.messages``; written only when the operator opted in."""
    pairs = [("system", system)] if system else []
    for message in messages:
        role = str(getattr(getattr(message, "role", ""), "value", getattr(message, "role", "")))
        text = getattr(message, "text", None)
        content = text() if callable(text) else text if isinstance(text, str) else getattr(message, "content", "")
        pairs.append((role, content if isinstance(content, str) else str(content)))
    return pairs


def _refused(log: HarnessLog, refused: HarnessRefusal) -> None:
    fields: dict[str, str | tuple[str, ...] | None] = {"reason": refused.reason_code}
    if isinstance(refused, ForbiddenCredential):
        fields["kinds"] = refused.kinds
    param = getattr(refused, "param", None)
    if isinstance(param, str):
        fields["param"] = param
    detail = getattr(refused, "detail", None)
    if isinstance(detail, str):
        fields["detail"] = detail
    log.warning("model_gateway.refused", **fields)


def refusal_status(refused: HarnessRefusal) -> int:
    if isinstance(refused, GatewayRefusal | ModelAccessRefused | BudgetRefused):
        return refused.http_status
    return 403


__all__ = [
    "CALLER_CREDENTIAL_HEADERS",
    "EGRESS_REQUEST_ID_HEADER",
    "ENGINE_TRACKING_HEADERS",
    "ChatCompletion",
    "ChatStream",
    "ModelGateway",
    "ProviderCallFailed",
    "classify_provider_failure",
    "upstream_fields",
    "refusal_status",
]
