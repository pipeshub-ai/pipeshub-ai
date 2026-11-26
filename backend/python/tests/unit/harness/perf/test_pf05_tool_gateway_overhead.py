"""PF-05 (C7.04, DP8-5): the tool gateway's overhead (auth, grant, policy, audit; the tool excluded) is p50 ≤ 35 ms
and p95 ≤ 100 ms at 40 calls a second on one server (one Node and MongoDB, which queues from ~55/s and saturates at
~90/s; intent-before-execute kept). Rig: ``tool_gateway``.

The audit check runs in every lane with Node: each call through the gateway is approved and journaled on Node
(intent and outcome). The latency gate is a quiet-host gate run (``slow``): ``PF05_SECONDS`` of load (30 by
default) at ``PF05_RPS`` (40) over ``PF05_RUNS`` runs (10: the most one user may hold active), each engine with up to
``PF05_PARALLEL`` calls in flight (4), after a warm-up.
``PF05_MONGO_URI`` (a replica set) puts Node's journal on MongoDB, as deployed; the gate needs it. Unset, the audit
check runs on Node's in-memory store.
The rig freezes its startup heap as the worker does (WP-77: a gen-2 collection over it was the one 0.8 s stall each
run showed). ``PF05_REPORT`` appends the overhead's p50/p95/p99, the rate reached and the host's load as a JSON line for
``bench_harness.py --profile tool-gateway``, with the worker process's CPU share over the window (``worker_cpu``).
"""

from __future__ import annotations

import asyncio
import gc
import json
import os
import time
import uuid
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import pytest

from app.utils.gc_startup import freeze_startup_heap
from harness_core.logs import HarnessLog
from tests.unit.harness.engines.p9_lane import ORG_ID, MongoNode, object_session
from tests.unit.harness.engines.start_time import percentile
from tests.unit.harness.perf.tool_gateway import JournalNode, Pace, tool_gateway_rig
from tests.unit.harness.test_node_journal_contract import (
    node_server,  # noqa: F401 - the module-scoped fixture
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.unit.harness.test_node_journal_contract import _NodeServer

pytestmark = [pytest.mark.timeout(1800, method="thread")]

SECONDS = float(os.environ.get("PF05_SECONDS", "30"))
RPS = float(os.environ.get("PF05_RPS", "40"))
RUNS = int(os.environ.get("PF05_RUNS", "10"))
# Calls one engine has in flight at once (parallel tool calls in a step).
PARALLEL = int(os.environ.get("PF05_PARALLEL", "4"))
WARM_S = 3.0
MONGO_URI = os.environ.get("PF05_MONGO_URI", "")
P50_MS = 35.0
P95_MS = 100.0


@pytest.fixture(scope="module")
def journal_node(request: pytest.FixtureRequest, tmp_path_factory: pytest.TempPathFactory) -> Iterator[JournalNode]:
    if not MONGO_URI:
        server: _NodeServer = request.getfixturevalue("node_server")
        yield JournalNode(server, "org-contract", lambda run_id: f"ses-{run_id}")
        return
    workdir = tmp_path_factory.mktemp("pf05-node")
    mongo = MongoNode("127.0.0.1", MONGO_URI, f"pxh_pf05_{uuid.uuid4().hex[:8]}", workdir / "state", workdir / "node.log")
    try:
        yield JournalNode(mongo, ORG_ID, object_session)
    finally:
        with httpx.Client(timeout=30) as client:
            _ = client.post(f"{mongo.base_url}/__control/drop-database")
        mongo.kill9()


def _events(node: JournalNode, run_id: str) -> Counter[str]:
    answer = node.control.get("/events", params={"orgId": node.org, "runId": run_id}).raise_for_status().json()
    return Counter(str(e["type"]) for e in answer["events"])


async def test_pf05_each_call_through_the_gateway_is_approved_and_journaled(journal_node: JournalNode) -> None:
    pace = Pace(rate_per_s=10, parallel=2)
    async with tool_gateway_rig(journal_node) as rig:
        await rig.run(2, pace)
        await asyncio.sleep(1.0)
        dispositions = await rig.finish(pace)
    calls = [t for t in rig.gateway.timed if t.method == "tools/call"]
    assert not pace.failed, pace.failed
    assert len(calls) == len(pace.latency) >= 10 and all(t.status == 200 for t in calls)
    assert all(t.tool_ms > 0 for t in calls), "the null tool was not run inside the gateway's request"
    assert all(getattr(d, "status", None) == "completed" for d in dispositions), dispositions
    journaled = [_events(journal_node, run_id) for run_id in rig.run_ids]
    assert sum(j["tool.intent"] for j in journaled) == sum(j["tool.outcome"] for j in journaled) == len(calls), journaled


@pytest.mark.slow
async def test_pf05_tool_gateway_overhead_within_budget_at_the_journals_design_capacity(journal_node: JournalNode) -> None:
    """The audit is what a deployment's is: Node's journal on MongoDB. In memory it costs a fraction of that, and a
    number taken so would pass a gateway whose audit does not fit the budget."""
    if not MONGO_URI:
        pytest.skip("PF05_MONGO_URI names no replica set: the gate measures the audit on MongoDB, as deployed")
    pace = Pace(rate_per_s=RPS / RUNS, parallel=PARALLEL)
    async with tool_gateway_rig(journal_node) as rig:
        # As the worker does once it has started (WP-77): its imported heap leaves the cyclic GC, which otherwise
        # walked it in a 0.8 s gen-2 pause mid-load, the one stall each run of this gate showed.
        _ = freeze_startup_heap(HarnessLog.named("tests.pf05"))
        try:
            await rig.run(RUNS, pace)
            await asyncio.sleep(WARM_S)
            measured_from = time.perf_counter()
            cpu_from = time.process_time()
            load1 = os.getloadavg()[0]
            await asyncio.sleep(SECONDS)
            measured_to = time.perf_counter()
            cpu_share = (time.process_time() - cpu_from) / (measured_to - measured_from)
            load1 = max(load1, os.getloadavg()[0])
            _ = await rig.finish(pace)
        finally:
            gc.unfreeze()
    calls = [t for t in rig.gateway.timed if t.method == "tools/call" and measured_from <= t.started < measured_to]
    overhead = [t.overhead_ms for t in calls]
    engine = [ms for started, ms in pace.latency if measured_from <= started < measured_to]
    stats = {f"p{q}": round(percentile(overhead, q), 2) for q in (50, 95, 99)} | {"max": round(max(overhead), 2)}
    client = {f"p{q}": round(percentile(engine, q), 2) for q in (50, 95, 99)}
    record = {"lane": f"{RPS:g}rps", "journal": "mongo", "runs": RUNS, "parallel": PARALLEL, "n": len(calls),
              "rate": round(len(calls) / SECONDS, 1), "failed": len(pace.failed), "overhead_ms": stats,
              "engine_ms": client, "load1_max": load1, "worker_cpu": round(cpu_share, 3)}
    path = os.environ.get("PF05_REPORT")
    if path:
        with Path(path).open("a", encoding="utf-8") as out:
            out.write(json.dumps({**record, "samples": [round(o, 2) for o in overhead]}) + "\n")
    assert not pace.failed, pace.failed[:5]
    assert record["rate"] >= RPS * 0.95, record
    assert stats["p50"] <= P50_MS and stats["p95"] <= P95_MS, record
