"""A search through `RetrievalService` on a real pgvector store with the serving planner on: the answer is the unchanged request's, whether the
connector is served or not, the filter is carried only where a connector is served now, a viewer who just gained access sees the record at once,
and serving alone never makes a search report `searchDegraded`.

Run: KERNEL_TEST_PG_BINS=<pg bin> KERNEL_TEST_PGBOUNCER=<pgbouncer> pytest tests/integration/vector_db/test_pgvector_serving_search.py -m integration
"""
from __future__ import annotations

import hashlib
import logging
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import pytest

import app.modules.retrieval.retrieval_service as mod
from app.services.cache import invalidation_hooks as hooks
from app.services.graph_db.interface.graph_db_provider import (
    AccessCheck,
    AccessibleContainers,
)
from app.services.permissions.cache import ViewerScopeCache
from app.services.permissions.currency import StampCurrency
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    ScopeFields,
    set_resolved_generation,
)
from app.services.permissions.scopes import ViewerScopes
from app.services.permissions.service import ViewerScopeService
from app.services.permissions.serving import ServingPlanner
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
from tests.integration.vector_db.scope_world import GENERATION, VERSION, WORLD
from tests.integration.vector_db.test_pgvector_scope_filter import A, point

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(900)]
G2 = {v for v, (_, groups, _r, stamp) in WORLD.items() if "g2" in groups and (stamp or {}).get("scopeStamp") == "container"}


def doc(**over: object) -> dict:
    return {"scopeBackfillState": "done", "scopeBackfillVersion": SCOPE_FIELDS_VERSION, "scopeBackfillGeneration": GENERATION,
            "scopeBackfillStructureGeneration": 5, "scopeProjectionGeneration": 5, **over}


class Viewer:
    """The viewer's state in the graph: what the request's arms enumerate and the access check admits, and what the resolver computes scopes from."""

    def __init__(self) -> None:
        self.groups = {"g1"}

    async def resolve(self, org_id: str, user_key: str, *, version: int = -1) -> ViewerScopes:
        return ViewerScopes(org_id, user_key, gated_apps=frozenset({"c1", "c9"}), readable_groups=frozenset(self.groups), version=version)

    def admitted(self) -> set[str]:
        # a record with a path stamp (union, intersection, override) is read through its path, which no group of the viewer is on
        return {v for v, (_, groups, _r, stamp) in WORLD.items() if set(groups) & self.groups and (stamp or {}).get("scopeStamp") not in ("union", "intersection", "override")}


@pytest.fixture(autouse=True)
def _generation() -> Iterator[None]:
    set_resolved_generation(GENERATION)
    yield
    set_resolved_generation(None)
    hooks.reset_viewer_scope_version()


@pytest.fixture
async def store(env) -> AsyncIterator[PgVectorService]:  # noqa: F811
    port = env.start_bouncer()
    svc = await PgVectorService.create(PgVectorConfig(dsn=env.dsn("indexing", port), jobs_dsn=env.dsn("jobs", port), pool_max=4, placement="none"))
    await svc.upsert_points("records", [point(A, v, i) for i, v in enumerate(WORLD)])
    yield svc
    await svc.disconnect()


class Rig:
    def __init__(self, store: PgVectorService, viewer: Viewer, docs: dict[str, dict] | None) -> None:
        self.store, self.viewer, self.docs = store, viewer, docs
        self.sent: list[ScopeFilter | None] = []
        self.limits: list[int] = []
        self.asked: list[set[str]] = []
        self.degraded: list[bool] = []
        client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
        self.cache = ViewerScopeCache(lambda: client)
        self.scopes = ViewerScopeService(viewer, self.cache)         # type: ignore[arg-type]
        hooks.init_viewer_scope_version(self.cache)
        graph = AsyncMock()
        graph.get_user_by_user_id = AsyncMock(return_value={"_key": "user-key", "email": "e@x"})
        graph.get_accessible_containers = AsyncMock(side_effect=lambda *a, **k: AccessibleContainers(record_group_ids_verify=frozenset(self.viewer.groups | {"g2"})))

        def check(_uk: str, _org: str, *, virtual_record_ids: object = (), **_k: object) -> AccessCheck:
            self.asked.append(set(virtual_record_ids))             # type: ignore[call-overload]
            return AccessCheck(records_by_vrid={v: f"r-{v}" for v in virtual_record_ids if v in self.viewer.admitted()})   # type: ignore[attr-defined]

        graph.check_access = AsyncMock(side_effect=check)
        graph.get_records_by_record_ids = AsyncMock(side_effect=lambda ids, *a, **k: [
            {"_key": i, "virtualRecordId": i[2:], "origin": "CONNECTOR", "recordName": "n", "mimeType": "text/plain", "connectorName": "X", "webUrl": "http://x"}
            for i in ids])
        graph.get_accessible_virtual_record_ids = AsyncMock(side_effect=lambda *a, **k: {v: f"r-{v}" for v in self.viewer.admitted()})
        graph.get_nodes_by_field_in = AsyncMock(side_effect=lambda coll, field, ids, **k: [{"_key": i, **self.docs[i]} for i in ids if i in (self.docs or {})])
        self.graph = graph
        registry = MagicMock(strategy_name="single")
        registry.resolve_for_query = AsyncMock(return_value=["records"])
        cfg = AsyncMock()
        cfg.get_config = AsyncMock(return_value={})
        with patch("app.modules.retrieval.retrieval_service.SparseEmbedder"):
            self.svc = mod.RetrievalService(logger=logging.getLogger("serving-e2e"), config_service=cfg, collection_registry=registry,
                                            vector_db_service=store, graph_provider=graph, blob_store=MagicMock())
        emb = MagicMock()
        emb.aembed_query = AsyncMock(return_value=[1.0, 0.0, 0.0, 0.0])
        self.svc.get_embedding_model_instance = AsyncMock(return_value=emb)       # type: ignore[method-assign]
        sparse = MagicMock()
        sparse.embed_query = AsyncMock(return_value=SparseVector([3, 7], [1.0, 1.0]))
        self.svc._sparse_embedder = sparse
        if docs is not None:
            self.svc._planner = ServingPlanner(self.scopes, StampCurrency(graph))
        orig = store.query_nearest_points

        async def spy(collection_name: str, requests: list[HybridSearchRequest], **k: object) -> list:
            self.sent.extend(r.scope_filter for r in requests)
            self.limits.extend(r.limit for r in requests)
            out = await orig(collection_name=collection_name, requests=requests, **k)
            self.degraded.extend(bool(h.degraded) for hits in out for h in hits)
            return out

        store.query_nearest_points = spy       # type: ignore[method-assign]

    async def search(self) -> set[str]:
        mod._user_cache.clear()
        self.sent.clear(), self.asked.clear()
        out = await self.svc.search_with_filters(queries=["q"], user_id="u1", org_id=A, limit=100)
        return {r["metadata"]["virtualRecordId"] for r in out["searchResults"]}

    async def warm(self) -> None:
        await self.scopes.resolve_now(A, "user-key")


async def test_a_served_and_a_not_served_connector_return_what_the_unchanged_request_returns(store) -> None:
    legacy_rig = Rig(store, Viewer(), None)
    legacy = await legacy_rig.search()
    assert legacy == Viewer().admitted() and legacy_rig.sent and all(s is None for s in legacy_rig.sent)
    for docs, carried in (
        ({"c1": doc(), "c9": doc()}, True),
        ({"c1": doc(scopeProjectionPendingRoots='{"r":5}'), "c9": doc(scopeBackfillState="failed")}, False),
        ({"c1": doc()}, True),                                  # c9 has no document: not served, c1 still is
    ):
        rig = Rig(store, Viewer(), docs)
        await rig.warm()
        assert await rig.search() == legacy
        assert rig.sent and all((s is not None and s.defer) is carried for s in rig.sent)
        if carried:
            assert len(rig.asked[0]) < len(legacy_rig.asked[0])       # the stamps hid g2 points in the store: fewer reach the access check


async def test_a_viewer_who_just_gained_access_sees_the_record_at_once(store) -> None:
    viewer = Viewer()
    rig = Rig(store, viewer, {"c1": doc(), "c9": doc()})
    await rig.warm()
    before = await rig.search()
    assert before == viewer.admitted() and not (G2 & before) and all(s is not None for s in rig.sent)       # served, and g2 hidden by its stamps
    viewer.groups.add("g2")                                   # the grant commits, then its writer moves the version
    await hooks.notify_principals_changed(A)
    assert await rig.search() == viewer.admitted() and G2 <= viewer.admitted()      # the entry of the old version is not read: the unchanged request answers
    assert all(s is None for s in rig.sent)
    await rig.scopes.wait_for_fills()
    after = await rig.search()
    assert after == viewer.admitted() and all(s is not None and "g2" in s.readable_groups for s in rig.sent)       # served again, from the new scopes


async def test_a_connector_with_a_sync_running_is_read_with_the_unchanged_request(store) -> None:
    """A running sync moves the version only when it ends: a grant it wrote is not in the cached scopes, so its connector is not served."""
    viewer = Viewer()
    docs = {"c1": doc(), "c9": doc()}
    rig = Rig(store, viewer, docs)
    await rig.warm()
    viewer.groups.add("g2")                                   # written by the sync, no version move yet
    docs["c1"]["status"] = "SYNCING"
    docs["c9"]["status"] = "FULL_SYNCING"
    assert await rig.search() == viewer.admitted() and all(s is None for s in rig.sent)
    docs["c1"]["status"] = docs["c9"]["status"] = "IDLE"
    assert all(s is not None for s in (await rig.search(), rig.sent)[1])


async def test_the_record_id_path_does_not_carry_the_filter(store) -> None:
    rig = Rig(store, Viewer(), {"c1": doc(), "c9": doc()})
    await rig.warm()
    plan = mod._QueryPlan()
    plan.scope_filter = ScopeFilter(current=frozenset({"c1"}), readable_groups=frozenset({"g1"}), version=VERSION, generation=GENERATION, defer=True)
    out = await rig.svc._search_by_record_ids(["q"], 100, A, "u1", "user-key", {}, None, None, None, [], plan)
    assert rig.sent and all(s is None for s in rig.sent) and plan.scope_filter is None
    assert {r["metadata"]["virtualRecordId"] for r in out.results} <= Viewer().admitted()


async def test_a_filter_that_hides_many_rows_does_not_make_the_lexical_leg_report_degraded(store) -> None:
    """More rows match the query than the leg can ask for, and the stamps hide nearly all of them: the unchanged request reports nothing for the rows
    the access check will drop, and the served one must not report them as a degraded search."""
    hidden = [VectorPoint(id=f"00000000-0000-0000-0000-{i:012d}", dense_vector=[1.0, 0.5, 0.0, 0.0], sparse_vector=SparseVector([3, 7], [1.0, 1.0]),
                          payload={"page_content": "x", "metadata": {"orgId": A, "virtualRecordId": f"h{i}", "blockId": f"h{i}", "embeddingModel": "p:m:4"},
                                   "connectorIds": ["c1"], "recordGroupIds": ["g2"], "rootRecordGroupIds": [], "scopeStamp": "container", "pathIds": [],
                                   "aclScope": [], "scopeFieldsVersion": SCOPE_FIELDS_VERSION, "scopeGeneration": GENERATION}) for i in range(1100)]
    for s in range(0, len(hidden), 500):
        await store.upsert_points("records", hidden[s:s + 500])
    flt = ScopeFilter(current=frozenset({"c1"}), readable_groups=frozenset({"g1"}), version=SCOPE_FIELDS_VERSION, generation=GENERATION, defer=True)
    arms = FilterExpression(must=[FieldCondition(key="metadata.orgId", value=A)], should=[FieldCondition(key="recordGroupIds", values=["g1", "g2"])])
    for scope in (None, flt):
        req = HybridSearchRequest(sparse_query=SparseVector([3, 7], [1.0, 1.0]), filter=arms, limit=10, scope_filter=scope)
        [hits] = await store.query_nearest_points("records", [req])
        assert hits and not any(h.degraded for h in hits), scope


class StarvedViewer(Viewer):
    """The viewer reads group ``s-open`` only; 15 of the 20 nearest points of the starved world are in ``g2``, which the request's arms name and the
    access check refuses."""

    def __init__(self, readable: set[str]) -> None:
        super().__init__()
        self.groups, self.readable = {"s-open"}, readable

    def admitted(self) -> set[str]:
        return {f"s{i}" for i in self.readable}


async def test_the_first_fetch_is_the_limit_when_every_connector_is_exact_and_no_search_starves_when_one_is_not(store) -> None:
    from app.services.vector_db.scope_backfill import BACKFILL_KEYS

    k, org = 10, hashlib.sha1(b"starved-search").hexdigest()[:24]
    readable = {3, 7, 11, 15, 19} | set(range(20, 80))
    await store.upsert_points("records", [
        VectorPoint(id=hashlib.md5(f"starved/{i}".encode()).hexdigest(), dense_vector=[1.0, i / 10000, 0.0, 0.0], payload={
            "page_content": f"c{i}", "metadata": {"orgId": org, "virtualRecordId": f"s{i}", "blockId": f"b{i}", "embeddingModel": "p:m:4"},
            "connectorIds": ["c1"], "recordGroupIds": ["s-open" if i in readable else "g2"], "rootRecordGroupIds": [],
            **ScopeFields.container().to_payload()}) for i in range(80)])
    exact = {BACKFILL_KEYS["exact"]: True}
    for docs, first_fetch in (({"c1": doc(**exact), "c9": doc(**exact)}, k),         # every gated connector served and exact: exactly the limit
                              ({"c1": doc(**exact), "c9": doc()}, None),             # one is not exact: today's over-fetch
                              (None, None)):                                         # the default mode: today's over-fetch
        rig = Rig(store, StarvedViewer(readable), docs)
        await rig.scopes.resolve_now(org, "user-key")
        out = await rig.svc.search_with_filters(queries=["q"], user_id="u1", org_id=org, limit=k)
        assert [r["metadata"]["virtualRecordId"] for r in out["searchResults"]] == [f"s{i}" for i in sorted(readable)[:k]], docs      # k answers, in rank order
        if first_fetch is not None:
            assert set(rig.limits) == {first_fetch} and len(rig.asked[0]) == k and all(s is not None for s in rig.sent)        # one fetch of k, every hit was readable
        else:
            assert max(rig.limits) > k and len(rig.asked[0]) > k
