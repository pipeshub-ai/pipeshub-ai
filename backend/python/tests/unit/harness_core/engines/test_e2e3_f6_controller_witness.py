"""E2E3-F6 (WP-59) in the engine controller: a session that witnesses the tool gateway gets the attempt's count.

The Tool Gateway is the only party that sees whether an engine with no MCP status API (Gemini CLI over ACP) got
the run's tools. The controller serves each attempt's endpoint, counts the tool lists it serves, and hands that
count to a session that asks for it (``WitnessesToolGateway``) before its first turn.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.engines.ports import ToolListings, WitnessesToolGateway
from harness_core.gateway.ports import NotesConnects
from harness_core.gateway.types import GatewayClaims
from tests.unit.harness_core.engines.test_controller import (  # pyright: ignore[reportPrivateUsage]
    World,
    _StubAdapter,
    _StubSession,
    task,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from harness_core.engines.events import EngineEvent
    from harness_core.engines.types import EngineTask, FollowUp

NEVER = 4_102_444_800


class _WitnessingSession(_StubSession):
    def __init__(self) -> None:
        super().__init__([], wait_for_interrupt=False)
        self.world: World | None = None
        self.listings: ToolListings | None = None
        self.counts: list[int] = []

    def witness_tool_gateway(self, listings: ToolListings) -> None:
        self.listings = listings

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        assert self.listings is not None and self.world is not None
        self.counts.append(self.listings.listed())
        # The engine lists the tools at the gateway the attempt is served by.
        ((endpoint, binding),) = self.world.host._mounts.values()  # pyright: ignore[reportPrivateUsage]
        _ = await endpoint.list_tools(GatewayClaims(binding, kid="test", expires_at=NEVER))
        self.counts.append(self.listings.listed())
        async for event in super().run_turn(task):
            yield event


async def test_e2e3_f6_the_controller_hands_a_witnessing_session_the_attempts_tool_list_count() -> None:
    session = _WitnessingSession()
    assert isinstance(session, WitnessesToolGateway)
    world = World(adapter=_StubAdapter(session))  # type: ignore[arg-type]
    session.world = world
    outcome = await world.controller.run_turn(world.run(), task(0, "x"))
    assert outcome.reason == "completed"
    assert session.counts == [0, 1]


async def test_e2e3_f6_a_session_that_does_not_witness_is_left_alone() -> None:
    session = _StubSession([], wait_for_interrupt=False)
    assert not isinstance(session, WitnessesToolGateway)
    world = World(adapter=_StubAdapter(session))  # type: ignore[arg-type]
    assert (await world.controller.run_turn(world.run(), task(0, "x"))).reason == "completed"


class _HandshakeSession(_WitnessingSession):
    """The engine's MCP handshake at the attempt's gateway, then no list (Goose before its first prompt)."""

    async def run_turn(self, task: EngineTask | FollowUp) -> AsyncIterator[EngineEvent]:
        assert self.listings is not None and self.world is not None
        ((endpoint, _),) = self.world.host._mounts.values()  # pyright: ignore[reportPrivateUsage]
        assert isinstance(endpoint, NotesConnects)
        self.counts.append(self.listings.connected())
        endpoint.note_connect()
        self.counts += [self.listings.connected(), self.listings.listed()]
        async for event in _StubSession.run_turn(self, task):
            yield event


async def test_r30eng_fu3_the_attempts_endpoint_counts_the_handshakes_its_transport_answered() -> None:
    session = _HandshakeSession()
    world = World(adapter=_StubAdapter(session))  # type: ignore[arg-type]
    session.world = world
    assert (await world.controller.run_turn(world.run(), task(0, "x"))).reason == "completed"
    assert session.counts == [0, 1, 0]
