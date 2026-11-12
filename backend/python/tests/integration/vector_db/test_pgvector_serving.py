"""The deferring scope filter on pgvector rows: it equals the filter tree on every case of the shared world, is ANDed with the request's own arms, fails
closed on a group it decides but cannot read, and on the whole scenario corpus it neither admits what the oracle denies nor hides what it admits.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_serving.py -m integration
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from app.services.permissions.scope_fields import (
    ScopeFields,
    set_resolved_generation,
)
from app.services.vector_db import scope_expr as sx
from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    HybridSearchRequest,
    VectorPoint,
)
from app.services.vector_db.pgvector.config import PgVectorConfig
from app.services.vector_db.pgvector.pgvector import PgVectorService
from tests.integration.db.conftest import cluster, env  # noqa: F401  (fixtures)
from tests.integration.vector_db.scope_world import ADMIT_CASES, EVERYTHING, WORLD, f
from tests.integration.vector_db.test_pgvector_scope_filter import A, point, search
from tests.permissions.serving_corpus import SCENARIOS, build, judge

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
Q = [1.0, 0.0, 0.0, 0.0]


@pytest.fixture
async def store(env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
    svc.env = env
    await svc.upsert_points("records", [point(A, v, i) for i, v in enumerate(WORLD)])
    yield svc
    await svc.disconnect()


def reference(flt) -> set[str]:
    node = sx.admits(flt)
    return {v for v, (conn, groups, roots, stamp) in WORLD.items()
            if sx.evaluate(node, {"connectorIds": conn, "recordGroupIds": groups, "rootRecordGroupIds": roots, **(stamp or {})})}


@pytest.mark.parametrize("leg", ["dense", "lexical", "both"])
@pytest.mark.parametrize(("kw", "admitted"), ADMIT_CASES)
async def test_the_deferring_filter_returns_what_its_tree_admits(store, leg, kw, admitted) -> None:
    flt = f(**kw, defer=True)
    got = await search(store, flt, leg=leg)
    assert got == reference(flt)
    assert {"unstamped", "legacy", "unknown_stamp", "old_version", "old_generation", "unavailable_stamp", "no_connector"} <= got      # left to the arms


async def test_it_is_anded_with_the_request_arms_and_hides_what_a_stamp_refuses(store) -> None:
    flt = f(readable_groups=frozenset({"g1"}), defer=True)
    assert "container_other" not in await search(store, flt)                                              # decided by its stamp: g2 is not read
    assert await search(store, flt, should={"recordGroupIds": ["g1"]}) == {"container", "unstamped", "legacy", "unknown_stamp", "old_version", "old_generation",
                                                                              "new_generation", "unavailable_stamp", "other_connector", "mixed_connectors", "no_connector"}
    assert await search(store, flt, should={"recordGroupIds": []}) == set()
    assert await search(store, flt, vrids=["legacy"]) == {"legacy"}


async def test_a_decided_group_with_a_null_list_is_hidden_and_an_undecided_one_is_not(store) -> None:
    conn = await store.env.admin()
    try:
        await conn.execute("UPDATE retrieval.point_groups SET acl_scope = NULL, path_ids = NULL WHERE org_id = $1 AND group_key IN ('union', 'intersection')", A)
        await conn.execute("UPDATE retrieval.point_groups SET scope_stamp = NULL WHERE org_id = $1 AND group_key = 'override'", A)
    finally:
        await conn.close()
    assert await store.unreadable_group_rows(A) == 2          # the two with a NULL list and a stamp that decides; the third has no stamp left to decide
    got = await search(store, f(readable_scopes=EVERYTHING, readable_groups=EVERYTHING, defer=True))
    assert "union" not in got and "intersection" not in got and "override" in got


async def test_only_a_group_the_filter_cannot_decide_is_counted_as_unreadable(store) -> None:
    conn = await store.env.admin()
    try:
        await conn.execute("UPDATE retrieval.point_groups SET acl_scope = NULL WHERE org_id = $1 AND group_key = 'container'", A)
    finally:
        await conn.close()
    assert await store.unreadable_group_rows(A) == 0          # a container stamp reads no list that can be NULL
    conn = await store.env.admin()
    try:
        await conn.execute("UPDATE retrieval.point_groups SET acl_scope = NULL WHERE org_id = $1 AND group_key = 'override'", A)
    finally:
        await conn.close()
    assert await store.unreadable_group_rows(A) == 1          # an override stamp reads its acl_scope
    conn = await store.env.admin()
    try:
        await conn.execute("UPDATE retrieval.point_groups SET scope_version = scope_version - 1 WHERE org_id = $1 AND group_key = 'override'", A)
    finally:
        await conn.close()
    assert await store.unreadable_group_rows(A) == 0          # a stamp of another version is left to the request's arms: nothing is hidden


async def test_a_union_group_with_a_null_acl_scope_is_counted_but_still_admitted_on_its_path(store) -> None:
    conn = await store.env.admin()
    try:
        await conn.execute("UPDATE retrieval.point_groups SET acl_scope = NULL WHERE org_id = $1 AND group_key = 'union'", A)
    finally:
        await conn.close()
    assert await store.unreadable_group_rows(A) == 1          # a data fault all the same
    assert "union" in await search(store, f(readable_scopes=EVERYTHING, readable_groups=EVERYTHING, defer=True))      # its path still reaches


async def test_no_group_row_and_a_trust_nothing_filter_are_the_arms_alone(store) -> None:
    conn = await store.env.admin()
    try:
        await conn.execute("DELETE FROM retrieval.point_groups WHERE org_id = $1 AND group_key = 'container'", A)
    finally:
        await conn.close()
    assert "container" in await search(store, f(defer=True))
    assert await search(store, f(current=frozenset(), defer=True)) == set(WORLD)


@pytest.mark.timeout(1800)
async def test_the_corpus_has_no_over_admit_and_no_recall_loss_on_real_rows(store) -> None:
    pairs = decided = 0
    over, lost, unreached = [], [], []
    for scenario in SCENARIOS:
        corpus = await build(scenario)
        if not corpus.points:
            continue
        org = hashlib.sha1(scenario.id.encode()).hexdigest()[:24]
        await store.upsert_points("records", [
            VectorPoint(id=hashlib.md5(f"{scenario.id}/{rid}".encode()).hexdigest(), dense_vector=[1.0, i / 100, 0.0, 0.0],
                        payload={"page_content": rid, "metadata": {"orgId": org, "virtualRecordId": rid, "blockId": rid, "embeddingModel": "p:m:4"}, **payload})
            for i, (rid, payload) in enumerate(corpus.points.items())])
        admitted = {}
        for viewer, (scopes, _) in corpus.viewers.items():
            flt = corpus.scope_filter(scopes)
            req = HybridSearchRequest(dense_query=Q, filter=FilterExpression(must=[FieldCondition(key="metadata.orgId", value=org)]), limit=1000, scope_filter=flt)
            [hits] = await store.query_nearest_points("records", [req])
            admitted[viewer] = {h.payload["metadata"]["virtualRecordId"] for h in hits}
            node = sx.admits(flt)
            assert admitted[viewer] == {r for r, p in corpus.points.items() if sx.evaluate(node, p)}, (scenario.id, viewer)      # the rows equal the tree
        verdict = judge(corpus, admitted)
        pairs += len(corpus.points) * len(corpus.viewers)
        decided += sum(1 for r in corpus.points if not corpus.refused(r)) * len(corpus.viewers)
        over += [(scenario.id, *p) for p in verdict.over]
        lost += [(scenario.id, *p) for p in verdict.lost]
        unreached += [(scenario.id, *p) for p in verdict.unreached]
    print(f"corpus on pgvector: pairs={pairs} decided={decided} over_admit={len(over)} recall_loss={len(lost)} unreached={len(unreached)}")
    assert pairs > 3000 and over == [] and lost == [] and unreached == []


async def test_k_answers_come_back_when_most_of_the_nearest_points_are_restricted(store) -> None:
    """15 of the 20 nearest points are in a group the viewer cannot read. A request for k with the viewer's filter holds k readable points in rank
    order, in one query; the unchanged request fetches the same k and holds only the readable ones among them, which is the shortfall serving removes."""
    k, org = 10, hashlib.sha1(b"starvation").hexdigest()[:24]
    readable = {3, 7, 11, 15, 19} | set(range(20, 80))
    await store.upsert_points("records", [
        VectorPoint(id=hashlib.md5(f"starve/{i}".encode()).hexdigest(), dense_vector=[1.0, i / 100, 0.0, 0.0], payload={
            "page_content": f"c{i}", "metadata": {"orgId": org, "virtualRecordId": f"v{i}", "blockId": f"b{i}", "embeddingModel": "p:m:4"},
            "connectorIds": ["c1"], "recordGroupIds": ["open" if i in readable else "closed"], "rootRecordGroupIds": [], **ScopeFields.container().to_payload()})
        for i in range(80)])
    arms = [FieldCondition(key="connectorIds", values=["c1"])]

    async def nearest(flt) -> list[int]:
        req = HybridSearchRequest(dense_query=Q, filter=FilterExpression(must=[FieldCondition(key="metadata.orgId", value=org)], should=arms), limit=k, scope_filter=flt)
        [hits] = await store.query_nearest_points("records", [req])
        return [int(h.payload["metadata"]["virtualRecordId"][1:]) for h in hits]

    unchanged = await nearest(None)
    assert unchanged == list(range(k)) and len([i for i in unchanged if i in readable]) < k      # the premise: the nearest k are mostly restricted
    served = await nearest(f(current=frozenset({"c1"}), readable_groups=frozenset({"open"}), defer=True))
    assert served == sorted(readable)[:k]


@pytest.fixture(autouse=True)
def _generation() -> Iterator[None]:
    set_resolved_generation(3)
    yield
    set_resolved_generation(None)
