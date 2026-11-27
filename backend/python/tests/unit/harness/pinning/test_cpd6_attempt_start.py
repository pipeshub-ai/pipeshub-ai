"""CPD6-50: what an attempt reads before its first model call, with and without a pin.

Attempt start is the path a user waits on after a claim. A run without a pin reads the approvals document for
every attempt (and the engine-profile ceilings for an engine attempt); a pinned run reads neither, and its live
layer is in process after the org's first read. The counts are the measure: no wall clock.
"""

from __future__ import annotations

from collections import Counter

from harness_core.testing import Reply

from .support import (
    APPROVALS,
    DEPLOYMENT_ASKS_FOR_WRITES,
    ENGINE_PROFILES,
    EngineRig,
    LoopRig,
)

DONE = Reply(text="done")


def _policy_reads(rig: LoopRig | EngineRig) -> Counter[str]:
    read = rig.services.config_service().read
    return Counter(key for key in read if key in (APPROVALS, ENGINE_PROFILES))


async def test_cpd6_50_a_pinned_loop_attempt_starts_without_a_policy_document_read() -> None:
    counts = {}
    for pins in (False, True):
        rig = LoopRig.scripted(DONE, pins=pins)
        rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
        rig.admit()
        rig.services.config_service().read.clear()
        assert (await rig.attempt()).status == "completed"
        counts[pins] = _policy_reads(rig)

    assert counts[False] == Counter({APPROVALS: 1})
    assert counts[True] == Counter()


async def test_cpd6_50_a_pinned_engine_attempt_starts_without_a_policy_document_read() -> None:
    counts = {}
    for pins in (False, True):
        rig = EngineRig(pins=pins)
        rig.control.approvals(DEPLOYMENT_ASKS_FOR_WRITES)
        rig.services.config_service().read.clear()
        _ = await rig.attempt()
        counts[pins] = _policy_reads(rig)

    assert counts[False] == Counter({APPROVALS: 1, ENGINE_PROFILES: 1})
    assert counts[True] == Counter()


async def test_cpd6_50_the_live_layer_is_read_once_per_org_not_per_attempt() -> None:
    rig = LoopRig.scripted(DONE, DONE, DONE)
    loads: list[int | None] = []
    load = rig.control.load

    async def counted(org_id: str, since: int | None):  # noqa: ANN202
        loads.append(since)
        return await load(org_id, since)

    rig.control.load = counted  # type: ignore[method-assign]
    for run in ("run-1", "run-2", "run-3"):
        rig.admit(run, session_id=f"ses-{run}")
        assert (await rig.attempt(run)).status == "completed"

    assert loads == [None]
