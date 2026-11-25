"""DK-1 (P8a): a schedule fires a durable engine run on the Docker lane.

Node's journal server holds the schedule (real ``ScheduleService``, ``ScheduleFirer``, ``RunAdmissionService``
over the in-memory store); two firers tick over one due slot; the worker runs the one admitted run in the real
engine image with the scripted model, through the real egress proxy and tool gateway (``durable_stack``).
"""

from __future__ import annotations

import os
from collections import Counter
from typing import TYPE_CHECKING, Any

import pytest

from harness_core.budget.types import Cap, PinnedBudget, RunAccountRef
from tests.unit.harness.engines.durable_stack import ORG, durable_lane, engine_spec
from tests.unit.harness.engines.real_stack import engine_image, lane_available

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.docker, pytest.mark.timeout(900, method="thread")]
ENGINE = os.environ.get("E1_ENGINE", "opencode")

DIGEST = """DK1
write out/digest.md weekly support digest
tool lookup {"q": "new tickets"}
say digest ready"""


@pytest.fixture(autouse=True)
def _available() -> None:
    available, reason = lane_available(engine_image(ENGINE))
    if not available:
        pytest.skip(reason)


def _scheduled_spec() -> dict[str, Any]:
    spec = engine_spec(kind=ENGINE)
    spec["runKind"] = "scheduled"
    spec["extensions"]["x.pipeshub"]["via"] = "trigger"
    return spec


async def test_dk1_a_schedule_fires_one_durable_engine_run_that_completes(tmp_path: Path) -> None:
    async with durable_lane(tmp_path, engine=ENGINE) as lane:
        created = (
            await lane.http.post(
                "/schedule",
                json={"cron": "*/5 * * * *", "timeZone": "Europe/Berlin", "inputText": DIGEST, "spec": _scheduled_spec()},
            )
        ).raise_for_status().json()
        ticked = (await lane.http.post("/schedule-tick", json={"scheduleId": created["id"], "firers": 2})).raise_for_status().json()
        firings = ticked["firings"]
        assert [f["status"] for f in firings] == ["admitted"], firings
        run_id, session = firings[0]["runId"], firings[0]["sessionId"]
        # Two firers, one slot: one firing; a second tick over the moved cursor fires nothing more.
        assert ticked["schedule"]["nextFireAt"] > firings[0]["slotAt"]

        lane.sessions[run_id] = session
        lane.ledger.pin(RunAccountRef(ORG, run_id), PinnedBudget(Cap()))
        worker = await lane.worker(run_id)
        result = await worker.wait(600)
        assert result is not None and result["status"] == "completed", (result, worker.log()[-4000:])

        events = await lane.events(run_id)
        admitted = next(e for e in events if e["type"] == "run.admitted")
        assert admitted["data"]["runKind"] == "scheduled"
        assert admitted["data"]["via"] == "trigger"
        assert admitted["data"]["principalRef"] == "p_actor-contract"
        types = Counter(e["type"] for e in events)
        assert types["run.completed"] == 1
        assert types["x.pipeshub.answer.finalized"] == 1
        assert (await lane.run(run_id))["status"] == "completed"
        assert lane.sandboxes(run_id) == []
