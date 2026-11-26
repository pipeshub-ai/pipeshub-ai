"""C5.05 (SLO #11): a run's cache-read share starts as the usage its provider reports on each call through the gateway.

``contracts/harness/vectors/run-cache-read.json`` is one table for both sides. Here the Model Gateway serves each
call of a run from a scripted provider that reports the vector's usage; the run's ledger ``used`` must be the sum, and
the usage body the gateway's Node ledger sends must carry the cache's read and write unchanged, since Node's run end
records ``cacheReadTokens / inputTokens`` from what that ledger summed (``ob04-ops-metrics.test.ts`` C5.05-2 runs the
same rows from that body to the histogram sample).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.harness.access.budget import _usage_wire  # pyright: ignore[reportPrivateUsage] - the body Node's ledger reads
from harness_core.budget.types import LedgerUsage, RunAccountRef
from harness_core.gateway.chat_wire import CHAT_WIRE
from harness_core.kernel.core.responses import TokenUsage
from harness_core.testing.model_gateway import GatewayRig, body_of, reply_events

VECTOR = Path(__file__).resolve().parents[6] / "contracts" / "harness" / "vectors" / "run-cache-read.json"
RUNS: list[dict[str, Any]] = json.loads(VECTOR.read_text(encoding="utf-8"))["runs"]


def _usage(row: dict[str, int]) -> TokenUsage:
    return TokenUsage(
        input_tokens=row["input"],
        output_tokens=row["output"],
        cache_read_tokens=row["cacheRead"],
        cache_write_tokens=row["cacheWrite"],
    )


@pytest.mark.parametrize("run", RUNS, ids=[r["name"] for r in RUNS])
async def test_c5_05_the_ledger_sums_what_the_provider_reported_and_sends_node_the_cache_counts(run: dict[str, Any]) -> None:
    rig = GatewayRig()
    token = rig.token(rig.pin())
    for n, call in enumerate(run["calls"]):
        rig.transport.events = [reply_events(f"step {n}", usage=_usage(call))]
        body = {"model": "engine-model", "messages": [{"role": "user", "content": f"step {n}"}], "stream": False}
        _ = await rig.gateway.serve(CHAT_WIRE, token, {}, body_of(body))
    used = (await rig.ledger.remaining(RunAccountRef("org-1", "run-1"))).used
    want = run["used"]
    assert used == LedgerUsage(want["input"], want["output"], want["cacheRead"], want["cacheWrite"])
    assert _usage_wire(used) == {
        "inputTokens": want["input"],
        "outputTokens": want["output"],
        "cacheReadTokens": want["cacheRead"],
        "cacheWriteTokens": want["cacheWrite"],
    }
    assert round(used.cache_read_tokens / used.input_tokens, 4) == run["ratio"]
    assert (run["ratio"] < 0.7) is run["belowFloor"]


def test_c5_05_the_vector_holds_a_run_on_each_side_of_the_70_percent_floor() -> None:
    assert {r["belowFloor"] for r in RUNS} == {True, False}
