"""Contract suites of the budget ledger (P6.5): ``BudgetAccountsContract``, ``BudgetLedgerContract``.

Provide a ``budget_harness`` fixture (``BudgetHarness``): the accounts under test
and the authority's side (open, pin, close), which the in-memory ledger does
itself and a remote authority does through its own control surface; and a
``budget_scenarios`` fixture with the shared table
(``contracts/harness/vectors/budget-ledger.json``, ``load_budget_scenarios``).
``BudgetLedgerContract`` also needs ``budget_ledger``: the ``BudgetLedger`` itself.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Protocol, cast

import pytest

from harness_core.budget.errors import (
    BudgetAccountClosed,
    BudgetAccountMissing,
    BudgetExhausted,
    BudgetInsufficient,
    BudgetRefused,
    BudgetReservationSettled,
    BudgetShareExceedsRemaining,
)
from harness_core.budget.types import (
    ZERO,
    Amount,
    Available,
    Cap,
    Hold,
    LedgerUsage,
    PinnedBudget,
    ReservationState,
    RunAccountRef,
)

if TYPE_CHECKING:
    from pathlib import Path

    from harness_core.budget.ports import BudgetAccounts, BudgetLedger
    from harness_core.budget.types import Remaining, Reservation, Settlement

Json = dict[str, object]
TTL_S = 60


class BudgetHarness(Protocol):
    @property
    def accounts(self) -> BudgetAccounts: ...

    def account(self, run: str) -> RunAccountRef:
        """A run of this test's own (a shared store keeps parallel runs apart)."""
        ...

    def credential(self, account: RunAccountRef) -> str | None: ...

    async def open_root(self, account: RunAccountRef, cap: Cap) -> None: ...

    async def open_child(self, parent: RunAccountRef, child: RunAccountRef, cap: Cap) -> Remaining: ...

    async def pin(self, account: RunAccountRef, pinned: PinnedBudget) -> None: ...

    async def close(self, account: RunAccountRef) -> None: ...

    async def advance(self, seconds: float) -> None: ...


def load_budget_scenarios(path: Path) -> list[Json]:
    return cast("list[Json]", json.loads(path.read_text(encoding="utf-8"))["scenarios"])


def _amount(value: object) -> Amount:
    data = cast("dict[str, int]", value)
    return Amount(data["usd"], data["tokens"])


def _usage(value: object) -> LedgerUsage | None:
    if value is None:
        return None
    data = cast("dict[str, int]", value)
    return LedgerUsage(data["input"], data["output"], data["cacheRead"], data["cacheWrite"])


def _cap(value: object) -> Cap:
    data = cast("dict[str, int | None]", value)
    return Cap(data["usd"], data["tokens"])


def _wire(value: Amount | Available) -> dict[str, int | None]:
    return {"usd": value.usd_micros, "tokens": value.tokens}


def _seen(result: Remaining | Settlement | Reservation) -> Json:
    from harness_core.budget.types import Remaining, Reservation

    if isinstance(result, Reservation):
        return {"granted": _wire(result.granted)}
    if isinstance(result, Remaining):
        used = result.used
        return {
            "available": _wire(result.available),
            "committed": _wire(result.committed),
            "reserved": _wire(result.reserved),
            "exhausted": result.exhausted,
            "estimated": result.estimated,
            "used": {
                "input": used.input_tokens,
                "output": used.output_tokens,
                "cacheRead": used.cache_read_tokens,
                "cacheWrite": used.cache_write_tokens,
            },
        }
    return {"charged": _wire(result.charged), "crossed": result.crossed, "state": result.reservation.state.value}


async def run_budget_scenario(harness: BudgetHarness, scenario: Json) -> None:
    """Runs one scenario of the shared table; assertion messages name the scenario and step."""
    accounts = harness.accounts
    reservations: dict[tuple[str, str], Reservation] = {}
    for index, raw in enumerate(cast("list[Json]", scenario["steps"])):
        step = raw
        where = f"{scenario['id']} step {index} ({step['op']})"
        run = str(step.get("run", ""))
        ref = harness.account(run)
        expect = cast("Json", step.get("expect", {}))
        result: Remaining | Settlement | Reservation | None = None
        try:
            match step["op"]:
                case "open_root":
                    await harness.open_root(ref, _cap(step["cap"]))
                case "open_child":
                    result = await harness.open_child(harness.account(str(step["parent"])), ref, _cap(step["cap"]))
                case "pin":
                    parent = step.get("parent")
                    await harness.pin(ref, PinnedBudget(_cap(step["cap"]), harness.account(str(parent)) if parent else None))
                case "close":
                    await harness.close(ref)
                case "advance":
                    await harness.advance(float(cast("float", step["seconds"])))
                case "reserve":
                    opening = step.get("opening")
                    result = await accounts.reserve(
                        ref,
                        str(step["key"]),
                        _amount(step["requested"]),
                        minimum=_amount(step["minimum"]),
                        ttl_s=int(cast("int", step["ttlS"])),
                        opening=_amount(opening) if opening is not None else None,
                        credential=harness.credential(ref),
                    )
                    reservations[run, str(step["key"])] = result
                case "commit":
                    held = reservations.get((run, str(step["key"])))
                    hold = step.get("hold")
                    result = await accounts.commit(
                        held or _unknown(ref, str(step["key"])),
                        _amount(step["actual"]),
                        usage=_usage(step.get("usage")),
                        credential=harness.credential(ref),
                        hold=Hold(_amount(hold), int(cast("int", step["holdTtlS"]))) if hold is not None else None,
                    )
                case "reconcile":
                    held = reservations.get((run, str(step["key"])))
                    result = await accounts.reconcile(
                        held or _unknown(ref, str(step["key"])),
                        _amount(step["actual"]),
                        usage=_usage(step.get("usage")),
                        credential=harness.credential(ref),
                    )
                case "release":
                    result = await accounts.release(reservations[run, str(step["key"])], credential=harness.credential(ref))
                case "remaining":
                    result = await accounts.remaining(ref, credential=harness.credential(ref))
                case other:
                    raise AssertionError(f"{where}: unknown op {other}")
        except BudgetRefused as refused:
            assert expect.get("refused") == refused.reason_code, f"{where}: refused {refused.reason_code}, expected {expect}"
            continue
        assert "refused" not in expect, f"{where}: expected refusal {expect['refused']}, got {result}"
        if result is None:
            continue
        seen = _seen(result)
        for name, value in expect.items():
            assert seen.get(name) == value, f"{where}: {name} = {seen.get(name)}, expected {value}"


def _unknown(account: RunAccountRef, key: str) -> Reservation:
    from datetime import UTC, datetime

    from harness_core.budget.types import Reservation

    return Reservation(account, key, ZERO, ZERO, datetime.fromtimestamp(0, tz=UTC))


class BudgetAccountsContract:
    @pytest.fixture
    def budget_harness(self) -> BudgetHarness:
        raise NotImplementedError("provide a budget_harness fixture")

    @pytest.fixture
    def budget_scenarios(self) -> list[Json]:
        raise NotImplementedError("provide budget_scenarios (load_budget_scenarios of the shared table)")

    async def _reserve(
        self, h: BudgetHarness, ref: RunAccountRef, key: str, usd: int, *, minimum: int = 0, tokens: int = 0
    ) -> Reservation:
        return await h.accounts.reserve(
            ref, key, Amount(usd, tokens), minimum=Amount(minimum, 0), ttl_s=TTL_S, credential=h.credential(ref)
        )

    async def test_bac1_the_shared_table(self, budget_harness: BudgetHarness, budget_scenarios: list[Json]) -> None:
        assert len(budget_scenarios) >= 13
        for scenario in budget_scenarios:
            await run_budget_scenario(_Scoped(budget_harness, str(scenario["id"]).split()[0]), scenario)

    async def test_bac2_reserve_commit_returns_the_rest(self, budget_harness: BudgetHarness) -> None:
        h = budget_harness
        root = h.account("bac2")
        await h.open_root(root, Cap(1_000, 100))
        held = await self._reserve(h, root, "k1", 600, tokens=50)
        assert (held.granted, held.state, held.partial) == (Amount(600, 50), ReservationState.OPEN, False)
        settled = await h.accounts.commit(held, Amount(250, 20), credential=h.credential(root))
        assert (settled.charged, settled.reservation.state) == (Amount(250, 20), ReservationState.COMMITTED)
        assert settled.remaining.available == Available(750, 80)
        assert (await h.accounts.remaining(root, credential=h.credential(root))).committed == Amount(250, 20)

    async def test_bac3_one_key_is_one_reservation(self, budget_harness: BudgetHarness) -> None:
        h = budget_harness
        root = h.account("bac3")
        await h.open_root(root, Cap(1_000, None))
        same = await asyncio.gather(*(self._reserve(h, root, "k", 300) for _ in range(6)))
        assert {r.granted for r in same} == {Amount(300, 0)}
        assert (await h.accounts.remaining(root, credential=h.credential(root))).reserved == Amount(300, 0)
        commits = await asyncio.gather(*(h.accounts.commit(same[0], Amount(100, 0), credential=h.credential(root)) for _ in range(4)))
        assert {c.charged for c in commits} == {Amount(100, 0)}
        assert (await h.accounts.remaining(root, credential=h.credential(root))).committed == Amount(100, 0)

    async def test_bac4_refusals_are_typed(self, budget_harness: BudgetHarness) -> None:
        h = budget_harness
        root = h.account("bac4")
        await h.open_root(root, Cap(1_000, None))
        held = await self._reserve(h, root, "k1", 900)
        with pytest.raises(BudgetInsufficient) as insufficient:
            await self._reserve(h, root, "k2", 300, minimum=200)
        assert insufficient.value.http_status == 402
        await h.accounts.release(held, credential=h.credential(root))
        with pytest.raises(BudgetReservationSettled):
            await h.accounts.commit(held, Amount(1, 0), credential=h.credential(root))
        spent = await self._reserve(h, root, "k3", 1_000)
        await h.accounts.commit(spent, Amount(1_000, 0), credential=h.credential(root))
        with pytest.raises(BudgetExhausted) as exhausted:
            await self._reserve(h, root, "k4", 10, minimum=1)
        assert exhausted.value.http_status == 402
        with pytest.raises(BudgetAccountMissing):
            await self._reserve(h, h.account("bac4-nobody"), "k1", 10)

    async def test_bac5_a_reservation_expires(self, budget_harness: BudgetHarness) -> None:
        h = budget_harness
        root = h.account("bac5")
        await h.open_root(root, Cap(1_000, None))
        crashed = await self._reserve(h, root, "crashed-call", 1_000)
        with pytest.raises(BudgetInsufficient):
            await self._reserve(h, root, "k2", 100, minimum=100)
        await h.advance(TTL_S + 1)
        assert (await self._reserve(h, root, "k2", 100, minimum=100)).granted == Amount(100, 0)
        late = await h.accounts.commit(crashed, Amount(400, 0), credential=h.credential(root))
        assert late.charged == Amount(400, 0)
        assert late.remaining.available == Available(500, None)

    async def test_bac6_parallel_reservations_never_exceed_the_root(self, budget_harness: BudgetHarness) -> None:
        h = budget_harness
        root = h.account("bac6")
        await h.open_root(root, Cap(10_000, None))
        children = [h.account(f"bac6-c{i}") for i in range(3)]
        await h.open_child(root, children[0], Cap(2_000, None))
        for child in children[1:]:
            await h.open_child(root, child, Cap())
        runs = [root, *children]

        async def call(i: int) -> Amount:
            ref = runs[i % len(runs)]
            try:
                held = await self._reserve(h, ref, f"call-{i}", 700, minimum=100)
            except BudgetRefused:
                return ZERO
            await h.accounts.commit(held, held.granted, credential=h.credential(ref))
            return held.granted

        granted = await asyncio.gather(*(call(i) for i in range(64)))
        total = sum(g.usd_micros for g in granted)
        assert total <= 10_000
        assert total >= 10_000 - 100
        rooted = await h.accounts.remaining(root, credential=h.credential(root))
        assert rooted.committed.usd_micros == total <= 10_000
        assert rooted.reserved == ZERO
        share = await h.accounts.remaining(children[0], credential=h.credential(children[0]))
        assert share.committed.usd_micros <= 2_000

    async def test_bac7_lazy_opening_starts_at_the_opening_spend(self, budget_harness: BudgetHarness) -> None:
        h = budget_harness
        root = h.account("bac7")
        await h.pin(root, PinnedBudget(Cap(1_000, 500)))
        held = await h.accounts.reserve(
            root, "k1", Amount(100, 10), minimum=ZERO, ttl_s=TTL_S, opening=Amount(300, 40), credential=h.credential(root)
        )
        assert held.granted == Amount(100, 10)
        left = await h.accounts.remaining(root, credential=h.credential(root))
        assert (left.committed, left.available) == (Amount(300, 40), Available(600, 450))

    async def test_bac8_a_commits_usage_reaches_the_runs_own_used(self, budget_harness: BudgetHarness) -> None:
        """EV-10, OWD-8 over the adapter's own wire (the shared table's LV-14 pins the rules): a commit's usage is
        the run's ``used``, once, and never its parent's."""
        h = budget_harness
        root, child = h.account("bac8"), h.account("bac8-c")
        await h.open_root(root, Cap(1_000_000, 10_000))
        await h.open_child(root, child, Cap())
        own, theirs = await self._reserve(h, root, "a", 0, tokens=500), await self._reserve(h, child, "c", 0, tokens=500)
        call = LedgerUsage(120, 7, 100, 3)
        settled = await h.accounts.commit(own, Amount(0, 30), usage=call, credential=h.credential(root))
        _ = await h.accounts.commit(own, Amount(0, 30), usage=call, credential=h.credential(root))
        _ = await h.accounts.commit(theirs, Amount(0, 9), usage=call, credential=h.credential(child))
        assert settled.remaining.used == call
        assert (await h.accounts.remaining(root, credential=h.credential(root))).used == call
        assert (await h.accounts.remaining(child, credential=h.credential(child))).used == call

    async def test_bac9_concurrent_estimated_commits_hold_every_bound(self, budget_harness: BudgetHarness) -> None:
        """WP66-CAP over the adapter's wire: cut calls settled at once each keep their bound's rest held, so the
        tree admits nothing past the cap while their charges are estimates, and their reconciles release it."""
        h = budget_harness
        root = h.account("bac9")
        await h.open_root(root, Cap(1_000, None))
        held = [await self._reserve(h, root, f"cut-{i}", 250) for i in range(4)]
        settled = await asyncio.gather(
            *(
                h.accounts.commit(r, Amount(50, 0), hold=Hold(Amount(200, 0), TTL_S), credential=h.credential(root))
                for r in held
            )
        )
        assert {s.charged for s in settled} == {Amount(50, 0)}
        left = await h.accounts.remaining(root, credential=h.credential(root))
        assert (left.committed, left.reserved, left.available, left.estimated) == (
            Amount(200, 0),
            Amount(800, 0),
            Available(0, None),
            4,
        ), left
        with pytest.raises(BudgetInsufficient):
            await self._reserve(h, root, "after", 10, minimum=1)
        _ = await asyncio.gather(
            *(h.accounts.reconcile(r, Amount(250, 0), credential=h.credential(root)) for r in held)
        )
        left = await h.accounts.remaining(root, credential=h.credential(root))
        assert (left.committed, left.reserved, left.exhausted, left.estimated) == (Amount(1_000, 0), ZERO, True, 0)


class BudgetLedgerContract(BudgetAccountsContract):
    @pytest.fixture
    def budget_ledger(self) -> BudgetLedger:
        raise NotImplementedError("provide the budget_ledger the harness wraps")

    async def test_blc1_open_is_idempotent(self, budget_harness: BudgetHarness, budget_ledger: BudgetLedger) -> None:
        root = budget_harness.account("blc1")
        first = await budget_ledger.open_root(root, Cap(1_000, None))
        again = await budget_ledger.open_root(root, Cap(5, 5), opening=Amount(9, 9))
        assert first == again
        child = budget_harness.account("blc1-c")
        await budget_ledger.open_child(root, child, Cap(400, None))
        assert (await budget_ledger.open_child(root, child, Cap(999_999, None))).available == Available(400, None)

    async def test_blc2_a_share_must_fit_what_the_parent_has_left(self, budget_harness: BudgetHarness, budget_ledger: BudgetLedger) -> None:
        root = budget_harness.account("blc2")
        await budget_ledger.open_root(root, Cap(1_000, 100))
        await budget_ledger.reserve(root, "k", Amount(800, 0), minimum=ZERO, ttl_s=TTL_S)
        with pytest.raises(BudgetShareExceedsRemaining):
            await budget_ledger.open_child(root, budget_harness.account("blc2-c"), Cap(300, None))
        with pytest.raises(BudgetShareExceedsRemaining):
            await budget_ledger.open_child(root, budget_harness.account("blc2-t"), Cap(None, 101))
        assert (await budget_ledger.open_child(root, budget_harness.account("blc2-c"), Cap(200, 100))).available == Available(200, 100)

    async def test_blc3_close_stops_reservations_not_settlements(self, budget_harness: BudgetHarness, budget_ledger: BudgetLedger) -> None:
        root = budget_harness.account("blc3")
        child = budget_harness.account("blc3-c")
        await budget_ledger.open_root(root, Cap(1_000, None))
        await budget_ledger.open_child(root, child, Cap())
        held = await budget_ledger.reserve(child, "k", Amount(100, 0), minimum=ZERO, ttl_s=TTL_S)
        await budget_ledger.close(root)
        with pytest.raises(BudgetAccountClosed):
            await budget_ledger.reserve(child, "k2", Amount(1, 0), minimum=ZERO, ttl_s=TTL_S)
        assert (await budget_ledger.commit(held, Amount(50, 0))).charged == Amount(50, 0)


class _Scoped:
    """A harness whose run names carry the scenario id, so scenarios never share accounts."""

    def __init__(self, inner: BudgetHarness, scope: str) -> None:
        self._inner = inner
        self._scope = scope

    @property
    def accounts(self) -> BudgetAccounts:
        return self._inner.accounts

    def account(self, run: str) -> RunAccountRef:
        return self._inner.account(f"{self._scope}-{run}")

    def credential(self, account: RunAccountRef) -> str | None:
        return self._inner.credential(account)

    async def open_root(self, account: RunAccountRef, cap: Cap) -> None:
        await self._inner.open_root(account, cap)

    async def open_child(self, parent: RunAccountRef, child: RunAccountRef, cap: Cap) -> Remaining:
        return await self._inner.open_child(parent, child, cap)

    async def pin(self, account: RunAccountRef, pinned: PinnedBudget) -> None:
        await self._inner.pin(account, pinned)

    async def close(self, account: RunAccountRef) -> None:
        await self._inner.close(account)

    async def advance(self, seconds: float) -> None:
        await self._inner.advance(seconds)


__all__ = [
    "BudgetAccountsContract",
    "BudgetHarness",
    "BudgetLedgerContract",
    "load_budget_scenarios",
    "run_budget_scenario",
]
