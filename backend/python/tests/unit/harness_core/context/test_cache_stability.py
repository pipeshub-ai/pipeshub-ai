"""TEST-STRATEGY 2.2 on the kernel: CC-1 prefix_stable_under_append (20 turns, two runs),
CC-3 disclosure appends, CC-8 compaction_changes_prefix_once."""

from __future__ import annotations

from harness_core.context.compiler import TAIL_HEADER
from harness_core.contracts import canonical_json
from tests.unit.harness_core.durable.support import World, scripted

from .kernel_support import SYSTEM, Host, answered, compiler, dump, looked_up


def _bands(call) -> dict[str, object]:
    """System, tools and history of a recorded request (the tail is its last message)."""
    return {
        "system": call.system_blocks,
        "tools": canonical_json([t.model_dump(mode="json") for t in call.tools or []]),
        "history": dump(call.messages[:-1]),
    }


def _assert_stable(calls: list) -> None:
    for before, after in zip(calls, calls[1:], strict=False):
        one, two = _bands(before), _bands(after)
        assert two["system"] == one["system"]
        assert two["history"][: len(one["history"])] == one["history"]
        assert len(two["history"]) > len(one["history"])


async def test_cc1_twenty_turns_and_a_second_run_only_append() -> None:
    world = World()
    host = Host()
    world.admit("run-1", text="look everything up", max_turns=25)
    _, first = scripted(*(looked_up(500, f"q{i}") for i in range(19)), answered())
    assert (await world.run(world.kernel(first, context=compiler(host)), "run-1")).status == "completed"
    world.admit("run-2", text="and once more")
    _, second = scripted(looked_up(500, "again"), answered())
    assert (await world.run(world.kernel(second, context=compiler(host)), "run-2")).status == "completed"
    calls = [*first.calls, *second.calls]
    assert len(calls) == 22
    _assert_stable(calls)
    tools = {_bands(c)["tools"] for c in calls}
    assert len(tools) == 1
    # Every request ends with its volatile tail and nothing else changes in it.
    assert calls[4].messages[-1].content == f"{TAIL_HEADER}\n\nNow: turn 5."
    assert all(c.cache_marks == (len(c.messages) - 2,) for c in calls)


async def test_cc3_a_tool_granted_mid_run_is_appended_to_the_band() -> None:
    world = World()
    host = Host(tools=["zeta", "lookup"], granted_at={4: ["alpha"]})
    world.admit("run-1", max_turns=8)
    _, port = scripted(*(looked_up(300, f"q{i}") for i in range(5)), answered())
    assert (await world.run(world.kernel(port, context=compiler(host)), "run-1")).status == "completed"
    names = [[t.name for t in c.tools or []] for c in port.calls]
    assert names[0] == ["lookup", "zeta"]
    assert names[3] == ["lookup", "zeta", "alpha"]
    assert names[-1] == ["lookup", "zeta", "alpha"]
    order_slots = [e.data["slots"] for e in world.events("loop.state_saved") if "context.toolOrder" in e.data["slots"]]
    assert [s["context.toolOrder"] for s in order_slots] == [
        ["inst/lookup", "inst/zeta"],
        ["inst/lookup", "inst/zeta", "inst/alpha"],
    ]


async def test_cc8_after_a_compaction_the_prefix_is_stable_again() -> None:
    world = World()
    world.admit("run-1", max_turns=20)
    usages = [300] * 6 + [9_000] + [300] * 6
    _, port = scripted(*(looked_up(u, f"q{i}") for i, u in enumerate(usages)), answered())
    assert (await world.run(world.kernel(port, context=compiler()), "run-1")).status == "completed"
    assert len(world.events("context.compacted")) == 1
    compacted_at = next(i for i, c in enumerate(port.calls) if "a summary of the earlier conversation" in str(c.messages[0].content))
    _assert_stable(port.calls[:compacted_at])
    _assert_stable(port.calls[compacted_at:])
    assert len(port.calls[compacted_at:]) >= 5


async def test_p46_3_the_input_rendered_as_the_goal_keeps_history_append_only() -> None:
    from harness_core.kernel.core.messages import ImagePart, ImageSource, TextPart

    world = World()
    image = ImagePart(source=ImageSource(type="url", data="https://img.test/a.png"))
    host = Host(input_text="Find the Q3 goals in the planning doc", input_parts=(image,))
    world.admit("run-1", text="q3 goals?", max_turns=12)
    _, port = scripted(*(looked_up(400, f"q{i}") for i in range(9)), answered())
    assert (await world.run(world.kernel(port, context=compiler(host)), "run-1")).status == "completed"
    assert len(port.calls) == 10
    _assert_stable(port.calls)
    first = port.calls[0].messages[0]
    assert first.content == [TextPart(text="Find the Q3 goals in the planning doc"), image]
    assert all(c.messages[0] == first for c in port.calls)


def test_p45_5_lifted_tool_images_keep_history_append_only() -> None:
    from harness_core.context.compiler import compile_context
    from harness_core.context.types import CompilerInput, HistoryItem, HostBands
    from harness_core.kernel.core.messages import (
        AssistantMessage,
        ImagePart,
        ImageSource,
        TextPart,
        ToolCall,
        ToolMessage,
        UserMessage,
    )

    image = ImagePart(source=ImageSource(type="url", data="https://img.test/chart.png"))

    def turn(seq: int, n: int, *, with_image: bool) -> HistoryItem:
        call = ToolCall(id=f"c{n}", name="lookup", arguments={})
        content = [TextPart(text=f"result {n}"), image] if with_image else f"result {n}"
        return HistoryItem(
            first_seq=seq,
            last_seq=seq + 1,
            messages=(
                AssistantMessage(content=[TextPart(text="t")], tool_calls=[call]),
                ToolMessage(content=content, tool_call_id=f"c{n}"),
            ),
        )

    bands = HostBands(system=SYSTEM, tool_images_after_results=True, tail=("Now.",))
    items = [HistoryItem(first_seq=1, last_seq=1, messages=(UserMessage(content="q"),), own_input=True)]
    previous: list[str] | None = None
    compiled = None
    for n in range(1, 6):
        items.append(turn(2 * n, n, with_image=n in (2, 4)))
        compiled = compile_context(CompilerInput(host=bands, history=tuple(items)))
        history = dump(list(compiled.history))
        if previous is not None:
            assert history[: len(previous)] == previous
        previous = history
    assert compiled is not None
    lifted = [m for m in compiled.history if isinstance(m, UserMessage) and m.injected]
    assert len(lifted) == 2
    assert all(isinstance(m.content, list) and image in m.content for m in lifted)
