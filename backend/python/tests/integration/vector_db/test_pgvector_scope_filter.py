"""The scope filter on pgvector against real rows: each admit rule admits exactly what it should, and everything that cannot be trusted
(never stamped, legacy, another field version or generation, a connector that is not current, an empty or NULL list, an empty filter)
admits nothing. The filter is ANDed with the request's own arms and narrowing list, in the dense and the lexical leg, and never leaks across
organisations. Native and forced HNSW safe mode.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_scope_filter.py -m integration
"""
from __future__ import annotations

import dataclasses
import uuid
from typing import TYPE_CHECKING

import pytest

from app.services.vector_db.filters import ScopeFilter
from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    HybridSearchRequest,
    SparseVector,
    VectorPoint,
)
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]
A, B = "a" * 24, "b" * 24
Q = [1.0, 0.0, 0.0, 0.0]
VERSION, GENERATION = 5, 3
TERMS = (3, 7)


def _stamp(kind: str, path: list[str], acl: list[str], *, version: int = VERSION, generation: int = GENERATION) -> dict:
    return {"scopeStamp": kind, "pathIds": path, "aclScope": acl, "scopeFieldsVersion": version, "scopeGeneration": generation}


#: vrid -> (connectors, record groups, root groups, stamp or None)
WORLD: dict[str, tuple[list[str], list[str], list[str], dict | None]] = {
    "container": (["c1"], ["g1"], [], _stamp("container", [], [])),
    "container_other": (["c1"], ["g2"], ["r2"], _stamp("container", [], [])),
    "union": (["c1"], ["g2"], [], _stamp("union", ["s1", "p1"], ["own_u"])),
    "union_hidden": (["c1"], ["g2"], [], _stamp("union", ["s1", "x1"], ["own_h"])),
    "intersection": (["c1"], ["g2"], [], _stamp("intersection", ["s2"], ["own_i"])),
    "override": (["c1"], ["g2"], [], _stamp("override", [], ["own_o", "seed"])),
    "unstamped": (["c1"], ["g1"], [], None),
    "legacy": (["c1"], ["g1"], [], _stamp("legacy", ["s1"], ["own_l"])),
    "unknown_stamp": (["c1"], ["g1"], [], _stamp("something", ["s1"], ["own_x"])),
    "old_version": (["c1"], ["g1"], [], _stamp("container", [], [], version=VERSION - 1)),
    "old_generation": (["c1"], ["g1"], [], _stamp("container", [], [], generation=GENERATION - 1)),
    "new_generation": (["c1"], ["g1"], [], _stamp("container", [], [], generation=GENERATION + 1)),
    "other_connector": (["c9"], ["g1"], [], _stamp("container", [], [])),
    "mixed_connectors": (["c1", "c9"], ["g1"], [], _stamp("container", [], [])),
    "no_connector": ([], ["g1"], [], _stamp("container", [], [])),
    # stamped by a process that could not read the scope generation (-2): no reader may trust it, not even one in the same state
    "unavailable_stamp": (["c1"], ["g1"], [], _stamp("container", [], [], generation=-2)),
}
EVERYTHING = frozenset({"g1", "g2", "r2", "s1", "s2", "p1", "own_u", "own_h", "own_i", "own_o", "seed", "own_l", "own_x", "c1", "c9"})


def pid(org: str, name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"pgvector-scope/{org}/{name}"))


def point(org: str, vrid: str, i: int) -> VectorPoint:
    conn, groups, roots, stamp = WORLD[vrid]
    payload: dict = {"page_content": f"text {vrid}", "metadata": {"orgId": org, "virtualRecordId": vrid, "blockId": vrid, "embeddingModel": "p:m:4"},
                     "connectorIds": conn, "recordGroupIds": groups, "rootRecordGroupIds": roots, **(stamp or {})}
    return VectorPoint(id=pid(org, vrid), dense_vector=[1.0, i / 100, 0.0, 0.0], payload=payload, sparse_vector=SparseVector(list(TERMS), [1.0, 1.0]))


def f(**kw: object) -> ScopeFilter:
    base = {"current": frozenset({"c1"}), "version": VERSION, "generation": GENERATION}
    return ScopeFilter(**{**base, **kw})  # type: ignore[arg-type]


@pytest.fixture(params=[False, True], ids=["native", "safe"])
async def store(request, env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
    if request.param:
        svc.pg_capabilities = dataclasses.replace(svc.pg_capabilities, hnsw_safe_mode=True)
    svc.env = env
    await svc.upsert_points("records", [point(A, v, i) for i, v in enumerate(WORLD)])
    await svc.upsert_points("records", [point(B, v, i) for i, v in enumerate(WORLD)])         # another org with the same record ids, all open to it
    yield svc
    await svc.disconnect()


async def search(store: PgVectorService, scope_filter: ScopeFilter | None, *, org: str = A, leg: str = "dense", should: dict[str, list[str]] | None = None,
                 vrids: list[str] | None = None) -> set[str]:
    must = [FieldCondition(key="metadata.orgId", value=org)]
    if vrids is not None:
        must.append(FieldCondition(key="metadata.virtualRecordId", values=vrids))
    flt = FilterExpression(must=must, should=[FieldCondition(key=k, values=v) for k, v in (should or {}).items()])
    sparse = SparseVector(list(TERMS), [1.0, 1.0])
    req = HybridSearchRequest(dense_query=Q if leg in ("dense", "both") else None, sparse_query=sparse if leg in ("lexical", "both") else None,
                              filter=flt, limit=100, scope_filter=scope_filter)
    [hits] = await store.query_nearest_points("records", [req])
    return {h.payload["metadata"]["virtualRecordId"] for h in hits}


LEGS = pytest.mark.parametrize("leg", ["dense", "lexical", "both"])


async def test_without_a_filter_every_group_is_returned_as_before(store) -> None:
    assert await search(store, None) == set(WORLD) == await search(store, None, leg="lexical")


@LEGS
@pytest.mark.parametrize(("kw", "admitted"), [
    ({"readable_groups": frozenset({"g1"})}, {"container"}),
    ({"readable_groups": frozenset({"g2"})}, {"container_other"}),
    ({"readable_groups": frozenset({"r2"})}, {"container_other"}),
    ({"open_apps": frozenset({"c1"})}, {"container", "container_other"}),
    ({"open_apps": frozenset({"c9"})}, set()),
    ({"readable_scopes": frozenset({"p1"})}, {"union"}),
    ({"readable_scopes": frozenset({"s1"}), "excluded": frozenset({"x1"})}, {"union"}),
    ({"readable_scopes": frozenset({"s2"})}, {"intersection"}),
    ({"readable_scopes": frozenset({"own_u"})}, {"union"}),
    ({"readable_scopes": frozenset({"seed"})}, {"override"}),
    ({"readable_groups": frozenset({"g2"})}, {"container_other"}),
    ({}, set()),
], ids=['record_group', 'other_group', 'root_group', 'open_app', 'open_app_not_current', 'path', 'path_excluded', 'intersection', 'own_id', 'override_seed', 'group_not_union', 'nothing'])
async def test_each_admit_rule_admits_exactly_its_groups(store, leg, kw, admitted) -> None:
    assert await search(store, f(**kw), leg=leg) == admitted


@LEGS
async def test_what_cannot_be_trusted_is_never_admitted_even_when_the_viewer_reads_everything(store, leg) -> None:
    everything = f(current=frozenset({"c1"}), open_apps=frozenset({"c1", "c9"}), readable_groups=EVERYTHING, readable_scopes=EVERYTHING)
    got = await search(store, everything, leg=leg)
    for never in ("unstamped", "legacy", "unknown_stamp", "old_version", "old_generation", "new_generation", "other_connector",
                  "mixed_connectors", "no_connector", "unavailable_stamp"):
        assert never not in got, never
    assert {"container", "container_other", "union", "union_hidden", "intersection", "override"} == got


@LEGS
async def test_a_path_through_an_excluded_scope_is_hidden_whatever_else_the_viewer_reads(store, leg) -> None:
    got = await search(store, f(readable_scopes=EVERYTHING, excluded=frozenset({"x1"})), leg=leg)
    assert "union_hidden" in got          # its own id (aclScope) meets the reach: the rule the stamp carries
    got = await search(store, f(readable_scopes=frozenset({"s1"}), excluded=frozenset({"x1", "s1"})), leg=leg)
    assert got == set()                   # reach s1 only meets the path, and the path meets the excluded scope


@LEGS
@pytest.mark.parametrize(("kw", "admitted"), [
    ({"generation": -2}, set()), ({"generation": -1}, set()), ({"current": frozenset()}, set()),
    ({"version": VERSION - 1}, {"old_version"}), ({"generation": GENERATION + 1}, {"new_generation"}),
], ids=["unavailable", "unknown", "no current connector", "another version", "another generation"])
async def test_a_filter_trusts_only_the_stamps_of_the_version_and_generation_it_names(store, leg, kw, admitted) -> None:
    flt = f(open_apps=frozenset({"c1"}), readable_groups=EVERYTHING, readable_scopes=EVERYTHING, **kw)
    assert await search(store, flt, leg=leg) == admitted           # the other generation's stamps are as good as nobody's


@LEGS
async def test_a_null_list_of_a_stamped_group_fails_closed(store, leg) -> None:
    for column in ("path_ids", "acl_scope"):
        conn = await store.env.admin()
        try:
            await conn.execute(f"UPDATE retrieval.point_groups SET {column} = NULL WHERE org_id = $1 AND group_key IN ('union', 'union_hidden', 'override', 'intersection')", A)
        finally:
            await conn.close()
    got = await search(store, f(readable_scopes=EVERYTHING), leg=leg)
    assert got.isdisjoint({"union", "override", "intersection", "union_hidden"})
    for col in ("scope_stamp", "scope_version", "scope_generation"):
        conn = await store.env.admin()
        try:
            await conn.execute(f"UPDATE retrieval.point_groups SET {col} = NULL WHERE org_id = $1 AND group_key = 'container'", A)
        finally:
            await conn.close()
        assert "container" not in await search(store, f(readable_groups=EVERYTHING, open_apps=frozenset({"c1"})), leg=leg)


@LEGS
async def test_the_filter_is_anded_with_the_request_arms_and_narrowing_list_never_replacing_them(store, leg) -> None:
    flt = f(readable_groups=frozenset({"g1", "g2"}), open_apps=frozenset({"c1"}))
    assert await search(store, flt, leg=leg) == {"container", "container_other"}
    assert await search(store, flt, leg=leg, should={"recordGroupIds": ["g2"]}) == {"container_other"}      # the arm hides what the filter admits
    assert await search(store, flt, leg=leg, should={"recordGroupIds": ["zzz"]}) == set()                  # an arm that matches nothing matches nothing
    assert await search(store, flt, leg=leg, vrids=["container"]) == {"container"}
    assert await search(store, flt, leg=leg, vrids=["unstamped", "legacy"]) == set()                       # narrowing to what the filter excludes
    assert await search(store, flt, leg=leg, should={"recordGroupIds": []}) == set()                       # an empty arm list is still nothing


@LEGS
async def test_another_organisation_never_appears_under_a_filter(store, leg) -> None:
    everything = f(open_apps=frozenset({"c1"}), readable_groups=EVERYTHING, readable_scopes=EVERYTHING)
    mine, theirs = await search(store, everything, leg=leg), await search(store, everything, leg=leg, org=B)
    assert mine == theirs and mine
    conn = await store.env.admin()
    try:
        await conn.execute("DELETE FROM retrieval.point_groups WHERE org_id = $1", B)       # org B's groups are gone: org A's stamps are not B's
    finally:
        await conn.close()
    assert await search(store, everything, leg=leg, org=B) == set() and await search(store, everything, leg=leg) == mine
