"""P6.5 PA-8 and ``BudgetAccountsContract`` on ``NodeBudgetLedger`` against a fake Node (httpx mock transport).

The fake answers Node's ledger routes over the in-memory reference ledger, so the
contract checks the adapter's wire; ``test_node_budget_live.py`` runs the same
contract against the real Node routes and service.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import override

import httpx
import pytest

from app import edition_services
from app.harness.access.budget import (
    NO_SHARED_BUDGET_REASONS,
    NodeBudgetLedger,
    SharedBudgetReasons,
    answer_of,
)
from harness_core.budget.errors import (
    BudgetExhausted,
    BudgetInsufficient,
    BudgetReservationMissing,
    BudgetUnavailable,
)
from harness_core.budget.types import (
    ZERO,
    Amount,
    Cap,
    Reservation,
    ReservationState,
    RunAccountRef,
)
from harness_core.edition import HarnessEdition
from harness_core.testing.contracts.budget import BudgetAccountsContract
from tests.unit.harness.access.budget_node import BudgetNode
from tests.unit.harness_core.budget.support import InMemoryBudgetHarness, scenarios


@dataclass
class FakeNode:
    """Node's ledger routes: only the run's own token (``token-<runId>``); ``status`` overrides."""

    budget: BudgetNode
    status: int | None = None
    requests: list[httpx.Request] = field(default_factory=list[httpx.Request])

    async def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.status is not None:
            return httpx.Response(self.status, json={"error": {"code": "AGENT_HARNESS_RUN_NOT_LEASED"}})
        parts = request.url.path.split("/")
        at = parts.index("runs")
        run_id = parts[at + 1]
        if request.headers.get("authorization") != f"Bearer token-{run_id}":
            return httpx.Response(401, json={"error": {"code": "AGENT_HARNESS_ATTEMPT_CLAIMS"}})
        body = json.loads(request.content) if request.content else {}
        status, answer = await self.budget.answer(request.method, run_id, parts[at + 2 :], body)
        return httpx.Response(status, json=answer)


class _Harness(InMemoryBudgetHarness):
    def __init__(self) -> None:
        super().__init__()
        self.node = FakeNode(BudgetNode(self.ledger, "org-v"))
        client = httpx.AsyncClient(base_url="http://node.test", transport=httpx.MockTransport(self.node.handle))
        self.node_ledger = NodeBudgetLedger(base_url="http://node.test", http_client=client)

    @property
    def accounts(self) -> NodeBudgetLedger:  # pyright: ignore[reportIncompatibleMethodOverride]
        return self.node_ledger

    def credential(self, account: RunAccountRef) -> str | None:
        return f"token-{account.run_id}"


class TestNodeBudgetLedger(BudgetAccountsContract):
    @pytest.fixture
    def budget_harness(self) -> _Harness:
        return _Harness()

    @pytest.fixture
    def budget_scenarios(self) -> list[dict[str, object]]:
        return scenarios()


@pytest.fixture
def harness() -> _Harness:
    return _Harness()


async def test_pa8_the_wire_and_the_token_as_bearer(harness: _Harness, caplog: pytest.LogCaptureFixture) -> None:
    run = harness.account("r1")
    await harness.open_root(run, Cap(1_000, None))
    caplog.set_level(logging.DEBUG)
    held = await harness.node_ledger.reserve(
        run, "chatcmpl-1", Amount(300, 30), minimum=Amount(10, 1), ttl_s=900, opening=Amount(5, 0), credential="token-r1"
    )
    sent = harness.node.requests[-1]
    assert sent.url.path == "/api/v1/agent-harness/internal/runs/r1/budget/reservations"
    assert json.loads(sent.content) == {
        "key": "chatcmpl-1",
        "requested": {"usdMicros": 300, "tokens": 30},
        "minimum": {"usdMicros": 10, "tokens": 1},
        "ttlS": 900,
        "opening": {"usdMicros": 5, "tokens": 0},
    }
    assert sent.headers["authorization"] == "Bearer token-r1"
    assert (held.granted, held.state) == (Amount(300, 30), ReservationState.OPEN)
    settled = await harness.node_ledger.commit(held, Amount(100, 3), credential="token-r1")
    assert harness.node.requests[-1].url.path.endswith("/budget/reservations/chatcmpl-1/commit")
    assert (settled.charged, settled.reservation.state) == (Amount(100, 3), ReservationState.COMMITTED)
    assert "token-r1" not in caplog.text and "token-r1" not in repr(harness.node_ledger)


async def test_pa8_refusal_codes_become_typed_refusals(harness: _Harness) -> None:
    run = harness.account("r2")
    await harness.open_root(run, Cap(10, None))
    held = await harness.node_ledger.reserve(run, "k", Amount(10, 0), minimum=ZERO, ttl_s=60, credential="token-r2")
    await harness.node_ledger.commit(held, Amount(10, 0), credential="token-r2")
    with pytest.raises(BudgetExhausted):
        await harness.node_ledger.reserve(run, "k2", Amount(10, 0), minimum=Amount(1, 0), ttl_s=60, credential="token-r2")
    stranger = Reservation(run, "nope", ZERO, ZERO, datetime.fromtimestamp(0, tz=UTC))
    with pytest.raises(BudgetReservationMissing):
        await harness.node_ledger.commit(stranger, ZERO, credential="token-r2")


class _PoolReasons(SharedBudgetReasons):
    """An edition whose shared budgets refuse with ``pool_<scope>_spent``."""

    @override
    def scope_of(self, reason_code: str) -> str | None:
        return reason_code.removeprefix("pool_").removesuffix("_spent") if reason_code.startswith("pool_") else None


@pytest.mark.parametrize(
    ("body", "refusal", "scope"),
    [
        ({"code": "HTTP_AGENT_HARNESS_BUDGET_EXHAUSTED", "reasonCode": "pool_a_spent"}, BudgetExhausted, "a"),
        ({"code": "HTTP_AGENT_HARNESS_BUDGET_INSUFFICIENT", "reasonCode": "pool_b-2_spent"}, BudgetInsufficient, "b-2"),
        ({"code": "HTTP_AGENT_HARNESS_BUDGET_EXHAUSTED"}, BudgetExhausted, None),
        ({"code": "HTTP_AGENT_HARNESS_BUDGET_EXHAUSTED", "reasonCode": "other_reason"}, BudgetExhausted, None),
        ({"code": "HTTP_AGENT_HARNESS_BUDGET_EXHAUSTED", "reasonCode": "pool_Not A Scope_spent"}, BudgetExhausted, None),
    ],
    ids=["exhausted", "insufficient", "run-cap", "unknown-reason", "badly-shaped-scope"],
)
def test_wp51_a_shared_budget_refusal_names_its_scope_through_the_edition(
    body: dict[str, str], refusal: type[BudgetExhausted], scope: str | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    edition = HarnessEdition({SharedBudgetReasons: _PoolReasons()})
    monkeypatch.setattr(edition_services, "harness_edition", lambda: edition)
    with pytest.raises(refusal) as refused:
        answer_of(httpx.Response(409, json={"error": body}))
    assert refused.value.budget_scope == scope


@pytest.mark.parametrize("reason", ["pool_a_spent", "anything"])
def test_wp51_this_build_names_no_shared_budget_in_a_refusal(reason: str) -> None:
    with pytest.raises(BudgetExhausted) as refused:
        answer_of(httpx.Response(409, json={"error": {"code": "HTTP_AGENT_HARNESS_BUDGET_EXHAUSTED", "reasonCode": reason}}))
    assert refused.value.budget_scope is None
    assert NO_SHARED_BUDGET_REASONS.scope_of(reason) is None


@pytest.mark.parametrize(("status", "http_status"), [(401, 403), (409, 403), (500, 503)])
async def test_pa8_other_refusals_and_outages_are_unavailable(harness: _Harness, status: int, http_status: int) -> None:
    harness.node.status = status
    with pytest.raises(BudgetUnavailable) as refused:
        await harness.node_ledger.remaining(harness.account("r3"), credential="token-r3")
    assert refused.value.http_status == http_status


async def test_pa8_an_unreachable_node_is_unavailable() -> None:
    def down(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    ledger = NodeBudgetLedger(
        base_url="http://node.test",
        http_client=httpx.AsyncClient(base_url="http://node.test", transport=httpx.MockTransport(down)),
    )
    with pytest.raises(BudgetUnavailable) as refused:
        await ledger.remaining(RunAccountRef("o", "r"), credential="t")
    assert (refused.value.http_status, refused.value.detail) == (503, "node_unreachable")



@pytest.mark.parametrize(
    ("scope", "read"), [("pool-a", "pool-a"), ("p", "p"), ("Pool A", None), ("x" * 33, None), (None, None)]
)
async def test_wp56_stop_1_a_budget_read_names_the_binding_shared_budget(
    harness: _Harness, scope: str | None, read: str | None
) -> None:
    """WP56-STOP-1: Node's budget read names which shared budget binds the run; the engine's kernel learns it there."""
    harness.node.budget.budget_scope = scope
    run = harness.account("r-scope")
    await harness.open_root(run, Cap(1_000, None))
    left = await harness.node_ledger.remaining(run, credential="token-r-scope")
    assert left.budget_scope == read
