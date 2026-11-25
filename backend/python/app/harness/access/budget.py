"""``BudgetAccounts`` over Node: the run's ledger account, reached with the run's own gateway token.

Node is the ledger's one authority (P6.5): accounts and reservations are Mongo
documents it writes, a child's in the transaction that admits it. The model
gateway reserves before a call and settles after it through these routes; Node
checks the token, the attempt's lease for a reservation (not for settling: the
spend happened), and opens a run's account lazily from its pinned budget.

  POST …/runs/{runId}/budget/reservations                  {key, requested, minimum, ttlS, opening?}
  POST …/runs/{runId}/budget/reservations/{key}/commit     {actual, usage?, hold?, holdTtlS?}
  POST …/runs/{runId}/budget/reservations/{key}/reconcile  {actual, usage?}
  POST …/runs/{runId}/budget/reservations/{key}/release    {}
  GET  …/runs/{runId}/budget
"""

from __future__ import annotations

import re
from abc import ABC, abstractmethod
from datetime import UTC, datetime
from typing import TYPE_CHECKING, cast, override
from urllib.parse import quote

from app.harness.journal_node.client import (
    INTERNAL_PREFIX,
    JournalUnavailable,
    error_code,
)
from app.harness.journal_node.http import NodeServiceClient
from harness_core.budget.errors import REFUSALS_BY_CODE, BudgetUnavailable
from harness_core.budget.types import (
    Amount,
    Available,
    Hold,
    LedgerUsage,
    Remaining,
    Reservation,
    ReservationState,
    Settlement,
)

if TYPE_CHECKING:
    import httpx
    from pydantic import JsonValue

    from app.harness.journal_node.client import AttemptRoute
    from harness_core.budget.types import RunAccountRef

Json = dict[str, object]
# Node refused the caller, not the budget: the gateway answers 403, not 503.
_CALLER_REFUSED = (401, 403, 404, 409)


def _amount_wire(amount: Amount) -> Json:
    return {"usdMicros": amount.usd_micros, "tokens": amount.tokens}


def _int(value: object, *, nullable: bool = False) -> int | None:
    if isinstance(value, bool) or not (isinstance(value, int) or (nullable and value is None)):
        raise BudgetUnavailable("the budget answer is malformed", detail="malformed_budget")
    return value


def _amount(raw: object) -> Amount:
    body = cast("Json", raw) if isinstance(raw, dict) else {}
    return Amount(cast("int", _int(body.get("usdMicros"))), cast("int", _int(body.get("tokens"))))


def _usage_wire(usage: LedgerUsage) -> Json:
    return {
        "inputTokens": usage.input_tokens,
        "outputTokens": usage.output_tokens,
        "cacheReadTokens": usage.cache_read_tokens,
        "cacheWriteTokens": usage.cache_write_tokens,
    }


def _usage(raw: object) -> LedgerUsage:
    body = cast("Json", raw) if isinstance(raw, dict) else {}
    counts = [cast("int", _int(body.get(k, 0))) for k in ("inputTokens", "outputTokens", "cacheReadTokens", "cacheWriteTokens")]
    return LedgerUsage(*counts)


def _remaining(raw: object) -> Remaining:
    body = cast("Json", raw) if isinstance(raw, dict) else {}
    available = cast("Json", body.get("available")) if isinstance(body.get("available"), dict) else {}
    return Remaining(
        available=Available(
            _int(available.get("usdMicros"), nullable=True), _int(available.get("tokens"), nullable=True)
        ),
        committed=_amount(body.get("committed")),
        reserved=_amount(body.get("reserved")),
        exhausted=body.get("exhausted") is True,
        used=_usage(body.get("used")),
        budget_scope=_scope(body.get("budgetScope")),
        # Absent from a Node before WP-66: nothing it holds is estimated.
        estimated=cast("int", _int(body.get("estimated", 0))),
    )


# A shared budget's scope as Node names it (WP56-STOP-1): opaque here, kept only in its shape.
_SCOPE = re.compile(r"[a-z][a-z0-9_-]{0,31}")


def _scope(value: object) -> str | None:
    return value if isinstance(value, str) and _SCOPE.fullmatch(value) else None


class SharedBudgetReasons:
    """Which shared budget a refusal names by its finer reason (Node's ``reasonCode``, C9.06), for an edition
    that keeps budgets beyond the run's own. The default names none: only the run's own limit refuses."""

    def scope_of(self, reason_code: str) -> str | None:
        return None


NO_SHARED_BUDGET_REASONS = SharedBudgetReasons()


def _reservation(account: RunAccountRef, raw: object) -> Reservation:
    body = cast("Json", raw) if isinstance(raw, dict) else {}
    key, state = body.get("key"), body.get("state")
    if not isinstance(key, str) or state not in {s.value for s in ReservationState}:
        raise BudgetUnavailable("the budget answer is malformed", detail="malformed_budget")
    expires = cast("int", _int(body.get("expiresAt")))
    return Reservation(
        account=account,
        key=key,
        requested=_amount(body.get("requested")),
        granted=_amount(body.get("granted")),
        expires_at=datetime.fromtimestamp(expires / 1000, tz=UTC),
        state=ReservationState(cast("str", state)),
    )


class _NodeBudgetWire(ABC):
    """The ledger routes' bodies and answers; subclasses say how a request reaches Node."""

    @abstractmethod
    async def _send(
        self, method: str, account: RunAccountRef, path: str, credential: str | None, body: Json | None
    ) -> httpx.Response: ...

    async def reserve(
        self,
        account: RunAccountRef,
        key: str,
        requested: Amount,
        *,
        minimum: Amount,
        ttl_s: int,
        opening: Amount | None = None,
        credential: str | None = None,
    ) -> Reservation:
        body: Json = {
            "key": key,
            "requested": _amount_wire(requested),
            "minimum": _amount_wire(minimum),
            "ttlS": ttl_s,
            **({"opening": _amount_wire(opening)} if opening is not None else {}),
        }
        answer = await self._call("POST", account, "/budget/reservations", credential, body)
        return _reservation(account, answer.get("reservation"))

    async def commit(
        self,
        reservation: Reservation,
        actual: Amount,
        *,
        usage: LedgerUsage | None = None,
        credential: str | None = None,
        hold: Hold | None = None,
        model_role: str | None = None,
    ) -> Settlement:
        path = f"/budget/reservations/{quote(reservation.key, safe='')}/commit"
        body: Json = {
            "actual": _amount_wire(actual),
            **({"usage": _usage_wire(usage)} if usage is not None else {}),
            **({"hold": _amount_wire(hold.amount), "holdTtlS": hold.ttl_s} if hold is not None else {}),
            **({"modelRole": model_role} if model_role is not None else {}),
        }
        answer = await self._call("POST", reservation.account, path, credential, body)
        return self._settlement(reservation.account, answer)

    async def reconcile(
        self,
        reservation: Reservation,
        actual: Amount,
        *,
        usage: LedgerUsage | None = None,
        credential: str | None = None,
    ) -> Settlement:
        path = f"/budget/reservations/{quote(reservation.key, safe='')}/reconcile"
        body: Json = {"actual": _amount_wire(actual), **({"usage": _usage_wire(usage)} if usage is not None else {})}
        answer = await self._call("POST", reservation.account, path, credential, body)
        return self._settlement(reservation.account, answer)

    async def release(self, reservation: Reservation, *, credential: str | None = None) -> Settlement:
        path = f"/budget/reservations/{quote(reservation.key, safe='')}/release"
        return self._settlement(reservation.account, await self._call("POST", reservation.account, path, credential, {}))

    async def remaining(self, account: RunAccountRef, *, credential: str | None = None) -> Remaining:
        return _remaining((await self._call("GET", account, "/budget", credential, None)).get("remaining"))

    @staticmethod
    def _settlement(account: RunAccountRef, answer: Json) -> Settlement:
        return Settlement(
            reservation=_reservation(account, answer.get("reservation")),
            charged=_amount(answer.get("charged")),
            crossed=answer.get("crossed") is True,
            remaining=_remaining(answer.get("remaining")),
        )

    async def _call(self, method: str, account: RunAccountRef, path: str, credential: str | None, body: Json | None) -> Json:
        return answer_of(await self._send(method, account, path, credential, body))


def _budget_scope(response: httpx.Response) -> str | None:
    try:
        body: object = response.json()
    except ValueError:
        return None
    error = cast("dict[str, object]", body).get("error") if isinstance(body, dict) else None
    reason = cast("dict[str, object]", error).get("reasonCode") if isinstance(error, dict) else None
    if not isinstance(reason, str):
        return None
    from app.edition_services import harness_edition

    return _scope(harness_edition().port(SharedBudgetReasons, NO_SHARED_BUDGET_REASONS).scope_of(reason))


def answer_of(response: httpx.Response) -> Json:
    """A ledger route's answer, or its refusal as the typed ``BudgetRefused``."""
    if response.status_code == 200:
        try:
            document: object = response.json()
        except ValueError as exc:
            raise BudgetUnavailable("the budget answer is malformed", detail="malformed_budget") from exc
        if not isinstance(document, dict):
            raise BudgetUnavailable("the budget answer is malformed", detail="malformed_budget")
        return cast("Json", document)
    code = error_code(response)
    refusal = REFUSALS_BY_CODE.get(code.lower())
    if refusal is not None:
        refused = refusal("Node refused the budget operation")
        refused.budget_scope = _budget_scope(response)
        raise refused
    raise BudgetUnavailable(
        "Node refused the budget operation",
        detail=code.lower() or f"status_{response.status_code}",
        http_status=403 if response.status_code in _CALLER_REFUSED else 503,
    )



def account_path(account: RunAccountRef) -> str:
    """A run's ledger routes."""
    return f"/runs/{quote(account.run_id, safe='')}"


class NodeBudgetLedger(_NodeBudgetWire):
    """The model gateway's side: the presented run token as the bearer."""

    def __init__(self, *, base_url: str, http_client: httpx.AsyncClient | None = None, timeout_s: float = 10.0) -> None:
        self._node = NodeServiceClient(
            base_url=base_url, timeout_s=timeout_s, http_client=http_client, service_name="node.budget"
        )

    @override
    def __repr__(self) -> str:
        return "NodeBudgetLedger()"

    @override
    async def _send(
        self, method: str, account: RunAccountRef, path: str, credential: str | None, body: Json | None
    ) -> httpx.Response:
        return await self._node.send(
            method,
            f"{INTERNAL_PREFIX}{account_path(account)}{path}",
            bearer=credential or None,
            json=body,
            unreachable=lambda _: BudgetUnavailable("Node is unreachable", detail="node_unreachable"),
        )

    async def aclose(self) -> None:
        await self._node.aclose()


class AttemptBudgetAccounts(_NodeBudgetWire):
    """The same routes for a worker's own run, with the attempt's token, id and fence
    (``AttemptRoute``): a loop run's model calls are charged as the gateway's are."""

    def __init__(self, route: AttemptRoute) -> None:
        self._route = route

    @override
    def __repr__(self) -> str:
        return "AttemptBudgetAccounts()"

    @override
    async def _send(
        self, method: str, account: RunAccountRef, path: str, credential: str | None, body: Json | None
    ) -> httpx.Response:
        try:
            if method == "GET":
                return await self._route.get(path, operation="budget")
            return await self._route.post(path, body=cast("dict[str, JsonValue]", body or {}), operation="budget")
        except JournalUnavailable as exc:
            raise BudgetUnavailable("Node is unreachable", detail="node_unreachable") from exc


__all__ = ["AttemptBudgetAccounts", "NodeBudgetLedger", "account_path", "answer_of"]
