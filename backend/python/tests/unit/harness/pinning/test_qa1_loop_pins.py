"""QA-1 PIN-15, NTF-08, APR-29 on the durable loop: what an admin changes while a run is in flight.

A run in flight is one suspended on a person's decision, or one whose worker died and that another attempt
picks up. A loosening must not reach it (NFR-24); a tightening must (the live layer narrows, NFR-23); the
next admission sees the loosening.
"""

from __future__ import annotations

import pytest

from app.agents.chat_modes import policy as chat_modes
from app.agents.chat_modes.policy import ChatModePolicy
from app.harness.assembly.context import ChatGrant
from harness_core.testing import Reply

from .support import (
    CREATE,
    DEPLOYMENT_ASKS_FOR_WRITES,
    DEPLOYMENT_DENIES_WRITES,
    DEPLOYMENT_FLOW_DENIES,
    JIRA_CREATE,
    NO_RULES,
    LoopRig,
    call,
)

DONE = Reply(text="done")


# PIN-15, NTF-08, IX-ADM-PIN-4, IX-TGW-APR-7


async def test_pin15_loosened_approvals_do_not_reach_a_suspended_run() -> None:
    rig = LoopRig.scripted(call(CREATE, title="first"), call(CREATE, title="second"), DONE)
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    rig.admit()
    assert (await rig.attempt()).status == "suspended"

    rig.control.approvals(NO_RULES)
    rig.approve_open()
    resumed = await rig.attempt()

    assert resumed.status == "suspended", "the second write must still ask"
    assert rig.ran(JIRA_CREATE) == 1
    assert rig.asked() == 2


async def test_ntf08_the_next_admission_sees_the_loosening() -> None:
    rig = LoopRig.scripted(call(CREATE, title="held"), call(CREATE, title="next run"), DONE)
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    rig.admit("run-1")
    assert (await rig.attempt("run-1")).status == "suspended"

    rig.control.approvals(NO_RULES)
    rig.admit("run-2", session_id="ses-2")

    assert (await rig.attempt("run-2")).status == "completed"
    assert rig.ran(JIRA_CREATE, "run-2", "ses-2") == 1
    assert rig.asked("run-2", "ses-2") == 0


# PIN-15 (flow rule): the org's own rule, read from the approvals document with each attempt today.


async def test_pin15_a_loosened_flow_rule_does_not_reach_a_reclaimed_attempt() -> None:
    rig = LoopRig.scripted(call(CREATE, title="first"), call(CREATE, title="second"), DONE)
    rig.control.approvals(DEPLOYMENT_FLOW_DENIES)
    rig.admit()
    await rig.worker_dies_after_model_response(2)
    assert rig.ran(JIRA_CREATE) == 1

    rig.control.approvals(NO_RULES)
    _ = await rig.attempt()

    assert rig.ran(JIRA_CREATE) == 1, "a write after an unknown context stays refused under the pinned rule"
    assert rig.refused() == 1


# APR-29: a tightening reaches the run (the live layer narrows).


@pytest.mark.parametrize("tightened", [DEPLOYMENT_DENIES_WRITES, DEPLOYMENT_FLOW_DENIES], ids=["approvals", "flow_rule"])
async def test_apr29_a_tightening_reaches_a_reclaimed_attempt(tightened: object) -> None:
    rig = LoopRig.scripted(call(CREATE, title="first"), call(CREATE, title="second"), DONE)
    rig.control.approvals(NO_RULES)
    rig.admit()
    await rig.worker_dies_after_model_response(2)
    assert rig.ran(JIRA_CREATE) == 1

    rig.control.approvals(tightened)  # type: ignore[arg-type]
    _ = await rig.attempt()

    assert rig.ran(JIRA_CREATE) == 1
    assert rig.refused() == 1


# PIN-15 (chat-mode policy): the mode's policy is code the worker reads; the pinned grant bounds it.

WIDE_OPEN = ChatModePolicy(
    name="internal_search",
    has_knowledge=True,
    include_web_search=True,
    prefetch_retrieval=True,
    system_prompt_key="internal_search",
)


def _wide_open(_mode: str | None) -> ChatModePolicy:
    return WIDE_OPEN


def test_pin15_a_widened_chat_mode_policy_stays_inside_the_pinned_grant(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat_modes, "resolve_chat_mode_policy", _wide_open)
    pinned = ChatGrant(mode="internal_search", internal_search=False, web_search=False)

    policy = pinned.policy()

    assert (policy.has_knowledge, policy.include_web_search, policy.prefetch_retrieval) == (False, False, False)
