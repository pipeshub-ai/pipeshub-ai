"""Model calls a run makes itself (its own loop, not through the model gateway), charged
to its ledger account (P6.5): ``MeteredTransport`` reserves before each call and
settles after it, as the gateway does, so a loop run and its children (deep mode's
sub-agents) draw from one tree whose root caps them all. ``RunBudgetView`` keeps what
the run's account said last, for decisions that cannot wait on the ledger (a
child's share, ``LedgerChildBudget``).
"""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, override, runtime_checkable

from harness_core.access.spend import (
    OutputAllowance,
    estimate_spend,
    input_estimate,
    ledger_usage,
    output_estimate,
    reported_nothing,
    reservation_for,
    usage_spend,
)
from harness_core.budget.errors import BudgetRefused, BudgetUnavailable
from harness_core.budget.roles import charged_as, current_charge_role
from harness_core.journal.children import BudgetShare, child_budget_share
from harness_core.kernel.core.streaming import (
    StreamCompleteEvent,
    TextDeltaEvent,
    ThinkingDeltaEvent,
    ToolCallDeltaEvent,
)
from harness_core.logs import HarnessLog
from harness_core.models.output_bound import output_limit
from harness_core.models.transports.admitted import AdmittedTransport
from harness_core.models.transports.base import LLMTransport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from harness_core.budget.ports import BudgetAccounts
    from harness_core.budget.types import Remaining, Reservation, RunAccountRef
    from harness_core.budget.wrap_up import WrapUpReserve
    from harness_core.ids import IdSource
    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.responses import (
        ModelResponse,
        StructuredResponse,
        TokenUsage,
    )
    from harness_core.kernel.core.streaming import StreamEvent
    from harness_core.kernel.core.tool_schema import ToolChoice, ToolSchema

# A run's own call lasts at most its turn; a crashed worker's hold returns after this.
METERED_RESERVATION_TTL_S = 900


class RunBudgetView:
    """What the run's account said last (after a reservation or a settlement)."""

    def __init__(self) -> None:
        self.remaining: Remaining | None = None

    def tokens_left(self) -> int | None:
        return None if self.remaining is None else self.remaining.available.tokens


@dataclass(frozen=True)
class ReservedCall:
    reservation: Reservation
    estimate: int


@runtime_checkable
class ReservesAhead(Protocol):
    """A metered transport, or one decorating it, that can reserve a call before making it (the follow-ups)."""

    async def reserve_ahead(
        self, messages: list[Message], system: str | None, *, max_output_tokens: int | None, ttl_s: int
    ) -> ReservedCall: ...

    async def complete_reserved(
        self, reserved: ReservedCall, messages: list[Message], *, system: str | None, max_output_tokens: int | None
    ) -> ModelResponse: ...

    async def release_reserved(self, reserved: ReservedCall) -> None: ...


class MeteredTransport(LLMTransport):
    """Decorates a run's transport: every call is reserved on the run's account first
    (input estimate plus the output allowance, at least the input) and settled after
    (the provider's usage; an estimate when a stream broke; released when the provider
    failed before any output). A refusal is raised before the provider is called."""

    def __init__(
        self,
        inner: LLMTransport,
        *,
        accounts: BudgetAccounts,
        account: RunAccountRef,
        pricing_model: str,
        ids: IdSource,
        charges_usd: bool = True,
        allowance: OutputAllowance | None = None,
        view: RunBudgetView | None = None,
        ttl_s: int = METERED_RESERVATION_TTL_S,
        log: HarnessLog | None = None,
        wrap_up: WrapUpReserve | None = None,
        model_role: str = "primary",
    ) -> None:
        self._inner = inner
        # GA-15: what its calls are charged as, unless the caller names another role for a call (``charged_as``).
        self._model_role = model_role
        self._accounts = accounts
        self._account = account
        self._model = pricing_model
        self._ids = ids
        self._charges_usd = charges_usd
        self._allowance = allowance or OutputAllowance()
        self.view = view or RunBudgetView()
        self._ttl_s = ttl_s
        self._log = log or HarnessLog.named("harness_core.budget.metered")
        # The run's wrap-up reserve (BUDGETEND): the armed call draws on it instead of reserving.
        self.wrap_up = wrap_up

    @property
    @override
    def provider(self) -> str:
        return self._inner.provider

    @property
    @override
    def model_name(self) -> str:
        return self._inner.model_name

    @property
    @override
    def accepts_cache_marks(self) -> bool:
        return self._inner.accepts_cache_marks

    @property
    @override
    def accepts_output_limit(self) -> bool:
        return self._inner.accepts_output_limit

    @override
    def keep_provider_state(self) -> bool:
        return self._inner.keep_provider_state()

    @contextlib.asynccontextmanager
    async def _provider(self) -> AsyncIterator[LLMTransport]:
        """The transport to call, with the call's provider slot taken first when the one below admits (G-45): the
        slot before the reservation, as the model gateway orders them, so a call queued for its provider holds none
        of the run's budget (a parent's share its children could use) and its reservation's TTL runs from the call."""
        inner = self._inner
        if not isinstance(inner, AdmittedTransport):
            yield inner
            return
        slot = await inner.admit()
        try:
            yield inner.unadmitted
        finally:
            slot.release()

    async def _reserve(
        self,
        messages: Sequence[Message],
        system: str | None,
        tools: Sequence[ToolSchema],
        output_limit: int | None = None,
    ) -> tuple[Reservation, int]:
        # Off the event loop: the model's tokenizer counts a long history in milliseconds (round 14, R14-LOOP).
        estimate = await asyncio.to_thread(input_estimate, messages, system, tools, self._model)
        drawn = self.wrap_up.take() if self.wrap_up is not None else None
        if drawn is not None:
            return drawn, estimate
        requested, minimum = reservation_for(
            estimate, self._model, self._allowance, charges_usd=self._charges_usd, output_limit=output_limit
        )
        try:
            held = await self._accounts.reserve(
                self._account, self._ids.new_id("call"), requested, minimum=minimum, ttl_s=self._ttl_s
            )
        except BudgetRefused as refused:
            self._log.warning("budget.refused", reason=refused.reason_code, runId=self._account.run_id)
            raise
        except Exception as exc:
            self._log.warning("budget.refused", reason=BudgetUnavailable.reason_code, errorType=type(exc).__name__)
            raise BudgetUnavailable("the budget ledger cannot be reached", detail=type(exc).__name__) from exc
        return held, estimate

    async def _commit(self, held: Reservation, usage: TokenUsage | None, estimate: int, streamed: str) -> None:
        produced: int | None = None
        if usage is None or reported_nothing(usage):
            produced = await asyncio.to_thread(output_estimate, streamed, self._model)
            amount = estimate_spend(estimate, produced, self._model, charges_usd=self._charges_usd)
        else:
            amount = usage_spend(usage, self._model, charges_usd=self._charges_usd)
        used = ledger_usage(usage, input_estimate=estimate, streamed=streamed, model=self._model, streamed_tokens=produced)
        try:
            settled = await self._accounts.commit(
                held,
                amount,
                usage=used,
                model_role=current_charge_role() or self._model_role,
            )
        except Exception as exc:
            # The reservation expires; the spend it did not record is the one under-count.
            self._log.error("budget.charge_failed", errorType=type(exc).__name__, costMicroUsd=amount.usd_micros)
            return
        self.view.remaining = settled.remaining
        if settled.crossed:
            self._log.warning(
                "budget.crossed",
                reason="budget_exhausted",
                runId=self._account.run_id,
                spentMicroUsd=settled.remaining.committed.usd_micros,
                tokens=settled.remaining.committed.tokens,
            )

    async def _release(self, held: Reservation) -> None:
        try:
            settled = await self._accounts.release(held)
        except Exception as exc:
            self._log.error("budget.release_failed", errorType=type(exc).__name__)
            return
        self.view.remaining = settled.remaining

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
        cache_marks: Sequence[int] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> ModelResponse:
        async with self._provider() as provider:
            held, estimate = await self._reserve(
                messages, system or "\n\n".join(system_blocks or []), tools or [], max_output_tokens
            )
            try:
                response = await provider.complete(
                    messages=messages,
                    tools=tools,
                    system=system,
                    model=model,
                    thinking_budget=thinking_budget,
                    effort=effort,
                    system_blocks=system_blocks,
                    **_marks(cache_marks),
                    **output_limit(max_output_tokens),
                    **({"tool_choice": tool_choice} if tool_choice else {}),
                )
            except BaseException:
                await self._release(held)
                raise
            await self._commit(held, response.usage, estimate, response.message.text)
            return response

    async def reserve_ahead(
        self, messages: list[Message], system: str | None, *, max_output_tokens: int | None, ttl_s: int
    ) -> ReservedCall:
        """A call's reservation made now, while the attempt holds its lease (the ledger refuses one later), for a
        call made after it (``complete_reserved``): the run's end need not wait for the call (UX5)."""
        estimate = await asyncio.to_thread(input_estimate, messages, system, [], self._model)
        requested, minimum = reservation_for(
            estimate, self._model, self._allowance, charges_usd=self._charges_usd, output_limit=max_output_tokens
        )
        held = await self._accounts.reserve(
            self._account, self._ids.new_id("call"), requested, minimum=minimum, ttl_s=ttl_s
        )
        return ReservedCall(held, estimate)

    async def complete_reserved(
        self, reserved: ReservedCall, messages: list[Message], *, system: str | None, max_output_tokens: int | None
    ) -> ModelResponse:
        """The call ``reserve_ahead`` held for, settled on that reservation (a settlement needs no lease)."""
        try:
            async with self._provider() as provider:
                response = await provider.complete(
                    messages=messages,
                    tools=None,
                    system=system,
                    model=None,
                    thinking_budget=None,
                    effort=None,
                    system_blocks=None,
                    **output_limit(max_output_tokens),
                )
        except BaseException:
            await self._release(reserved.reservation)
            raise
        await self._commit(reserved.reservation, response.usage, reserved.estimate, response.message.text)
        return response

    async def release_reserved(self, reserved: ReservedCall) -> None:
        await self._release(reserved.reservation)

    @override
    async def complete_structured(
        self,
        messages: list[Message],
        output_schema: dict[str, Any],
        system: str | None = None,
        model: str | None = None,
        max_output_tokens: int | None = None,
    ) -> StructuredResponse:
        async with self._provider() as provider:
            held, estimate = await self._reserve(messages, system, [], max_output_tokens)
            try:
                response = await provider.complete_structured(
                    messages=messages,
                    output_schema=output_schema,
                    system=system,
                    model=model,
                    **output_limit(max_output_tokens),
                )
            except BaseException:
                await self._release(held)
                raise
            await self._commit(held, response.usage, estimate, "")
            return response

    @override
    def stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None = None,
        system: str | None = None,
        model: str | None = None,
        thinking_budget: int | None = None,
        effort: str | None = None,
        system_blocks: list[str] | None = None,
        cache_marks: Sequence[int] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        return self._metered_stream(
            messages, tools, system, model, thinking_budget, effort, system_blocks, cache_marks, max_output_tokens,
            tool_choice=tool_choice,
        )

    async def _metered_stream(
        self,
        messages: list[Message],
        tools: list[ToolSchema] | None,
        system: str | None,
        model: str | None,
        thinking_budget: int | None,
        effort: str | None,
        system_blocks: list[str] | None,
        cache_marks: Sequence[int] | None = None,
        max_output_tokens: int | None = None,
        *,
        tool_choice: ToolChoice | None = None,
    ) -> AsyncIterator[StreamEvent]:
        async with self._provider() as provider:
            held, estimate = await self._reserve(
                messages, system or "\n\n".join(system_blocks or []), tools or [], max_output_tokens
            )
            streamed: list[str] = []
            usage: TokenUsage | None = None
            emitted = False
            try:
                async for event in provider.stream(
                    messages=messages,
                    tools=tools,
                    system=system,
                    model=model,
                    thinking_budget=thinking_budget,
                    effort=effort,
                    system_blocks=system_blocks,
                    **_marks(cache_marks),
                    **output_limit(max_output_tokens),
                    **({"tool_choice": tool_choice} if tool_choice else {}),
                ):
                    emitted = True
                    if isinstance(event, StreamCompleteEvent):
                        usage = event.response.usage
                    elif isinstance(event, (TextDeltaEvent, ThinkingDeltaEvent)):
                        # Reasoning is billed as output too: a stream cut before its usage arrived settles on both.
                        streamed.append(event.delta)
                    elif isinstance(event, ToolCallDeltaEvent):
                        streamed.append(event.arguments_delta)
                    yield event
            except BaseException:
                if emitted:
                    await self._commit(held, usage, estimate, "".join(streamed))
                else:
                    await self._release(held)
                raise
            await self._commit(held, usage, estimate, "".join(streamed))


def _marks(cache_marks: Sequence[int] | None) -> dict[str, Any]:
    return {} if cache_marks is None else {"cache_marks": cache_marks}


class LedgerChildBudget:
    """``ChildBudget`` (K-L's seam) over the ledger: a deep run's child gets its share of
    what the parent's account has left, as of the parent's last model call, not of the
    parent's pinned budget. Node opens the child's account with that share as its cap
    and refuses one the parent cannot cover (``CHILD_BUDGET_EXCEEDS_REMAINING``)."""

    def __init__(self, view: RunBudgetView) -> None:
        self._view = view

    def share(
        self,
        *,
        parent_max_turns: int,
        parent_max_tokens: int,
        definition_max_turns: int,
        batch: int,
    ) -> BudgetShare:
        left = self._view.tokens_left()
        return child_budget_share(
            parent_max_turns=parent_max_turns,
            parent_max_tokens=parent_max_tokens if left is None else min(parent_max_tokens, left),
            definition_max_turns=definition_max_turns,
            batch=batch,
        )


__all__ = [
    "METERED_RESERVATION_TTL_S",
    "charged_as",
    "current_charge_role",
    "LedgerChildBudget",
    "MeteredTransport",
    "ReservedCall",
    "ReservesAhead",
    "RunBudgetView",
]
