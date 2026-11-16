"""The throughput cost of the diagnostics writes, measured in one process with the old and the new writes
interleaved batch by batch (the in-process A/B method, so run-to-run and host noise cancel).

    python -m tests.integration.graph_db.record_diagnostics_ab --store arango [--pairs 40] [--batch 100] [--canary]

One "record" is the status-write sequence of an indexing attempt plus a synthetic 1.4 ms of CPU for its processing
(about the record size of a light indexing step):

* A (before): ``update_node`` for IN_PROGRESS, ``update_node`` for the parse-complete status, ``update_node`` for COMPLETED;
* B (after): ``write_attempt_start`` (the one AQL statement), the parse-complete status with the page count in one
  conditional write (``update_node_if_unchanged``: a plain document update guarded by the revision on ArangoDB), ``update_node``
  for COMPLETED. Both arms read the record once, as the pipeline does.

Reports the median of the paired B/A ratios with a bootstrap 95 % interval and the one-sided 95 % upper bound of the
slowdown. ``--canary`` adds 2 % of A's record time as a spin to B: the run is valid only when it reads about 2 % more.
The gate is stated for a realistic record (``--processing-ms 100``, a light indexing step; heavier ones dilute the same cost further):
one-sided 95 % upper bound <= 2 %, inconclusive (half-width > 1 %) = fail. The overhead per record in microseconds is the same at any size. Run it on a quiet host (``bin/capacity.sh``).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import statistics
import time

from tests.integration.graph_db.record_diagnostics_support import (
    RECORDS,
    Store,
    new_org,
)
from tests.integration.real_graph import connect_arango, connect_neo4j

PROCESSING_US = 1400  # overridden by --processing-ms


def spin(microseconds: float) -> None:
    end = time.perf_counter() + microseconds / 1e6
    while time.perf_counter() < end:
        pass


class Monkey:
    def setenv(self, name: str, value: str) -> None:
        import os

        os.environ[name] = value


async def connect(kind: str):  # noqa: ANN201
    logger = logging.getLogger("ab")
    if kind == "arango":
        return await connect_arango(logger, "record_diagnostics_ab")
    return await connect_neo4j(logger, Monkey())  # type: ignore[arg-type]


async def one_batch(store: Store, keys: list[str], new: bool, canary_us: float, rev: str) -> float:
    p = store.p
    started = time.perf_counter()
    for key in keys:
        spin(PROCESSING_US + (canary_us if new else 0))
        fields = {"indexingStatus": "IN_PROGRESS", "processingStartedAt": 1, "contentRev": rev}
        if new:
            await p.write_attempt_start(key, fields)
            snapshot = await p.get_document(key, RECORDS)
            await p.update_node_if_unchanged(key, RECORDS, {"parsingStatus": "COMPLETED", "pageCount": 12}, snapshot, {"contentRev": rev, "indexingStatus": "IN_PROGRESS"})
        else:
            await p.update_node(key, RECORDS, fields)
            await p.get_document(key, RECORDS)
            await p.update_node(key, RECORDS, {"parsingStatus": "COMPLETED"})
        await p.update_node(key, RECORDS, {"indexingStatus": "COMPLETED", "lastIndexTimestamp": 2, "processingStartedAt": None})
    return (time.perf_counter() - started) / len(keys) * 1e6


def bootstrap(values: list[float]) -> tuple[float, float, float]:
    rng = random.Random(1)
    meds = sorted(statistics.median(rng.choices(values, k=len(values))) for _ in range(4000))
    return statistics.median(values), meds[100], meds[3800]


async def measure(kind: str, pairs: int, batch: int, canary_pct: float) -> dict[str, float]:
    provider = await connect(kind)
    store = Store(kind, provider, new_org())
    keys = [store.key("ab") for _ in range(batch)]
    try:
        for key in keys:
            await store.put(key, indexingStatus="QUEUED", firstQueuedAt=5, indexingAttempts=0)
        await one_batch(store, keys, False, 0, "warm")
        await one_batch(store, keys, True, 0, "warm")
        base = await one_batch(store, keys, False, 0, "rev")
        canary_us = base * canary_pct / 100
        ratios = []
        for i in range(pairs):
            first_new = i % 2 == 1
            times = {}
            for new in (first_new, not first_new):
                times[new] = await one_batch(store, keys, new, canary_us, "rev")
            ratios.append((times[True] / times[False] - 1) * 100)
        mid, lo, hi = bootstrap(ratios)
        return {"median_pct": mid, "ci_lo": lo, "upper95": hi, "record_us_before": base}
    finally:
        await store.cleanup()
        await provider.disconnect()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--store", choices=["arango", "neo4j"], default="arango")
    parser.add_argument("--pairs", type=int, default=40)
    parser.add_argument("--batch", type=int, default=100)
    parser.add_argument("--canary", action="store_true")
    parser.add_argument("--gate", type=float, default=2.0, help="fail when the one-sided 95 %% upper bound of the slowdown is above this")
    parser.add_argument("--processing-ms", type=float, default=1.4, help="CPU spent on a record besides its status writes")
    args = parser.parse_args()
    global PROCESSING_US  # noqa: PLW0603
    PROCESSING_US = args.processing_ms * 1000
    result = asyncio.run(measure(args.store, args.pairs, args.batch, 0.0))
    print(f"{args.store} diagnostics writes: median slowdown {result['median_pct']:+.2f} %, lower {result['ci_lo']:+.2f}, "
          f"one-sided 95 % upper bound {result['upper95']:+.2f}, record {result['record_us_before']:.0f} us before (processing {args.processing_ms} ms)")
    passed = result["upper95"] <= args.gate and (result["upper95"] - result["ci_lo"]) / 2 <= 1.0
    print(f"gate: upper bound <= {args.gate} % and half-width <= 1 %: {'PASS' if passed else 'FAIL'}")
    if args.canary:
        # the harness must see a 2 % change: 2 % of the record's time injected on the new side reads 2 +- 0.5 % above the plain run
        canary = asyncio.run(measure(args.store, args.pairs, args.batch, 2.0))
        over = canary["median_pct"] - result["median_pct"]
        valid = abs(over - 2.0) <= 0.5
        print(f"canary (2 % injected) reads {over:+.2f} % over the plain run: {'VALID' if valid else 'VOID'}")
        passed = passed and valid
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
