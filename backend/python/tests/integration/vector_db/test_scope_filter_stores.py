"""The scope filter on Qdrant, OpenSearch and Redis against a real server: for the same world of groups and the same viewers as the pgvector suite
(``scope_world``), each store admits exactly the groups pgvector admits, on the dense leg, the lexical leg and both. Everything that cannot be
trusted admits nothing, the filter is ANDed with the request's own filter, and another organisation never appears.

Requires: docker compose -f tests/integration/compose/vector-db.yml up -d
"""

import uuid

import pytest

from app.services.vector_db.const.const import (
    PAYLOAD_KEYWORD_INDEXES,
    SCOPE_INTEGER_INDEXES,
    SCOPE_KEYWORD_INDEXES,
)
from app.services.vector_db.models import (
    FieldCondition,
    FilterExpression,
    HybridSearchRequest,
    SparseVector,
    VectorPoint,
)
from app.services.vector_db.filters import ScopeFilter
from tests.integration.vector_db.conftest import make_collection
from tests.integration.vector_db.helpers import (
    make_collection_config,
    make_dense,
    wait_for,
)
from tests.integration.vector_db.scope_world import (
    ADMIT_CASES,
    EVERYTHING,
    TERMS,
    GENERATION,
    TRUST_CASES,
    VERSION,
    WORLD,
    f,
)

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="module")]

A, B = "a" * 24, "b" * 24
WORD = "scopeword"
LEGS = ("dense", "lexical", "both")


def _pid(org: str, name: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"scope-filter-stores/{org}/{name}"))


def _point(org: str, vrid: str, i: int) -> VectorPoint:
    conn, groups, roots, stamp = WORLD[vrid]
    payload = {"page_content": f"{WORD} {vrid}", "metadata": {"orgId": org, "virtualRecordId": vrid, "blockId": vrid},
               "connectorIds": conn, "recordGroupIds": groups, "rootRecordGroupIds": roots, **(stamp or {})}
    return VectorPoint(id=_pid(org, vrid), dense_vector=make_dense([1.0, i / 100]), payload=payload,
                       sparse_vector=SparseVector(list(TERMS), [1.0, 1.0]))


class _ScopeFilterContract:
    @pytest.fixture
    async def loaded(self, vector_service, monkeypatch):  # noqa: ANN202
        monkeypatch.setenv("ACL_SCOPE_FIELDS", "true")                 # indexes and mappings of the scope fields are created while the fields are on
        svc, col = vector_service, make_collection(vector_service.get_service_name())
        await svc.create_collection(col, make_collection_config())
        for field, schema in (*PAYLOAD_KEYWORD_INDEXES, *SCOPE_KEYWORD_INDEXES, *SCOPE_INTEGER_INDEXES):
            await svc.create_index(col, field, schema)
        points = [_point(org, v, i) for org in (A, B) for i, v in enumerate(WORLD)]
        if "refresh" in svc.upsert_points.__code__.co_varnames:
            await svc.upsert_points(col, points, refresh=True)
        else:
            await svc.upsert_points(col, points)
        try:
            everything = HybridSearchRequest(dense_query=make_dense([1.0]), text_query=WORD, filter=self._org(A), limit=100)

            async def ready() -> bool:
                [hits] = await svc.query_nearest_points(col, [everything])
                return len(hits) == len(WORLD)
            await wait_for(ready, timeout=60.0)
            yield svc, col
        finally:
            await svc.delete_collection(col)

    @staticmethod
    def _org(org: str) -> FilterExpression:
        return FilterExpression(must=[FieldCondition(key="metadata.orgId", value=org)])

    async def _search(self, loaded, scope_filter, *, org: str = A, leg: str = "dense", should: dict | None = None, vrids: list | None = None,
                      limit: int = 100, dense: list[float] | None = None) -> set[str]:
        svc, col = loaded
        must = [FieldCondition(key="metadata.orgId", value=org)]
        if vrids is not None:
            must.append(FieldCondition(key="metadata.virtualRecordId", values=vrids))
        flt = FilterExpression(must=must, should=[FieldCondition(key=k, values=v) for k, v in (should or {}).items()])
        req = HybridSearchRequest(
            dense_query=(dense or make_dense([1.0])) if leg in ("dense", "both") else None,
            sparse_query=SparseVector(list(TERMS), [1.0, 1.0]) if leg in ("lexical", "both") and svc.get_service_name() == "qdrant" else None,
            text_query=WORD if leg in ("lexical", "both") and svc.get_service_name() != "qdrant" else None,
            filter=flt, limit=limit, scope_filter=scope_filter)
        [hits] = await svc.query_nearest_points(col, [req])
        return {h.payload["metadata"]["virtualRecordId"] for h in hits}

    async def test_without_a_filter_every_group_is_returned_as_before(self, loaded) -> None:
        for leg in LEGS:
            assert await self._search(loaded, None, leg=leg) == set(WORLD)

    async def test_each_admit_rule_admits_exactly_the_groups_pgvector_admits(self, loaded) -> None:
        for leg in LEGS:
            for case in ADMIT_CASES:
                kw, admitted = case.values
                assert await self._search(loaded, f(**kw), leg=leg) == admitted, case.id

    async def test_a_filter_trusts_only_the_stamps_of_the_version_and_generation_it_names(self, loaded) -> None:
        for leg in LEGS:
            for case in TRUST_CASES:
                kw, admitted = case.values
                flt = f(open_apps=frozenset({"c1"}), readable_groups=EVERYTHING, readable_scopes=EVERYTHING, **kw)
                assert await self._search(loaded, flt, leg=leg) == admitted, case.id

    async def test_what_cannot_be_trusted_is_never_admitted_even_when_the_viewer_reads_everything(self, loaded) -> None:
        for leg in LEGS:
            everything = f(open_apps=frozenset({"c1", "c9"}), readable_groups=EVERYTHING, readable_scopes=EVERYTHING)
            got = await self._search(loaded, everything, leg=leg)
            for never in ("unstamped", "legacy", "unknown_stamp", "old_version", "old_generation", "new_generation", "other_connector",
                          "mixed_connectors", "no_connector", "unavailable_stamp"):
                assert never not in got, never
            assert {"container", "container_other", "union", "union_hidden", "intersection", "intersection_hidden", "override", "override_pathed"} == got

    async def test_a_path_through_an_excluded_scope_is_hidden_whatever_else_the_viewer_reads(self, loaded) -> None:
        for leg in LEGS:
            assert "union_hidden" in await self._search(loaded, f(readable_scopes=EVERYTHING, excluded=frozenset({"x1"})), leg=leg)      # by its own id
            assert await self._search(loaded, f(readable_scopes=frozenset({"s1"}), excluded=frozenset({"x1", "s1"})), leg=leg) == set()

    async def test_a_filter_built_by_of_hides_pending_paths_and_blocked_containers(self, loaded) -> None:
        from app.services.permissions.scopes import ViewerScopes
        for leg in LEGS:
            def built(blocked: tuple[str, ...] = (), **kw: object) -> ScopeFilter:
                scopes = ViewerScopes(org_id=A, user_key="u", **kw)  # type: ignore[arg-type]
                return ScopeFilter.of(scopes, {"c1", "c2"}, version=VERSION, generation=GENERATION, blocked=blocked)
            assert await self._search(loaded, built(readable_scopes=frozenset({"s1"})), leg=leg) == {"union", "union_hidden"}
            assert await self._search(loaded, built(readable_scopes=frozenset({"s1"}), pending=frozenset({"p1"})), leg=leg) == {"union_hidden"}
            assert await self._search(loaded, built(readable_groups=frozenset({"g3"})), leg=leg) == {"container_own"}
            assert await self._search(loaded, built(blocked=("c2",), readable_groups=frozenset({"g3"})), leg=leg) == set()

    async def test_the_filter_is_anded_with_the_request_arms_and_narrowing_list_never_replacing_them(self, loaded) -> None:
        for leg in LEGS:
            flt = f(readable_groups=frozenset({"g1", "g2"}), open_apps=frozenset({"c1"}))
            assert await self._search(loaded, flt, leg=leg) == {"container", "container_other"}
            assert await self._search(loaded, flt, leg=leg, should={"recordGroupIds": ["g2"]}) == {"container_other"}
            assert await self._search(loaded, flt, leg=leg, should={"recordGroupIds": ["zzz"]}) == set()
            assert await self._search(loaded, flt, leg=leg, vrids=["container"]) == {"container"}
            assert await self._search(loaded, flt, leg=leg, vrids=["unstamped", "legacy"]) == set()

    async def test_another_organisation_never_appears_under_a_filter(self, loaded) -> None:
        for leg in LEGS:
            everything = f(open_apps=frozenset({"c1"}), readable_groups=EVERYTHING, readable_scopes=EVERYTHING)
            mine, theirs = await self._search(loaded, everything, leg=leg), await self._search(loaded, everything, leg=leg, org=B)
            assert mine == theirs and mine
            assert await self._search(loaded, everything, leg=leg, org="c" * 24) == set()

    async def test_a_filter_with_a_stamp_but_a_missing_field_fails_closed(self, loaded) -> None:
        svc, col = loaded
        broken = _point(A, "container", 99)
        broken.id = _pid(A, "broken")
        broken.payload["metadata"].update(virtualRecordId="broken", blockId="broken")
        del broken.payload["scopeGeneration"]
        await (svc.upsert_points(col, [broken], refresh=True) if "refresh" in svc.upsert_points.__code__.co_varnames else svc.upsert_points(col, [broken]))

        async def seen() -> bool:
            return "broken" in await self._search(loaded, None)
        await wait_for(seen, timeout=60.0)
        assert "broken" not in await self._search(loaded, f(open_apps=frozenset({"c1"}), readable_groups=EVERYTHING))


class TestQdrantScopeFilter(_ScopeFilterContract):
    @pytest.fixture
    async def vector_service(self, qdrant_service):  # noqa: ANN201
        return qdrant_service


class TestOpenSearchScopeFilter(_ScopeFilterContract):
    @pytest.fixture
    async def vector_service(self, opensearch_service):  # noqa: ANN201
        return opensearch_service

    async def test_the_longest_current_list_that_fits_the_clause_limit_works_and_one_more_is_refused(self, loaded) -> None:
        from app.services.vector_db.filters import ScopeFilterTooLarge

        svc, _ = loaded
        limit = await svc._clause_limit()
        assert limit == 1024
        room = limit - 64                                                       # 64 clauses are kept for the rest of the query
        at = frozenset({"c1"} | {f"x{i}" for i in range(room - 1)})
        assert len(at) == room
        assert await self._search(loaded, f(current=at, open_apps=frozenset({"c1"}))) == {"container", "container_other"}        # sent, and answered
        with pytest.raises(ScopeFilterTooLarge, match="max_clause_count"):
            await self._search(loaded, f(current=at | {"one-more"}, open_apps=frozenset({"c1"})))
        big = frozenset(f"z{i}" for i in range(5000))                                           # other lists are not clauses: terms lists of thousands work
        assert await self._search(loaded, f(readable_groups=big | {"g1"})) == {"container"}


class TestRedisScopeFilter(_ScopeFilterContract):
    @pytest.fixture
    async def vector_service(self, redis_service):  # noqa: ANN201
        return redis_service


class _Recorder:
    def __init__(self) -> None:
        self.dropped = 0

    def inc(self, *labels: str, value: float = 1.0) -> None:
        self.dropped += int(value)


class TestRedisPreFilterIsExact(TestRedisScopeFilter):
    """A Redis tag query compares without case and the connectors it excludes are read just before the search: what it lets through that the
    exact rule refuses is dropped before the answer is returned, and counted."""

    @pytest.fixture
    def dropped(self, monkeypatch):  # noqa: ANN201
        from app.services.vector_db.redis import redis_vector

        rec = _Recorder()
        monkeypatch.setattr(redis_vector, "SCOPE_PREFILTER_DROPPED", rec)
        return rec

    async def _put(self, loaded, name: str, dense: list[float] | None = None, **fields) -> None:
        svc, col = loaded
        p = _point(A, "container", 50)
        p.id = _pid(A, name)
        p.payload["metadata"].update(virtualRecordId=name, blockId=name)
        p.payload.update(fields)
        if dense is not None:
            p.dense_vector = dense
        await svc.upsert_points(col, [p])

        async def indexed() -> bool:
            return name in await self._search(loaded, None)
        await wait_for(indexed, timeout=30.0)

    async def test_ids_stored_with_capitals_do_not_meet_the_lowercase_ids_of_the_filter(self, loaded, dropped) -> None:
        await self._put(loaded, "cap_connector", connectorIds=["C1"])
        await self._put(loaded, "cap_group", recordGroupIds=["G1"])
        await self._put(loaded, "cap_path", scopeStamp="union", pathIds=["S1"], aclScope=[], recordGroupIds=["g9"])
        flt = f(readable_groups=frozenset({"g1"}), readable_scopes=frozenset({"s1"}))
        got = await self._search(loaded, flt)
        assert {"container", "union", "union_hidden"} <= got
        for name in ("cap_connector", "cap_group", "cap_path"):
            assert name not in got, name
        assert dropped.dropped >= 3                                               # the query let them through (no case); the exact rule did not

    async def test_a_search_with_a_small_limit_is_not_emptied_by_capitalised_ids_that_rank_above_the_valid_ones(self, loaded, dropped) -> None:
        """Several capitalised-connector points that match the query best: with limit 1 the answer is a valid point, not nothing (the hybrid
        ranking mixes in a lexical leg, so which point ranks first is not fixed here; the unit test fixes the order and pins the widening)."""
        query = make_dense([1.0, -0.3])
        for i in range(4):
            await self._put(loaded, f"cap_first_{i}", dense=query, connectorIds=["C1"])
        for limit in (1, 2):
            got = await self._search(loaded, f(readable_groups=frozenset({"g1"})), limit=limit, dense=query)
            assert got == {"container"}, limit

    async def test_a_stored_capital_is_not_the_excluded_lowercase_id(self, loaded, dropped) -> None:
        await self._put(loaded, "cap_path", scopeStamp="union", pathIds=["s1", "X1"], aclScope=[], recordGroupIds=["g9"])
        got = await self._search(loaded, f(readable_scopes=frozenset({"s1"}), excluded=frozenset({"x1"})))
        assert "cap_path" in got and "union_hidden" not in got and "union" in got      # X1 is not x1; the real x1 is still excluded

    async def test_ids_with_whitespace_in_them_are_matched_exactly(self, loaded, dropped) -> None:
        await self._put(loaded, "tabbed", recordGroupIds=["g\ttab"])
        await self._put(loaded, "lined", recordGroupIds=["g\nline"])
        assert await self._search(loaded, f(readable_groups=frozenset({"g\ttab"}))) == {"tabbed"}
        assert await self._search(loaded, f(readable_groups=frozenset({"g\nline", "g1"}))) == {"lined", "container"}
        assert await self._search(loaded, f(readable_groups=frozenset({"g tab"}))) == set()

    async def test_a_connector_that_appears_between_the_read_and_the_search_is_dropped(self, loaded, dropped) -> None:
        svc, col = loaded
        real = svc.client.execute_command
        planted = {"done": False}

        async def racing(*cmd, **kw) -> object:
            out = await real(*cmd, **kw)
            if cmd[0] == "FT.TAGVALS" and not planted["done"]:
                planted["done"] = True                                           # a point with a connector the index has not shown yet
                p = _point(A, "container", 60)
                p.id, p.payload["connectorIds"] = _pid(A, "raced"), ["c1", "cnew"]
                p.payload["metadata"].update(virtualRecordId="raced", blockId="raced")
                await svc.upsert_points(col, [p])
            return out

        svc.client.execute_command = racing
        try:
            got = await self._search(loaded, f(readable_groups=frozenset({"g1"})))
        finally:
            svc.client.execute_command = real
        assert planted["done"] and "raced" not in got and "container" in got
        assert dropped.dropped >= 1
