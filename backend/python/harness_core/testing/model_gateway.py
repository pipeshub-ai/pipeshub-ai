"""Test doubles for the model gateway: a recording transport and a wired gateway.

``RecordedTransport`` answers with scripted stream events (as a provider family's
transport reports a turn) and records what it was asked; ``GatewayRig`` wires a
``ModelGateway`` with a ``TokenMint``, in-memory grants, a static resolver, an
in-memory budget ledger and E3's in-memory spend counter as the migration source.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, override

from harness_core.access.memory import (
    InMemoryRunModelGrants,
    InMemorySpendCounter,
    StaticModelAccessResolver,
)
from harness_core.access.ports import ResolvedRoute
from harness_core.access.spend import OutputAllowance
from harness_core.access.types import (
    AccessRoute,
    AccountKind,
    AccountRef,
    ModelApi,
    PinnedRoute,
    RouteKind,
    RunBudget,
    RunModelGrant,
    neutral_models,
    route_digest,
)
from harness_core.budget.memory import InMemoryBudgetLedger
from harness_core.budget.tree import usd_micros
from harness_core.budget.types import (
    Cap,
    PinnedBudget,
    RunAccountRef,
)
from harness_core.gateway.cut_calls import CutCalls
from harness_core.gateway.model_gateway import ModelGateway
from harness_core.gateway.refusal_reports import (
    InMemoryRefusalSink,
    RefusalReporter,
    RefusalSink,
)
from harness_core.gateway.token import GatewayTokenVerifier
from harness_core.gateway.types import RunAttemptBinding
from harness_core.kernel.core.messages import AssistantMessage, TextPart
from harness_core.kernel.core.responses import (
    ModelResponse,
    StopReason,
    StructuredResponse,
    TokenUsage,
)
from harness_core.kernel.core.streaming import StreamCompleteEvent, StreamEvent
from harness_core.models.output_bound import OutputLimits
from harness_core.models.provider_receipt import current_receipt
from harness_core.models.transports.base import LLMTransport
from harness_core.telemetry.memory import NOOP_TRACER
from harness_core.testing.clock import ManualClock
from harness_core.testing.gateway import TokenMint
from harness_core.testing.ids import SeededIds

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence

    from harness_core.access.ports import (
        ConversationClassification,
        ModelAccessResolver,
    )
    from harness_core.access.types import Payer
    from harness_core.budget.ports import BudgetAccounts
    from harness_core.gateway.priority_gate import ProviderAdmission
    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema
    from harness_core.priority import PriorityClass
    from harness_core.telemetry.ports import HarnessTracer


@dataclass
class TransportCall:
    messages: list[Message]
    tools: list[ToolSchema] | None
    system: str | None
    effort: str | None
    stream: bool
    max_output_tokens: int | None = None
    tool_choice: str | None = None


class RecordedTransport(LLMTransport):
    """Replays ``events`` (the last one a ``StreamCompleteEvent``) for ``stream`` and its
    final response for ``complete``; ``fail``/``fail_after`` raise like a provider."""

    def __init__(
        self,
        events: Sequence[StreamEvent] | None = None,
        *,
        family: str = "openai",
        fail: BaseException | None = None,
        fail_after: int | None = None,
    ) -> None:
        self.events = list(events or [reply_events("hello")])
        self.family = family
        self.fail = fail
        self.fail_after = fail_after
        self.calls: list[TransportCall] = []

    @property
    @override
    def provider(self) -> str:
        return self.family

    @property
    @override
    def accepts_output_limit(self) -> bool:
        return True

    def _record(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        system: str | None,
        effort: str | None,
        *,
        stream: bool,
        limit: int | None = None,
        tool_choice: str | None = None,
    ) -> None:
        self.calls.append(TransportCall(list(messages), tools, system, effort, stream, limit, tool_choice))

    @override
    async def complete(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        self._record(messages, tools, system, effort, stream=False, limit=max_output_tokens, tool_choice=tool_choice)
        if self.fail is not None:
            raise self.fail
        final = self.events[-1]
        assert isinstance(final, StreamCompleteEvent)
        return final.response

    @override
    async def complete_structured(
        self, messages: list[Message], output_schema: dict[str, Any], system: str | None = None, model: str | None = None
    ) -> StructuredResponse:
        raise NotImplementedError

    @override
    async def stream(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        self._record(messages, tools, system, effort, stream=True, limit=max_output_tokens, tool_choice=tool_choice)
        for position, event in enumerate(self.events):
            if self.fail is not None and (self.fail_after is None or position >= self.fail_after):
                raise self.fail
            yield event


class StoringTransport(RecordedTransport):
    """A fake provider that keeps each response (OpenAI Responses with ``store``) under an id it notes on the call's
    receipt, and answers a usage lookup by that id from ``billed`` (WP-66). ``credential`` stands for the API key the
    transport was built with; every lookup records it with the id it was asked."""

    def __init__(self, *, credential: str, prefix: str = "resp", **kwargs: Any) -> None:  # noqa: ANN401 - RecordedTransport's
        super().__init__(**kwargs)
        self.credential = credential
        self._prefix = prefix
        self.billed: dict[str, TokenUsage] = {}
        self.lookups: list[tuple[str, str]] = []
        self.responses: list[str] = []

    @override
    async def stream(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        receipt = current_receipt()
        response_id = f"{self._prefix}_{len(self.responses) + 1}"
        self.responses.append(response_id)
        if receipt is not None:
            receipt.note(response_id, stored=True)
        async for event in super().stream(
            messages, tools, system, model, thinking_budget, effort, system_blocks, max_output_tokens, tool_choice=tool_choice
        ):
            yield event

    async def reported_usage(self, response_id: str) -> TokenUsage | None:
        self.lookups.append((response_id, self.credential))
        return self.billed.get(response_id)


def reply_events(text: str, *, usage: TokenUsage | None = None) -> StreamCompleteEvent:
    return StreamCompleteEvent(
        response=ModelResponse(
            message=AssistantMessage(content=[TextPart(text=text)]),
            usage=usage or TokenUsage(input_tokens=10, output_tokens=5),
            stop_reason=StopReason.END_TURN,
            model="provider-model-name",
        )
    )


def org_key_route(
    route: PinnedRoute, transport: LLMTransport, *, pays: Payer = "org", region: str | None = None
) -> ResolvedRoute:
    access = AccessRoute(
        kind=RouteKind.GATEWAY,
        api=ModelApi.CHAT_COMPLETIONS,
        provider=route.provider,
        model=route.model,
        account=AccountRef(AccountKind.ORG_API_KEY, route.config_key or "default"),
        pays=pays,
        region=region,
    )
    return ResolvedRoute(access=access, transport=transport, pricing_model=route.config_model or route.model)


ROUTE = PinnedRoute("openai", "gpt-x", config_key="key-1", config_model="claude-sonnet-4-6")


@dataclass
class GatewayRig:
    """A gateway over in-memory ports. ``pin`` registers a run; ``token`` mints its gateway token."""

    transport: RecordedTransport = field(default_factory=RecordedTransport)
    clock: ManualClock = field(default_factory=ManualClock)
    grants: InMemoryRunModelGrants = field(default_factory=InMemoryRunModelGrants)
    resolver: StaticModelAccessResolver = field(default_factory=StaticModelAccessResolver)
    legacy: InMemorySpendCounter = field(default_factory=InMemorySpendCounter)
    allowance: OutputAllowance = field(default_factory=OutputAllowance)
    # P4.17: the engine calls' output limit and the repetition cut, as production's default.
    limits: OutputLimits = field(default_factory=OutputLimits)
    ledger: InMemoryBudgetLedger = field(init=False)
    budget: BudgetAccounts | None = None
    # P9: where the gateway's spans go (none by default).
    tracer: HarnessTracer = field(default_factory=lambda: NOOP_TRACER)
    # SEC2 O-2: where refusals of a run's calls are reported (Node's journal in production).
    refusal_sink: RefusalSink = field(default_factory=InMemoryRefusalSink)
    # OWD-15: whether a conversation's data may reach the call's model (none: every model may).
    classification: ConversationClassification | None = None
    # G-45: each provider's calls in flight, by the run's priority class (none: no limit).
    gate: ProviderAdmission | None = None
    # What the gateway resolves through, built over ``resolver`` (a ``GuardedModelAccess``); none: ``resolver``.
    wrap_resolver: Callable[[StaticModelAccessResolver], ModelAccessResolver] | None = None
    # WP-66: a cut call's hold and estimate; the usage lookup goes out at once here.
    cut_calls: CutCalls = field(default_factory=lambda: CutCalls(reconcile_delay_s=0))
    mint: TokenMint = field(init=False)
    gateway: ModelGateway = field(init=False)
    refusals: RefusalReporter = field(init=False)

    def __post_init__(self) -> None:
        self.mint = TokenMint(self.clock)
        self.ledger = InMemoryBudgetLedger(self.clock)
        self.refusals = RefusalReporter(self.refusal_sink)
        self.gateway = ModelGateway(
            verifier=GatewayTokenVerifier(self.mint.source, self.clock),
            grants=self.grants,
            resolver=self.wrap_resolver(self.resolver) if self.wrap_resolver is not None else self.resolver,
            budget=self.budget or self.ledger,
            clock=self.clock,
            ids=SeededIds(0),
            allowance=self.allowance,
            limits=self.limits,
            legacy_spend=self.legacy,
            tracer=self.tracer,
            refusals=self.refusals,
            classification=self.classification,
            gate=self.gate,
            cut_calls=self.cut_calls,
        )

    def pin(
        self,
        run_id: str = "run-1",
        *,
        tenant: str = "org-1",
        routes: tuple[PinnedRoute, ...] = (ROUTE,),
        budget: RunBudget | None = None,
        effort: str | None = None,
        pays: Payer = "org",
        transport: LLMTransport | None = None,
        parent_run_id: str | None = None,
        actor: str | None = "user-1",
        engine: str | None = "opencode",
        carried: Mapping[str, str] | None = None,
        priority_class: PriorityClass | None = None,
        region: str | None = None,
        roles: Mapping[str, PinnedRoute] | None = None,
        role_transport: LLMTransport | None = None,
    ) -> RunAttemptBinding:
        """Registers a run's grant and what its host pinned; the ledger opens its account
        on the first reservation (a child's under ``parent_run_id``'s). ``roles``: the engine
        roles' own routes (GA-15), served by ``role_transport`` (else the run's)."""
        pinned = budget or RunBudget(max_cost_usd=2.0, max_tokens=400_000)
        self.grants.register(
            RunModelGrant(
                tenant=tenant,
                run_id=run_id,
                routes=routes,
                models=neutral_models(routes, roles),
                budget=pinned,
                reasoning_effort=effort,
                actor=actor,
                engine=engine,
                carried=tuple(sorted((carried or {}).items())),
                priority_class=priority_class,
            )
        )
        cap = Cap(
            usd_micros(pinned.max_cost_usd) if pinned.max_cost_usd is not None else None,
            pinned.max_tokens,
        )
        parent = RunAccountRef(tenant, parent_run_id) if parent_run_id is not None else None
        self.ledger.pin(RunAccountRef(tenant, run_id), PinnedBudget(cap, parent))
        for route in routes:
            self.resolver.add(
                tenant, route, org_key_route(route, transport or self.transport, pays=pays, region=region)
            )
        for route in (roles or {}).values():
            served = role_transport or transport or self.transport
            self.resolver.add(tenant, route, org_key_route(route, served, pays=pays, region=region))
        return RunAttemptBinding(
            tenant=tenant,
            scope="session-1",
            run_id=run_id,
            parent_run_id=parent_run_id,
            attempt_id=f"{run_id}-a1",
            fence=1,
            tool_ids_digest="tools",
            routes_digest=route_digest(routes),
        )

    def token(self, binding: RunAttemptBinding, **kwargs: Any) -> str:  # noqa: ANN401 - TokenMint.mint's options
        return self.mint.mint(binding, **kwargs)


def body_of(value: object):  # noqa: ANN201 - a ModelGateway body reader
    async def read() -> object:
        return value

    return read


__all__ = [
    "ROUTE",
    "GatewayRig",
    "RecordedTransport",
    "StoringTransport",
    "TransportCall",
    "body_of",
    "org_key_route",
    "reply_events",
]
