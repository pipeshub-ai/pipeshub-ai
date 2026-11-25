"""QA13-13 (CTX-17, TEST-STRATEGY §2.2): a request recompiled from the journal alone is the one recorded.

Each golden scenario (the journals every replay corpus freezes) runs again with its worker killed just
before the response commit of model turn k; another worker (a fresh assembler: no process state) claims
the run and compiles turn k from the fold of the journal. Its ``model.request.contextDigest`` and every
earlier one must equal the golden journal's. After turn k the scripted model has spent the reply the
dead worker sampled, so the runs may part ways there; that is the rig, not the property.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from tests.contracts.test_durable_goldens import run_durably
from tests.contracts.test_durable_journals import JOURNALS, RECORDED, STEERING

if TYPE_CHECKING:
    from tests.contracts.golden_support import Scenario

pytestmark = pytest.mark.usefixtures("flow_guard_off")

CRASH = "worker.model.before_response_commit"


def _recorded(name: str) -> list[str]:
    events = json.loads((JOURNALS / f"{name}.journal.json").read_text(encoding="utf-8"))["events"]
    return [str(e["data"]["contextDigest"]) for e in events if e["type"] == "model.request"]


CASES = [(scenario, k) for scenario in RECORDED for k in range(1, len(_recorded(scenario.name)) + 1)]


@pytest.mark.parametrize(("scenario", "turn"), CASES, ids=[f"{s.name}-turn{k}" for s, k in CASES])
async def test_qa13_13_a_resumed_worker_recompiles_the_recorded_request(scenario: Scenario, turn: int) -> None:
    recorded = _recorded(scenario.name)
    events, _ = await run_durably(scenario, crash_at=CRASH, crash_on=turn, steer=STEERING.get(scenario.name))
    attempts = {str(e.data["fence"]) for e in events if e.type == "run.claimed"}
    assert len(attempts) == 2, "the first worker did not die before the response commit"
    compiled = [str(e.data["contextDigest"]) for e in events if e.type == "model.request"]
    assert compiled[:turn] == recorded[:turn]
