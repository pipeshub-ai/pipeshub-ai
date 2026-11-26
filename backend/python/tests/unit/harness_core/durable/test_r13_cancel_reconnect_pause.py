"""R13-RCX (worker side): a run paused for a reconnect (WP-64) ends cancelled, promptly, when the person cancels.

WP-64 pauses a run whose toolset's saved sign-in the vendor rejected: the next call of it waits on a connect card,
and the run suspends. WP-65 ends any wait before an engine opens on a cancel. A paused run holds no worker, so Node
ends it in the cancel's own commit: no attempt runs again, the waiting call never reaches the vendor, and a reconnect
answered late does not bring the run back. Node's half is the execution-store contract case R13-RCX-1.
"""

from __future__ import annotations

import pytest

from harness_core.journal.types import InteractionNotOpen, RunNotClaimable

from .support import RUN, reply, scripted
from .test_wp64_reconnect_pause import (
    SECOND,
    RejectedAfterFirstCall,
    _kernel,
    _posts,
    _world,
)


async def test_r13_rcx_2_a_run_paused_for_a_reconnect_is_cancelled_at_once_and_never_runs_again() -> None:
    world = _world()
    _, port = scripted(
        reply("", ("post_message", {"text": "one"})),
        reply("", ("post_message", {"text": "two"})),
        reply("both posted"),
    )
    gate = RejectedAfterFirstCall(world)
    paused = await world.run(_kernel(world, port, gate))
    assert paused.status == "suspended" and paused.reason == SECOND

    world.journal.request_cancel(RUN)
    assert world.types()[-2:] == ["run.cancel_requested", "run.cancelled"], "ended in the cancel's own commit"

    # The person reconnects after cancelling: the answer is not taken and nothing runs again.
    with pytest.raises(InteractionNotOpen):
        world.journal.resolve_interaction(RUN, SECOND, "approved")
    gate.reconnected = True
    try:
        claim = await world.journal.claim(RUN, worker_id="w")
    except RunNotClaimable:
        claim = None
    assert claim is None
    assert _posts(world) == 1, "the waiting call never reached the vendor"
    assert world.types()[-1] == "run.cancelled"
