"""AP-05 (C3.72) on the loop: a person who declines a held call says why, and the model reads the reason in the
refusal of that call, as data, with an instruction to adapt rather than retry."""

from __future__ import annotations

import json

from harness_core.effects.types import (
    APPROVAL_DENIAL,
    MAX_DENY_REASON_CHARS,
    approval_denial,
)
from harness_core.testing.interactions import ManualWakeup

from .support import RUN, World, project, reply, scripted

FIRST = "ix.approve.t1.0"
REASON = "Post it in #team-updates, not #general"


def _ask_for_writes(call: object, effect: object) -> str:
    return "ask" if getattr(effect, "needs_intent_commit", False) else "allow"


async def _declined(reason: str | None) -> tuple[World, list[tuple[str, str]]]:
    world = World()
    world.admit()
    _, port = scripted(reply("", ("post_message", {"text": "hello"})), reply("done"))
    wakeup = ManualWakeup(world.clock)

    def on_wait(waits: int) -> None:
        if waits == 1:
            world.journal.resolve_interaction(RUN, FIRST, "denied", reason=reason)
            wakeup.wake(RUN)

    wakeup.on_wait = on_wait
    done = await world.run(
        world.kernel(port, approval=_ask_for_writes, interactions=wakeup, approval_hold_s=10.0, interaction_poll_s=2.0)
    )
    assert done.status == "completed"
    return world, await project(world)


async def test_ap05_the_loop_model_reads_the_reason_with_the_refusal() -> None:
    world, pairs = await _declined(REASON)
    assert [e.data["rule"] for e in world.events("tool.denied")] == ["interaction_denied"]
    assert [e for e in world.saas.effects if e.op == "post_message"] == []
    results = [text for role, text in pairs if role == "tool"]
    assert len(results) == 1
    assert json.dumps(REASON) in results[0]
    assert "Do not call it again with the same arguments" in results[0]


async def test_ap05_without_a_reason_the_refusal_is_the_one_it_always_was() -> None:
    _, pairs = await _declined(None)
    results = [text for role, text in pairs if role == "tool"]
    assert len(results) == 1 and APPROVAL_DENIAL[1] in results[0]


def test_ap05_the_reason_is_bounded_and_one_line() -> None:
    rule, text = approval_denial("line one\n\nline two " + "x" * (MAX_DENY_REASON_CHARS * 2))
    assert rule == APPROVAL_DENIAL[0]
    quoted = text.split("Their reason: ", 1)[1].split(". Do not call", 1)[0]
    said = json.loads(quoted)
    assert said.startswith("line one line two ") and len(said) == MAX_DENY_REASON_CHARS
    assert approval_denial("   ") == APPROVAL_DENIAL
