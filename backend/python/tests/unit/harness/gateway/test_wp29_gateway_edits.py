"""AP-06 (C3.75) at the engine's tool gateway, over real HTTP and the MCP SDK client: a person edits a held call
before approving it. The engine's re-call with its own arguments runs the edit once, its result says what ran, and a
later identical re-call answers from the log; an edit nobody can read runs nothing."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

import pytest

from harness_core.effects.edits import EDIT_UNREADABLE, ApprovedEdit
from harness_core.engines.durable.kernel import (
    _decisions_note,  # pyright: ignore[reportPrivateUsage]
)
from harness_core.interactions import InMemoryInteractionBridge
from tests.unit.harness.gateway.support import GatewayServer, served
from tests.unit.harness.gateway.test_wp10_gateway_hitl import Run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


class _UnreadableEdits(InMemoryInteractionBridge):
    """A journal whose approval carries an edit that does not verify."""

    @override
    def edit(self, interaction_id: str) -> ApprovedEdit | None:
        return ApprovedEdit(None, "sha256:" + "0" * 64) if interaction_id in self.requests else None


@pytest.fixture
async def server() -> AsyncIterator[GatewayServer]:
    async with GatewayServer().running() as running:
        yield running


async def _requested(bridge: InMemoryInteractionBridge) -> str:
    for _ in range(500):
        if bridge.requests:
            return next(iter(bridge.requests))
        await asyncio.sleep(0.001)
    raise AssertionError("the call was never held")


async def test_ap06_the_engines_recall_runs_the_persons_edit_once(server: GatewayServer) -> None:
    runs: list[str] = []
    bridge = InMemoryInteractionBridge()
    run = Run("run-a", runs, bridge)
    async with served(server, run.endpoint) as client:
        held = await client.call_tool(run.claims, "tickets__create_ticket", {"q": "x"})
        assert held.interaction_id is not None
        bridge.decide(held.interaction_id, "approved", edits={"q": "y"})
        approved = await client.call_tool(run.claims, "tickets__create_ticket", {"q": "x"})
        again = await client.call_tool(run.claims, "tickets__create_ticket", {"q": "x"})
    assert (approved.disposition, again.disposition) == ("executed", "recorded")
    assert runs == ["tickets__create_ticket:y"]
    assert '"q": "y"' in approved.text and "changed this call's arguments" in approved.text


async def test_ap06_an_edit_decided_during_the_hold_runs_in_place_of_the_call(server: GatewayServer) -> None:
    runs: list[str] = []
    bridge = InMemoryInteractionBridge()
    run = Run("run-a", runs, bridge)
    async with served(server, run.endpoint) as client:
        calling = asyncio.ensure_future(client.call_tool(run.claims, "tickets__create_ticket", {"q": "x"}))
        bridge.decide(await _requested(bridge), "approved", edits={"q": "z"})
        done = await calling
    assert (done.is_error, done.disposition) == (False, "executed")
    assert runs == ["tickets__create_ticket:z"]


async def test_ap06_an_edit_nobody_can_read_runs_nothing(server: GatewayServer) -> None:
    runs: list[str] = []
    bridge = _UnreadableEdits()
    run = Run("run-a", runs, bridge)
    async with served(server, run.endpoint) as client:
        held = await client.call_tool(run.claims, "tickets__create_ticket", {"q": "x"})
        assert held.interaction_id is not None
        bridge.decide(held.interaction_id, "approved")
        refused = await client.call_tool(run.claims, "tickets__create_ticket", {"q": "x"})
    assert (refused.is_error, refused.disposition) == (True, "denied")
    assert EDIT_UNREADABLE[0] in refused.text
    assert runs == [] and run.log.intents == []


def test_ap06_the_engines_resume_note_says_which_approval_carried_changes() -> None:
    note = _decisions_note({"ix.a": "approved", "ix.b": "approved"}, {"ix.a": '{"q":"y"}'})
    assert "changed the arguments of ix.a" in note and "ix.b" not in note.split("changed the arguments of", 1)[1]
    plain = _decisions_note({"ix.b": "approved"})
    assert "changed the arguments" not in plain
