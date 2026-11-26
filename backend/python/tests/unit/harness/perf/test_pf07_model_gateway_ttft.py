"""PF-07 (C7.05, DP8-7): the model gateway adds at most 50 ms at p95 to an engine's time to first token, and
forwards the first token as the provider sends it (no stream buffering), on every engine wire. Rig: ``model_ttft``.

The forwarding check runs in every unit lane. The latency gate is a quiet-host gate run (``slow``): ``PF07_SAMPLES``
requests per wire (200 by default), and ``PF07_REPORT`` appends each wire's p50/p95/p99 with the host's load as a
JSON line for ``bench_harness.py --profile model-gateway``.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from tests.unit.harness.engines.start_time import percentile
from tests.unit.harness.perf.model_ttft import WIRES, Sample, ScriptedProvider, ttft_rig
from tests.unit.harness.test_node_journal_contract import (
    node_server,  # noqa: F401 - the module-scoped fixture
)

if TYPE_CHECKING:
    from tests.unit.harness.test_node_journal_contract import _NodeServer

SAMPLES = int(os.environ.get("PF07_SAMPLES", "200"))
ADDED_P95_MS = 50.0
HOLD_S = 1.0


def _report(lane: str, samples: list[Sample], load1: list[float]) -> dict[str, float]:
    added = [s.added_ms for s in samples]
    stats = {f"p{q}": round(percentile(added, q), 2) for q in (50, 95, 99)} | {"max": round(max(added), 2)}
    path = os.environ.get("PF07_REPORT")
    if path:
        with Path(path).open("a", encoding="utf-8") as out:
            out.write(json.dumps({"lane": lane, "n": len(samples), "added_ms": stats, "load1_max": max(load1),
                                  "samples": [round(a, 2) for a in added]}) + "\n")
    return stats


@pytest.mark.parametrize("wire", WIRES)
async def test_pf07_the_first_token_is_forwarded_before_the_provider_sends_the_rest(wire: str) -> None:
    async with ttft_rig(ScriptedProvider(first_s=0.01, hold_s=HOLD_S)) as rig:
        sample = await rig.through_gateway(wire)
    assert sample.first_token_lead_s > HOLD_S / 2, f"the gateway held the first token back: {sample}"


@pytest.mark.slow
@pytest.mark.timeout(1800, method="thread")
@pytest.mark.parametrize("wire", WIRES)
async def test_pf07_gateway_added_ttft_p95_within_50_ms(wire: str, request: pytest.FixtureRequest) -> None:
    """Production's wiring: the reservation before each call is a round trip to Node's ledger routes."""
    load1: list[float] = []
    node: _NodeServer = request.getfixturevalue("node_server")
    async with ttft_rig(ScriptedProvider(first_s=0.05), node) as rig:
        for _ in range(5):
            _ = await rig.through_gateway(wire)
            _ = await rig.direct()
        through: list[Sample] = []
        floor: list[Sample] = []
        for _ in range(SAMPLES):
            load1.append(os.getloadavg()[0])
            through.append(await rig.through_gateway(wire))
            floor.append(await rig.direct())
    _report(f"floor:{wire}", floor, load1)
    stats = _report(wire, through, load1)
    assert stats["p95"] <= ADDED_P95_MS, stats
