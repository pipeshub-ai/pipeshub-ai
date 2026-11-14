"""Search throughput of the pgvector provider next to Qdrant on the same points, with the servers as the bottleneck.

Both stores are driven through ``IVectorDBService.query_nearest_points`` by several client processes (one event loop and one
service object each), so the Python client is not what limits the numbers. Give both servers the same cores (for example
``docker update --cpuset-cpus 0-3`` on each container) and the clients other cores (``--client-cores``). Server CPU per search
is read from a container's cgroup (``/sys/fs/cgroup/.../cpu.stat``) or a process id and its children (PgBouncer, a local
postmaster): that number, not QPS, is the capacity of a store, because a store that does not use all its cores at a
client count is held back by latency, not by CPU.

    # once: write the corpus into both stores (the pgvector side through the indexing login, with the placement worker
    #   python -m db.maintain placement --watch   running, as for the indexing service), then on the database
    #   python -m db.maintain build-indexes && python -m db.maintain fold   and   VACUUM (ANALYZE)
    python tests/perf/bench_pgvector_search.py load --corpus corpus.npz --org <org id> \\
        --pg-dsn postgresql://pipeshub_indexing_login@127.0.0.1:6432/pipeshub --qdrant 127.0.0.1:6333
    # measure (repeat with --store qdrant; interleave the two)
    python tests/perf/bench_pgvector_search.py run --store pgvector --corpus corpus.npz --org <org id> \\
        --pg-dsn postgresql://pipeshub_query_login@127.0.0.1:6432/pipeshub --clients 16 --mode hybrid \\
        --cpu pg=/sys/fs/cgroup/system.slice/docker-<id>.scope/cpu.stat --cpu bouncer=<pgbouncer pid> --client-cores 8-31

``corpus.npz`` (any embedding and sparse model, the same arrays for both stores): ``dense`` (N x d float32), the documents' sparse
vectors as CSR ``sparse_ptr`` / ``sparse_idx`` / ``sparse_val``, and the queries as ``q_dense`` and ``q_ptr`` / ``q_idx`` / ``q_val``.
Document ``i`` belongs to record ``i // --chunks`` and connector ``c<record % 4>``. ``--filter member`` reads connectors c0 and c1
and the record groups of the others (every record, through the membership arms). Prints one JSON line. Not collected by pytest.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import multiprocessing as mp
import os
import sys
import time
import uuid

import numpy as np

COLLECTION = {"pgvector": "records", "qdrant": "bench"}


def _corpus(path: str) -> dict[str, np.ndarray]:
    with np.load(path) as z:
        return {k: z[k] for k in z.files}


def _sparse(ptr: np.ndarray, idx: np.ndarray, val: np.ndarray, i: int) -> tuple[list[int], list[float]]:
    lo, hi = int(ptr[i]), int(ptr[i + 1])
    return idx[lo:hi].astype(int).tolist(), val[lo:hi].astype(float).tolist()


def _filter(org: str, kind: str):  # noqa: ANN202
    from app.services.vector_db.models import FieldCondition, FilterExpression

    must = [FieldCondition(key="metadata.orgId", value=org)]
    if kind == "org":
        return FilterExpression(must=must)
    groups = [f"g{j}" for j in range(40) if j % 4 in (2, 3)]
    return FilterExpression(must=must, should=[FieldCondition(key="connectorIds", values=["c0", "c1"]),
                                               FieldCondition(key="recordGroupIds", values=groups)])


async def _service(args: argparse.Namespace, store: str):  # noqa: ANN202
    if store == "pgvector":
        from app.services.vector_db.pgvector.config import PgVectorConfig
        from app.services.vector_db.pgvector.pgvector import PgVectorService

        return await PgVectorService.create(PgVectorConfig.from_dict({"dsn": args.pg_dsn, "pool_max": args.pool, "search": {"sampler_rate": 0}}))
    from app.services.vector_db.qdrant.config import QdrantConfig
    from app.services.vector_db.qdrant.qdrant import QdrantService

    host, port = args.qdrant.rsplit(":", 1)
    svc = QdrantService(QdrantConfig.from_dict({"host": host, "port": int(port), "grpc_port": int(port) + 1, "prefer_grpc": True}))
    await svc.connect()
    return svc


async def _load(args: argparse.Namespace) -> dict:
    from app.services.vector_db.const.const import PAYLOAD_KEYWORD_INDEXES
    from app.services.vector_db.models import (
        CollectionConfig,
        SparseVector,
        VectorPoint,
    )

    c = _corpus(args.corpus)
    n = len(c["dense"])

    def point(i: int) -> VectorPoint:
        rec = i // args.chunks
        return VectorPoint(id=str(uuid.uuid5(uuid.NAMESPACE_URL, f"bench/{i}")), dense_vector=c["dense"][i].tolist(),
                           sparse_vector=SparseVector(*_sparse(c["sparse_ptr"], c["sparse_idx"], c["sparse_val"], i)),
                           payload={"page_content": f"doc {i}", "metadata": {"orgId": args.org, "virtualRecordId": f"r{rec}", "blockId": f"b{i}"},
                                    "connectorIds": [f"c{rec % 4}"], "recordGroupIds": [f"g{rec % 40}"]})
    out = {}
    for store in ("qdrant", "pgvector"):
        svc = await _service(args, store)
        if store == "qdrant":
            await svc.create_collection(COLLECTION[store], CollectionConfig(embedding_size=c["dense"].shape[1]))
            for field, schema in PAYLOAD_KEYWORD_INDEXES:
                await svc.create_index(COLLECTION[store], field, schema)
        t0 = time.perf_counter()
        for s in range(0, n, 1000):
            await svc.upsert_points(COLLECTION[store], [point(i) for i in range(s, min(n, s + 1000))])
        out[f"{store}_load_s"] = round(time.perf_counter() - t0, 1)
        await svc.disconnect()
    return {"points": n, **out}


def _cpu_seconds(source: str) -> float:
    """A cgroup ``cpu.stat`` file (every process in the container), or a process id with its children."""
    if not source.isdigit():
        with open(source) as f:
            return next(int(line.split()[1]) for line in f if line.startswith("usage_usec ")) / 1e6
    pids = [int(source)]
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                with open(f"/proc/{d}/stat") as f:
                    if int(f.read().rsplit(")", 1)[1].split()[1]) == int(source):
                        pids.append(int(d))
            except OSError:
                continue
    total = 0.0
    for pid in pids:
        try:
            with open(f"/proc/{pid}/stat") as f:
                fields = f.read().rsplit(")", 1)[1].split()
            total += sum(int(x) for x in fields[11:15]) / os.sysconf("SC_CLK_TCK")
        except OSError:
            continue
    return total


def _client(args: argparse.Namespace, idx: int, ready, go, out) -> None:
    if args.client_cores:
        lo, hi = (int(x) for x in args.client_cores.split("-"))
        os.sched_setaffinity(0, range(lo, hi + 1))
    asyncio.run(_client_main(args, idx, ready, go, out))


async def _client_main(args: argparse.Namespace, idx: int, ready, go, out) -> None:
    from app.services.vector_db.models import HybridSearchRequest, SparseVector

    c = _corpus(args.corpus)
    svc = await _service(args, args.store)
    flt = _filter(args.org, args.filter)
    nq = len(c["q_dense"])
    stats = {"degraded": 0, "errors": 0}

    async def one(qi: int) -> None:
        qi %= nq
        sparse = SparseVector(*_sparse(c["q_ptr"], c["q_idx"], c["q_val"], qi)) if args.mode != "dense" else None
        dense = c["q_dense"][qi].tolist() if args.mode != "sparse" else None
        req = HybridSearchRequest(dense_query=dense, sparse_query=sparse, filter=flt, limit=args.limit)
        res = (await svc.query_nearest_points(COLLECTION[args.store], [req]))[0]
        if not res or any(getattr(r, "degraded", None) for r in res):        # an empty answer is a failure to answer too
            stats["degraded"] += 1

    async def run(qids: list[int], lat: list[float]) -> None:
        it = iter(qids)

        async def worker() -> None:
            for qi in it:
                t = time.perf_counter()
                try:
                    await one(qi)
                except Exception:
                    stats["errors"] += 1        # counted and reported; one failed search does not end the run
                lat.append((time.perf_counter() - t) * 1000)
        await asyncio.gather(*[worker() for _ in range(max(1, args.clients // args.procs))])

    await run(list(range(idx * 7, idx * 7 + 64)), [])          # warm-up: connections, caches
    stats.update(degraded=0, errors=0)
    ready.wait()
    go.wait()
    lat: list[float] = []
    t0 = time.perf_counter()
    await run([idx * 101 + i * args.procs + 13 for i in range(max(1, args.queries // args.procs))], lat)
    out.put({"lat": lat, "t0": t0, "t1": time.perf_counter(), **stats})
    await svc.disconnect()


def _run(args: argparse.Namespace) -> dict:
    args.procs = min(args.procs, args.clients)
    ctx = mp.get_context("spawn")
    ready, go, out = ctx.Barrier(args.procs + 1), ctx.Barrier(args.procs + 1), ctx.Queue()
    procs = [ctx.Process(target=_client, args=(args, i, ready, go, out)) for i in range(args.procs)]
    for p in procs:
        p.start()
    cpu = dict(s.split("=", 1) for s in args.cpu)
    ready.wait()
    before = {k: _cpu_seconds(v) for k, v in cpu.items()}
    go.wait()
    res = [out.get() for _ in procs]
    after = {k: _cpu_seconds(v) for k, v in cpu.items()}
    for p in procs:
        p.join(30)
    lat = np.array([x for r in res for x in r["lat"]])
    wall = max(r["t1"] for r in res) - min(r["t0"] for r in res)
    n = len(lat)
    ms = {k: round((after[k] - before[k]) / n * 1000, 2) for k in cpu}
    return {"store": args.store, "mode": args.mode, "filter": args.filter, "clients": args.clients, "searches": n,
            "qps": round(n / wall, 1), "p50_ms": round(float(np.percentile(lat, 50)), 1), "p95_ms": round(float(np.percentile(lat, 95)), 1),
            "degraded": sum(r["degraded"] for r in res), "errors": sum(r["errors"] for r in res),
            "server_cpu_ms_per_search": ms, "server_cpu_ms_total": round(sum(ms.values()), 2)}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("action", choices=["load", "run"])
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--org", required=True, help="the organisation id the points are written under")
    parser.add_argument("--pg-dsn", default=os.getenv("PGVECTOR_DSN", ""))
    parser.add_argument("--qdrant", default="127.0.0.1:6333", help="host:http-port; gRPC is the next port")
    parser.add_argument("--store", choices=["pgvector", "qdrant"], default="pgvector")
    parser.add_argument("--mode", choices=["hybrid", "dense", "sparse"], default="hybrid", help="both legs, or one of them")
    parser.add_argument("--filter", choices=["org", "member"], default="org")
    parser.add_argument("--chunks", type=int, default=1, help="documents per record")
    parser.add_argument("--clients", type=int, default=16, help="concurrent searches")
    parser.add_argument("--procs", type=int, default=4, help="client processes")
    parser.add_argument("--queries", type=int, default=2000)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--pool", type=int, default=12, help="pgvector pool size per client process")
    parser.add_argument("--cpu", action="append", default=[], help="name=<cgroup cpu.stat path or pid>, repeatable")
    parser.add_argument("--client-cores", default="", help="e.g. 8-31: the cores the client processes may use")
    args = parser.parse_args()
    if (args.store == "pgvector" or args.action == "load") and not args.pg_dsn:
        sys.exit("--pg-dsn (or PGVECTOR_DSN) is required")
    result = asyncio.run(_load(args)) if args.action == "load" else _run(args)
    print(json.dumps(result), flush=True)


if __name__ == "__main__":
    main()
