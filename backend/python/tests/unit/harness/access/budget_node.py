"""Node's budget-ledger routes as a fake: the wire over the in-memory reference ledger.

``answer`` takes what a request carries (method, the path after ``/runs/``, body)
and returns Node's status and JSON, so an httpx mock transport and an aiohttp app
can both serve it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast

from harness_core.budget.errors import BudgetRefused
from harness_core.budget.types import (
    ZERO,
    Amount,
    Hold,
    LedgerUsage,
    Remaining,
    Reservation,
    RunAccountRef,
    Settlement,
)

if TYPE_CHECKING:
    from harness_core.budget.memory import InMemoryBudgetLedger

Json = dict[str, object]


def _amount(raw: object) -> Amount:
    body = cast("dict[str, int]", raw)
    return Amount(body["usdMicros"], body["tokens"])


def wire_amount(amount: Amount) -> Json:
    return {"usdMicros": amount.usd_micros, "tokens": amount.tokens}


def wire_remaining(left: Remaining) -> Json:
    return {
        "available": {"usdMicros": left.available.usd_micros, "tokens": left.available.tokens},
        "committed": wire_amount(left.committed),
        "reserved": wire_amount(left.reserved),
        "exhausted": left.exhausted,
        "estimated": left.estimated,
        "used": {
            "inputTokens": left.used.input_tokens,
            "outputTokens": left.used.output_tokens,
            "cacheReadTokens": left.used.cache_read_tokens,
            "cacheWriteTokens": left.used.cache_write_tokens,
        },
    }


def wire_reservation(held: Reservation) -> Json:
    return {
        "key": held.key,
        "requested": wire_amount(held.requested),
        "granted": wire_amount(held.granted),
        "expiresAt": int(held.expires_at.timestamp() * 1000),
        "state": held.state.value,
    }


def wire_settlement(settled: Settlement) -> Json:
    return {
        "reservation": wire_reservation(settled.reservation),
        "charged": wire_amount(settled.charged),
        "crossed": settled.crossed,
        "remaining": wire_remaining(settled.remaining),
    }


@dataclass
class BudgetNode:
    ledger: InMemoryBudgetLedger
    tenant: str
    held: dict[tuple[str, str], Reservation] = field(default_factory=dict)
    calls: list[str] = field(default_factory=list)
    # What Node's read says binds the run (WP56-STOP-1): a shared budget's scope, or None for the run's own.
    budget_scope: str | None = None

    async def answer(self, method: str, run_id: str, rest: list[str], body: Json) -> tuple[int, Json]:
        """``rest`` is the path after ``/runs/{runId}``, split on ``/``."""
        account = RunAccountRef(self.tenant, run_id)
        self.calls.append(f"{method} {'/'.join(rest)}")
        try:
            if method == "GET":
                left = wire_remaining(await self.ledger.remaining(account))
                return 200, {"remaining": left if self.budget_scope is None else {**left, "budgetScope": self.budget_scope}}
            if rest[-1] == "reservations":
                opening = body.get("opening")
                held = await self.ledger.reserve(
                    account,
                    cast("str", body["key"]),
                    _amount(body["requested"]),
                    minimum=_amount(body["minimum"]),
                    ttl_s=cast("int", body["ttlS"]),
                    opening=_amount(opening) if opening is not None else None,
                )
                self.held[run_id, held.key] = held
                return 200, {"reservation": wire_reservation(held), "remaining": wire_remaining(await self.ledger.remaining(account))}
            key = rest[-2]
            known = self.held.get((run_id, key)) or Reservation(account, key, ZERO, ZERO, datetime.fromtimestamp(0, tz=UTC))
            usage = cast("dict[str, int] | None", body.get("usage"))
            used = (
                LedgerUsage(usage["inputTokens"], usage["outputTokens"], usage["cacheReadTokens"], usage["cacheWriteTokens"])
                if usage is not None
                else None
            )
            if rest[-1] == "commit":
                hold = body.get("hold")
                kept = Hold(_amount(hold), cast("int", body["holdTtlS"])) if hold is not None else None
                return 200, wire_settlement(await self.ledger.commit(known, _amount(body["actual"]), usage=used, hold=kept))
            if rest[-1] == "reconcile":
                return 200, wire_settlement(await self.ledger.reconcile(known, _amount(body["actual"]), usage=used))
            return 200, wire_settlement(await self.ledger.release(known))
        except BudgetRefused as refused:
            return 409, {"error": {"code": f"AGENT_HARNESS_{refused.reason_code.upper()}"}}


__all__ = ["BudgetNode", "wire_amount", "wire_remaining", "wire_reservation", "wire_settlement"]
