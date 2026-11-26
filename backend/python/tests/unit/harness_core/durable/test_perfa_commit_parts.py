"""PERFA-1, PERFA-3 (CON-15, WRK-34; QA22-F1): the commit limit is the contract's, and ``pack`` cuts a
step's events into commits within it without cutting what must commit together."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from harness_core.journal.commit_parts import (
    MAX_COMMIT_EVENTS,
    STATE_SAVED,
    CommitUnit,
    pack,
)
from harness_core.journal.types import NewEvent
from harness_core.kernel.durable import DurableKernel

REPO = Path(__file__).resolve().parents[6]


def ev(name: str) -> NewEvent:
    return NewEvent("t", {"name": name})


def unit(prefix: str, size: int, **slots: int) -> CommitUnit:
    return CommitUnit(tuple(ev(f"{prefix}{i}") for i in range(size)), dict(slots))


def names(parts: list[list[NewEvent]]) -> list[list[str]]:
    return [
        [str(e.data["name"]) if e.type == "t" else json.dumps(e.data["slots"], sort_keys=True) for e in part]
        for part in parts
    ]


def test_perfa_1_the_limit_is_the_contract_value_and_the_kernel_default() -> None:
    release = json.loads((REPO / "contracts/harness/core/policy/release.json").read_text(encoding="utf-8"))
    assert MAX_COMMIT_EVENTS == release["journal"]["maxCommitEvents"]
    assert DurableKernel.__dataclass_fields__["max_commit_events"].default == MAX_COMMIT_EVENTS


def test_perfa_3_a_batch_within_the_limit_is_one_part_as_it_was_given() -> None:
    batch = [ev("a"), unit("r", 2, seen=1), ev("b"), unit("s", 2, done=2)]
    assert names(pack(batch, 7)) == [["a", "r0", "r1", "b", "s0", "s1", '{"done": 2, "seen": 1}']]
    assert pack([], 7) == []


@pytest.mark.parametrize("count", [99, 100, 101, 250, 1000])
def test_perfa_3_events_keep_their_order_and_no_part_is_over_the_limit(count: int) -> None:
    parts = pack([ev(str(i)) for i in range(count)])
    assert [n for part in names(parts) for n in part] == [str(i) for i in range(count)]
    assert [len(part) for part in parts] == [MAX_COMMIT_EVENTS] * (count // MAX_COMMIT_EVENTS) + (
        [count % MAX_COMMIT_EVENTS] if count % MAX_COMMIT_EVENTS else []
    )


def test_perfa_3_a_unit_is_never_cut_while_a_commit_can_hold_it() -> None:
    parts = names(pack([unit("a", 3), unit("b", 3), unit("c", 2), unit("d", 4)], 5))
    assert parts == [["a0", "a1", "a2"], ["b0", "b1", "b2", "c0", "c1"], ["d0", "d1", "d2", "d3"]]


def test_perfa_3_the_slots_of_a_part_are_one_event_at_its_end_and_count_against_the_limit() -> None:
    parts = names(pack([unit("a", 2, x=1), unit("b", 2, y=2), unit("c", 2, x=3), unit("d", 1)], 5))
    assert parts == [
        ["a0", "a1", "b0", "b1", '{"x": 1, "y": 2}'],
        ["c0", "c1", "d0", '{"x": 3}'],
    ]
    assert all(len(part) <= 5 for part in parts)


def test_perfa_3_a_unit_with_slots_does_not_squeeze_into_a_part_without_room_for_them() -> None:
    parts = names(pack([unit("a", 3), unit("b", 1, x=1)], 4))
    assert parts == [["a0", "a1", "a2"], ["b0", '{"x": 1}']]


def test_perfa_3_a_unit_larger_than_a_commit_is_cut_in_order_with_its_slots_in_its_first_piece() -> None:
    parts = names(pack([ev("lead"), unit("big", 7, x=1), ev("tail")], 3))
    assert parts == [["lead"], ["big0", "big1", '{"x": 1}'], ["big2", "big3", "big4"], ["big5", "big6", "tail"]]


def test_perfa_3_state_saved_is_the_events_own_type() -> None:
    (part,) = pack([unit("a", 1, x=1)], 4)
    assert part[-1] == NewEvent(STATE_SAVED, {"slots": {"x": 1}})


def test_perfa_3_a_limit_that_cannot_hold_an_event_with_its_slots_is_refused() -> None:
    with pytest.raises(ValueError, match="at least two"):
        _ = pack([ev("a")], 1)
