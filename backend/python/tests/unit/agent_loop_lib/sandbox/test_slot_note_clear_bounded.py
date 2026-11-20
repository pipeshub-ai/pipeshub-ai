"""A stalled event stream cannot hold a granted sandbox slot: clearing the
slot-wait note is bounded."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from app.agent_loop_lib.sandbox import manager as manager_mod
from app.agent_loop_lib.sandbox.governor import GovernorLimits, SandboxResourceGovernor
from app.agent_loop_lib.sandbox.manager import SandboxType

from .qa.test_request_slot_limit import _chat

if TYPE_CHECKING:
    import pytest


async def test_a_note_that_never_clears_does_not_hold_the_granted_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(manager_mod, "_SLOT_NOTE_S", 0.05)
    governor = SandboxResourceGovernor.in_process(GovernorLimits(max_total_sandboxes=1))
    stalled = asyncio.Event()
    queued = asyncio.Event()
    cleared = asyncio.Event()

    async def note(tool: str, *, waiting: bool) -> None:
        if waiting:
            queued.set()
            return
        cleared.set()
        await stalled.wait()  # the event queue is full and nobody drains it

    holder, holder_sid = await _chat(governor)
    waiter, waiter_sid = await _chat(governor, slot_wait_s=5.0, on_slot_wait=note)
    ran = asyncio.Event()

    async def wait_for_slot() -> None:
        async with waiter.slot(SandboxType.CODING, waiter_sid):
            ran.set()

    async with holder.slot(SandboxType.CODING, holder_sid):
        task = asyncio.ensure_future(wait_for_slot())
        await asyncio.wait_for(queued.wait(), 2)  # the waiter is queued, with the note shown
    await asyncio.wait_for(ran.wait(), 2)
    assert cleared.is_set()  # the stalled clear was really attempted
    await asyncio.wait_for(task, 2)
    assert governor.held_total() == 0
