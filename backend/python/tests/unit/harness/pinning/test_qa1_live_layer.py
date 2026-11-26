"""QA-1 NTF-06, NTF-07, NTF-09: the live layer narrows a running attempt, within bounds, and fails closed.

A narrowing reaches a running attempt within 5 s with the change notifier and within the 30 s TTL without it
(NFR-23). A live layer that cannot be refreshed for longer than the TTL is closed for writes and open for
reads inside the pin (NFR-33). The change lands while the attempt runs: on the loop while its read tool
executes, on an engine between two tool calls. The clock is the rig's ``ManualClock``.
"""

from __future__ import annotations

import pytest

from harness_core.testing import Reply

from .support import (
    CREATE,
    DEPLOYMENT_DENIES_WRITES,
    JIRA_CREATE,
    JIRA_SEARCH,
    LIVE_TTL_S,
    NO_RULES,
    NOTIFIED_WITHIN_S,
    SEARCH,
    EngineRig,
    LoopRig,
    call,
    on_search,
)

DONE = Reply(text="done")
BOUNDS = pytest.mark.parametrize(
    ("notifier_up", "within_s"),
    [(True, NOTIFIED_WITHIN_S), (False, LIVE_TTL_S)],
    ids=["ntf06_notifier_up", "ntf07_notifier_down"],
)


@BOUNDS
async def test_ntf06_ntf07_a_tightening_reaches_a_running_loop_attempt(
    monkeypatch: pytest.MonkeyPatch, notifier_up: bool, within_s: float
) -> None:
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), call(CREATE, title="x"), DONE)
    rig.control.approvals(NO_RULES)
    rig.control.notifier(up=notifier_up)

    def admin_denies_writes() -> None:
        rig.control.approvals(DEPLOYMENT_DENIES_WRITES)
        _ = rig.clock.advance(within_s)

    on_search(monkeypatch, admin_denies_writes)
    rig.admit()
    _ = await rig.attempt()

    assert rig.ran(JIRA_SEARCH) == 1
    assert rig.ran(JIRA_CREATE) == 0


@BOUNDS
async def test_ntf06_ntf07_a_tightening_reaches_a_running_engine_attempt(notifier_up: bool, within_s: float) -> None:
    rig = EngineRig()
    rig.control.approvals(NO_RULES)
    rig.control.notifier(up=notifier_up)
    attempt = await rig.attempt()
    assert await attempt.call(CREATE, title="first") == "deferred"

    rig.control.approvals(DEPLOYMENT_DENIES_WRITES)
    _ = rig.clock.advance(within_s)

    assert await attempt.call(CREATE, title="second") == "denied"


async def test_ntf09_a_stale_live_layer_closes_a_running_attempt_for_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), call(CREATE, title="x"), DONE)
    rig.control.approvals(NO_RULES)

    def live_layer_goes_stale() -> None:
        rig.control.partition()
        _ = rig.clock.advance(LIVE_TTL_S + 1)

    on_search(monkeypatch, live_layer_goes_stale)
    rig.admit()
    _ = await rig.attempt()

    assert rig.ran(JIRA_CREATE) == 0


async def test_ntf09_a_stale_live_layer_keeps_reads_inside_the_pin_open(monkeypatch: pytest.MonkeyPatch) -> None:
    rig = LoopRig.scripted(call(SEARCH, jql="open bugs"), call(SEARCH, jql="closed bugs"), DONE)
    rig.control.approvals(NO_RULES)

    def live_layer_goes_stale() -> None:
        rig.control.partition()
        _ = rig.clock.advance(LIVE_TTL_S + 1)

    on_search(monkeypatch, live_layer_goes_stale)
    rig.admit()

    assert (await rig.attempt()).status == "completed"
    assert rig.ran(JIRA_SEARCH) == 2
