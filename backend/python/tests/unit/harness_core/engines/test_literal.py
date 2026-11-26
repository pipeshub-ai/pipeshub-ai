"""SLASH0: a user's text reaches an engine as text, never as one of the engine's own commands."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.engines.literal import (
    WORD_JOINER,
    LiteralTextSession,
    literal_session,
    literal_task,
    literal_text,
)
from harness_core.engines.types import EngineTask, FollowUp
from harness_core.testing.engines.contract import SLASH_TEXTS

if TYPE_CHECKING:
    from collections.abc import Iterator


@pytest.mark.parametrize("typed", [*SLASH_TEXTS, "  /compact", "\n/clear", "\t/model x", "/"])
def test_a_leading_slash_gets_a_word_joiner_in_front(typed: str) -> None:
    assert literal_text(typed) == WORD_JOINER + typed


@pytest.mark.parametrize(
    "text", ["", "say hi", "a path /etc/hosts", "1/2", "trailing/", "\u2060/compact", "café /x"]
)
def test_other_text_is_the_same_object(text: str) -> None:
    assert literal_text(text) is text


def test_it_is_idempotent() -> None:
    once = literal_text("/compact")
    assert literal_text(once) is once


def test_tasks_keep_every_other_field() -> None:
    task = EngineTask(turn_id="t1", prompt="/compact\nsay one", instructions="be brief")
    out = literal_task(task)
    assert isinstance(out, EngineTask)
    assert (out.turn_id, out.prompt, out.instructions) == ("t1", WORD_JOINER + task.prompt, "be brief")
    follow = FollowUp(turn_id="t2", text="/clear")
    assert literal_task(follow) == FollowUp(turn_id="t2", text=WORD_JOINER + "/clear")
    plain = FollowUp(turn_id="t3", text="say hi")
    assert literal_task(plain) is plain


class _Recording:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def run_turn(self, task: EngineTask | FollowUp) -> Iterator[object]:
        self.calls.append(("run_turn", task))
        return iter(())

    async def steer(self, text: str) -> None:
        self.calls.append(("steer", text))

    async def interrupt(self) -> None:
        self.calls.append(("interrupt", None))

    async def answer(self, request_id: str, decision: object) -> None:
        self.calls.append(("answer", (request_id, decision)))

    async def compact(self, focus: str | None, *, instructions: str = "") -> str:
        self.calls.append(("compact", (focus, instructions)))
        return "compacted"

    async def capture_state(self) -> str:
        return "state"

    async def health(self) -> str:
        return "health"

    async def close(self) -> None:
        self.calls.append(("close", None))


async def test_the_session_neutralises_turns_and_steers_and_passes_the_rest_through() -> None:
    inner = _Recording()
    session = literal_session(inner)  # type: ignore[arg-type]
    assert isinstance(session, LiteralTextSession)
    assert literal_session(session) is session
    _ = session.run_turn(FollowUp(turn_id="t", text="/model x"))
    await session.steer("/compact now")
    await session.steer("keep going")
    await session.interrupt()
    await session.answer("r1", "approved")
    assert await session.compact("/clear the rest", instructions="be brief") == "compacted"  # type: ignore[comparison-overlap]
    await session.close()
    assert inner.calls == [
        ("run_turn", FollowUp(turn_id="t", text=WORD_JOINER + "/model x")),
        ("steer", WORD_JOINER + "/compact now"),
        ("steer", "keep going"),
        ("interrupt", None),
        ("answer", ("r1", "approved")),
        # The adapter's own compaction (SLASH2 C2): the focus is data the adapter places, passed as it is.
        ("compact", ("/clear the rest", "be brief")),
        ("close", None),
    ]
    assert (await session.capture_state(), await session.health()) == ("state", "health")
