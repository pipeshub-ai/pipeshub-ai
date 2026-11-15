"""Lane admission in bytes: the window, growth, and the Excel load in flight (PX-2e, T79, T80).

Every test drives a real ``LaneBudget`` on a real ledger file with a fake clock and a fake
memory reader, so the headroom predicate is exercised at chosen fractions of the limit.
"""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

import pytest

from app.services.resource_governor import lane_ledger
from app.services.resource_governor.kinds import ParseKind
from app.services.resource_governor.lane_ledger import (
    RECORD_SIZE,
    LedgerRecord,
    add_window_bytes,
    decide_spawn,
    live_window_bytes,
    remove_window_bytes,
)
from tests.unit.lane_gauge import FakeClock, Mem

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.services.resource_governor.lanes import LaneBudget

GIB = 2**30
MIB = 2**20
WARMUP = 20.0  # the budget's default LANE_WARMUP_SECONDS


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GOVERNOR_LANE_WORKING_SET_GB", raising=False)
    monkeypatch.delenv("LANE_WARMUP_SECONDS", raising=False)


# -- T79: the ledger ------------------------------------------------------------------


def test_window_bytes_roundtrip() -> None:
    record = LedgerRecord(plan_lanes=8, window_bytes=3 * GIB + 17, bytes_until=1234.5, spawns_in_window=2)
    data = record.encode()
    assert len(data) == RECORD_SIZE
    decoded = LedgerRecord.decode(data)
    assert decoded == record
    assert decoded is not None
    assert decoded.window_bytes == 3 * GIB + 17
    assert decoded.bytes_until == 1234.5


def test_a_v2_record_is_not_read_as_a_v3_one() -> None:
    data = bytearray(LedgerRecord(window_bytes=5).encode())
    data[8:12] = (2).to_bytes(4, "little")
    assert LedgerRecord.decode(bytes(data)) is None


def test_directory_name_carries_ledger_version_3(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    from app.services.resource_governor import lanes

    assert lane_ledger.LEDGER_VERSION == 3
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp_path))
    budget = lanes.open_lane_budget(
        lanes.plan_lanes(cpus=4, heavy=4), local_share=1, environ={"PARSE_LANES": "on"}, memory_reader=Mem()
    )
    assert budget is not None
    budget.close()
    names = [p.name for p in tmp_path.iterdir() if "cpu-lanes" in p.name]
    assert names and all(n.startswith("pipeshub-cpu-lanes-v3-64") for n in names), names


def test_window_charges_bytes_by_kind_not_count(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
) -> None:
    """A text spawn then a PDF spawn charge 0.1 + 1.25 GiB, and the next spawn is judged against both."""
    mem.limit, mem.raw = 10 * GIB, int(5.6 * GIB)
    budget = make_budget(16)
    text, pdf = int(0.1 * GIB), int(1.25 * GIB)
    first = budget.admit_spawn(text)
    assert first is not None  # the first worker is exempt, its bytes are charged
    assert budget.admit_spawn(pdf) is not None  # (5.6 + 0.1 + 1.25) / 10 = 0.695
    assert budget.stats()["window_bytes"] == text + pdf
    # 5.6 + 1.35 + 0.1 = 7.05 GiB is over 70 %: counted as "n spawns x this spawn's figure" it would be 5.9.
    assert budget.admit_spawn(text) is None
    mem.raw = int(5.5 * GIB)
    clock.advance(1.1)  # the budget reads memory at most once a second
    assert budget.admit_spawn(text) is not None  # 5.5 + 1.35 + 0.1 = 6.95 GiB


def test_window_bytes_are_one_sum_across_budgets(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    mem.limit, mem.raw = 10 * GIB, 4 * GIB
    a, b = make_budget(16), make_budget(16)
    assert a.admit_spawn(int(1.25 * GIB)) is not None
    assert b.admit_spawn(int(0.5 * GIB)) is not None
    assert a.stats()["window_bytes"] == b.stats()["window_bytes"] == int(1.75 * GIB)


def test_decide_spawn_without_a_byte_window_keeps_the_count_rule() -> None:
    """Callers that keep no byte window (the pure tests of 2b) get count x figure, as before."""
    decision = decide_spawn(
        held_before=1, window_start=100.0, spawns_in_window=2, now=101.0, plan_lanes=8,
        raw_working_set=5 * GIB, mem_limit=10 * GIB, ws_bytes=GIB, warmup_seconds=20.0, allowance=8,
    )
    assert decision.admit is False  # 5 + 3 x 1 = 8 GiB
    assert decision.reason == "headroom"


# -- R6-2: the bytes expire on their own clock --------------------------------------------


def test_growth_charge_lasts_one_warmup_then_expires(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
) -> None:
    mem.limit, mem.raw = 10 * GIB, 2 * GIB
    budget = make_budget(16)
    assert budget.admit_spawn(int(0.1 * GIB)) is not None  # opens the spawn window at t0
    clock.advance(19.0)
    assert budget.admit_growth(GIB)  # 19 s into a 20 s spawn window
    clock.advance(5.0)  # the spawn window is over; the growth is 5 s old
    assert budget.stats()["window_bytes"] == GIB + int(0.1 * GIB), "a growth must not lose its charge with the spawn window"
    clock.advance(WARMUP - 5.0 - 0.01)
    assert budget.stats()["window_bytes"] > 0
    clock.advance(0.02)
    assert budget.stats()["window_bytes"] == 0


def test_growth_charge_lasts_a_full_warmup(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
) -> None:
    mem.limit, mem.raw = 10 * GIB, 2 * GIB
    budget = make_budget(16)
    assert budget.admit_spawn(0) is not None
    for _ in range(4):
        clock.advance(4.9)
        assert budget.admit_growth(100 * MIB)
    clock.advance(WARMUP - 0.5)
    assert budget.stats()["window_bytes"] == 400 * MIB, "every addition restarts the expiry"


def test_growth_in_an_expired_window_starts_a_new_byte_window(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
) -> None:
    mem.limit, mem.raw = 10 * GIB, 2 * GIB
    budget = make_budget(16)
    ticket = budget.admit_spawn(2 * GIB)
    assert ticket is not None
    ticket.release(started=True)
    clock.advance(WARMUP + 1.0)
    assert budget.stats()["window_bytes"] == 0
    assert budget.admit_growth(300 * MIB)
    assert budget.stats()["window_bytes"] == 300 * MIB, "stale bytes must not carry into the new window"


def test_ticket_return_after_bytes_expired_does_not_go_negative(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
) -> None:
    mem.limit, mem.raw = 10 * GIB, 2 * GIB
    budget = make_budget(16)
    ticket = budget.admit_spawn(GIB)
    assert ticket is not None
    clock.advance(WARMUP + 1.0)
    assert budget.admit_growth(200 * MIB)  # a newer window
    ticket.release(started=False)  # the old spawn's bytes expired long ago: they are not this window's
    assert budget.stats()["window_bytes"] == 200 * MIB
    # And with nothing in the window at all, a return never goes below zero.
    clock.advance(WARMUP + 1.0)
    other = budget.admit_spawn(GIB)
    assert other is not None
    clock.advance(WARMUP + 1.0)
    other.release(started=False)
    assert budget.stats()["window_bytes"] == 0
    assert live_window_bytes(budget._ledger.read(), clock()) == 0


def test_a_ticket_returns_its_bytes_while_they_are_live(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    mem.limit, mem.raw = 10 * GIB, 2 * GIB
    budget = make_budget(16)
    keep = budget.admit_spawn(int(0.5 * GIB))
    assert keep is not None
    lost = budget.admit_spawn(GIB)
    assert lost is not None
    lost.release(started=False)
    assert budget.stats()["window_bytes"] == int(0.5 * GIB)


def test_pure_window_helpers() -> None:
    record = LedgerRecord()
    record = add_window_bytes(record, 100, now=10.0, warmup_seconds=20.0)
    record = add_window_bytes(record, 50, now=25.0, warmup_seconds=20.0)
    assert (record.window_bytes, record.bytes_until) == (150, 45.0)
    assert live_window_bytes(record, 44.9) == 150
    assert live_window_bytes(record, 45.0) == 0
    assert remove_window_bytes(record, 500, now=30.0).window_bytes == 0
    assert remove_window_bytes(record, 10, now=50.0) is record
    expired_then_added = add_window_bytes(record, 7, now=60.0, warmup_seconds=20.0)
    assert expired_then_added.window_bytes == 7


# -- T80: growth ---------------------------------------------------------------------------


def test_growth_is_judged_against_headroom_and_asks_for_a_reap(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
) -> None:
    mem.limit, mem.raw = 10 * GIB, GIB
    budget = make_budget(16)
    assert budget.admit_spawn(0) is not None
    assert budget.admit_spawn(0) is not None  # two residents: nobody is alone
    mem.raw = int(6.5 * GIB)
    clock.advance(1.1)
    assert budget.reap_requested_at() == 0.0
    assert budget.admit_growth(int(0.6 * GIB)) is False  # 6.5 + 0.6 = 7.1 GiB
    assert budget.reap_requested_at() == clock()
    assert budget.stats()["window_bytes"] == 0, "a denial charges nothing"
    mem.raw = 6 * GIB
    clock.advance(1.1)
    assert budget.admit_growth(int(0.6 * GIB)) is True  # 6.6 GiB
    assert budget.stats()["window_bytes"] == int(0.6 * GIB)


def test_growth_takes_no_token_and_no_lease(make_budget: Callable[..., LaneBudget], mem: Mem) -> None:
    mem.limit, mem.raw = 10 * GIB, GIB
    budget = make_budget(8)
    tickets = [budget.admit_spawn(int(0.1 * GIB)) for _ in range(2)]
    assert all(t is not None for t in tickets)
    before = budget.held_count()
    spawns = budget.stats()["spawns_in_window"]
    for _ in range(5):
        assert budget.admit_growth(int(0.2 * GIB))
    assert budget.held_count() == before
    assert budget.stats()["spawns_in_window"] == spawns, "growth is not a spawn"


def test_growth_without_token_cannot_exceed_resident_bound_or_cap(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    mem.limit, mem.raw = 10 * GIB, GIB
    budget = make_budget(2)
    held = [budget.admit_spawn(0) for _ in range(budget.resident_limit())]
    assert all(t is not None for t in held)
    assert budget.admit_spawn(0) is None, "at the bound"
    resident_before = budget.held_count()[1]
    assert budget.admit_growth(GIB)
    assert budget.held_count()[1] == resident_before
    assert budget.admit_spawn(0) is None, "growth did not make room for, or take, a token"


def test_growth_exempt_when_the_worker_is_alone(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
) -> None:
    mem.limit, mem.raw = 10 * GIB, int(9.5 * GIB)
    budget = make_budget(8)
    assert budget.admit_spawn(0) is not None  # one resident worker, memory nearly full
    assert budget.admit_growth(3 * GIB) is True
    assert budget.admit_spawn(0) is None  # a second worker is not exempt
    mem.raw = GIB
    clock.advance(1.1)
    assert budget.admit_spawn(0) is not None
    mem.raw = int(9.5 * GIB)
    clock.advance(25.0)
    assert budget.admit_growth(GIB) is False, "with two workers resident the test applies"


def test_growth_exemption_needs_a_resident_worker_not_an_empty_domain(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    mem.limit, mem.raw = 10 * GIB, int(9.5 * GIB)
    budget = make_budget(8)
    assert budget.admit_growth(GIB) is True  # nobody resident: the floor of one still holds


def test_lone_worker_growth_exemption_is_atomic_with_first_spawn(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    """A lone worker's growth racing a second worker's spawn: the two never both pass.

    5 GiB of 10 are used. The growth (3 GiB) is exempt only while its worker is the one resident
    worker; the spawn (nothing to add) fits only while the growth is not yet in the window. If
    the lone check were made outside the ledger lock, the spawn could slip in between the check
    and the write and both would be admitted.
    """
    mem.limit, mem.raw = 10 * GIB, 5 * GIB
    budgets = [make_budget(8) for _ in range(2)]
    both = 0
    for _ in range(150):
        first = budgets[0].admit_spawn(0)
        assert first is not None
        out: dict[str, object] = {}
        barrier = threading.Barrier(2)

        def grow(out: dict[str, object] = out, barrier: threading.Barrier = barrier) -> None:
            barrier.wait()
            out["grow"] = budgets[0].admit_growth(3 * GIB)

        def spawn(out: dict[str, object] = out, barrier: threading.Barrier = barrier) -> None:
            barrier.wait()
            out["spawn"] = budgets[1].admit_spawn(0)

        threads = [threading.Thread(target=grow), threading.Thread(target=spawn)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        both += out["grow"] is True and out["spawn"] is not None
        second = out["spawn"]
        if second is not None:
            second.release(started=False)  # type: ignore[attr-defined]
        first.release(started=False)
        budgets[0]._update(lambda r: lane_ledger.LedgerRecord(plan_lanes=r.plan_lanes))
    assert both == 0


def test_growth_delta_zero_is_free(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
) -> None:
    mem.limit, mem.raw = 10 * GIB, GIB
    budget = make_budget(8)
    assert budget.admit_spawn(0) is not None
    assert budget.admit_spawn(0) is not None
    mem.raw = int(9.9 * GIB)
    clock.advance(1.1)
    assert budget.admit_growth(0) is True


# -- R6-3: a worker that retires after its job loads no models -------------------------------


def test_excel_batch_not_throttled_by_spawn_allowance(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock
) -> None:
    mem.limit, mem.raw = 100 * GIB, GIB
    budget = make_budget(32)
    assert budget.admit_spawn(0) is not None  # something resident, so the first-worker floor is not what admits
    tickets = []
    for _ in range(20):
        ticket = budget.admit_spawn(10 * MIB, count_spawn=False)
        assert ticket is not None, "an Excel loader batch waited on the spawn allowance"
        ticket.release(started=True)
        tickets.append(ticket)
        ticket.worker_exited()  # retired after its job: the token returns
    assert budget.stats()["spawns_in_window"] == 1, "only the cold-model spawn counts"
    assert clock() == 1000.0, "and nothing waited"


def test_counted_spawns_are_still_limited_by_the_allowance(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    """The control: the same loop with counted spawns hits the allowance (max(2, 32 // 4) = 8)."""
    mem.limit, mem.raw = 100 * GIB, GIB
    budget = make_budget(32)
    admitted = [budget.admit_spawn(10 * MIB) for _ in range(20)]
    assert sum(t is not None for t in admitted) == 8


def test_a_failed_uncounted_spawn_returns_bytes_but_no_count(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    mem.limit, mem.raw = 100 * GIB, GIB
    budget = make_budget(32)
    assert budget.admit_spawn(0) is not None
    ticket = budget.admit_spawn(GIB, count_spawn=False)
    assert ticket is not None
    assert budget.stats()["window_bytes"] == GIB
    ticket.release(started=False)
    assert budget.stats()["window_bytes"] == 0
    assert budget.stats()["spawns_in_window"] == 1


def test_excel_load_still_needs_raw_below_soft_except_first_worker(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    mem.limit, mem.raw = 10 * GIB, int(7.2 * GIB)
    budget = make_budget(8)
    covered = budget.charge_for(ParseKind.EXCEL_LOAD, 20 * MIB, True)
    assert budget.admit_spawn(covered[1], count_spawn=False) is not None, "the first worker is exempt"
    assert budget.admit_spawn(0, count_spawn=False) is None, "a zero-byte charge does not waive the raw reading"


# -- R6-4: a load a permit covers is visible to the rest of the domain -------------------------


def test_excel_load_in_flight_bytes_visible_to_other_processes(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    mem.limit, mem.raw = 10 * GIB, 2 * GIB
    a, b = make_budget(16), make_budget(16)
    assert a.admit_spawn(0) is not None  # a resident worker, so nothing is exempt any more
    worker_charge, window_charge = a.charge_for(ParseKind.EXCEL_LOAD, 20 * MIB, permit_covered=True)
    assert worker_charge == 0, "a covered load is not resident worker memory"
    assert window_charge > 0
    assert a.admit_spawn(window_charge, count_spawn=False) is not None
    assert b.stats()["window_bytes"] >= window_charge, "the other process sees the load in flight"
    # 2 + 1.5 (the load) + 3.9 = 7.4 GiB: over the soft limit only because of the load.
    assert b.admit_spawn(int(3.9 * GIB)) is None
    assert b.admit_spawn(int(2.9 * GIB)) is not None


def test_two_services_cannot_both_admit_the_last_excel_load(
    make_budget: Callable[..., LaneBudget], mem: Mem
) -> None:
    mem.limit, mem.raw = 10 * GIB, int(4.2 * GIB)
    a, b = make_budget(16), make_budget(16)
    assert a.admit_spawn(0) is not None
    results: list[object] = []
    barrier = threading.Barrier(2)

    def load(budget: LaneBudget) -> None:
        _, window = budget.charge_for(ParseKind.EXCEL_LOAD, 20 * MIB, permit_covered=True)
        barrier.wait()
        results.append(budget.admit_spawn(window, count_spawn=False))

    threads = [threading.Thread(target=load, args=(x,)) for x in (a, b)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    # 4.2 + 1.5 = 5.7 fits (70 % of 10 GiB is 7.0); a second load makes 7.2.
    assert sum(r is not None for r in results) == 1


def test_charge_for_is_flat_when_the_operator_sets_one_figure(
    make_budget: Callable[..., LaneBudget], monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = make_budget(8, working_set_bytes=900 * MIB)
    for kind in (ParseKind.TEXT, ParseKind.PDF_LAYOUT, ParseKind.EXCEL_LOAD, None):
        assert budget.charge_for(kind, 10 * MIB, True) == (900 * MIB, 900 * MIB)


def test_charge_for_uses_the_scale_the_guard_raised(make_budget: Callable[..., LaneBudget]) -> None:
    from app.services.resource_governor.kinds import SCALES

    budget = make_budget(8)
    SCALES.reset()
    try:
        before = budget.charge_for(ParseKind.TEXT, 5 * MIB)[0]
        SCALES.observe(ParseKind.TEXT, int(before * 1.6), before)
        assert budget.charge_for(ParseKind.TEXT, 5 * MIB)[0] > before
    finally:
        SCALES.reset()


def _busy(*_a: object, **_k: object) -> int:
    raise TimeoutError("resident mutex busy")


def test_growth_with_a_busy_resident_mutex_goes_through_the_headroom_test(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another process holds the mutex: the count is unknown, so the worker is not treated as alone."""
    mem.limit, mem.raw = 10 * GIB, int(9.5 * GIB)
    budget = make_budget(8)
    assert budget.admit_spawn(0) is not None
    monkeypatch.setattr(budget._resident, "held_count", _busy)
    assert budget.admit_growth(GIB) is False, "no headroom: denied, not raised"
    mem.raw = GIB
    clock.advance(1.1)
    assert budget.admit_growth(GIB) is True, "headroom: admitted"


def test_local_growth_with_a_busy_resident_mutex_goes_through_the_headroom_test(
    make_budget: Callable[..., LaneBudget], mem: Mem, clock: FakeClock, monkeypatch: pytest.MonkeyPatch
) -> None:
    mem.limit, mem.raw = 10 * GIB, int(9.5 * GIB)
    budget = make_budget(8)
    assert budget.admit_spawn(0) is not None
    monkeypatch.setattr(budget._resident, "held_count", _busy)
    assert budget._grow_local(clock(), GIB, mem.limit, mem.raw) is False
    assert budget._grow_local(clock(), GIB, mem.limit, GIB) is True
