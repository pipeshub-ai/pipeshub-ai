"""QA-3 OBS-1..OBS-3: what a run observes of its toolset tools from one attempt to the next.

A pin without ``digestVersion`` (a run admitted before CPS, or by a Node that binds nothing) is not checked against
admission; what its first attempt observed is all a later attempt has to hold a toolset tool to.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from harness_core.driver import RunDriver
from harness_core.failpoints import Raise, registry
from harness_core.testing import (
    ManualClock,
    ModelScript,
    Reply,
    ScriptedModel,
    ToolCallSpec,
    Turn,
)
from harness_core.testing.durable import ScriptedModelPort

from . import releases
from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

JIRA_SEARCH = f"jira@{w.JIRA}/search_issues"
OBSERVED = f"tool:{JIRA_SEARCH}"
CHANGES: dict[str, dict[str, Any]] = {
    "another description": {"description": "Search Jira issues, and mail them."},
    "an enum that gained a value": {"statuses": ("open", "closed", "deleted")},
    "array items of another type": {"items": {"type": "object"}},
    "a nested property of another type": {"sprint": {"type": "object", "properties": {"id": {"type": "string"}}}},
}


class _Died(BaseException):
    pass


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield
    registry.reset()


def _port(*replies: Reply) -> ScriptedModelPort:
    return ScriptedModelPort(ScriptedModel(ModelScript(id="obs", turns=[Turn(reply=r) for r in replies])))


async def _observed() -> dict[str, Any]:
    assembler = PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(_port(Reply(text="ok"))))
    runtime = await assembler.build(w.claim(w.pinned_spec(tool_ids=[JIRA_SEARCH])))
    assert runtime.capability_check is not None
    return dict(await runtime.capability_check.observe())


@pytest.mark.parametrize("change", CHANGES, ids=str)
async def test_obs1_a_granted_toolset_tool_is_observed_as_the_release_declares_it(
    change: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    releases.deploy(monkeypatch, jira=releases.jira())
    first = await _observed()
    releases.deploy(monkeypatch, jira=releases.jira())
    assert await _observed() == first, "the same release observes the same"

    releases.deploy(monkeypatch, jira=releases.jira(**CHANGES[change]))

    second = await _observed()
    assert set(second) == set(first) == {OBSERVED}
    assert second[OBSERVED] != first[OBSERVED]


async def test_obs2_a_later_attempt_does_not_run_on_a_toolset_tool_that_changed_since_the_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases.deploy(monkeypatch, jira=releases.jira())
    clock = ManualClock()
    journal = w.pipeshub_journal(clock=clock, lease_ms=30_000)
    journal.admit(
        "run-1", org_id=w.ORG, session_id="ses-1", spec=w.pinned_spec(tool_ids=[JIRA_SEARCH]), input_text="find it"
    )
    port = _port(
        Reply(tool_calls=[ToolCallSpec(name="jira__search_issues", args={"status": "open", "labels": [], "filter": {}})]),
        Reply(text="never"),
    )
    driver = RunDriver(
        journal=journal,
        assembler=w.ClaimAssembler(PipesHubRuntimeAssembler(w.Services(), model_factory=w.model_factory(port))),
        clock=clock,
    )
    registry.arm("worker.model.after_response_commit", Raise(_Died))
    with pytest.raises(_Died):
        _ = await driver.run_claimed(await w.claim_next(journal))
    registry.reset()
    _ = clock.advance(31)
    _ = journal.sweep()

    releases.deploy(monkeypatch, jira=releases.jira(statuses=("open", "closed", "deleted")))

    lost = await driver.run_claimed(await w.claim_next(journal))
    assert (lost.status, lost.reason) == ("yielded", "capability_wait")
    assert len(port.calls) == 1


async def test_obs3_a_tool_the_grant_does_not_name_and_the_release_s_own_tools_cost_the_run_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    releases.deploy(monkeypatch, jira=releases.jira())
    first = await _observed()

    releases.deploy(
        monkeypatch,
        jira=releases.jira(create_description="Create a Jira issue in any project."),
        knowledgegraph=releases.knowledge("Search everything the organization knows."),
    )

    assert await _observed() == first
