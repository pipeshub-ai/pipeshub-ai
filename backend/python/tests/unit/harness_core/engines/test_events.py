"""EC-5, EC-10: deterministic command ids and the event vocabulary."""

from __future__ import annotations

import dataclasses
import subprocess
import sys
from pathlib import Path
from typing import get_args

import pytest

from harness_core.engines import events
from harness_core.engines.events import (
    EngineEvent,
    EngineEventMeta,
    command_id,
    turn_id_for,
)
from harness_core.engines.stream import EventBuffer, TurnSeq


def test_ec5_command_id_is_deterministic_across_processes() -> None:
    here = command_id("run-1", "trn_a", 3)
    code = "from harness_core.engines.events import command_id; print(command_id('run-1', 'trn_a', 3))"
    other = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
        env={"PYTHONHASHSEED": "1"},
        cwd=Path(__file__).resolve().parents[4],
    )
    assert other.stdout.strip() == here


@pytest.mark.parametrize("changed", [("run-2", "trn_a", 3), ("run-1", "trn_b", 3), ("run-1", "trn_a", 4)])
def test_ec5_any_input_changes_the_id(changed: tuple[str, str, int]) -> None:
    assert command_id(*changed) != command_id("run-1", "trn_a", 3)


def test_ec5_fields_cannot_run_together() -> None:
    assert command_id("ab", "c", 1) != command_id("a", "bc", 1)


def test_ec5_turn_id_depends_on_run_and_index_only() -> None:
    assert turn_id_for("run-1", 0) == turn_id_for("run-1", 0)
    assert turn_id_for("run-1", 0) != turn_id_for("run-1", 1)
    with pytest.raises(ValueError):
        turn_id_for("", 0)
    with pytest.raises(ValueError):
        command_id("run", "turn", -1)


def test_ec10_union_has_the_twenty_events_each_with_meta() -> None:
    """Fifteen engine events, the sign-in card's line (SIGNIN), the repository's line (WP-26), an engine's own
    compaction (LC2FIX-3), an unknown native message (EV-13) and a tool call's later facts (WP-112)."""
    members = get_args(EngineEvent)
    assert len(members) == 20
    for member in members:
        assert dataclasses.is_dataclass(member)
        params = member.__dataclass_params__  # type: ignore[attr-defined]
        assert params.frozen
        assert hasattr(member, "__slots__")
        assert next(iter(dataclasses.fields(member))).name == "meta"
    assert set(events.__all__) >= {m.__name__ for m in members}


async def test_turn_seq_and_bounded_buffer() -> None:
    seq = TurnSeq("trn_x")
    assert [seq.next() for _ in range(3)] == [EngineEventMeta("trn_x", i) for i in range(3)]
    buffer = EventBuffer(maxsize=2)
    await buffer.put(events.TextDelta(seq.next(), "m", "a"))
    await buffer.put(events.TurnCompleted(seq.next(), "completed"))
    assert buffer.qsize() == buffer.maxsize
    drained = [e async for e in buffer.drain()]
    assert [type(e).__name__ for e in drained] == ["TextDelta", "TurnCompleted"]
