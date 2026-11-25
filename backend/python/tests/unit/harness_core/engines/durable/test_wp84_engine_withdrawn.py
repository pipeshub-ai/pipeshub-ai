"""WP84-MCP (WP-79 open 4): an engine run keeps the capability observation the loop keeps, so WP-79's D3 holds there.

The engine kernel journals what its first attempt was granted (``capabilities.observed``, the loop's slot); a later
attempt of the same run (a resume after an approval, a re-drive after a crash or a wait) that finds a tool its MCP
server withdrew fails at once ``mcp_tool_withdrawn`` naming the server, before any turn, instead of re-driving the
engine onto a session whose pinned list answers the call "removed". The real ``EngineKernel`` over the in-memory
journal; the host's observation is the world's.

WP84-MCP-9  the first attempt journals its observation once.
WP84-MCP-10 a resumed attempt that finds a tool the run had withdrawn fails at once naming the server; no turn runs.
WP84-MCP-11 a tool withheld since the first attempt is no change: the resumed attempt runs.
WP84-MCP-12 any other difference (a digest moved, a sign-in made) does not stop the engine: the claim's pins (CPD-7)
            refuse a moved digest at the build, and a sign-in is no capability.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.kernel.durable.capability import (
    NEEDS_CONNECT,
    TOOL_WITHDRAWN,
    withdrawn_mark,
)
from harness_core.kernel.durable.slots import CAPABILITY_SLOT
from harness_core.testing.engines.durable import EngineWorld

if TYPE_CHECKING:
    from pydantic import JsonValue

RUN = "run-wp84"
SEARCH = "tool:mcp@mcp-9/search"


def _observations(world: EngineWorld) -> list[object]:
    return [
        e.data["slots"][CAPABILITY_SLOT]
        for e in world.events(RUN)
        if e.type == "loop.state_saved" and CAPABILITY_SLOT in (e.data.get("slots") or {})
    ]


async def _suspended(observed: dict[str, JsonValue]) -> EngineWorld:
    world = EngineWorld(approvals={"create": "ask"}, capabilities=lambda _claim: dict(observed))
    world.admit(RUN, 'tool create {"name": "a"}\nsay waiting')
    disposition = await world.attempt(RUN)
    assert disposition.status == "suspended" and disposition.reason is not None
    _ = world.journal.queue_input(RUN, 'tool create {"name": "a"}\nsay done')
    world.journal.resolve_interaction(RUN, disposition.reason, "approved")
    return world


async def test_wp84_mcp_9_the_first_attempt_journals_its_observation_once() -> None:
    world = EngineWorld(capabilities=lambda _claim: {SEARCH: "sha256:a"})
    world.admit(RUN, "say done")
    assert (await world.attempt(RUN)).status == "completed"
    assert _observations(world) == [{SEARCH: "sha256:a"}]


async def test_wp84_mcp_10_a_resumed_attempt_that_finds_a_tool_withdrawn_fails_at_once_naming_the_server() -> None:
    observed: dict[str, JsonValue] = {SEARCH: "sha256:a"}
    world = await _suspended(observed)
    observed[SEARCH] = withdrawn_mark("GitHub")

    disposition = await world.attempt(RUN)

    assert (disposition.status, disposition.reason) == ("failed", TOOL_WITHDRAWN)
    (failed,) = [e for e in world.events(RUN) if e.type == "run.failed"]
    assert failed.data["errorType"] == TOOL_WITHDRAWN and "GitHub" in failed.data["message"]
    assert failed.data["servers"] == ["GitHub"]
    assert world.types(RUN).count("turn.started") == 1
    assert sum(world.effects.values()) == 0
    assert len(_observations(world)) == 1


async def test_wp84_mcp_11_a_tool_withheld_since_the_first_attempt_is_no_change() -> None:
    world = await _suspended({SEARCH: withdrawn_mark("GitHub")})
    assert (await world.attempt(RUN)).status == "completed"
    assert sum(world.effects.values()) == 1


async def test_wp84_mcp_12_a_moved_digest_or_a_sign_in_does_not_stop_the_engine() -> None:
    observed: dict[str, JsonValue] = {SEARCH: "sha256:a", "tool:mcp@mcp-9/list": NEEDS_CONNECT}
    world = await _suspended(observed)
    observed.update({SEARCH: "sha256:b", "tool:mcp@mcp-9/list": "sha256:c"})
    assert (await world.attempt(RUN)).status == "completed"


async def test_wp79_return_2_a_tool_withheld_at_the_first_attempt_that_came_back_stays_withheld() -> None:
    # WP79-FU-5 on engine runs: the run never held it, so the kernel asks the host to withhold it again before the
    # resumed turn, and the run goes on without it; the next run offers it.
    observed: dict[str, JsonValue] = {SEARCH: withdrawn_mark("GitHub"), "tool:mcp@mcp-9/list": "sha256:l"}
    world = await _suspended(observed)
    observed[SEARCH] = "sha256:back"

    assert (await world.attempt(RUN)).status == "completed"
    assert world.withheld == {SEARCH: withdrawn_mark("GitHub")}
    assert sum(world.effects.values()) == 1
    assert len(_observations(world)) == 1
