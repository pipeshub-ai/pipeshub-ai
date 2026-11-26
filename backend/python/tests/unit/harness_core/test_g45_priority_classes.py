"""G-45 / GA-24: priority classes in the worker's core.

The class table is ``contracts/harness/vectors/priority-classes.json``, which Node's ``domain/priority-class.ts`` is
held to too; the in-memory journal plays Node's queue (highest class first, a floor claim every fifth, ``above`` for a
preempting worker, a person's answer raising a lower class, a child taking its parent's class).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, cast

from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.journal import InMemoryJournal
from harness_core.priority import (
    PRIORITY_CLASSES,
    PreemptRequests,
    after_approval,
    class_for_run_kind,
    parse_class,
    preemptible,
    priority_rank,
)
from harness_core.testing import ManualClock
from harness_core.testing.run_spec import minimal_run_spec

if TYPE_CHECKING:
    from harness_core.priority import PriorityClass

VECTOR = json.loads(
    (Path(__file__).resolve().parents[5] / "contracts" / "harness" / "vectors" / "priority-classes.json").read_text()
)


def _spec(run_kind: str) -> RunSpec:
    raw = dump_contract(minimal_run_spec())
    raw["runKind"] = run_kind
    return validate_contract(RunSpec, raw)


def test_ga24_classes_are_served_in_the_vectors_order() -> None:
    assert list(PRIORITY_CLASSES) == VECTOR["order"]
    assert [priority_rank(c) for c in PRIORITY_CLASSES] == [0, 1, 2, 3]


def test_ga24_each_run_kind_gets_the_vectors_class_and_an_unknown_one_is_interactive() -> None:
    for run_kind, expected in VECTOR["runKinds"].items():
        assert class_for_run_kind(run_kind) == expected, run_kind
    assert class_for_run_kind("child") == VECTOR["unknownRunKind"]
    assert class_for_run_kind(None) == VECTOR["unknownRunKind"]


def test_ga24_a_persons_answer_raises_a_lower_class_and_never_lowers_one() -> None:
    for start, end in VECTOR["afterApproval"].items():
        assert after_approval(cast("PriorityClass", start)) == end


def test_ga24_only_background_and_eval_are_preempted() -> None:
    assert sorted(c for c in PRIORITY_CLASSES if preemptible(c)) == sorted(VECTOR["preemptible"])


def test_a_claim_names_a_class_or_nothing() -> None:
    assert [parse_class(v) for v in ("batch", "interactive", "urgent", None, 3)] == ["batch", "interactive", None, None, None]


def test_preempt_requests_are_asked_and_cleared_per_run() -> None:
    asked = PreemptRequests()
    asked.request("run-1")
    assert (asked.requested("run-1"), asked.requested("run-2")) == (True, False)
    asked.clear("run-1")
    assert not asked.requested("run-1")


async def _claimed(journal: InMemoryJournal, *, above: PriorityClass | None = None) -> str | None:
    claim = await journal.claim(worker_id="w1", above=above)
    return claim.run_id if claim is not None else None


async def test_ga24_the_in_memory_queue_serves_the_highest_class_and_the_claim_names_it() -> None:
    journal = InMemoryJournal(clock=ManualClock(), claim_floor_every=0)
    for index in range(3):
        journal.admit(f"firing-{index}", org_id="org-1", session_id=f"s-f{index}", spec=_spec("scheduled"), input_text="q")
    journal.admit("turn", org_id="org-1", session_id="s-t", spec=_spec("interactive"), input_text="q")
    claim = await journal.claim(worker_id="w1")
    assert claim is not None
    assert (claim.run_id, claim.priority_class) == ("turn", "interactive")
    assert [await _claimed(journal) for _ in range(3)] == ["firing-0", "firing-1", "firing-2"]


async def test_ga24_floor_every_fifth_claim_takes_the_first_claimable_of_any_class() -> None:
    journal = InMemoryJournal(clock=ManualClock())
    journal.admit("firing", org_id="org-1", session_id="s-f", spec=_spec("scheduled"), input_text="q")
    for index in range(6):
        journal.admit(f"turn-{index}", org_id="org-1", session_id=f"s-{index}", spec=_spec("interactive"), input_text="q")
    order = [await _claimed(journal) for _ in range(6)]
    assert order == ["turn-0", "turn-1", "turn-2", "turn-3", "firing", "turn-4"]


async def test_ga24_above_takes_only_a_strictly_higher_class_and_is_never_the_floor() -> None:
    journal = InMemoryJournal(clock=ManualClock(), claim_floor_every=1)
    journal.admit("firing", org_id="org-1", session_id="s-f", spec=_spec("scheduled"), input_text="q")
    assert await _claimed(journal, above="background") is None
    journal.admit("turn", org_id="org-1", session_id="s-t", spec=_spec("interactive"), input_text="q")
    assert await _claimed(journal, above="interactive") is None
    assert await _claimed(journal, above="background") == "turn"
