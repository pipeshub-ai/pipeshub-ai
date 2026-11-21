# ruff: noqa: ANN201, ANN202, RUF100
"""Speed gate of the insert-only record write, on a real store. Not collected by pytest.

The shipped write paths against a baseline that already carries ``incarnationId`` (the gate: ratio of medians >= 0.90 at every batch
size), and that baseline against the write without the field (stated, not gated). The records are realistic (about 35 fields, ~1.7 KB, all valid on the strict ArangoDB schema).
Neo4j also runs the shipped statement's KH clauses. ArangoDB runs a per-record AQL UPSERT as the slow control, and the run fails if
the control passes the gate (a bench that cannot tell the slow shape from the good one proves nothing).

  python tests/perf/bench_record_insert_only_gate.py <store> --n 150 --sizes 1,500,2000 --calls 6 --rounds 8 --seed 55017

Per round the variants run in a random order (seeded). Ratio = median(new) / median(baseline). Prints one JSON line per (phase, size).
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import random
import statistics
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.constants.arangodb import CollectionNames

RECORDS = CollectionNames.RECORDS.value
GATE = 0.90


def doc(k: str, n: int, *, idkey: str) -> dict:
    return {idkey: k, "orgId": "bench", "externalRecordId": f"e-{k}", "recordType": "FILE",
            "origin": "CONNECTOR", "connectorName": "KB", "connectorId": "bench-conn", "createdAtTimestamp": 1000 + n,
            "updatedAtTimestamp": 2000 + n, "isDeleted": False, "isPlaceholder": False, "indexingStatus": "COMPLETED",
            "incarnationId": uuid.uuid4().hex,
            "recordGroupId": uuid.uuid4().hex, "externalGroupId": uuid.uuid4().hex, "mimeType": "application/pdf",
            "webUrl": f"https://example.com/files/{k}?v=" + "x" * 80, "sizeInBytes": 12345 + n, "sourceCreatedAtTimestamp": 900 + n,
            "sourceLastModifiedTimestamp": 950 + n, "parsingStatus": "COMPLETED", "extractionStatus": "COMPLETED",
            "virtualRecordId": uuid.uuid4().hex, "md5Checksum": uuid.uuid4().hex, "version": 3, "isArchived": False,
            "isShared": False, "isInternal": False, "isDependentNode": False, "hideWeburl": False, "previewRenderable": True,
            "accessRule": "OPEN", "externalRevisionId": uuid.uuid4().hex, "summaryDocumentId": uuid.uuid4().hex,
            "recordName": f"Quarterly planning notes {n}-{random.random():.6f} " + "lorem ipsum " * 40,
            "reason": "r" * 200, "parentNodeId": uuid.uuid4().hex, "entityExtractionStatus": "COMPLETED",
            "lastExtractionTimestamp": 3000 + n, "externalParentId": uuid.uuid4().hex}


def strip_id(d: dict) -> dict:
    return {k: v for k, v in d.items() if k != "incarnationId"}


async def rate(fn, batches: list[list[dict]]) -> float:
    t0 = time.perf_counter()
    for b in batches:
        await fn(b)
    return sum(len(b) for b in batches) / (time.perf_counter() - t0)


def report(store, phase, size, res, a, b, gated):
    r = statistics.median(res[b]) / statistics.median(res[a])
    out = {"store": store, "phase": phase, "per_call": size, "vs": f"{b}/{a}", "ratio": round(r, 3),
           a: round(statistics.median(res[a])), b: round(statistics.median(res[b])), "gate": GATE if gated else None,
           "pass": (r >= GATE) if gated else None, "load1": round(os.getloadavg()[0], 1)}
    print(json.dumps(out), flush=True)
    return out


async def run_store(store: str, variants_update: dict, variants_insert: dict, key_of, n: int, sizes: list[int], calls: int, rounds: int, rng, cleanup=None):
    """variants_*: name -> async fn(list[doc]). Update phase rewrites stored keys; insert phase writes fresh keys."""
    out = []
    if cleanup:
        await cleanup()
    for size, count in [(z, n if z == 1 else z * calls) for z in sizes]:
        stored = [f"bench-{uuid.uuid4().hex[:8]}-{i}" for i in range(count)]
        seed_fn = variants_update["baseline_with_field"]
        for i in range(0, count, max(size, 100)):
            await seed_fn([doc(k, j, idkey=key_of) for j, k in enumerate(stored[i:i + max(size, 100)])])
        res_u: dict[str, list[float]] = {}
        res_i: dict[str, list[float]] = {}
        for _r in range(rounds):
            order = list(variants_update)
            rng.shuffle(order)
            for name in order:
                docs = [doc(k, j, idkey=key_of) for j, k in enumerate(stored)]
                res_u.setdefault(name, []).append(await rate(variants_update[name], [docs[i:i + size] for i in range(0, count, size)]))
            order = list(variants_insert)
            rng.shuffle(order)
            for name in order:
                fresh = [doc(f"bench-{uuid.uuid4().hex[:10]}", j, idkey=key_of) for j in range(count)]
                res_i.setdefault(name, []).append(await rate(variants_insert[name], [fresh[i:i + size] for i in range(0, count, size)]))
        out.append(report(store, "update shape", size, res_u, "baseline_with_field", "new", True))
        out.append(report(store, "update field", size, res_u, "baseline_no_field", "baseline_with_field", False))
        out.append(report(store, "insert shape", size, res_i, "baseline_with_field", "new", True))
        out.append(report(store, "insert field", size, res_i, "baseline_no_field", "baseline_with_field", False))
        if cleanup:
            await cleanup()          # the store a size ends with must not slow the next one (the rows of every round are left behind)
        if "control" in res_u:
            out.append(report(store, "update control", size, res_u, "baseline_with_field", "control", True))
            out.append(report(store, "insert control", size, res_i, "baseline_with_field", "control", True))
            # one AQL statement for the whole batch: reported, not judged (it amortises the per-statement cost; the per-record control is the judge)
            out.append(report(store, "update control_batch", size, res_u, "baseline_with_field", "control_batch", False))
            out.append(report(store, "insert control_batch", size, res_i, "baseline_with_field", "control_batch", False))
    return out


async def arango(a, rng):
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    cfg = AsyncMock()
    cfg.get_config = AsyncMock(return_value={"url": os.environ["ARANGO_IT_URL"], "username": "root",
                                             "password": os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass"), "db": "qa55_bench"})
    p = ArangoHTTPProvider(logging.getLogger("bench"), cfg)
    assert await p.connect()
    if not await p.http_client.has_collection(RECORDS):
        await p.ensure_schema()
    c = p.http_client
    control_aql = ("FOR d IN @docs UPSERT {_key: d._key} INSERT d UPDATE UNSET(d, ['createdAtTimestamp','incarnationId']) IN records")

    async def base(docs):
        await c.batch_insert_documents(RECORDS, docs, overwrite=True, overwrite_mode="update")

    async def base_nf(docs):
        await c.batch_insert_documents(RECORDS, [strip_id(d) for d in docs], overwrite=True, overwrite_mode="update")

    async def new_upd(docs):
        await p._upsert_record_base_docs(docs, None)

    async def new_ins(docs):
        await p._upsert_record_base_docs(docs, None, known_new={d["_key"] for d in docs})

    async def control(docs):
        # the rejected shape: one AQL UPSERT statement per record, as batch_upsert_records sends them
        for d in docs:
            await c.execute_aql(control_aql, {"docs": [d]})

    async def control_batch(docs):
        await c.execute_aql(control_aql, {"docs": docs})

    v = {"baseline_with_field": base, "baseline_no_field": base_nf, "new": new_upd, "control": control, "control_batch": control_batch}
    vi = {"baseline_with_field": base, "baseline_no_field": base_nf, "new": new_ins, "control": control, "control_batch": control_batch}
    async def clean() -> None:
        await c.execute_aql("FOR d IN records FILTER STARTS_WITH(d._key, 'bench-') REMOVE d IN records")

    out = await run_store("arangodb", v, vi, "_key", a.n, a.sizes, a.calls, a.rounds, rng, clean)
    await c.execute_aql("FOR d IN records FILTER STARTS_WITH(d._key, 'bench-') REMOVE d IN records")
    await p.disconnect()
    return out


async def neo4j(a, rng):
    from app.services.graph_db.neo4j import neo4j_provider as np

    os.environ.update(NEO4J_URI=os.environ["NEO4J_IT_URI"], NEO4J_USERNAME="neo4j",
                      NEO4J_PASSWORD=os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass"), NEO4J_DATABASE="neo4j")
    p = np.Neo4jProvider(logging.getLogger("bench"), MagicMock())
    assert await p.connect()
    # the store the product runs has its id constraint: without it every MERGE scans the label
    await p.client.execute_query("CREATE CONSTRAINT bench_record_id IF NOT EXISTS FOR (n:Record) REQUIRE n.id IS UNIQUE")
    q = p.client.execute_query
    kh = np._cypher_kh_node_state("n")
    old = f"UNWIND $nodes AS node MERGE (n:Record {{id: node.id}}) SET n += node {kh} RETURN n.id"
    new = (f"UNWIND $nodes AS row WITH row.rest AS rest, row.ins AS ins MERGE (n:Record {{id: rest.id}}){np._CYPHER_RECORD_INSERT_ONLY}"
           f" {kh} RETURN n.id")

    async def base(docs):
        await q(old, parameters={"nodes": docs})

    async def base_nf(docs):
        await q(old, parameters={"nodes": [strip_id(d) for d in docs]})

    async def newer(docs):
        await q(new, parameters={"nodes": [dict(zip(("rest", "ins"), np._record_insert_split(d))) for d in docs]})

    v = {"baseline_with_field": base, "baseline_no_field": base_nf, "new": newer}
    async def clean() -> None:
        while (await q("MATCH (n:Record) WHERE n.id STARTS WITH 'bench-' WITH n LIMIT 5000 DETACH DELETE n RETURN count(*) AS c"))[0]["c"]:
            pass

    out = await run_store("neo4j", v, v, "id", a.n, a.sizes, a.calls, a.rounds, rng, clean)
    await q("MATCH (n:Record) WHERE n.id STARTS WITH 'bench-' DETACH DELETE n")
    await p.disconnect()
    return out


async def postgres(a, rng):
    from app.core.db import DbConfig, KernelDB
    from app.core.org_context import OrgContext, bind
    from app.services.graph_db.postgres.provider import PostgresGraphProvider
    from app.services.graph_db.postgres.records import _upsert_docs, _upsert_record_docs

    db = KernelDB(DbConfig.from_env("connectors"))
    await db.start()
    p = PostgresGraphProvider(logging.getLogger("bench"), db)
    org = uuid.uuid4().hex[:24]

    def via(fn):
        async def call(docs):
            async def go(tx):
                await fn(tx, org, docs)
            with bind(OrgContext(org, None, "job", "job:bench", "t")):
                await p._q(go, None, write=True)
        return call

    base = via(lambda tx, o, docs: _upsert_docs(tx, o, RECORDS, docs))
    base_nf = via(lambda tx, o, docs: _upsert_docs(tx, o, RECORDS, [strip_id(d) for d in docs]))
    newer = via(_upsert_record_docs)
    v = {"baseline_with_field": base, "baseline_no_field": base_nf, "new": newer}
    out = await run_store("postgres", v, v, "id", a.n, a.sizes, a.calls, a.rounds, rng)
    await db.close()
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("store", choices=["arango", "neo4j", "postgres"])
    ap.add_argument("--n", type=int, default=150)
    ap.add_argument("--sizes", type=lambda v: [int(x) for x in v.split(",")], default=[1, 500, 2000])
    ap.add_argument("--calls", type=int, default=6)
    ap.add_argument("--rounds", type=int, default=8)
    ap.add_argument("--seed", type=int, default=55017)
    args = ap.parse_args()
    os.environ["RECORD_INCARNATION_WRITE"] = "true"
    rng = random.Random(args.seed)
    random.seed(args.seed)
    lines = asyncio.run({"arango": arango, "neo4j": neo4j, "postgres": postgres}[args.store](args, rng))
    bad = [r for r in lines if (r["phase"].endswith("shape") and not r["pass"]) or (r["phase"].endswith("control") and r["pass"])]
    for r in bad:
        print(json.dumps({"FAIL": r["phase"], "per_call": r["per_call"], "ratio": r["ratio"]}))
    sys.exit(1 if bad else 0)
