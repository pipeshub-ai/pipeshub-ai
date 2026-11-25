"""AP-09 (C3.76): several calls held at once are each decided on their own. A later call approved first runs without
waiting for an earlier one; each decision applies to its own call only; the model reads the results in the order it
made the calls, whatever order the person decided them in."""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.testing.interactions import ManualWakeup

from .support import RUN, World, project, reply, scripted

if TYPE_CHECKING:
    from pydantic import JsonValue

    from harness_core.effects.types import ApprovalDecision, ToolEffect
    from harness_core.kernel.core.messages import ToolCall

FIRST, SECOND, THIRD = "ix.approve.t1.0", "ix.approve.t1.1", "ix.approve.t1.2"


class AsksForWrites:
    def __call__(self, call: ToolCall, effect: ToolEffect) -> ApprovalDecision:
        return "ask" if effect.needs_intent_commit else "allow"

    def ask_reason(self, call: ToolCall, effect: ToolEffect) -> dict[str, JsonValue]:
        return {"rule": "writes", "layer": "org", "toolId": effect.tool_id, "conversationGrant": False}


def _posted(world: World) -> list[object]:
    return [e.payload.get("text") for e in world.saas.effects if e.op == "post_message"]


async def test_ap09_each_held_call_is_decided_on_its_own_and_results_keep_the_calls_order() -> None:
    world = World()
    world.admit()
    _, port = scripted(
        reply(
            "",
            ("post_message", {"text": "one"}),
            ("post_message", {"text": "two"}),
            ("post_message", {"text": "three"}),
        ),
        reply("done"),
    )
    wakeup = ManualWakeup(world.clock)
    posted_when: dict[int, list[object]] = {}

    def on_wait(waits: int) -> None:
        posted_when[waits] = _posted(world)
        decide = {1: (THIRD, "approved"), 2: (FIRST, "denied"), 3: (SECOND, "approved")}.get(waits)
        if decide is not None:
            world.journal.resolve_interaction(RUN, decide[0], decide[1])  # type: ignore[arg-type]
            wakeup.wake(RUN)

    wakeup.on_wait = on_wait
    done = await world.run(
        world.kernel(port, approval=AsksForWrites(), interactions=wakeup, approval_hold_s=10.0, interaction_poll_s=2.0)
    )
    assert done.status == "completed"
    # The third ran as soon as it was approved, while the first two still waited.
    assert posted_when[2] == ["three"]
    assert _posted(world) == ["three", "two"]
    requested = [e.data["interactionId"] for e in world.events("interaction.requested")]
    assert requested == [FIRST, SECOND, THIRD]
    resolved = [(e.data["interactionId"], e.data["decision"]) for e in world.events("interaction.resolved")]
    assert resolved == [(THIRD, "approved"), (FIRST, "denied"), (SECOND, "approved")]
    results = [text for role, text in await project(world) if role == "tool"]
    assert len(results) == 3
    assert "approval_denied" in results[0]
    assert '"posted"' in results[1] and '"posted"' in results[2]
