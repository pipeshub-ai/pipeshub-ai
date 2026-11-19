"""Load worlds into a vector store as points, run the scope projection job, and read the stamps back.

Used by the in-memory suite (`test_scope_projection_job.py`) and by the real-store contract tests, so every store is checked
by the same code: the stored fields equal the projector's, and the projector's equal the oracle's (`test_scope_projector.py`).
"""

from __future__ import annotations

import zlib
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from app.services.permissions.projector import ScopeProjector
from app.services.permissions.scope_fields import ScopeFields
from app.services.vector_db.collection_locator import StaticCollectionLocator
from app.services.vector_db.models import SparseVector, VectorPoint

from .stamp_world import ORG, WorldGraph

if TYPE_CHECKING:
    from app.services.vector_db.interface.vector_db import IVectorDBService

    from .model import World

DIM = 8


def point_uuid(name: str) -> str:
    import uuid

    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pipeshub-scope-it/{name}"))


@dataclass
class Loaded:
    graph: WorldGraph
    collection: str
    locator: StaticCollectionLocator
    #: prefixed vrid -> the point ids of its chunks
    points: dict[str, list[str]] = field(default_factory=dict)
    #: prefixed vrid -> {prefixed record id: prefixed app id} (live records)
    records: dict[str, dict[str, str]] = field(default_factory=dict)
    apps: list[str] = field(default_factory=list)


def vrids_for(world: World, share: bool | str) -> dict[str, str]:
    """A virtual record per record; with `share`, the first two live records of each app share one (deduplicated content). With
    ``"cross"`` the first live record of every app shares one virtual record with that of the others (the same content in two connectors)."""
    out: dict[str, str] = {}
    first: dict[str, str] = {}
    for n in world.nodes:
        if n.kind != "record" or not n.connector:
            continue
        if share is True and n.connector in first and not n.deleted and not n.placeholder:
            out[n.id] = out[first[n.connector]]
        else:
            out[n.id] = f"v-{n.id}"
            if not n.deleted and not n.placeholder:
                first.setdefault(n.connector, n.id)
    if share == "cross":
        apps = {n.id for n in world.nodes if n.kind == "app"}
        for connector, rid in first.items():
            if connector in apps:
                out[rid] = "v-cross"
    return out


async def load(vdb: IVectorDBService, collection: str, worlds: dict[str, World], *, share: bool | str = False,
               chunks: int = 1, sparse: bool = False, create: bool = True) -> Loaded:
    """Points for every live record of every world, ids namespaced `<name>:`, no scope fields yet. With `sparse` every point also
    carries the lexical term 1 (for the stores that search it)."""
    graph = WorldGraph()
    from tests.integration.vector_db.helpers import make_collection_config, make_dense

    if create:
        await vdb.create_collection(collection, make_collection_config())
    loaded = Loaded(graph, collection, StaticCollectionLocator([collection]))
    points: list[VectorPoint] = []
    for name, world in worlds.items():
        prefix = f"{name}:"
        vrids = vrids_for(world, share)
        graph.add(world, prefix, vrids)
        loaded.apps += [prefix + n.id for n in world.nodes if n.kind == "app"]
        for rid, v in vrids.items():
            node = world.node(rid)
            if node.deleted or node.placeholder:
                continue
            loaded.records.setdefault(prefix + v, {})[prefix + rid] = prefix + node.connector
        for v in sorted({prefix + v for r, v in vrids.items() if not world.node(r).deleted and not world.node(r).placeholder}):
            for c in range(chunks):
                pid = point_uuid(f"{v}#{c}")
                loaded.points.setdefault(v, []).append(pid)
                # membership is the union over every record the virtual record stands for (`resolve_virtual_record_state`), not the first's
                groups = list(dict.fromkeys(g for rid in loaded.records[v] for g in graph.stamps.node_map[rid].groups))
                points.append(VectorPoint(id=pid, sparse_vector=SparseVector([1], [1.0]) if sparse else None, dense_vector=make_dense([1.0, float(c), (zlib.crc32(v.encode()) % 997) / 997, (zlib.crc32(v.encode()[::-1]) % 991) / 991, 0, 0, 0, 0]), payload={
                    "page_content": f"{v} chunk {c}", "metadata": {"orgId": ORG, "virtualRecordId": v, "blockId": f"b{c}"},
                    "connectorIds": sorted(set(loaded.records[v].values())), "recordGroupIds": groups,
                    "rootRecordGroupIds": []}))
    for i in range(0, len(points), 200):
        await vdb.upsert_points(collection, points[i:i + 200])
    return loaded


async def expected(loaded: Loaded) -> dict[str, ScopeFields | None]:
    """What the projector says for every point (the job must write exactly this). A point whose records belong to no app (an
    unattached upload, R13) is never visited by a per-connector backfill: it stays unstamped, which reads as legacy."""
    projector = ScopeProjector(loaded.graph.stamps)
    got = await projector.project_virtual_records(ORG, loaded.records)
    attached = {v for v, recs in loaded.records.items() if any(a in loaded.apps for a in recs.values())}
    return {v: (f if v in attached else None) for v, (f, _) in got.items()}


async def read_stamps(vdb: IVectorDBService, loaded: Loaded) -> dict[str, ScopeFields | None]:
    """The scope fields each point carries now (None: unstamped); every chunk of a VRID must agree."""
    out: dict[str, ScopeFields | None] = {}
    for v, pids in loaded.points.items():
        seen = {repr(ScopeFields.from_payload(p.payload)) for p in await vdb.retrieve_points(loaded.collection, pids)}
        assert len(seen) == 1, (v, seen)
        out[v] = ScopeFields.from_payload((await vdb.retrieve_points(loaded.collection, pids[:1]))[0].payload)
    return out
