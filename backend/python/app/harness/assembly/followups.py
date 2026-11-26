"""Suggested next questions after a durable answer (UX5 D-6), an after-run step: the run ends at once.

While the attempt still holds its lease, the step reserves the call on the run's ledger (only a leased attempt may);
once the terminal commit has landed, it asks the run's own model, with a small output limit under P4.17's bounds, and
settles that reservation (a settlement needs no lease). The model sees only the user's question and the final
answer, so a suggestion can say nothing the person was not shown. The suggestions go to the activity bus (a waiting
browser wakes) and to Node, which keeps them with the run for a reload. A call that fails or misses its deadline
offers nothing and changes nothing else.
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, Protocol, cast

from harness_core.activity import shared_redactor
from harness_core.budget.metered import ReservedCall, ReservesAhead
from harness_core.journal.types import FOLLOWUPS, LiveFrame
from harness_core.kernel.core.messages import UserMessage
from harness_core.models.transport_model import TransportModel

if TYPE_CHECKING:
    from collections.abc import Sequence

    from pydantic import JsonValue

    from harness_core.journal.ports import LiveSinkPort
    from harness_core.journal.types import Claim
    from harness_core.kernel.core.messages import Message
    from harness_core.kernel.core.responses import ModelResponse
    from harness_core.kernel.durable.ports import AfterRunStep, RunEnding
    from harness_core.logs import HarnessLog
    from harness_core.models.transports.base import LLMTransport

FOLLOWUPS_STEP: Final = "followups"
FOLLOWUPS_KIND: Final = "followups"
# Enough for three short questions and a reasoning model's brief thought; a call that spends it all offers nothing.
OUTPUT_TOKENS: Final = 384
DEADLINE_S: Final = 10.0
# The reservation outlives the call it holds for, so a slow provider is still settled on it.
RESERVATION_TTL_S: Final = 60
MAX_ITEMS: Final = 3
MIN_ITEMS: Final = 2
MAX_CHARS: Final = 160
SHOWN_CHARS: Final = 6000

SYSTEM: Final = (
    "You suggest what the user could ask next in a conversation with an assistant. Reply with only a JSON array of "
    "two or three short follow-up questions or requests the user might send next, in the same language as the "
    "user's question, each under 120 characters, specific to the answer, written as the user would type them. "
    "Do not repeat the question, do not answer anything, and add nothing outside the JSON array."
)


class FollowupsSink(Protocol):
    """Where the suggestions are kept for the run (Node, with the attempt's token)."""

    async def deliver(self, items: Sequence[str]) -> bool: ...


def _bounded(text: str) -> str:
    return text if len(text) <= SHOWN_CHARS else text[:SHOWN_CHARS] + "…"


def parse_followups(text: str, question: str) -> list[str]:
    """Two or three distinct, trimmed suggestions from the model's reply; none when fewer than two survive."""
    found = re.search(r"\[[\s\S]*\]", text)
    raw: object = None
    if found is not None:
        try:
            raw = json.loads(found.group(0))
        except ValueError:
            raw = None
    candidates: list[object] = (
        cast("list[object]", raw)
        if isinstance(raw, list)
        else [line.lstrip("-*0123456789. ").strip() for line in text.splitlines()]
    )
    redact = shared_redactor()
    seen = {question.strip().casefold()}
    items: list[str] = []
    for item in candidates:
        if not isinstance(item, str):
            continue
        shown = redact(" ".join(item.split()).strip(" \"'"))
        if not shown or len(shown) > MAX_CHARS or shown.casefold() in seen:
            continue
        seen.add(shown.casefold())
        items.append(shown)
        if len(items) == MAX_ITEMS:
            break
    return items if len(items) >= MIN_ITEMS else []


@dataclass(frozen=True)
class _Prepared:
    messages: list[Message]
    reserved: ReservedCall | None


class FollowupsStep:
    """``AfterRunStep``: 2–3 suggestions after an answered top-level run."""

    key = FOLLOWUPS_STEP

    def __init__(
        self,
        transport: LLMTransport,
        *,
        question: str,
        run_id: str,
        fence: int,
        bus: LiveSinkPort | None,
        sink: FollowupsSink | None,
        log: HarnessLog,
        deadline_s: float = DEADLINE_S,
    ) -> None:
        base = TransportModel(transport, purpose="structured").output_limits()
        self._limit = min(OUTPUT_TOKENS, base.structured) if base.structured else OUTPUT_TOKENS
        bounded = dataclasses.replace(base, structured=self._limit, repetition=True)
        self._transport = transport
        self._model = TransportModel(transport, purpose="structured", limits=bounded)
        self._question = question
        self._run_id = run_id
        self._fence = fence
        self._bus = bus
        self._sink = sink
        self._log = log
        self._deadline_s = deadline_s

    async def prepare(self, ending: RunEnding) -> object | None:
        answer = ending.answer if ending.answer is not None else (ending.texts[-1] if ending.texts else "")
        if ending.outcome != "answered" or not answer.strip() or not self._question.strip():
            return None
        prompt = f"User's question:\n{_bounded(self._question)}\n\nAssistant's answer:\n{_bounded(answer)}"
        messages: list[Message] = [UserMessage(content=prompt)]
        if not isinstance(self._transport, ReservesAhead):
            return _Prepared(messages, None)
        reserved = await self._transport.reserve_ahead(
            messages, SYSTEM, max_output_tokens=self._wire_limit(), ttl_s=RESERVATION_TTL_S
        )
        return _Prepared(messages, reserved)

    async def run(self, prepared: object) -> None:
        assert isinstance(prepared, _Prepared)
        try:
            # In this task, so a deadline lands inside the call and its reservation is released there.
            async with asyncio.timeout(self._deadline_s):
                response = await self._call(prepared)
        except TimeoutError:
            self._log.info("followups.skipped", reason="deadline")
            return
        items = parse_followups(response.message.text or "", self._question)
        self._log.info("followups.offered", count=len(items))
        if items:
            await self._deliver(items)

    async def discard(self, prepared: object) -> None:
        if isinstance(prepared, _Prepared) and prepared.reserved is not None:
            assert isinstance(self._transport, ReservesAhead)
            await self._transport.release_reserved(prepared.reserved)

    def _wire_limit(self) -> int | None:
        return self._limit if self._transport.accepts_output_limit else None

    async def _call(self, prepared: _Prepared) -> ModelResponse:
        if prepared.reserved is None:
            return await self._model.complete(prepared.messages, system=SYSTEM)
        assert isinstance(self._transport, ReservesAhead)
        return await self._transport.complete_reserved(
            prepared.reserved, prepared.messages, system=SYSTEM, max_output_tokens=self._wire_limit()
        )

    async def _deliver(self, items: list[str]) -> None:
        data: dict[str, JsonValue] = {"items": [{"text": item} for item in items]}
        if self._bus is not None:
            try:
                await self._bus.publish(LiveFrame(self._run_id, self._fence, 0, 0, FOLLOWUPS_KIND, data))  # pyright: ignore[reportArgumentType]
            except Exception as exc:
                self._log.info("followups.unpublished", errorType=type(exc).__name__)
        if self._sink is not None:
            try:
                kept = await self._sink.deliver(items)
            except Exception as exc:
                self._log.warning("followups.unkept", errorType=type(exc).__name__)
                return
            if not kept:
                self._log.warning("followups.unkept", reason="refused")


def followups_steps(
    transport: LLMTransport | None,
    *,
    claim: Claim,
    question: str,
    enabled: bool,
    child: bool,
    bus: LiveSinkPort | None,
    sink: FollowupsSink | None,
    log: HarnessLog,
) -> list[AfterRunStep]:
    """The step for a top-level run whose org has suggestions on and whose model has a transport. A Node that does
    not keep suggestions (UX2's handshake) gets none sent; the browser still sees them on the activity bus."""
    if not enabled or child or transport is None:
        return []
    kept = sink if claim.journal_accepts(FOLLOWUPS) else None
    return [
        FollowupsStep(
            transport, question=question, run_id=claim.run_id, fence=claim.fence, bus=bus, sink=kept, log=log
        )
    ]


__all__ = ["FOLLOWUPS_KIND", "FOLLOWUPS_STEP", "FollowupsSink", "FollowupsStep", "followups_steps", "parse_followups"]
