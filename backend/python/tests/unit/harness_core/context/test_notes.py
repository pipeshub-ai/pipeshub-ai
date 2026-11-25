"""P4.11 CL-1..CL-7: the host's notes are journaled once, where they first apply, so no compiled request ends with
text the next request drops: each request of a run is a prefix of the next, the provider's cache can reuse it.
P4.15: a run's notes follow its input."""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.context.notes import (
    NOTE_HEADER,
    NOTES_SLOT,
    held,
    new_notes,
    note_message,
)
from harness_core.failpoints import Raise
from harness_core.kernel.core.messages import AssistantMessage, ToolMessage, UserMessage
from harness_core.testing import armed
from tests.unit.harness_core.durable.support import Crash, World, scripted

from .kernel_support import Host, answered, compiler, dump, looked_up

if TYPE_CHECKING:
    from harness_core.context.types import HostBands
    from harness_core.kernel.durable.ports import ModelCall
    from harness_core.kernel.fold import RunState

TIME = "## Time context\nCurrent time: 2026-10-06T09:00:00Z"
WARNING = "[System: You have 2 turns remaining.]"


@dataclass
class NotingHost(Host):
    """``Host`` whose volatile text is a note: the run's context, and ``warn_at`` a turn's warning."""

    context: str = TIME
    context_at: dict[int, str] = field(default_factory=dict[int, str])
    warn_at: int | None = None

    async def bands(self, call: ModelCall, turn: int, state: RunState) -> HostBands:
        bands = await super().bands(call, turn, state)
        text = self.context
        for since, later in self.context_at.items():
            if turn >= since:
                text = later
        return replace(bands, tail=(), notes=(text, WARNING if turn == self.warn_at else ""))


async def _run(world: World, host: Host, run_id: str, *replies: object, text: str = "look it up") -> list[ModelCall]:
    world.admit(run_id, text=text, max_turns=30)
    _, port = scripted(*replies)  # type: ignore[arg-type]
    assert (await world.run(world.kernel(port, context=compiler(host)), run_id)).status == "completed"
    return port.calls


def _notes(world: World) -> list[str]:
    return [str(e.data["slots"][NOTES_SLOT]) for e in world.events("loop.state_saved") if NOTES_SLOT in e.data["slots"]]


def _injected(world: World) -> list[Any]:
    return [e for e in world.events("message.completed") if e.data.get("injected")]


def _assert_append_only(calls: list[ModelCall]) -> None:
    for before, after in zip(calls, calls[1:], strict=False):
        assert dump(after.messages)[: len(before.messages)] == dump(before.messages)
        assert not isinstance(after.messages[-1], UserMessage) or not after.messages[-1].injected or (
            NOTE_HEADER in str(after.messages[-1].content)
        )


@pytest.mark.parametrize(
    ("notes", "journaled", "expected"),
    [
        (("a", "b"), (), ("a", "b")),
        (("a", "", "b"), ("a",), ("b",)),
        (("a", "a"), (), ("a",)),
        (("a",), ("a",), ()),
        (("b",), ("a",), ("b",)),
    ],
)
def test_cl4_new_notes_are_the_host_s_texts_not_journaled_in_this_run(
    notes: tuple[str, ...], journaled: tuple[str, ...], expected: tuple[str, ...]
) -> None:
    assert new_notes(notes, journaled) == expected


def test_cl1_a_note_is_one_injected_message_with_its_header() -> None:
    assert note_message(["a", "", "b"]) == f"{NOTE_HEADER}\n\na\n\nb"
    assert note_message([]) == ""
    history = [UserMessage(content=note_message(["a"]), injected=True), UserMessage(content="b")]
    assert held(history, "a") and not held(history, "b"), "only an injected message holds a note"


async def test_cl1_cl2_the_run_s_notes_follow_its_input_once_and_every_request_extends_the_last() -> None:
    world = World()
    calls = await _run(world, NotingHost(), "run-1", looked_up(300, "a"), looked_up(300, "b"), answered())
    for call in calls:
        first, second = call.messages[0], call.messages[1]
        assert isinstance(first, UserMessage) and not first.injected and first.content == "look it up"
        assert isinstance(second, UserMessage) and second.injected and second.content == note_message([TIME])
        assert call.input_index == 0
        assert TIME not in (call.system or "")
    _assert_append_only(calls)
    assert not isinstance(calls[-1].messages[-1], UserMessage), "no tail after the results"
    injected = _injected(world)
    users = [e for e in world.events("message.completed") if e.data["role"] == "user"]
    assert len(injected) == 1 and users[1] is injected[0] and users[0].seq + 1 == users[1].seq
    assert _notes(world) == [str([TIME])]


async def test_cl3_a_note_that_appears_mid_run_follows_the_history_and_stays() -> None:
    world = World()
    host = NotingHost(warn_at=3)
    calls = await _run(world, host, "run-1", looked_up(300, "a"), looked_up(300, "b"), looked_up(300, "c"), answered())
    warned = calls[2].messages
    assert isinstance(warned[-1], UserMessage) and warned[-1].content == note_message([WARNING])
    assert isinstance(warned[-2], ToolMessage), "after the history"
    assert any(isinstance(m, UserMessage) and m.content == note_message([WARNING]) for m in calls[3].messages)
    _assert_append_only(calls)
    assert len(_injected(world)) == 2


async def test_cl4_a_changed_note_is_journaled_alone_and_the_old_one_is_not_repeated() -> None:
    world = World()
    later = "## Time context\nCurrent time: later"
    calls = await _run(world, NotingHost(context_at={3: later}), "run-1", looked_up(300, "a"), looked_up(300, "b"), answered())
    assert calls[-1].messages[1].content == note_message([TIME]), "the journaled history is not rewritten"
    assert calls[-1].messages[-1].content == note_message([later])
    _assert_append_only(calls)
    assert _notes(world) == [str([TIME]), str([TIME, later])]


async def test_cl5_a_note_a_compaction_summarised_away_is_journaled_again_after_the_summary() -> None:
    world = World()
    usages = [300] * 5 + [9_000] + [300]
    calls = await _run(world, NotingHost(), "run-1", *(looked_up(u, f"q{i}") for i, u in enumerate(usages)), answered())
    assert len(world.events("context.compacted")) == 1
    after = calls[-1].messages
    notes = [i for i, m in enumerate(after) if isinstance(m, UserMessage) and m.content == note_message([TIME])]
    assert len(notes) == 1, "once in the request"
    summary = next(i for i, m in enumerate(after) if "a summary of the earlier conversation" in str(m.content))
    assert summary < notes[0], "journaled again after the summary"
    assert any(isinstance(m, AssistantMessage) for m in after[summary : notes[0]]), "where the run had got to"
    assert len(_injected(world)) == 2


async def test_cl6_a_resumed_attempt_sends_the_same_bytes_and_journals_the_note_once() -> None:
    async def requests(crash: bool) -> tuple[list[str], int]:
        world = World()
        replies = [looked_up(300, "a"), looked_up(300, "b"), answered()]
        world.admit(max_turns=30)
        if crash:
            from harness_core.failpoints import Arm, Nth

            _, port = scripted(*replies)
            with armed({"worker.model.after_response_commit": Arm(Raise(Crash), Nth(1))}):
                await world.crash(world.kernel(port, context=compiler(NotingHost())))
            replies = replies[len(list(world.events("model.response"))) :]
        _, port = scripted(*replies)
        assert (await world.run(world.kernel(port, context=compiler(NotingHost())))).status == "completed"
        return [str(e.data["contextDigest"]) for e in world.events("model.request")], len(_injected(world))

    assert await requests(True) == await requests(False)


async def test_cl7_a_later_run_states_its_own_notes_and_extends_the_earlier_run() -> None:
    world = World()
    host = NotingHost()
    first = await _run(world, host, "run-1", looked_up(300, "a"), answered())
    host.context = "## Time context\nCurrent time: 2026-10-07T10:00:00Z"
    second = await _run(world, host, "run-2", looked_up(300, "c"), answered(), text="and again")
    history = dump(first[-1].messages)
    assert dump(second[0].messages)[: len(history)] == history, "run 1's last request is a prefix of run 2's first"
    _assert_append_only(second)
    contexts = [m.content for m in second[-1].messages if isinstance(m, UserMessage) and m.injected]
    assert contexts == [note_message([TIME]), note_message([host.context])]
    own = second[-1].messages.index(next(m for m in second[-1].messages if m.content == "and again"))
    assert second[-1].messages[own + 1].content == note_message([host.context]), "right after its own input"
    assert second[0].input_index == own
