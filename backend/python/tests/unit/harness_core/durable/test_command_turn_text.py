"""SLASH1: the durable loop's half of the cross-executor case (the engines' half is the conformance suite's cf18).

Node expands ``/conf-echo alpha second=two`` before admission; the loop journals the expanded text as the user's
message and the model reads exactly that, so a command means the same on the loop and on every engine.
"""

from __future__ import annotations

from harness_core.kernel.core.messages import UserMessage
from harness_core.testing.engines.contract import conf_echo_expansion

from .support import World, reply, scripted


async def test_command_turn_text() -> None:
    expanded = conf_echo_expansion()
    world = World()
    world.admit(text=expanded)
    _, port = scripted(reply("ok"))
    assert (await world.run(world.kernel(port))).status == "completed"

    users = [e for e in world.events("message.completed") if e.data["role"] == "user"]
    assert [await world.blob(e.data["textRef"]) for e in users] == [expanded.encode("utf-8")]
    assert port.calls[0].messages == [UserMessage(content=expanded)]
