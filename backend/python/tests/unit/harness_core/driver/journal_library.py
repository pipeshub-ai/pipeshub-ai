"""Journals the run machine writes for fixed step sequences: one per way a run goes.

The replay-determinism checks (QA13-12, QA13-15) fold these alongside the golden journals, so they
cover approvals denied, crashes, cancels, steering, unknown effects and quarantine, which the goldens
mostly do not.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from harness_core.testing.history import assert_journal_history
from tests.unit.harness_core.driver.test_run_state_machine import RunStateMachine
from tests.unit.harness_core.durable.support import SESSION

if TYPE_CHECKING:
    from harness_core.journal import JournalEvent

Steps = Callable[[RunStateMachine], None]


def _tool_turn(m: RunStateMachine) -> None:
    m.plan_turns([["lookup", "create_issue"]])
    m.drive(None, 1)


def _approval(approve: bool) -> Steps:
    def steps(m: RunStateMachine) -> None:
        m.plan_turns([["post_message"]])
        m.drive(None, 1)
        m.resolve(approve)
        m.drive(None, 1)

    return steps


def _crash_after_intent(m: RunStateMachine) -> None:
    m.plan_turns([["create_issue"], ["lookup"]])
    m.drive("worker.tool.after_intent", 1)
    m.lease_expire()
    m.sweep()
    m.drive(None, 1)


def _cancel_mid_run(m: RunStateMachine) -> None:
    m.plan_turns([["lookup"]])
    m.drive("worker.model.after_response_commit", 1)
    m.cancel()
    m.lease_expire()
    # The sweeper ends a run being cancelled once its attempt is gone, as Node's does (SL-05).
    m.sweep()


def _steered(m: RunStateMachine) -> None:
    m.plan_turns([["lookup"]])
    m.steer("also this")
    m.drive(None, 1)


def _unknown_effect(m: RunStateMachine) -> None:
    m.plan_turns([["flaky_write"]])
    m.drive(None, 1)
    m.resolve(False)
    m.drive(None, 1)


def _quarantined(m: RunStateMachine) -> None:
    m.plan_turns([["lookup"]])
    m.crash_loop("worker.model.before_response_commit")


def _waiting(m: RunStateMachine) -> None:
    m.plan_turns([["lookup", "post_message"]])
    m.drive(None, 1)


LIBRARY: dict[str, Steps] = {
    "tool_turn": _tool_turn,
    "approval_approved": _approval(True),
    "approval_denied": _approval(False),
    "crash_after_intent": _crash_after_intent,
    "cancel_mid_run": _cancel_mid_run,
    "steered": _steered,
    "unknown_effect": _unknown_effect,
    "quarantined": _quarantined,
    "waiting_for_approval": _waiting,
}


def generated_journal(name: str) -> list[JournalEvent]:
    machine = RunStateMachine()
    try:
        LIBRARY[name](machine)
        events = machine.world.journal.events(SESSION)
    finally:
        machine.teardown()
    assert_journal_history(events)
    return events
