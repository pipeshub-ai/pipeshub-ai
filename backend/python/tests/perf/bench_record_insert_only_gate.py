# ruff: noqa: ANN202
"""Speed gate of the insert-only record write (G-55), on a real store. Not collected by pytest.

Two comparisons, interleaved A/B rounds, medians, one record per call (the shape ``batch_upsert_records`` sends):
  shape cost  the new write against the baseline that already carries ``incarnationId`` (the gate: ratio >= 0.90)
  field cost  that baseline against the write without the field (stated, not gated)
ArangoDB also runs the AQL UPSERT shape as a control, which must fail the gate.

    ARANGO_IT_URL=http://127.0.0.1:18529 python tests/perf/bench_record_insert_only_gate.py arango --n 200 --rounds 8
    NEO4J_IT_URI=bolt://127.0.0.1:17687 python tests/perf/bench_record_insert_only_gate.py neo4j
    PG_CORE_URL=... PG_POOLER=none python tests/perf/bench_record_insert_only_gate.py postgres

Prints one JSON line per phase: ratio of medians, docs/s of each side, host load average.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import statistics
import sys
import time
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from app.config.constants.arangodb import CollectionNames
from app.services.graph_db.common.utils import record_update_doc

RECORDS = CollectionNames.RECORDS.value
GATE = 0.90


def _doc(key: str, n: int) -> dict:
    return {"_key": key, "orgId": "bench", "recordName": f"r{n}", "externalRecordId": f"e-{key}", "recordType": "FILE", "version": 0,
            "origin": "CONNECTOR", "connectorName": "KB", "connectorId": "bench-conn", "createdAtTimestamp": 1000 + n,
            "updatedAtTimestamp": 2000 + n, "incarnationId": uuid.uuid4().hex}


async def _timed(coro_fns: list) -> float:
    start = time.perf_counter()
    for fn in coro_fns:
        await fn()
    return len(coro_fns) / (time.perf_counter() - start)


def _report(phase: str, store: str, a: list[float], b: list[float], a_name: str, b_name: str, gated: bool) -> bool:
    ratio = statistics.median(b) / statistics.median(a)
    ok = ratio >= GATE or not gated
    print(json.dumps({"store": store, "phase": phase, "vs": f"{b_name}/{a_name}", "ratio": round(ratio, 3), a_name: round(statistics.median(a)),
                      b_name: round(statistics.median(b)), "gate": GATE if gated else None, "pass": ok, "load": os.getloadavg()[0]}))
    return ok


async def arango(n: int, rounds: int) -> bool:
    from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider

    cfg = AsyncMock()
    cfg.get_config = AsyncMock(return_value={"url": os.environ.get("ARANGO_IT_URL", "http://localhost:18529"), "username": "root",
                                             "password": os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass"), "db": "record_insert_only_bench"})
    p = ArangoHTTPProvider(logging.getLogger("bench"), cfg)
    assert await p.connect()
    if not await p.http_client.has_collection(RECORDS):
        await p.ensure_schema()
    c = p.http_client
    upsert_aql = ("UPSERT {_key: @d._key} INSERT @d UPDATE UNSET(@d, ['createdAtTimestamp','incarnationId']) IN records")

    async def base(d):  # today's write, carrying the field
        await c.batch_insert_documents(RECORDS, [d], overwrite=True, overwrite_mode="update")

    async def new_existing(d):
        await p._upsert_record_base_docs([d], None)

    async def new_insert(d):
        await p._upsert_record_base_docs([d], None, known_new={d["_key"]})

    async def control(d):
        await c.execute_aql(upsert_aql, {"d": d})

    async def no_field(d):
        d = {k: v for k, v in d.items() if k != "incarnationId"}
        await c.batch_insert_documents(RECORDS, [d], overwrite=True, overwrite_mode="update")

    keys = [f"bench-{uuid.uuid4().hex[:8]}-{i}" for i in range(n)]
    for i, k in enumerate(keys):
        await base(_doc(k, i))
    ok = True
    res: dict[str, dict[str, list[float]]] = {}
    for r in range(rounds):
        for name, fn in (("baseline", base), ("new", new_existing), ("control", control), ("no_field", no_field)):
            res.setdefault("update", {}).setdefault(name, []).append(await _timed([lambda k=k, i=i, fn=fn: fn(_doc(k, i)) for i, k in enumerate(keys)]))
        fresh = {name: [f"bench-{uuid.uuid4().hex[:8]}-{r}-{i}" for i in range(n)] for name in ("baseline", "new", "control", "no_field")}
        for name, fn in (("baseline", base), ("new", new_insert), ("control", control), ("no_field", no_field)):
            res.setdefault("insert", {}).setdefault(name, []).append(await _timed([lambda k=k, i=i, fn=fn: fn(_doc(k, i)) for i, k in enumerate(fresh[name])]))
    for phase in ("update", "insert"):
        ok &= _report(phase + " shape", "arangodb", res[phase]["baseline"], res[phase]["new"], "baseline_with_field", "new", True)
        _report(phase + " field", "arangodb", res[phase]["no_field"], res[phase]["baseline"], "without_field", "with_field", False)
        if _report(phase + " control(UPSERT)", "arangodb", res[phase]["baseline"], res[phase]["control"], "baseline_with_field", "control", True):
            print(json.dumps({"control_should_fail": True, "phase": phase, "failed_to_fail": True}))
            ok = False
    await c.execute_aql("FOR d IN records FILTER STARTS_WITH(d._key, 'bench-') REMOVE d IN records")
    await p.disconnect()
    return ok


async def neo4j(n: int, rounds: int) -> bool:
    from app.services.graph_db.neo4j.neo4j_provider import (
        _CYPHER_RECORD_INSERT_ONLY,
        Neo4jProvider,
    )

    os.environ.update(NEO4J_URI=os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687"), NEO4J_USERNAME="neo4j",
                      NEO4J_PASSWORD=os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass"), NEO4J_DATABASE="neo4j")
    p = Neo4jProvider(logging.getLogger("bench"), MagicMock())
    assert await p.connect()
    q = p.client.execute_query
    old = "UNWIND $rows AS node MERGE (n:Record {id: node.id}) SET n += node RETURN n.id"
    new = f"UNWIND $rows AS row WITH row.full AS full, row.rest AS rest MERGE (n:Record {{id: full.id}}){_CYPHER_RECORD_INSERT_ONLY} RETURN n.id"

    def d(k, i):
        x = _doc(k, i)
        x["id"] = x.pop("_key")
        return x

    async def base(k, i):
        await q(old, parameters={"rows": [d(k, i)]})

    async def newer(k, i):
        full = d(k, i)
        await q(new, parameters={"rows": [{"full": full, "rest": record_update_doc(full)}]})

    async def no_field(k, i):
        x = d(k, i)
        x.pop("incarnationId")
        await q(old, parameters={"rows": [x]})

    keys = [f"bench-{uuid.uuid4().hex[:8]}-{i}" for i in range(n)]
    for i, k in enumerate(keys):
        await base(k, i)
    res: dict = {}
    for r in range(rounds):
        for name, fn in (("baseline", base), ("new", newer), ("no_field", no_field)):
            res.setdefault("update", {}).setdefault(name, []).append(await _timed([lambda k=k, i=i, fn=fn: fn(k, i) for i, k in enumerate(keys)]))
        for name, fn in (("baseline", base), ("new", newer), ("no_field", no_field)):
            fresh = [f"bench-{uuid.uuid4().hex[:8]}-{r}-{i}" for i in range(n)]
            res.setdefault("insert", {}).setdefault(name, []).append(await _timed([lambda k=k, i=i, fn=fn: fn(k, i) for i, k in enumerate(fresh)]))
    ok = True
    for phase in ("update", "insert"):
        ok &= _report(phase + " shape", "neo4j", res[phase]["baseline"], res[phase]["new"], "baseline_with_field", "new", True)
        _report(phase + " field", "neo4j", res[phase]["no_field"], res[phase]["baseline"], "without_field", "with_field", False)
    await q("MATCH (n:Record) WHERE n.id STARTS WITH 'bench-' DETACH DELETE n")
    await p.disconnect()
    return ok


async def postgres(n: int, rounds: int) -> bool:
    from app.core.db import DbConfig, KernelDB
    from app.core.org_context import OrgContext, bind
    from app.services.graph_db.postgres.provider import PostgresGraphProvider
    from app.services.graph_db.postgres.records import _upsert_docs, _upsert_record_docs

    db = KernelDB(DbConfig.from_env("connectors"))
    await db.start()
    p = PostgresGraphProvider(logging.getLogger("bench"), db)
    org = uuid.uuid4().hex[:24]

    def d(k, i):
        x = _doc(k, i)
        x["id"] = x.pop("_key")
        return x

    async def run(fn, k, i):
        async def go(tx):
            await fn(tx, org, [d(k, i)])
        with bind(OrgContext(org, None, "job", "job:bench", "t")):
            await p._q(go, None, write=True)

    async def base(k, i):
        await run(lambda tx, o, docs: _upsert_docs(tx, o, RECORDS, docs), k, i)

    async def newer(k, i):
        await run(_upsert_record_docs, k, i)

    async def no_field(k, i):
        async def f(tx, o, docs):
            await _upsert_docs(tx, o, RECORDS, [{kk: v for kk, v in docs[0].items() if kk != "incarnationId"}])
        await run(f, k, i)

    keys = [f"bench-{uuid.uuid4().hex[:8]}-{i}" for i in range(n)]
    for i, k in enumerate(keys):
        await base(k, i)
    res: dict = {}
    for r in range(rounds):
        for name, fn in (("baseline", base), ("new", newer), ("no_field", no_field)):
            res.setdefault("update", {}).setdefault(name, []).append(await _timed([lambda k=k, i=i, fn=fn: fn(k, i) for i, k in enumerate(keys)]))
        for name, fn in (("baseline", base), ("new", newer), ("no_field", no_field)):
            fresh = [f"bench-{uuid.uuid4().hex[:8]}-{r}-{i}" for i in range(n)]
            res.setdefault("insert", {}).setdefault(name, []).append(await _timed([lambda k=k, i=i, fn=fn: fn(k, i) for i, k in enumerate(fresh)]))
    ok = True
    for phase in ("update", "insert"):
        ok &= _report(phase + " shape", "postgres", res[phase]["baseline"], res[phase]["new"], "baseline_with_field", "new", True)
        _report(phase + " field", "postgres", res[phase]["no_field"], res[phase]["baseline"], "without_field", "with_field", False)
    await db.close()
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("store", choices=["arango", "neo4j", "postgres"])
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--rounds", type=int, default=8)
    a = ap.parse_args()
    sys.exit(0 if asyncio.run({"arango": arango, "neo4j": neo4j, "postgres": postgres}[a.store](a.n, a.rounds)) else 1)
