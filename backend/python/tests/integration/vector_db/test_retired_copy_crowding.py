"""search while a connector is re-indexed in bulk, with the old copies kept.

A connector of N records, 3 chunks each, is re-indexed in bulk: R records commit per second and each retired old copy is kept for the grace
(the cache TTL plus 30 s). The container search fetches FETCH points by the connector's membership, then keeps the points whose virtual
record is a record's committed one (the access check); a search whose survivors fall under 0.8 of the fetch with something denied makes the
service search again by record id (``_narrow_shortfall``). Three worlds on a real Qdrant:

  off      today's order: the old points of a record being re-indexed are deleted at its start
  swap     the old copy kept, its membership taken off its points at the commit (the code under test)
  control  the old copy kept with its membership left on its points until the grace is over (what the first version did)

The share of fallback searches and the recall of the target record at 10 are measured; with the old copy kept, search is no worse than today's
order and the control is clearly worse, so the test would catch the membership staying on.

Needs a Qdrant REST endpoint: QDRANT_REST_URL, or QDRANT_IT_HOST and QDRANT_IT_PORT (the graph jobs' Qdrant). N and R come from KEEP_OLD_COPY_CROWDING_N (default 4000) and
KEEP_OLD_COPY_CROWDING_RATE (default 5). Seeded, so the numbers repeat.
"""
from __future__ import annotations

import os
import random
import uuid

import numpy as np
import pytest

from tests.support import it_backends
from tests.support.require_service import missing

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]

qdrant_client = pytest.importorskip("qdrant_client")
from qdrant_client.models import (  # noqa: E402
    Distance,
    FieldCondition,
    Filter,
    MatchAny,
    PointStruct,
    VectorParams,
)


def _qdrant_url() -> str:
    """QDRANT_REST_URL, else the integration Qdrant on QDRANT_IT_HOST (no default host: unset skips)."""
    return os.environ.get("QDRANT_REST_URL") or f"http://{it_backends.endpoint('QDRANT_IT_HOST')}:{os.environ.get('QDRANT_IT_PORT', '6343')}"

N = int(os.environ.get("KEEP_OLD_COPY_CROWDING_N", "4000"))
RATE = float(os.environ.get("KEEP_OLD_COPY_CROWDING_RATE", "5"))
GRACE, INFLIGHT, DIM, FETCH, QUERIES, CHUNKS = 330, 7, 96, 30, 400, 3


def _corpus() -> np.ndarray:
    rng = np.random.default_rng(8901)
    base = rng.normal(size=(N, DIM))
    chunks = base[:, None, :] + 0.35 * rng.normal(size=(N, CHUNKS, DIM))
    return chunks / np.linalg.norm(chunks, axis=2, keepdims=True)


def _world(kind: str, chunks: np.ndarray) -> dict:
    """The points of each record: (vrid, chunk count, committed, carries the connector's membership)."""
    order = list(range(N))
    random.Random(8902).shuffle(order)
    recent_count = int(RATE * GRACE)
    recent, inflight = set(order[:recent_count]), set(order[recent_count:recent_count + INFLIGHT])
    rng = random.Random(8904)
    sets: dict[int, list[tuple[str, int, bool, bool]]] = {}
    for r in range(N):
        if r in recent:
            sets[r] = (
                [(f"Y{r}", CHUNKS, True, True)] if kind == "off"
                else [(f"X{r}", CHUNKS, False, kind == "control"), (f"Y{r}", CHUNKS, True, True)]
            )
        elif r in inflight:
            sets[r] = (
                [(f"Y{r}", rng.randint(0, CHUNKS), True, True)] if kind == "off"           # the record names a partial copy
                else [(f"X{r}", CHUNKS, True, True), (f"Y{r}", rng.randint(0, CHUNKS), False, True)]
            )
        else:
            sets[r] = [(f"X{r}", CHUNKS, True, True)]
    return {"sets": sets, "inflight": inflight}


def _measure(client, kind: str, chunks: np.ndarray) -> dict:
    world = _world(kind, chunks)
    name = f"crowding_{kind}_{uuid.uuid4().hex[:6]}"
    client.create_collection(name, vectors_config=VectorParams(size=DIM, distance=Distance.COSINE))
    try:
        committed = {v for s in world["sets"].values() for v, _, ok, _ in s if ok}
        points, pid = [], 0
        for r, s in world["sets"].items():
            for vrid, n, _, member in s:
                for c in range(n):
                    pid += 1
                    points.append(PointStruct(id=pid, vector=chunks[r, c].tolist(),
                                              payload={"virtualRecordId": vrid, "record": r, "connectorIds": ["k1"] if member else []}))
        for i in range(0, len(points), 2000):
            client.upsert(name, points[i:i + 2000], wait=True)
        container = Filter(must=[FieldCondition(key="connectorIds", match=MatchAny(any=["k1"]))])
        rng, noise = random.Random(8903), np.random.default_rng(8905)
        targets = [rng.randrange(N) for _ in range(QUERIES)]
        pinned = [t for t in sorted(world["inflight"]) for _ in range(4)]
        targets[: len(pinned)] = pinned  # the records being re-indexed are asked for too: their recall is the point of the swap
        trips = hit = inflight_hit = inflight_n = 0
        for target in targets:
            query = chunks[target, rng.randrange(CHUNKS)] + 0.2 * noise.normal(size=DIM)
            found = client.query_points(name, query=query.tolist(), query_filter=container, limit=FETCH, with_payload=True).points
            kept = [p for p in found if p.payload["virtualRecordId"] in committed]
            if len(found) and len(found) > len(kept) and len(kept) / len(found) < 0.8:
                trips += 1
            got = target in {p.payload["record"] for p in kept[:10]}
            hit += got
            if target in world["inflight"]:
                inflight_n += 1
                inflight_hit += got
        return {"fallback_share": trips / QUERIES, "recall": hit / QUERIES, "inflight_recall": inflight_hit / inflight_n if inflight_n else None}
    finally:
        client.delete_collection(name)


def test_search_with_the_old_copy_kept_is_no_worse_than_without_it_during_a_bulk_reindex() -> None:
    url = _qdrant_url()
    try:
        client = qdrant_client.QdrantClient(url=url, timeout=60, prefer_grpc=False)
        client.get_collections()
    except Exception as exc:
        if not (os.environ.get("QDRANT_REST_URL") or os.environ.get("QDRANT_IT_HOST")):
            pytest.skip("no Qdrant is configured for this job (QDRANT_REST_URL or QDRANT_IT_HOST)")  # the offline job; a job that names one must reach it
        missing(f"Qdrant is not reachable at {url}: {exc}")
    chunks = _corpus()
    off, swap, control = (_measure(client, kind, chunks) for kind in ("off", "swap", "control"))
    print(f"N={N} rate={RATE}/s off={off} swap={swap} control={control}")
    assert swap["fallback_share"] <= off["fallback_share"] + 0.02, (off, swap)
    assert swap["recall"] >= off["recall"] - 0.01, (off, swap)
    assert (swap["inflight_recall"] or 0) >= (off["inflight_recall"] or 0), (off, swap)
    # the control bites where the retired copies are a large share of the corpus: with the membership left on, most searches fall back
    if int(RATE * GRACE) >= 0.2 * N:  # enough records retired at once for the control to crowd
        assert control["fallback_share"] > swap["fallback_share"] + 0.2, (swap, control)
