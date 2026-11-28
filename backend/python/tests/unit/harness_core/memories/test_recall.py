"""P7.3 PY-4, PY-5, PY-6 (core): recall journaled once, given to every turn; the remember tool;
the ``MemorySource`` contract on the reference adapter; the package imports without PipesHub."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from harness_core.failpoints import Raise
from harness_core.kernel.core.messages import UserMessage
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.logs import HarnessLog
from harness_core.memories import (
    MEMORY_SLOT,
    InMemoryMemorySource,
    MemoryContextShaper,
    MemoryRecallStep,
    RecalledMemory,
    RememberTool,
    memory_text,
    render_memories,
    with_memory,
)
from harness_core.testing import armed
from harness_core.testing.contracts.memories import MemorySourceContract, MemoryWorld
from tests.unit.harness_core.durable.support import Crash, World, reply, scripted

if TYPE_CHECKING:
    from collections.abc import Sequence

    from harness_core.kernel.durable.ports import ModelCall
    from harness_core.kernel.fold import RunState
    from harness_core.memories import MemoryScope, MemorySource

PYTHON_ROOT = Path(__file__).resolve().parents[4]
LOG = HarnessLog.named("tests.memories")
PREF = RecalledMemory("mem_pref", "user", "preference", "Prefers answers in German.", {"complete": True, "sources": []})


class _World:
    def __init__(self) -> None:
        self.sources: list[InMemoryMemorySource] = []

    async def source(self, recalled: Sequence[RecalledMemory], write_scopes: Sequence[MemoryScope]) -> MemorySource:
        source = InMemoryMemorySource(recalled, write_scopes=write_scopes)
        self.sources.append(source)
        return source

    async def forget(self, memory_id: str) -> None:
        for source in self.sources:
            source.forget(memory_id)


class TestInMemoryMemorySource(MemorySourceContract):
    @pytest.fixture
    def memory_world(self) -> MemoryWorld:
        return _World()


class _Plain:
    """The product's shaper, as far as memory cares: it leaves the call alone."""

    async def shape(self, call: ModelCall, turn: int, state: RunState) -> ModelCall:
        return call

    async def enter(self, turn: int, state: RunState) -> None:
        return None

    tool_scope = "inner-attribute"


async def test_py4_the_step_journals_text_entries_and_labels() -> None:
    slot = await MemoryRecallStep(InMemoryMemorySource([PREF]), log=LOG).run("hi")
    assert isinstance(slot, dict)
    assert slot["text"] == render_memories([PREF]).text
    assert slot["entries"] == [
        {"memoryId": "mem_pref", "scope": "user", "kind": "preference", "label": {"complete": True, "sources": []}}
    ]
    assert slot["truncated"] is False
    assert "Prefers answers in German." not in str(slot["entries"])


async def test_py4_an_unavailable_store_gives_no_memory_and_the_run_goes_on() -> None:
    source = InMemoryMemorySource([PREF])
    source.down = True
    slot = await MemoryRecallStep(source, log=LOG).run("hi")
    assert slot == {"text": "", "entries": [], "chars": 0, "truncated": False, "unavailable": True}


async def test_py4_the_shaper_puts_the_text_right_before_the_input_and_delegates() -> None:
    from harness_core.kernel.durable.ports import ModelCall

    text = render_memories([PREF]).text
    call = ModelCall(
        messages=[UserMessage(content="earlier"), UserMessage(content="now")],
        slots={MEMORY_SLOT: {"text": text, "entries": []}},
        input_index=1,
    )
    shaper = MemoryContextShaper(_Plain())
    shaped = await shaper.shape(call, 1, None)  # type: ignore[arg-type]
    assert [m.content for m in shaped.messages] == ["earlier", text, "now"]
    assert shaped.input_index == 2
    assert isinstance(shaped.messages[1], UserMessage) and shaped.messages[1].injected
    assert shaper.tool_scope == "inner-attribute"
    untouched = ModelCall(messages=[UserMessage(content="now")], input_index=0)
    assert await shaper.shape(untouched, 1, None) is untouched  # type: ignore[arg-type]
    unknown_input = await shaper.shape(
        ModelCall(messages=[UserMessage(content="x")], slots={MEMORY_SLOT: {"text": text}}), 2, None  # type: ignore[arg-type]
    )
    assert [m.content for m in unknown_input.messages] == [text, "x"]


async def test_py5_the_model_is_given_the_memory_before_the_input_on_every_turn() -> None:
    world = World()
    world.admit(text="what should I wear?")
    source = InMemoryMemorySource([PREF])
    _, port = scripted(reply("", ("lookup", {"q": "weather"})), reply("A coat."))
    kernel = world.kernel(port, pre_run=[MemoryRecallStep(source, log=LOG)], shaper=MemoryContextShaper(_Plain()))
    assert (await world.run(kernel)).status == "completed"
    text = render_memories([PREF]).text
    for call in port.calls:
        contents = [m.content for m in call.messages]
        assert text in contents
        assert contents.index(text) == contents.index("what should I wear?") - 1
    saved = [e for e in world.events("loop.state_saved") if MEMORY_SLOT in e.data.get("slots", {})]  # type: ignore[operator]
    assert len(saved) == 1
    assert memory_text(saved[0].data["slots"]) == text  # type: ignore[arg-type]


async def test_py4_a_resumed_attempt_sends_the_journaled_text_and_asks_nothing() -> None:
    world = World()
    world.admit(text="hello")
    source = InMemoryMemorySource([PREF])
    _, port = scripted(reply("hi"))
    with armed({"worker.prerun.after_commit": Raise(Crash)}):
        await world.crash(
            world.kernel(port, pre_run=[MemoryRecallStep(source, log=LOG)], shaper=MemoryContextShaper(_Plain()))
        )
    source.forget(PREF.memory_id)
    source.down = True
    kernel = world.kernel(port, pre_run=[MemoryRecallStep(source, log=LOG)], shaper=MemoryContextShaper(_Plain()))
    assert (await world.run(kernel)).status == "completed"
    assert render_memories([PREF]).text in [m.content for m in port.calls[-1].messages]


async def test_py5_without_a_memory_step_the_requests_are_unchanged() -> None:
    world = World()
    world.admit(text="hello")
    _, port = scripted(reply("hi"))
    assert (await world.run(world.kernel(port, shaper=MemoryContextShaper(_Plain())))).status == "completed"
    assert [m.content for m in port.calls[0].messages] == ["hello"]


async def test_py6_remember_proposes_in_a_granted_scope_only_and_never_asks_approval() -> None:
    source = InMemoryMemorySource(write_scopes=["session"])
    tool = RememberTool(source, ["session"])
    assert [p.items["properties"]["scope"]["enum"] for p in tool.parameters if p.name == "memories"] == [["session"]]  # type: ignore[index]
    effect = classify_tool_effect(tool.name, tool.tags)
    assert effect.effect == "workspace" and effect.idempotent
    done = await tool.execute(memories=[{"content": "Meets on Mondays.", "kind": "fact", "scope": "session"}])
    assert done.success and done.data["memories"][0]["status"] == "proposed for review"
    refused = await tool.execute(memories=[{"content": "x", "kind": "fact", "scope": "user"}])
    assert not refused.success
    assert list(source.proposed) == [done.data["memories"][0]["memoryId"]]
    source.down = True
    unavailable = await tool.execute(memories=[{"content": "y", "kind": "fact", "scope": "session"}])
    assert not unavailable.success and "unavailable" in (unavailable.error or "")


async def test_evalfix_f2_each_fact_in_one_call_is_its_own_memory() -> None:
    """FIX-P1-F2: a live model saved four taught facts as one memory, so deleting one deleted all four. The tool takes
    a list, one fact per item, and proposes each as its own memory; the person can delete any one of them."""
    source = InMemoryMemorySource(write_scopes=["user", "session"])
    tool = RememberTool(source, ["user", "session"])
    schema = tool.to_schema().input_schema
    item = schema["properties"]["memories"]["items"]
    assert schema["required"] == ["memories"] and item["required"] == ["content", "kind", "scope"]
    assert item["properties"]["scope"]["enum"] == ["user", "session"]
    assert "one fact" in tool.description and "delete" in tool.description
    facts = ["Our office is in Lisbon.", "My manager is Priya Raman.", "Standup is at 09:30."]
    done = await tool.execute(
        memories=[
            {"content": facts[0], "kind": "fact", "scope": "user"},
            {"content": facts[1], "kind": "fact", "scope": "user"},
            {"content": facts[2], "kind": "fact", "scope": "session"},
        ]
    )
    assert done.success, done.error
    assert [p.content for p in source.proposed.values()] == facts
    assert [m["memoryId"] for m in done.data["memories"]] == list(source.proposed)
    # A retried call proposes the same memories, not new ones.
    again = await tool.execute(memories=[{"content": facts[0], "kind": "fact", "scope": "user"}])
    assert again.data["memories"][0]["memoryId"] == done.data["memories"][0]["memoryId"] and len(source.proposed) == 3


async def test_evalfix_f2_a_call_in_the_old_shape_is_one_memory_and_bad_items_are_named() -> None:
    """A call journaled before the list (a run resumed across the upgrade, a scripted client) still validates."""
    source = InMemoryMemorySource(write_scopes=["user"])
    tool = RememberTool(source, ["user"])
    legacy = {"content": "Prefers short answers.", "kind": "preference", "scope": "user"}
    tool.validate(legacy)
    assert legacy == {"memories": [{"content": "Prefers short answers.", "kind": "preference", "scope": "user"}]}
    assert (await tool.execute(**legacy)).success
    some = await tool.execute(
        memories=[{"content": "Uses metric units.", "kind": "fact", "scope": "user"}, {"content": "x", "kind": "fact", "scope": "session"}]
    )
    assert some.success and some.data["memories"][1]["kept"] is False and "scope" in some.data["memories"][1]["reason"]
    assert len(source.proposed) == 2
    for bad in ([], [{"content": "no kind", "scope": "user"}], ["a string"], [{"content": "y", "kind": "fact", "scope": "user"}] * 11):
        refused = await tool.execute(memories=bad)
        assert not refused.success and refused.error_code == "invalid_call", bad


async def test_evalfix2_c2_remember_can_replace_a_recalled_memory_by_its_id() -> None:
    """FIX-P2 C2: a corrected fact and the fact it corrected were both recalled and the model hedged between them.
    `remember` takes the id of the memory a correction replaces (the recalled block shows each memory's id), so the
    host can retire the old one once the correction is accepted."""
    source = InMemoryMemorySource([PREF], write_scopes=["user"])
    tool = RememberTool(source, ["user"])
    item = tool.to_schema().input_schema["properties"]["memories"]["items"]
    assert item["properties"]["replaces"]["type"] == "string"
    assert "replaces" not in item["required"]
    assert "`replaces`" in tool.description and "id" in tool.description
    done = await tool.execute(
        memories=[{"content": "Prefers answers in English.", "kind": "preference", "scope": "user", "replaces": PREF.memory_id}]
    )
    assert done.success, done.error
    (proposal,) = source.proposed.values()
    assert proposal.replaces == PREF.memory_id
    unknown = await tool.execute(
        memories=[{"content": "Prefers answers in French.", "kind": "preference", "scope": "user", "replaces": "mem_nope"}]
    )
    assert not unknown.success and "MEMORY_REPLACE_UNKNOWN" in (unknown.error or "")
    bad = await tool.execute(memories=[{"content": "z", "kind": "fact", "scope": "user", "replaces": 7}])
    assert not bad.success and bad.error_code == "invalid_call"


def test_engines_get_the_text_as_instructions() -> None:
    rendered = render_memories([PREF])
    assert with_memory("Do the task.", rendered) == f"Do the task.\n\n{rendered.text}"
    assert with_memory("", rendered) == rendered.text
    assert with_memory("Do the task.", render_memories([])) == "Do the task."


_PROBE = """
import importlib.abc, sys

BLOCKED = ("app", "docker", "fastapi", "dependency_injector")

class Block(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split(".")[0] in BLOCKED:
            raise ImportError(f"blocked: {name}")
        return None

sys.meta_path.insert(0, Block())
import harness_core.memories
import harness_core.testing.contracts.memories
assert not any(m.split(".")[0] in BLOCKED for m in sys.modules)
print("isolated")
"""


def test_the_memory_package_imports_with_pipeshub_blocked() -> None:
    result = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=PYTHON_ROOT, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "isolated"
