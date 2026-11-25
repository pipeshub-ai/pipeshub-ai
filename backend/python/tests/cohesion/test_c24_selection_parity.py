"""C-24 with a selection below app level (SYNC4, MAINSTUDY test 9): the durable loop and today's loop send the model the
same requests on a turn that selected a record, and refuse a selection the user can no longer read the same way.

A selection changes what the model is shown (main #4255: ``has_knowledge`` counts a selection, ``list_files`` lists
the selected nodes, browsing outside it is refused), so a durable path that drops the selection differs on the wire.
``DriveSpec.selection`` is what the client sends beside ``apps``/``kb``; legacy receives it in the request's
filters, the durable route in its pin (``x.pipeshub.knowledge``) and its admitted input.

Written test-first, before ``origin/main`` was merged: red until S1-S5.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from tests.harness_replay import durable_driver, legacy_driver
from tests.harness_replay.wire import (
    drive_captured,
    first_difference,
    outputs_difference,
)
from tests.support.parity_cases.harness_cases import load_cases as harness_cases

pytestmark = pytest.mark.usefixtures("flow_guard_off")

CASES = ("chat-01-kb-answer", "quick-01-kb-lookup")
# A record the fixture user owns on both routes (the goldens' refund policy).
SELECTED = "rec-refund"


def _case(case_id: str):  # noqa: ANN202 - a ParityCase
    (case,) = [c for c in harness_cases() if c.id == case_id]
    assert case.script is not None
    return case


@pytest.mark.parametrize("case_id", CASES)
async def test_c24_a_selection_turn_sends_the_same_requests(case_id: str) -> None:
    case = _case(case_id)
    spec = replace(case.drive_spec(), selection={"records": [SELECTED]})
    legacy = await drive_captured(legacy_driver.drive, spec, case.script)
    durable = await drive_captured(durable_driver.drive, replace(spec), case.script)
    assert legacy.requests, "the scripted model was never called"
    assert first_difference(legacy.requests, durable.requests) is None, first_difference(
        legacy.requests, durable.requests
    )
    assert outputs_difference(legacy.result, durable.result) is None, outputs_difference(
        legacy.result, durable.result
    )


async def test_c24_the_assistant_refuses_an_unreadable_selection_the_same_way_before_the_model_runs() -> None:
    case = _case("chat-01-kb-answer")
    spec = replace(case.drive_spec(), selection={"records": ["rec-nobody-can-read"]})
    legacy = await drive_captured(legacy_driver.drive, spec, case.script)
    durable = await drive_captured(durable_driver.drive, replace(spec), case.script)
    assert legacy.requests == durable.requests == []
    assert legacy.result.error_code == durable.result.error_code == "selection_empty"


async def test_c24_an_agent_runs_an_unreadable_selection_as_main_does_on_both_loops() -> None:
    """Main's agent route refuses only a selection it cannot search (too large, not ready); one that came out
    empty runs and searches nothing. The durable loop does the same."""
    case = _case("quick-01-kb-lookup")
    spec = replace(case.drive_spec(), selection={"records": ["rec-nobody-can-read"]})
    legacy = await drive_captured(legacy_driver.drive, spec, case.script)
    durable = await drive_captured(durable_driver.drive, replace(spec), case.script)
    assert legacy.requests and legacy.result.error_code is None and durable.result.error_code is None
    assert first_difference(legacy.requests, durable.requests) is None, first_difference(
        legacy.requests, durable.requests
    )
