"""CPD-6 with CPD-5b's child pin: a narrowing made after the root's admission reaches its children.

A child of a pinned run is pinned as release ⊓ parent ⊓ request and reads no org or agent layer again, so its pin
holds the policy of its root's admission. What an admin tightened since then reaches it only through the live
layer: when the child's attempt is built (a child spawned after the narrowing), and call by call (a child already
running). A loosening reaches neither. The child here is a run admitted with a child's pin; its policy is read
from that pin alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from harness_core.testing import Reply
from tests.unit.harness.assembly import world as w

from .support import (
    CREATE,
    DEPLOYMENT_ASKS_FOR_WRITES,
    DEPLOYMENT_DENIES_WRITES,
    JIRA_CREATE,
    JIRA_SEARCH,
    NO_RULES,
    NOTIFIED_WITHIN_S,
    SEARCH,
    LoopRig,
    call,
    child_pin,
    on_search,
)

if TYPE_CHECKING:
    import pytest

DONE = Reply(text="done")
CHILD = {"run_id": "run-child", "session_id": "ses-child"}


def _spawn(rig: LoopRig) -> None:
    root = rig.admit()
    assert root is not None
    _ = rig.admit(CHILD["run_id"], session_id=CHILD["session_id"], pin=child_pin(root))


async def test_a_child_spawned_after_a_tightening_runs_under_it() -> None:
    rig = LoopRig.scripted(call(CREATE, title="x"), DONE)
    rig.control.approvals(NO_RULES)
    root = rig.admit()
    assert root is not None

    rig.control.approvals(DEPLOYMENT_DENIES_WRITES)
    _ = rig.admit(CHILD["run_id"], session_id=CHILD["session_id"], pin=child_pin(root))
    _ = await rig.attempt(CHILD["run_id"])

    assert rig.ran(JIRA_CREATE, **CHILD) == 0
    assert rig.refused(**CHILD) == 1


async def test_a_child_already_running_is_reached_by_a_tightening(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), call(CREATE, title="x"), DONE)
    rig.control.approvals(NO_RULES)
    _spawn(rig)

    def admin_denies_writes() -> None:
        rig.control.approvals(DEPLOYMENT_DENIES_WRITES)
        _ = rig.clock.advance(NOTIFIED_WITHIN_S)

    on_search(monkeypatch, admin_denies_writes)
    _ = await rig.attempt(CHILD["run_id"])

    assert rig.ran(JIRA_SEARCH, **CHILD) == 1
    assert rig.ran(JIRA_CREATE, **CHILD) == 0


async def test_a_child_spawned_after_a_loosening_keeps_its_roots_rules() -> None:
    rig = LoopRig.scripted(call(CREATE, title="x"), DONE)
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    root = rig.admit()
    assert root is not None

    rig.control.approvals(NO_RULES)
    _ = rig.admit(CHILD["run_id"], session_id=CHILD["session_id"], pin=child_pin(root))

    assert (await rig.attempt(CHILD["run_id"])).status == "suspended"
    assert rig.asked(**CHILD) == 1
    assert rig.ran(JIRA_CREATE, **CHILD) == 0


async def test_ntf11_a_child_spawned_under_the_kill_switch_still_runs_and_its_live_layer_marks_it() -> None:
    rig = LoopRig.scripted(DONE)
    root = rig.admit()
    assert root is not None

    rig.control.kill_switch(on=True)
    _ = rig.admit(CHILD["run_id"], session_id=CHILD["session_id"], pin=child_pin(root))

    assert (await rig.attempt(CHILD["run_id"])).status == "completed"
    layer = await rig.control.live.current(w.ORG)
    assert layer.for_agent(w.AGENT).entries["rollout.AGENT_HARNESS_KILL_SWITCH"].value is True
