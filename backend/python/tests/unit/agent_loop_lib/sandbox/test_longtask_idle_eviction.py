"""LONGTASK-F5: a request never starves itself of sandboxes.

Each ``run_code`` call without a ``sandbox_id`` starts a sandbox that lived until the request ended, so the call
after ``max_concurrent`` failed with "retry this call shortly" although only the request's own idle sandboxes held
the slots. A sandbox the tool acquired and released is now evicted (least recently used first) to make room; one a
call is running in never is, and when every slot is busy the error says what to do.
"""

from __future__ import annotations

import pytest

from app.agent_loop_lib.sandbox.manager import (
    SandboxLimitExceeded,
    SandboxLimits,
    SandboxType,
)
from tests.unit.agent_loop_lib.sandbox.test_manager_concurrency import _make_manager

CODING = SandboxType.CODING


async def test_a_released_sandbox_makes_room_for_a_new_one() -> None:
    mgr = _make_manager(limits=SandboxLimits(max_concurrent=2))
    first, _ = await mgr.acquire(CODING)
    mgr.release(CODING, first)
    second, _ = await mgr.acquire(CODING)
    mgr.release(CODING, second)
    third, _ = await mgr.acquire(CODING)
    assert mgr.active_count(CODING) == 2
    assert {second, third} == {k[1] for k in mgr._instances}
    mgr.release(CODING, third)


async def test_a_sandbox_a_call_is_running_in_is_never_evicted() -> None:
    mgr = _make_manager(limits=SandboxLimits(max_concurrent=2))
    await mgr.acquire(CODING)
    await mgr.acquire(CODING)
    with pytest.raises(SandboxLimitExceeded, match="running code right now; wait for one of those calls"):
        await mgr.acquire(CODING)


async def test_a_held_sandbox_reused_by_id_stays_held_until_each_call_releases_it() -> None:
    mgr = _make_manager(limits=SandboxLimits(max_concurrent=1))
    sid, _ = await mgr.acquire(CODING)
    await mgr.acquire(CODING, sid)
    mgr.release(CODING, sid)
    with pytest.raises(SandboxLimitExceeded):
        await mgr.acquire(CODING)
    mgr.release(CODING, sid)
    assert (await mgr.acquire(CODING))[0] != sid
