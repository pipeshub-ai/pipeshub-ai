"""ASSIST1 AS1-14: the default chat's Agent mode on the durable harness, end to end on ST-1.

A turn of the built-in assistant in Agent mode (``chatMode: agent``) through the real Node app, the real worker and
the scripted model: since CTL-5 Agent mode comes with the agent harness, so with ``harness: durable`` on the body it
runs durably on the system assistant definition (pinned ``agentIdPlaceholder``, the loop in ``quick``); without the
field it stays on today's loop, logged ``chat_mode_not_supported``.

    python -m pytest test_assist1_assistant_agent.py --attach=ST-1=RUN
"""

from __future__ import annotations

import secrets
from typing import TYPE_CHECKING, Any

import pytest
from driver import Driver, collect
from fixtures import Fixtures
from matrix import Row
from plan import RowPlan, TurnSpec
from scripts import Script

if TYPE_CHECKING:
    from conftest import Stacks
    from lane import Stack
    from observe import Observation

Json = dict[str, Any]
STACK = "ST-1"


class AssistantDriver(Driver):
    """The web app's default chat in Agent mode: ``harness`` is on the body only when the turn asks."""

    def __init__(self, stack: Stack, fixtures: Fixtures, plan: RowPlan, *, asks: bool) -> None:
        super().__init__(stack, fixtures, plan)
        self.asks = asks

    def _body(self, script: Script) -> Json:
        return {**super()._body(script), **({"harness": "durable"} if self.asks else {})}


def _turn(stack: Stack, *, asks: bool) -> Observation:
    fixtures = Fixtures(stack)
    row = Row(id=f"ASSIST1-{'asks' if asks else 'not'}", array="assist1", stack=STACK, factors={})
    plan = RowPlan(row=row, turn=TurnSpec(kind="assistant", chat_mode="agent"))
    driver = AssistantDriver(stack, fixtures, plan, asks=asks)
    driver.model = fixtures.model(plan.turn.wire)
    stack.model.ok("POST", "/__control/reset")
    marker = f"ASSIST1 {secrets.token_hex(3)}"
    record = driver.send(Script("assist1", (f"say {marker}",)))
    obs = collect(stack, plan, record)
    return obs


@pytest.fixture(scope="module")
def stack(stacks: Stacks) -> Stack:
    return stacks.get(STACK)


def test_as1_14_asked_runs_durably_on_the_system_assistant_definition(stack: Stack) -> None:
    """CTL-5: Agent mode comes with the agent harness; a new conversation that asks runs on it."""
    obs = _turn(stack, asks=True)
    assert obs.route == "durable", (obs.route, obs.reason, obs.legacy_reasons, obs.notes)
    assert obs.terminal == "finished", (obs.terminal, obs.error_code)
    assert obs.marker in obs.answer, obs.answer[:500]
    (pinned,) = obs.of_type("run.spec_pinned")
    spec = (pinned.get("data") or {}).get("spec") or {}
    agent = ((spec.get("extensions") or {}).get("x.pipeshub") or {}).get("agent") or {}
    assert agent == {"agentKey": "agentIdPlaceholder", "definitionVersion": 1, "mode": "quick"}, agent


def test_as1_14_not_asked_it_stays_on_todays_loop(stack: Stack) -> None:
    obs = _turn(stack, asks=False)
    assert obs.route == "legacy", (obs.route, obs.notes)
    assert "chat_mode_not_supported" in obs.legacy_reasons, obs.legacy_reasons
