"""P6.5 BAC and QA19-5 (BUD-15) BLC against a live Node: ``NodeBudgetLedger`` over the real ledger routes,
``BudgetLedgerService``, the gateway-token authority and the in-memory ledger store
(``journal-http-server.ts``), with Node's ledger opening and closing accounts as admission does.
Skipped where the Node workspace has no ``node_modules``.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

import pytest

from app.harness.access.budget import AttemptBudgetAccounts, NodeBudgetLedger
from harness_core.budget.errors import REFUSALS_BY_CODE
from harness_core.budget.types import (
    Amount,
    Available,
    Cap,
    Hold,
    LedgerUsage,
    PinnedBudget,
    Remaining,
    RunAccountRef,
)
from harness_core.testing.contracts.budget import (
    BudgetAccountsContract,
    BudgetLedgerContract,
)
from tests.unit.harness.test_node_journal_contract import (
    NodeHarness,
    node_server,  # noqa: F401 - the module-scoped fixture
)

if TYPE_CHECKING:
    from harness_core.budget.types import Reservation, Settlement
    from harness_core.journal.types import Claim
    from tests.unit.harness.test_node_journal_contract import _NodeServer

ORG = "org-contract"


def _cap(cap: Cap) -> dict[str, int | None]:
    return {"usd": cap.usd_micros, "tokens": cap.tokens}


def _remaining(body: dict[str, Any]) -> Remaining:
    return Remaining(
        available=Available(body["available"]["usdMicros"], body["available"]["tokens"]),
        committed=Amount(**{"usd_micros": body["committed"]["usdMicros"], "tokens": body["committed"]["tokens"]}),
        reserved=Amount(**{"usd_micros": body["reserved"]["usdMicros"], "tokens": body["reserved"]["tokens"]}),
        exhausted=body["exhausted"],
        used=LedgerUsage(
            body["used"]["inputTokens"], body["used"]["outputTokens"],
            body["used"]["cacheReadTokens"], body["used"]["cacheWriteTokens"],
        ),
        estimated=body.get("estimated", 0),
    )


class _LiveHarness:
    def __init__(self, server: _NodeServer) -> None:
        self._control = server.control
        self._scope = uuid.uuid4().hex[:8]
        self._tokens: dict[str, str] = {}
        self.ledger = NodeBudgetLedger(base_url=server.base_url)

    @property
    def accounts(self) -> NodeBudgetLedger:
        return self.ledger

    def account(self, run: str) -> RunAccountRef:
        return RunAccountRef(ORG, f"{self._scope}-{run}")

    def credential(self, account: RunAccountRef) -> str | None:
        if account.run_id not in self._tokens:
            answer = self._control.post("/budget/token", json={"runId": account.run_id}).raise_for_status()
            self._tokens[account.run_id] = answer.json()["token"]
        return self._tokens[account.run_id]

    def _admission(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        """Node's ledger refusing an open, as the typed refusal admission turns it into."""
        answer = self._control.post(path, json=body)
        if answer.status_code == 409:
            code = str(answer.json().get("code", "")).removeprefix("AGENT_HARNESS_").lower()
            raise REFUSALS_BY_CODE[code]("Node's ledger refused the open")
        return answer.raise_for_status().json()

    async def open_root(self, account: RunAccountRef, cap: Cap, opening: Amount | None = None) -> Remaining:
        body: dict[str, Any] = {"runId": account.run_id, "cap": _cap(cap)}
        if opening is not None:
            body["opening"] = {"usdMicros": opening.usd_micros, "tokens": opening.tokens}
        return _remaining(self._admission("/budget/open-root", body))

    async def open_child(self, parent: RunAccountRef, child: RunAccountRef, cap: Cap) -> Remaining:
        return _remaining(
            self._admission("/budget/open-child", {"parentRunId": parent.run_id, "runId": child.run_id, "cap": _cap(cap)})
        )

    async def pin(self, account: RunAccountRef, pinned: PinnedBudget) -> None:
        assert pinned.cap.usd_micros is not None and pinned.cap.tokens is not None, "a RunSpec pins both"
        _ = self._control.post(
            "/budget/run",
            json={
                "runId": account.run_id,
                "parentRunId": pinned.parent.run_id if pinned.parent else None,
                "maxCostUsd": pinned.cap.usd_micros / 1_000_000,
                "maxTokens": pinned.cap.tokens,
            },
        ).raise_for_status()

    async def close(self, account: RunAccountRef) -> None:
        _ = self._control.post("/budget/close", json={"runId": account.run_id}).raise_for_status()

    async def advance(self, seconds: float) -> None:
        _ = self._control.post("/advance", json={"seconds": seconds}).raise_for_status()


class _AdmittingLedger:
    """The whole ``BudgetLedger`` as PipesHub splits it: Node's admission opens and closes accounts, and the
    worker's ``NodeBudgetLedger`` reserves and settles with the run's gateway token."""

    def __init__(self, harness: _LiveHarness) -> None:
        self._harness = harness
        self._accounts = harness.ledger

    def _proof(self, account: RunAccountRef, credential: str | None) -> str | None:
        return credential if credential is not None else self._harness.credential(account)

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
        return await self._accounts.reserve(
            account, key, requested, minimum=minimum, ttl_s=ttl_s, opening=opening,
            credential=self._proof(account, credential),
        )

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
        return await self._accounts.commit(
            reservation,
            actual,
            usage=usage,
            credential=self._proof(reservation.account, credential),
            hold=hold,
            model_role=model_role,
        )

    async def reconcile(
        self, reservation: Reservation, actual: Amount, *, usage: LedgerUsage | None = None, credential: str | None = None
    ) -> Settlement:
        return await self._accounts.reconcile(
            reservation, actual, usage=usage, credential=self._proof(reservation.account, credential)
        )

    async def release(self, reservation: Reservation, *, credential: str | None = None) -> Settlement:
        return await self._accounts.release(reservation, credential=self._proof(reservation.account, credential))

    async def remaining(self, account: RunAccountRef, *, credential: str | None = None) -> Remaining:
        return await self._accounts.remaining(account, credential=self._proof(account, credential))

    async def open_root(self, account: RunAccountRef, cap: Cap, *, opening: Amount | None = None) -> Remaining:
        return await self._harness.open_root(account, cap, opening)

    async def open_child(self, parent: RunAccountRef, child: RunAccountRef, cap: Cap) -> Remaining:
        return await self._harness.open_child(parent, child, cap)

    async def close(self, account: RunAccountRef) -> None:
        await self._harness.close(account)


class _NoSharedTable:
    @pytest.fixture
    def budget_scenarios(self) -> list[dict[str, object]]:
        return []

    async def test_bac1_the_shared_table(self, budget_harness: Any, budget_scenarios: Any) -> None:  # noqa: ANN401
        pytest.skip(
            "the table pins caps a RunSpec cannot (a dimension without a cap); Node's own stores run it "
            "(budget-ledger.test.ts, budget-ledger.mongo.itest.ts) and the fake Node runs it over this wire"
        )


class TestLiveNodeBudgetLedger(_NoSharedTable, BudgetLedgerContract):
    @pytest.fixture
    def budget_harness(self, node_server: _NodeServer) -> _LiveHarness:  # noqa: F811
        return _LiveHarness(node_server)

    @pytest.fixture
    def budget_ledger(self, budget_harness: _LiveHarness) -> _AdmittingLedger:
        return _AdmittingLedger(budget_harness)


class _ClaimedAttempts(_LiveHarness):
    """Each account is a run a worker claimed; its calls go out on that attempt's route with the attempt token,
    as a loop run's model calls are charged."""

    def __init__(self, server: _NodeServer) -> None:
        super().__init__(server)
        self._runs = NodeHarness(server)
        self._claims: dict[str, Claim] = {}

    @property
    def accounts(self) -> _PerAttempt:  # type: ignore[override]
        return _PerAttempt(self)

    def credential(self, account: RunAccountRef) -> str | None:
        return None

    async def claimed(self, account: RunAccountRef, *, parent: RunAccountRef | None = None) -> Claim:
        if account.run_id not in self._claims:
            self._runs.admit(account.run_id, session_id=f"session-{account.run_id}")
            claim = await self._runs.journal.claim(account.run_id, worker_id="worker-budget")
            assert claim is not None
            self._claims[account.run_id] = claim
            self._lease(account, parent, {})
        return self._claims[account.run_id]

    def _lease(self, account: RunAccountRef, parent: RunAccountRef | None, pin: dict[str, Any]) -> None:
        claim = self._claims[account.run_id]
        _ = self._control.post(
            "/budget/run",
            json={
                "runId": account.run_id,
                "parentRunId": parent.run_id if parent else None,
                "attempt": {"attemptId": claim.attempt_id, "fence": claim.fence},
                **pin,
            },
        ).raise_for_status()

    def route(self, claim: Claim) -> AttemptBudgetAccounts:
        return AttemptBudgetAccounts(self._runs.node.attempt_route(claim.token))

    async def open_root(self, account: RunAccountRef, cap: Cap, opening: Amount | None = None) -> Remaining:
        await self.claimed(account)
        return await super().open_root(account, cap, opening)

    async def open_child(self, parent: RunAccountRef, child: RunAccountRef, cap: Cap) -> Remaining:
        await self.claimed(child, parent=parent)
        return await super().open_child(parent, child, cap)

    async def pin(self, account: RunAccountRef, pinned: PinnedBudget) -> None:
        assert pinned.cap.usd_micros is not None and pinned.cap.tokens is not None, "a RunSpec pins both"
        await self.claimed(account, parent=pinned.parent)
        self._lease(
            account,
            pinned.parent,
            {"maxCostUsd": pinned.cap.usd_micros / 1_000_000, "maxTokens": pinned.cap.tokens},
        )


class _PerAttempt:
    """``BudgetAccounts`` for many runs, each through its own claimed attempt's ``AttemptBudgetAccounts``."""

    def __init__(self, harness: _ClaimedAttempts) -> None:
        self._harness = harness

    async def _for(self, account: RunAccountRef) -> AttemptBudgetAccounts:
        return self._harness.route(await self._harness.claimed(account))

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
        accounts = await self._for(account)
        return await accounts.reserve(account, key, requested, minimum=minimum, ttl_s=ttl_s, opening=opening)

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
        return await (await self._for(reservation.account)).commit(
            reservation, actual, usage=usage, hold=hold, model_role=model_role
        )

    async def reconcile(
        self, reservation: Reservation, actual: Amount, *, usage: LedgerUsage | None = None, credential: str | None = None
    ) -> Settlement:
        return await (await self._for(reservation.account)).reconcile(reservation, actual, usage=usage)

    async def release(self, reservation: Reservation, *, credential: str | None = None) -> Settlement:
        return await (await self._for(reservation.account)).release(reservation)

    async def remaining(self, account: RunAccountRef, *, credential: str | None = None) -> Remaining:
        return await (await self._for(account)).remaining(account)


class TestLiveAttemptBudgetAccounts(_NoSharedTable, BudgetAccountsContract):
    @pytest.fixture
    def budget_harness(self, node_server: _NodeServer) -> _ClaimedAttempts:  # noqa: F811
        return _ClaimedAttempts(node_server)
