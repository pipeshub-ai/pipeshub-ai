"""WP-22 OWD-7 / TG-14: one gateway tool list per conversation for an engine.

TP-1 the first engine run of a session journals the list its gateway serves (``session.toolLists``, keyed by engine
     kind); a later run of the session with the same grant journals nothing more.
TP-2 a tool the grant lost since stays listed for the conversation, and a call to it answers plainly that it was removed
     after the chat started; nothing runs.
TP-3 a tool granted since is not listed until a new chat (an engine calls only what it was shown; a lazy grant
     reaches it through call_tool, which the grant decides).
TP-4 the loop's own lists in the same slot ignore an engine's list.
TP-5 TG-14: a new conversation pins its own list and sees what was granted since.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.durable.tool_pin import (
    EngineToolPin,
    decode_list,
    encode_list,
    list_digest,
)
from harness_core.gateway.types import McpTool
from harness_core.kernel.session_tools import ENGINE_LIST_PREFIX, SESSION_TOOLS_SLOT
from harness_core.logs import HarnessLog
from harness_core.testing.engines.durable import (
    CREATE,
    LOOKUP,
    SESSION,
    EngineWorld,
    engine_run_spec,
)

if TYPE_CHECKING:
    from harness_core.journal.types import JournalEvent


def _pins(events: list[JournalEvent]) -> list[dict[str, object]]:
    found: list[dict[str, object]] = []
    for event in events:
        slots = event.data.get("slots") if event.type == "loop.state_saved" else None
        lists = slots.get(SESSION_TOOLS_SLOT) if isinstance(slots, dict) else None
        if isinstance(lists, dict):
            found.extend({"key": key, **entry} for key, entry in lists.items())
    return found


def _results(world: EngineWorld) -> list[tuple[str, str]]:
    return [(kind, text) for kind, text in world.presenter.calls if kind == "tool_result"]


async def test_tp1_the_first_run_pins_the_list_and_a_later_run_reuses_it() -> None:
    world = EngineWorld(approvals={"create": "allow"}, tool_pin=True)
    world.admit("run-1", "say one")
    assert (await world.attempt("run-1")).status == "completed"
    world.admit("run-2", "say two")
    assert (await world.attempt("run-2")).status == "completed"
    pins = _pins(world.journal.events(SESSION))
    assert len(pins) == 1
    assert str(pins[0]["key"]).startswith(ENGINE_LIST_PREFIX)
    # The two tools and the gateway's ask_user (the run has a person to ask).
    assert pins[0]["count"] == 3


async def test_tp2_a_tool_removed_since_stays_listed_and_answers_that_it_is_gone() -> None:
    world = EngineWorld(approvals={"create": "allow"}, tool_pin=True)
    world.admit("run-1", "say one")
    assert (await world.attempt("run-1")).status == "completed"
    world.tools = (LOOKUP,)
    world.admit("run-2", 'tool create {"name": "a"}\nsay two', spec=engine_run_spec(tools=(LOOKUP,)))
    assert (await world.attempt("run-2")).status == "completed"
    texts = [text for _, text in _results(world)] + [
        str(e.data.get("data", {}).get("text")) for e in world.journal.events(SESSION) if e.type.endswith("progress")
    ]
    assert any("no longer available: it was removed after this chat started" in t for t in texts), texts
    assert sum(world.effects.values()) == 0


async def test_tp3_a_tool_granted_since_is_not_listed_until_a_new_chat() -> None:
    world = EngineWorld(approvals={"create": "allow"}, tool_pin=True, tools=(LOOKUP,))
    world.admit("run-1", "say one", spec=engine_run_spec(tools=(LOOKUP,)))
    assert (await world.attempt("run-1")).status == "completed"
    world.tools = (LOOKUP, CREATE)
    world.admit("run-2", 'tool create {"name": "a"}\nsay two', spec=engine_run_spec(tools=(LOOKUP, CREATE)))
    assert (await world.attempt("run-2")).status == "completed"
    assert sum(world.effects.values()) == 0
    assert len(_pins(world.journal.events(SESSION))) == 1


def test_tp_lists_round_trip_and_digest_is_order_sensitive() -> None:
    tools = (McpTool("b", "B", {"type": "object"}), McpTool("a", "A", {"type": "object"}, auth="needs_authentication"))
    assert decode_list(encode_list(tools)) == tools
    assert list_digest(tools) != list_digest(tuple(reversed(tools)))
    assert EngineToolPin("codex", HarnessLog.named("t")).key == f"{ENGINE_LIST_PREFIX}codex"


async def test_tp4_the_loops_own_lists_ignore_an_engines() -> None:
    from harness_core.kernel.core.messages import ToolCall
    from harness_core.kernel.durable.tool_lists import SessionToolLists
    from harness_core.kernel.fold import SessionState

    async def unreadable(_: object) -> bytes:
        raise AssertionError("an engine's list is never read by the loop")

    lists = SessionToolLists(put=unreadable, blob=unreadable, tools=None, log=HarnessLog.named("t"))  # type: ignore[arg-type]
    session = SessionState(stream_id="s", tool_lists={f"{ENGINE_LIST_PREFIX}jcode": {"ref": {}, "digest": "x", "count": 1}})
    assert await lists.refusal(ToolCall(id="c", name="create", arguments={}), session) is None


async def test_tp5_tg14_a_new_conversation_sees_the_tool_granted_since() -> None:
    world = EngineWorld(approvals={"create": "allow"}, tool_pin=True, tools=(LOOKUP,))
    world.admit("run-1", "say one", spec=engine_run_spec(tools=(LOOKUP,)))
    assert (await world.attempt("run-1")).status == "completed"
    world.tools = (LOOKUP, CREATE)
    world.admit("run-new", 'tool create {"name": "a"}\nsay two', spec=engine_run_spec(tools=(LOOKUP, CREATE)), session="session-new")
    assert (await world.attempt("run-new")).status == "completed"
    assert sum(world.effects.values()) == 1
    assert [p["count"] for p in _pins(world.journal.events("session-new"))] == [3]
