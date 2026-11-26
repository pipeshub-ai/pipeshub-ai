"""MA1 QA37-K6, K7 (design D10): a stored pin is data, not a recipe.

The worker verifies a pin against the digest stored in it (``pin_digest``: the canonical digest of the pin's own
members) and reads its dimensions as a map. It never folds a pin again with its own dimension table, so adding a
row (``models.roleCostCeilings``) cannot invalidate a pin made before it (upgrade, K6), and a pin holding a row
this reader's table lacks is accepted and run on the rows the reader knows (rollback, K7).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.control_plane.runtime.deployment import DeploymentLayer
from app.control_plane.runtime.pinning import assemble, release_layer
from app.harness.assembly.context import RESOLVED_CONFIG_INVALID, RunContext
from app.harness.run_policy.pinned import PinnedRunPolicy
from harness_core.contracts import dump_contract
from harness_core.driver import RunRefused
from harness_core.logs import HarnessLog
from harness_core.policy import DimensionPolicyResolver
from harness_core.policy.registry import DimensionRegistry, policy_registry
from harness_core.policy.types import ScopedLayer
from harness_core.testing import Reply
from tests.unit.harness.assembly import world as w

from .support import (
    CREATE,
    DEPLOYMENT_ASKS_FOR_WRITES,
    DEPLOYMENT_DENIES_WRITES,
    JIRA_CREATE,
    JIRA_SEARCH,
    LoopRig,
    call,
    pinned,
)

if TYPE_CHECKING:
    from pydantic import JsonValue

DONE = Reply(text="done")
NEW_ROW = "models.roleCostCeilings"
CASES: list[dict[str, Any]] = json.loads(
    (Path(__file__).resolve().parents[6] / "contracts/control-plane/vectors/resolved-run-config.json").read_text(
        encoding="utf-8"
    )
)["cases"]
# The pins the release before MA1 recorded: roots that resolve (PV-1..PV-8 hold refusals and children too).
BEFORE_MA1 = [
    case
    for case in CASES
    if int(case["id"].removeprefix("PV-")) <= 8 and "parent" not in case and "digest" in case["expected"]
]


def _stored(case: dict[str, Any]) -> dict[str, JsonValue]:
    """The pin as the previous release stored it: its members, and the digest that release recorded."""
    chain = [release_layer(), *(ScopedLayer(layer["scope"]["kind"], layer) for layer in case["layers"])]
    config = DimensionPolicyResolver().resolve(chain).config
    assert config is not None
    return {"schemaVersion": 1, **case["pin"], "effectiveConfig": cast("JsonValue", config), "digest": case["expected"]["digest"]}


def _claimed(pin: dict[str, JsonValue]) -> RunContext:
    spec = pinned(w.pinned_spec(tool_ids=[JIRA_SEARCH, JIRA_CREATE]), pin)
    return RunContext.from_claim(w.claim(spec))


def test_qa37_k6_there_are_pins_of_the_previous_release_to_read() -> None:
    assert len(BEFORE_MA1) >= 4
    assert all(NEW_ROW not in _stored(case)["effectiveConfig"]["dimensions"] for case in BEFORE_MA1)  # type: ignore[index, call-overload]


@pytest.mark.parametrize("case", BEFORE_MA1, ids=[case["id"] for case in BEFORE_MA1])
def test_qa37_k6_a_pin_of_the_previous_release_is_accepted_on_its_own_stored_digest(
    case: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    stored = _stored(case)

    def no_table(*_: object, **__: object) -> DimensionRegistry:
        raise AssertionError("a claim's pin was folded again with this release's dimension table")

    # Reading the pin consults no dimension table: only the pin's own members and digest.
    monkeypatch.setattr("harness_core.policy.registry.policy_registry", no_table)
    monkeypatch.setattr("harness_core.policy.resolver.policy_registry", no_table)
    monkeypatch.setattr("app.control_plane.runtime.pinning.policy_registry", no_table)

    run = _claimed(stored)

    assert run.resolved is not None
    assert run.resolved.digest.root == case["expected"]["digest"]
    assert dump_contract(run.resolved.effective_config) == stored["effectiveConfig"]


@pytest.mark.parametrize("member", ["effectiveConfig", "headRevision"])
def test_qa37_k6_the_digest_check_reads_the_pins_own_content(member: str) -> None:
    stored = _stored(BEFORE_MA1[0])
    if member == "headRevision":
        stored["headRevision"] = cast("int", stored["headRevision"]) + 1
    else:
        dimensions = cast("dict[str, Any]", stored["effectiveConfig"])["dimensions"]
        dimensions["budgets.maxTurns"] = {**dimensions["budgets.maxTurns"], "value": 99}

    with pytest.raises(RunRefused) as refused:
        _ = _claimed(stored)

    assert refused.value.error_type == RESOLVED_CONFIG_INVALID


def _rig_pin(rig: LoopRig, *, without: str | None = None, extra: dict[str, JsonValue] | None = None) -> dict[str, JsonValue]:
    """The rig's pin as another release would have stored it: without a row, or with one this release lacks."""
    fields = {key: value for key, value in (rig.control.pin()).items() if key != "digest"}
    dimensions = dict(cast("dict[str, JsonValue]", cast("dict[str, JsonValue]", fields["effectiveConfig"])["dimensions"]))
    if without is not None:
        _ = dimensions.pop(without, None)
    dimensions.update(extra or {})
    fields["effectiveConfig"] = {"schemaVersion": 1, "dimensions": cast("JsonValue", dimensions)}
    return cast("dict[str, JsonValue]", dump_contract(assemble(fields)))


async def test_qa37_k6_a_run_pinned_before_the_row_existed_resumes_and_replays_on_its_pin() -> None:
    rig = LoopRig.scripted(call(CREATE, title="x"), DONE)
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    pin = _rig_pin(rig, without=NEW_ROW)
    assert NEW_ROW not in cast("dict[str, Any]", pin["effectiveConfig"])["dimensions"]
    _ = rig.admit(pin=pin)

    assert (await rig.attempt()).status == "suspended"
    # After the upgrade an admin loosens the rule: the run keeps the approvals it was pinned with.
    rig.control.approvals(None)
    rig.approve_open()
    assert (await rig.attempt()).status == "completed"

    assert rig.asked() == 1 and rig.ran(JIRA_CREATE) == 1
    assert rig.refused() == 0
    assert [e.type for e in rig.events() if e.type == "run.failed"] == []


FUTURE: dict[str, JsonValue] = {
    "models.aRowOfALaterRelease": {
        "value": {"default": 3},
        "setBy": "org",
        "ceilings": [{"layer": "org", "value": {"default": 3}}],
    }
}


def test_qa37_k7_a_pin_holding_a_row_this_reader_lacks_is_accepted_as_stored() -> None:
    rig = LoopRig.scripted(DONE)
    pin = _rig_pin(rig, extra=FUTURE)

    run = _claimed(pin)

    assert run.resolved is not None and run.resolved.digest.root == pin["digest"]
    held = cast("dict[str, Any]", dump_contract(run.resolved.effective_config))["dimensions"]
    assert held["models.aRowOfALaterRelease"] == FUTURE["models.aRowOfALaterRelease"]


def _without(path: str) -> DimensionRegistry:
    return DimensionRegistry([d for d in policy_registry().all() if d.path != path])


@pytest.mark.parametrize(
    ("extra", "resolver"),
    [
        pytest.param(FUTURE, None, id="an_invented_later_row"),
        pytest.param(
            {NEW_ROW: {"value": {"default": 20}, "setBy": "org", "ceilings": [{"layer": "org", "value": {"default": 20}}]}},
            "previous",
            id="this_releases_row_read_by_the_previous_table",
        ),
    ],
)
async def test_qa37_k7_the_unknown_row_is_carried_as_the_pin_holds_it_and_the_known_rows_are_still_narrowed(
    extra: dict[str, JsonValue], resolver: str | None
) -> None:
    rig = LoopRig.scripted(DONE)
    pin = _rig_pin(rig, extra=extra)
    run = _claimed(pin)
    assert run.resolved is not None
    # An admin tightens after admission: the live layer narrows the rows the reader knows.
    rig.control.approvals(DEPLOYMENT_DENIES_WRITES)
    table = _without(NEW_ROW) if resolver == "previous" else None
    policy = PinnedRunPolicy(
        run,
        run.resolved,
        deployment=DeploymentLayer(),
        live=rig.control.live,
        log=HarnessLog.named("tests.ma1"),
        resolver=DimensionPolicyResolver(table),
    )

    dimensions = await policy._dimensions()  # pyright: ignore[reportPrivateUsage]
    approvals = await policy.approvals()

    [path] = extra
    assert dimensions[path] == extra[path], "kept as the pin holds it: nothing narrows a row the reader does not know"
    assert approvals.rules.layers[0].classes.get("write") == "deny"  # type: ignore[union-attr]
    assert run.resolved.digest.root == pin["digest"]


async def test_qa37_k7_a_run_whose_pin_holds_an_unknown_row_runs_and_is_not_refused() -> None:
    rig = LoopRig.scripted(call(CREATE, title="x"), DONE)
    rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
    _ = rig.admit(pin=_rig_pin(rig, extra=FUTURE))

    assert (await rig.attempt()).status == "suspended"
    rig.approve_open()
    assert (await rig.attempt()).status == "completed"

    failed = [e.data.get("errorType") for e in rig.events() if e.type == "run.failed"]
    assert failed == [] and rig.ran(JIRA_CREATE) == 1
