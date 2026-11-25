"""CPD6-20, CPD6-21, CPD6-23, CPD6-24, CPD6-43: a pinned run reads its policy from its pin and nothing live.

A run admitted with a pin carries what admission resolved; its attempts read no approvals, engine-profile or
model-access document and no Labs setting it has pinned. A run admitted without one reads them as before.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

import pytest

from app.harness.assembly.context import RESOLVED_CONFIG_INVALID, RunContext
from app.services.featureflag import platform_settings
from app.services.featureflag.config.config import CONFIG
from app.services.featureflag.platform_settings import (
    bind_pinned_stages,
    is_actions_enabled,
)
from harness_core.contracts import dump_contract
from harness_core.driver import RunRefused
from harness_core.testing import Reply
from tests.unit.harness.assembly import world as w

from .support import (
    CREATE,
    DEPLOYMENT_ASKS_FOR_WRITES,
    DEPLOYMENT_DENIES_WRITES,
    JIRA_CREATE,
    JIRA_SEARCH,
    NO_RULES,
    PINNED_STAGES,
    EngineRig,
    LoopRig,
    call,
    pinned,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

DONE = Reply(text="done")
READERS = {
    "ENABLE_SKILLS": platform_settings.is_skills_enabled,
    "ENABLE_USER_CONTEXT": platform_settings.is_user_context_enabled,
    "ENABLE_KNOWLEDGE_PUBLICATIONS": platform_settings.is_knowledge_publications_enabled,
    # WP-117: the entity tools a durable run is offered (``tool_sources``) follow the pin, not a flip mid-run.
    "ENABLE_NAMED_ENTITY_EXTRACTION": platform_settings.is_named_entity_extraction_enabled,
}


async def _set_flag(config: Any, flag: str, *, on: bool) -> None:  # noqa: ANN401
    """The admin flips a Labs flag now."""
    settings = await config.get_config(platform_settings.PLATFORM_SETTINGS_KEY)
    settings["featureFlags"][flag] = on
    config.put(platform_settings.PLATFORM_SETTINGS_KEY, settings)


@pytest.fixture(autouse=True)
def no_stages_left_bound() -> Iterator[None]:
    yield
    bind_pinned_stages(None)


async def test_cpd6_20_a_pinned_loop_run_reads_no_policy_document() -> None:
    rig = LoopRig.scripted(call(CREATE, title="first"), DONE)
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    rig.admit()
    rig.control.forbid_policy_reads()

    assert (await rig.attempt()).status == "suspended"
    rig.approve_open()
    assert (await rig.attempt()).status == "completed"
    assert rig.ran(JIRA_CREATE) == 1


async def test_cpd6_20_a_pinned_engine_attempt_reads_no_policy_document() -> None:
    rig = EngineRig()
    rig.control.approvals(DEPLOYMENT_DENIES_WRITES)
    attempt = await rig.attempt()
    rig.control.forbid_policy_reads()

    assert await attempt.call(CREATE, title="first") == "denied"
    assert await (await rig.next_attempt()).call(CREATE, title="second") == "denied"


async def test_cpd6_21_a_run_admitted_without_a_pin_reads_the_documents_as_before() -> None:
    rig = LoopRig.scripted(call(CREATE, title="first"), call(CREATE, title="second"), DONE, pins=False)
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    rig.admit()
    assert (await rig.attempt()).status == "suspended"

    rig.control.approvals(NO_RULES)
    rig.approve_open()

    assert (await rig.attempt()).status == "completed", "without a pin the loosening reaches the run (until the flip)"
    assert rig.ran(JIRA_CREATE) == 2


async def test_cpd6_23_labs_stages_follow_the_pin_whatever_the_settings_say_now() -> None:
    rig = LoopRig.scripted(DONE)
    rig.admit()
    assert (await rig.attempt()).status == "completed"
    config = rig.services.config_service()

    for flag, pinned_value in PINNED_STAGES.items():
        await _set_flag(config, flag, on=not pinned_value)
        assert await READERS[flag](config) is pinned_value, flag
    # A flag the pin does not hold is read now.
    await _set_flag(config, CONFIG.ENABLE_ACTIONS, on=False)
    assert await is_actions_enabled(config) is False


async def test_cpd6_23_the_next_unpinned_attempt_of_the_task_reads_the_settings_again() -> None:
    pinned_rig = LoopRig.scripted(DONE)
    pinned_rig.admit()
    _ = await pinned_rig.attempt()
    rig = LoopRig.scripted(DONE, pins=False)
    rig.admit()
    _ = await rig.attempt()
    config = rig.services.config_service()

    await _set_flag(config, "ENABLE_SKILLS", on=True)

    assert await READERS["ENABLE_SKILLS"](config) is True


@pytest.mark.parametrize(
    "damage",
    [
        pytest.param(lambda pin: pin.update(schemaVersion=2), id="a_schema_version_it_does_not_read"),
        pytest.param(lambda pin: pin["effectiveConfig"]["dimensions"].pop("budgets.maxTurns"), id="not_what_was_digested"),
        pytest.param(lambda pin: pin.pop("effectiveConfig"), id="no_effective_config"),
    ],
)
def test_cpd6_24_a_pin_the_worker_cannot_read_refuses_the_claim(damage: Any) -> None:  # noqa: ANN401
    rig = LoopRig.scripted(DONE)
    pin = cast("dict[str, Any]", rig.control.pin())
    damage(pin)
    spec = w.pinned_spec(tool_ids=[JIRA_SEARCH])
    wire = spec.model_dump(mode="json", by_alias=True, exclude_unset=True)
    wire["extensions"]["x.pipeshub"]["resolved"] = pin
    claim = w.claim(type(spec).model_validate(wire))

    with pytest.raises(RunRefused) as refused:
        _ = RunContext.from_claim(claim)

    assert refused.value.error_type == RESOLVED_CONFIG_INVALID


def test_cpd6_24_a_pin_as_admission_made_it_is_read() -> None:
    rig = LoopRig.scripted(DONE)
    pin = rig.control.pin()

    run = RunContext.from_claim(w.claim(pinned(w.pinned_spec(tool_ids=[JIRA_SEARCH]), pin)))

    assert run.resolved is not None
    assert dump_contract(run.resolved) == pin


async def test_cpd6_43_a_run_resumed_across_a_loosening_a_repoint_and_a_labs_flip_runs_as_pinned() -> None:
    rig = LoopRig.scripted(call(CREATE, title="first"), call(CREATE, title="second"), DONE)
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    rig.admit()
    assert (await rig.attempt()).status == "suspended"

    rig.control.approvals(NO_RULES)
    await _set_flag(rig.services.config_service(), "ENABLE_USER_CONTEXT", on=False)
    await _set_flag(rig.services.config_service(), "ENABLE_SKILLS", on=True)
    rig.approve_open()
    resumed = await rig.attempt()

    assert resumed.status == "suspended", "the second write still asks"
    assert rig.asked() == 2
    assert await READERS["ENABLE_USER_CONTEXT"](rig.services.config_service()) is True
    assert await READERS["ENABLE_SKILLS"](rig.services.config_service()) is False


async def test_ntf11_the_kill_switch_marks_a_run_in_flight_and_lets_it_finish() -> None:
    rig = LoopRig.scripted(call(CREATE, title="first"), DONE)
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    rig.admit()
    assert (await rig.attempt()).status == "suspended"

    rig.control.kill_switch(on=True)
    rig.approve_open()

    assert (await rig.attempt()).status == "completed"
    assert rig.ran(JIRA_CREATE) == 1
    layer = await rig.control.live.current(w.ORG)
    assert layer.for_agent(w.AGENT).entries["rollout.AGENT_HARNESS_KILL_SWITCH"].value is True
