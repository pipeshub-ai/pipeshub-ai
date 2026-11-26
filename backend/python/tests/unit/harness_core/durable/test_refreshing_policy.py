"""CPD-6 NTF-06 at the kernel: an approval policy over live state is refreshed before each turn's calls are decided,
so a rule tightened while the run executes reaches its next write."""

from __future__ import annotations

from typing import TYPE_CHECKING

from .support import World, reply, scripted

if TYPE_CHECKING:
    from harness_core.effects.types import ToolEffect
    from harness_core.kernel.core.messages import ToolCall


class _TightenedAfterTheFirstRefresh:
    """Allows writes until its second refresh, as a policy whose live source was tightened in between."""

    def __init__(self) -> None:
        self.refreshes = 0
        self.decided_at: list[int] = []

    async def refresh(self) -> None:
        self.refreshes += 1

    def __call__(self, call: ToolCall, effect: ToolEffect) -> str:
        self.decided_at.append(self.refreshes)
        return "deny" if effect.needs_intent_commit and self.refreshes > 1 else "allow"


async def test_ntf06_the_kernel_refreshes_a_live_policy_before_it_decides_each_turn_s_calls() -> None:
    world = World()
    world.admit()
    policy = _TightenedAfterTheFirstRefresh()
    _, port = scripted(
        reply("", ("post_message", {"text": "first"})),
        reply("", ("post_message", {"text": "second"})),
        reply("done"),
    )

    disposition = await world.run(world.kernel(port, approval=policy))

    assert disposition.status == "completed"
    assert min(policy.decided_at) >= 1, "no decision before a refresh"
    assert (len(world.events("tool.intent")), len(world.events("tool.denied"))) == (1, 1)
