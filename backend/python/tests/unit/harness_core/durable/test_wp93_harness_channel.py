"""WP93-CHAN (ADR-RUN-01 D5a): a loop director's harness instruction reaches the model in the system channel of the
turn it is for, under PipesHub's frame, and never as a message of the person's turn.

A long research run's DEEPROUTE steer was journaled as an injected ``user`` message and sent after the person's request; the
model read it as "the only additional user instruction". A ``HarnessInstruction`` is journaled as a ``system``
message, so neither the run's fold nor the session's history reads it as input, and a later request does not render it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from harness_core.kernel.core.messages import AssistantMessage, ToolMessage, UserMessage
from harness_core.kernel.durable.harness_channel import (
    HARNESS_FRAME,
    HarnessInstruction,
    harness_block,
    origin_of,
)
from harness_core.kernel.durable.ports import ModelCall, TurnPlan
from harness_core.kernel.durable.transcript import text_of

from .support import World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.kernel.durable.ports import TurnResult
    from harness_core.kernel.fold import RunState

STEER = HarnessInstruction("deeproute.upfront", "Hand it to the coding engine now: call code_task once.")


@dataclass
class _Steer:
    def before(self, turn: int, state: RunState, previous: Sequence[TurnResult]) -> TurnPlan:
        del state, previous
        return TurnPlan(harness=(STEER,)) if turn == 1 else TurnPlan()


async def test_wp93_chan_the_instruction_rides_the_system_channel_of_its_turn_only() -> None:
    world = World()
    world.admit(max_turns=3)
    _, port = scripted(reply("", ("lookup", {"q": "x"})), reply("answer"))
    done = await world.run(world.kernel(port, director=_Steer(), system="You are a helpful agent."))
    assert done.status == "completed"
    first, second = port.calls
    assert first.system == "You are a helpful agent.\n\n" + harness_block((STEER,))
    assert HARNESS_FRAME in (first.system or "") and STEER.text in (first.system or "")
    users = [text_of(m) for m in first.messages if isinstance(m, UserMessage)]
    assert users == ["please help"], "the person's turn holds the person's words only"
    assert second.system == "You are a helpful agent.", "spent: a later request does not render it"
    assert all(STEER.text not in text_of(m) for m in second.messages)
    completed = [e.data for e in world.events("message.completed") if e.data.get("injected")]
    assert [d["role"] for d in completed] == ["system"]


def test_wp93_chan_both_system_forms_carry_the_block_and_the_digest_names_it() -> None:
    call = ModelCall([], system="S", system_blocks=["S"], context_digest="sha256:aa")
    block = harness_block((STEER,))
    sent = call.with_system_block(block)
    assert sent.system == f"S\n\n{block}" and sent.system_blocks == ["S", block]
    assert sent.context_digest not in (None, "sha256:aa")
    assert call.with_system_block(harness_block(())) is call
    assert harness_block((HarnessInstruction("k", "  "),)) == ""


def test_wp93_chan_every_message_has_an_origin() -> None:
    assert origin_of(UserMessage(content="please help")) == "person"
    assert origin_of(UserMessage(content="note", injected=True)) == "harness"
    assert origin_of(AssistantMessage(content="hi")) == "agent"
    assert origin_of(ToolMessage(content="r", tool_call_id="c1")) == "tool"
