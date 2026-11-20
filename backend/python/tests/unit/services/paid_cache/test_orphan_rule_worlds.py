"""The reviewer's R3-1 / R4-2 / R4-3 probe worlds, run through the real guard.

Each world is a timeline of marker writes, graph states (absent, or live incarnation k) and graph read failures, with the run's
checks between them. The oracle is "stop iff the incarnation this run serves was deleted, whatever exists now".
"""

from __future__ import annotations

from typing import Any

import pytest

from app.services.paid_cache.locks import DELETED_TTL_MS, InProcessLocks, deleted_name
from app.services.paid_cache.orphan import RETRY_S, DeliveryGuard, ProbeResult

ORG, REC = "org-1", "rec-1"

WORLDS = [
    ("S13d: an old delete redelivered after a re-push", 2, "T0",
     [("T0", 2, False), ("T40", 2, False), ("T40", 2, False)], "continue"),
    ("a real delete after the run started", 1, None,
     [(None, 1, False), ("T50", None, False), ("T50", None, False)], "stop"),
    ("a real delete, the graph read fails at the marker change, then works", 1, None,
     [("T50", None, True), ("T50", None, False), ("T50", None, False)], "stop"),
    ("a delete and a quick re-push while the OLD run is in flight", 1, None,
     [(None, 1, False), ("T50", 2, False), ("T50", 2, False)], "stop"),
    ("re-created under a marker, the old delete redelivered twice more", 2, "T0",
     [("T15", 2, False), ("T75", 2, False), ("T315", 2, False)], "continue"),
    ("a legacy '1' at the start, then a real delete by new code", 2, "1",
     [("1", 2, False), ("T90", None, False)], "stop"),
]


@pytest.mark.parametrize(("name", "inc", "start", "checks", "should"), WORLDS, ids=[w[0] for w in WORLDS])
async def test_the_rule_stops_a_run_exactly_when_the_incarnation_it_serves_was_deleted(
    name: str, inc: int, start: str | None, checks: list[tuple[str | None, int | None, bool]], should: str,
) -> None:
    locks, clock = InProcessLocks(), [1000.0]
    answer: dict[str, Any] = {"result": ProbeResult(True, True, inc), "fails": False}

    async def probe() -> ProbeResult:
        if answer["fails"]:
            raise ConnectionError("graph down")
        return answer["result"]

    guard = DeliveryGuard(
        org_id=ORG, record_id=REC, wm_record=start, created=inc, probe=probe, confirm_first=start is not None,
        clock=lambda: clock[0],
    )
    guard.mark_read()
    outcome = "continue"
    for marker, graph, fails in checks:
        clock[0] += RETRY_S + 1
        if marker is not None:
            await locks.mark(deleted_name(ORG, REC), DELETED_TTL_MS, marker)
        answer["result"] = ProbeResult(found=False) if graph is None else ProbeResult(True, True, graph)
        answer["fails"] = fails
        if await guard.check(locks):
            outcome = "stop"
            break
    assert outcome == should, name
