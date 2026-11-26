"""P4.17 UX-2, UX-3, UX-4, UX-5, UX-6 on the durable loop: a capped or cut turn ends coherently."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from harness_core.contracts.generated.common import ContentRef
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.durable.transcript import (
    decode_assistant,
    decode_result,
    encode_assistant,
)
from harness_core.kernel.hooks.builtin.truncation_recovery import truncation_note
from harness_core.models.output_bound import shortened
from harness_core.testing import Reply, ToolCallSpec
from tests.unit.harness_core.durable.support import SESSION, World, reply, scripted

if TYPE_CHECKING:
    from harness_core.kernel.core.messages import AssistantMessage
    from harness_core.kernel.durable import DurableKernel
    from harness_core.testing.durable import ScriptedModelPort


class _Review:
    """The recovery the product's review makes (``PipesHubResponseReview`` runs the same middleware)."""

    async def review(self, message: AssistantMessage, turn: int, state: object) -> str | None:
        return truncation_note(message.text, message.cut) if message.truncated else None


def _kernel(world: World, port: ScriptedModelPort) -> DurableKernel:
    return world.kernel(port, review=_Review())


def _cut(text: str = "", *calls: tuple[str, dict[str, Any]]) -> Reply:
    return Reply(
        text=text,
        tool_calls=[ToolCallSpec(name=name, args=args) for name, args in calls],
        chunks=2 if text else None,
        stop_reason="max_tokens",
    )


async def _answer(world: World) -> str:
    [final] = [e for e in world.journal.events(SESSION) if e.type == "message.completed" and e.data["isFinal"]]
    return (await world.content.get(ContentRef.model_validate(final.data["textRef"]))).decode()


def _injected(port: ScriptedModelPort, call: int) -> str:
    messages = port.calls[call].messages
    notes = [m for m in messages if isinstance(m, UserMessage) and m.injected]
    content = notes[-1].content if notes else ""
    return content if isinstance(content, str) else "".join(getattr(p, "text", "") for p in content)


async def test_ux2_a_capped_answer_is_continued_and_joined() -> None:
    world = World()
    world.admit()
    _, port = scripted(_cut("Acme's renewal was due 31 January 2026; "), reply("the SafeStop issue is the main risk."))
    done = await world.run(_kernel(world, port))
    assert done.status == "completed"
    assert await _answer(world) == "Acme's renewal was due 31 January 2026; the SafeStop issue is the main risk."
    assert "Continue from where" in _injected(port, 1)


async def test_ux3_a_reasoning_only_exhaustion_asks_for_the_answer() -> None:
    world = World()
    world.admit()
    _, port = scripted(_cut(""), reply("The renewal date has passed."))
    done = await world.run(_kernel(world, port))
    assert done.status == "completed"
    assert await _answer(world) == "The renewal date has passed."
    assert "produced no answer" in _injected(port, 1)


async def test_ux4_a_repetition_cut_is_journaled_and_recovered_with_its_own_note() -> None:
    message = decode_assistant(encode_assistant(_cut_message()))
    assert message.cut == "repetition" and message.truncated
    assert "repeating the same text" in truncation_note(message.text, message.cut)


def _cut_message() -> AssistantMessage:
    from harness_core.kernel.core.messages import AssistantMessage, TextPart

    return AssistantMessage(content=[TextPart(text="The renewal date has passed. ")], truncated=True, cut="repetition")


async def test_ux5_a_cut_tool_call_is_never_run() -> None:
    world = World()
    world.admit()
    _, port = scripted(
        _cut("Filing it.", ("create_issue", {"title": "SafeStop false st"})),
        reply("I could not file it in one go; here is the summary instead."),
    )
    done = await world.run(_kernel(world, port))
    assert done.status == "completed"
    assert world.saas.effects == []
    [outcome] = world.events("tool.outcome")
    assert outcome.data["status"] == "error"
    content, is_error = decode_result(await world.content.get(ContentRef.model_validate(outcome.data["resultRef"])))
    assert is_error and "Tool call not executed" in str(content)
    assert world.events("tool.intent") == []


async def test_ux5_a_whole_tool_call_still_runs() -> None:
    world = World()
    world.admit()
    _, port = scripted(reply("Filing it.", ("create_issue", {"title": "SafeStop"})), reply("Filed."))
    done = await world.run(_kernel(world, port))
    assert done.status == "completed"
    assert len(world.saas.effects) == 1


async def test_ux6_an_answer_still_cut_when_the_turns_run_out_says_it_was_shortened() -> None:
    world = World()
    world.admit(max_turns=2)
    _, port = scripted(
        reply("Looking it up.", ("lookup", {"q": "a"})),
        reply("Checking the renewal.", ("lookup", {"q": "b"})),
        _cut("Acme's renewal was due 31 January 2026, and the open P2 SafeStop issue"),
    )
    done = await world.run(_kernel(world, port))
    assert done.status == "completed"
    assert await _answer(world) == shortened("Acme's renewal was due 31 January 2026, and the open P2 SafeStop issue")
