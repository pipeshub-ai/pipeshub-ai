"""CPD6-22: an engine run's governance from the pin equals what the documents gave (QA10-F3).

The org's engine-profile ceiling names approval classes; a class it names is the org's rule even where it restates
the release value. A pinned run finds them in the provenance of ``engineProfile.approvals.*`` (the ceilings the
``org`` layer set), a run without a pin in the ceiling document. Both then narrow the org's approval rules by what
the attempt's profile narrows.
"""

from __future__ import annotations

import dataclasses
from typing import Any, get_args

import pytest

from app.control_plane.runtime.deployment import DeploymentLayer
from app.harness.assembly.context import RunContext
from app.harness.run_policy.select import RunPolicies
from harness_core.contracts.generated.sink import SinkDescriptor
from harness_core.effects.types import EffectKind, ToolEffect
from harness_core.engines.types import ProfileApprovals
from harness_core.kernel.core.messages import ToolCall
from harness_core.logs import HarnessLog
from harness_core.testing import ManualClock
from harness_core.testing.engines.durable import engine_profile
from tests.unit.harness.assembly import world as w

from .support import DEPLOYMENT_ASKS_FOR_WRITES, JIRA_CREATE, ControlPlane, pinned

LOG = HarnessLog.named("tests.cpd6.governance")
CALL = ToolCall(id="c", name="t", arguments={})
SINK = SinkDescriptor(egress="external", args_leave_boundary=True)
EFFECTS = [ToolEffect(JIRA_CREATE, kind, False, SINK) for kind in get_args(EffectKind)]

CEILINGS: list[Any] = [
    pytest.param(None, id="no_ceiling"),
    pytest.param({"approvals": {"write": "ask"}}, id="restates_the_release_value"),
    pytest.param({"approvals": {"write": "deny", "read": "ask"}}, id="tightens_two_classes"),
    pytest.param({"mode": "plan"}, id="names_no_approval_class"),
]
PROFILES: list[Any] = [
    pytest.param(ProfileApprovals(), id="release_profile"),
    pytest.param(ProfileApprovals(execute="deny"), id="profile_denies_execute"),
]


async def _decisions(control: ControlPlane, services: w.Services, profile: Any, *, pins: bool) -> list[str]:  # noqa: ANN401
    spec = w.pinned_spec(tool_ids=[JIRA_CREATE])
    run = RunContext.from_claim(w.claim(pinned(spec, control.pin()) if pins else spec))
    policy = RunPolicies(services, deployment=DeploymentLayer(), live=control.live).for_run(run, LOG)  # type: ignore[arg-type]
    governance = await policy.engine_governance(await policy.approvals(), profile)
    return [governance(CALL, effect) for effect in EFFECTS]


@pytest.mark.parametrize("approvals", PROFILES)
@pytest.mark.parametrize("ceiling", CEILINGS)
async def test_cpd6_22_pinned_governance_decides_as_the_documents_did(ceiling: Any, approvals: ProfileApprovals) -> None:  # noqa: ANN401
    services = w.Services()
    control = ControlPlane(services, ManualClock())
    control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    control.engine_ceiling(ceiling)
    profile = dataclasses.replace(engine_profile(), approvals=approvals)

    from_pin = await _decisions(control, services, profile, pins=True)

    assert from_pin == await _decisions(control, services, profile, pins=False)


async def test_cpd6_22_a_ceiling_tightened_after_admission_reaches_the_next_attempt_and_a_loosened_one_does_not() -> None:
    services = w.Services()
    control = ControlPlane(services, ManualClock())
    control.engine_ceiling({"approvals": {"read": "ask"}})
    spec = pinned(w.pinned_spec(tool_ids=[JIRA_CREATE]), control.pin())
    policies = RunPolicies(services, deployment=DeploymentLayer(), live=control.live)  # type: ignore[arg-type]
    read = next(effect for effect in EFFECTS if effect.effect == "read")

    async def decide() -> str:
        policy = policies.for_run(RunContext.from_claim(w.claim(spec)), LOG)
        return (await policy.engine_governance(await policy.approvals(), engine_profile()))(CALL, read)

    assert await decide() == "ask"
    control.engine_ceiling({"approvals": {"read": "deny"}})
    assert await decide() == "deny"
    control.engine_ceiling(None)
    assert await decide() == "ask", "the pinned ceiling stays"
