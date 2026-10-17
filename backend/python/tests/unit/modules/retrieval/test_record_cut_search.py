"""FUSION_RECORD_CUT through `search_with_filters`, over a fake vector store
that honours the permission filter the way Qdrant, OpenSearch and Redis do.

Off, a search must ask the store for what it asked before and keep the same
points. On, it fuses deeper, spreads the cut across records, and still shows a
user nothing outside what they may read, in their org.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.retrieval_service as mod
from app.modules.retrieval.record_cut import FUSION_RECORD_CUT_ENV_VAR, RecordCut
from app.services.graph_db.interface.graph_db_provider import (
    AccessCheck,
    AccessibleContainers,
)
from app.services.vector_db.const.const import CONNECTOR_IDS_FIELD
from app.services.vector_db.models import (
    HybridSearchRequest,
    SearchResult,
    VectorDBCapabilities,
)

if TYPE_CHECKING:
    from collections.abc import Iterable, Iterator

ORG_1, ORG_2 = "org-1", "org-2"


@dataclass
class _Doc:
    vrid: str
    org_id: str
    connector_id: str


# Ranked best first for every query. "secret" outranks everything in org 1 and
# "elsewhere" is another org's record that would outrank it too.
_CORPUS: list[tuple[str, int]] = [
    ("elsewhere", 6),
    ("secret", 6),
    ("long", 12),
    *((f"r{i}", 1) for i in range(30)),
]
_DOCS = {
    "elsewhere": _Doc("elsewhere", ORG_2, "app-2"),
    "secret": _Doc("secret", ORG_1, "app-1"),
    "long": _Doc("long", ORG_1, "app-1"),
    **{f"r{i}": _Doc(f"r{i}", ORG_1, "app-1") for i in range(30)},
}
_READERS = {
    ("alice", ORG_1): {"long", *(f"r{i}" for i in range(30))},
    ("bob", ORG_1): {"secret", "r0", "r1"},
    ("carol", ORG_2): {"elsewhere"},
}


def _points(
    corpus: list[tuple[str, int]] = _CORPUS, docs: dict[str, _Doc] = _DOCS
) -> list[SearchResult]:
    points = []
    for vrid, hits in corpus:
        doc = docs[vrid]
        for block in range(hits):
            points.append(SearchResult(
                id=f"{vrid}#{block}",
                score=1.0 - len(points) / 1000,
                payload={
                    "page_content": f"{vrid} block {block}",
                    "metadata": {"virtualRecordId": vrid, "orgId": doc.org_id, "blockId": f"b{block}"},
                    CONNECTOR_IDS_FIELD: [doc.connector_id],
                },
            ))
    return points


@dataclass
class _FakeStore:
    """Applies `must` and `should` as every backend does, then cuts at `limit`."""

    points: list[SearchResult] = field(default_factory=_points)
    requests: list[HybridSearchRequest] = field(default_factory=list)

    async def filter_collection(
        self, must: dict[str, Any] | None = None, should: dict[str, Any] | None = None
    ) -> dict[str, dict[str, Any]]:
        return {"must": dict(must or {}), "should": dict(should or {})}

    def _matches(self, point: SearchResult, flt: dict[str, Any]) -> bool:
        metadata = point.payload["metadata"]
        must, should = flt["must"], flt["should"]
        if metadata["orgId"] != must["orgId"]:
            return False
        if "virtualRecordId" in must and metadata["virtualRecordId"] not in must["virtualRecordId"]:
            return False
        if not should:
            return True
        return bool(
            set(point.payload[CONNECTOR_IDS_FIELD]) & set(should.get(CONNECTOR_IDS_FIELD, []))
            or metadata["virtualRecordId"] in should.get("virtualRecordId", [])
        )

    async def query_nearest_points(
        self, collection_name: str, requests: list[HybridSearchRequest]
    ) -> list[list[SearchResult]]:
        self.requests.extend(requests)
        return [
            [p for p in self.points if self._matches(p, req.filter)][: req.limit]
            for req in requests
        ]


def _record(vrid: str, org_id: str) -> dict[str, Any]:
    return {
        "_key": f"rec-{vrid}", "virtualRecordId": vrid, "origin": "CONNECTOR",
        "recordName": f"{vrid}.txt", "mimeType": "text/plain", "connectorName": "X",
        "webUrl": f"https://example.test/{vrid}", "orgId": org_id,
    }


def _graph(readers: dict[tuple[str, str], set[str]] = _READERS) -> AsyncMock:
    graph = AsyncMock()

    async def accessible(user_id: str, org_id: str, **_: object) -> dict[str, str]:
        return {vrid: f"rec-{vrid}" for vrid in readers.get((user_id, org_id), set())}

    async def verify(vrids: list[str], user_id: str, org_id: str, **_: object) -> dict[str, str]:
        readable = readers.get((user_id, org_id), set())
        return {vrid: f"rec-{vrid}" for vrid in vrids if vrid in readable}

    async def records(record_ids: list[str], org_id: str) -> list[dict[str, Any]]:
        return [_record(rid.removeprefix("rec-"), org_id) for rid in record_ids]

    # Hits are verified by the batch check, keyed by the user's graph key.
    async def check(
        user_key: str, org_id: str, *, node_ids: Iterable[str] = (),
        virtual_record_ids: Iterable[str] = (), **_: object,
    ) -> AccessCheck:
        readable = readers.get((user_key, org_id), set())
        nodes = frozenset(node for node in node_ids if node.removeprefix("rec-") in readable)
        return AccessCheck(
            node_ids=nodes,
            records_by_vrid={vrid: f"rec-{vrid}" for vrid in virtual_record_ids if vrid in readable},
            node_ids_in_scope=nodes,
        )

    async def user(user_id: str, **_: object) -> dict[str, str]:
        return {"_key": user_id, "email": f"{user_id}@example.test"}

    graph.get_accessible_virtual_record_ids = AsyncMock(side_effect=accessible)
    graph.filter_accessible_virtual_record_ids = AsyncMock(side_effect=verify)
    graph.check_access = AsyncMock(side_effect=check)
    graph.get_records_by_record_ids = AsyncMock(side_effect=records)
    graph.get_user_by_user_id = AsyncMock(side_effect=user)
    return graph


def _service(store: _FakeStore, graph: AsyncMock) -> mod.RetrievalService:
    service = mod.RetrievalService.__new__(mod.RetrievalService)
    service.logger = MagicMock()
    service.config_service = AsyncMock()
    service.graph_provider = graph
    service.blob_store = MagicMock()
    service.vector_db_service = store
    service.collection_registry = MagicMock()
    service.collection_registry.resolve_for_query = AsyncMock(return_value=["records"])
    service._capabilities = VectorDBCapabilities(
        supports_sparse_vectors=False, supports_server_side_text_search=True
    )
    service._sparse_embedder = None
    service._sparse_embedder_lock = asyncio.Lock()
    embedder = MagicMock()
    embedder.aembed_query = AsyncMock(return_value=[0.1, 0.2])
    service.get_embedding_model_instance = AsyncMock(return_value=embedder)
    service._excluded_demo_apps = AsyncMock(return_value=frozenset())
    service._container_filter_enabled = AsyncMock(return_value=False)
    return service


@pytest.fixture(autouse=True)
def _isolated() -> Iterator[None]:
    mod._user_cache.clear()
    with patch(
        "app.agents.actions.knowledge_graph.location.resolve_ancestor_locations",
        AsyncMock(return_value={}),
    ):
        yield
    mod._user_cache.clear()


@pytest.fixture
def store() -> _FakeStore:
    return _FakeStore()


@pytest.fixture
def service(store: _FakeStore) -> mod.RetrievalService:
    return _service(store, _graph())


def _switches(monkeypatch: pytest.MonkeyPatch, mode: str | None, feature: str | None) -> None:
    for name, value in (("HIGH_ACCURACY_MODE", mode), (FUSION_RECORD_CUT_ENV_VAR, feature)):
        if value is None:
            monkeypatch.delenv(name, raising=False)
        else:
            monkeypatch.setenv(name, value)


async def _search(service: mod.RetrievalService, user: str = "alice", org: str = ORG_1, **kwargs: object) -> dict:
    kwargs.setdefault("limit", 8)
    return await service.search_with_filters(["q"], user, org, **kwargs)


def _shown(response: dict) -> list[str]:
    return [hit["metadata"]["point_id"] for hit in response.get("searchResults", [])]


def _records(response: dict) -> set[str]:
    return {hit["metadata"]["virtualRecordId"] for hit in response.get("searchResults", [])}


_OFF = [(None, None), ("false", None), ("false", ""), ("true", "false"), (None, "0")]
_ON = [(None, "true"), ("false", "true"), ("true", None), ("true", ""), ("true", " ")]


class TestTheSwitch:
    @pytest.mark.parametrize(("mode", "feature"), _OFF)
    async def test_off_a_search_is_exactly_what_it_was(
        self, monkeypatch: pytest.MonkeyPatch, service, store: _FakeStore, mode, feature
    ) -> None:
        """The store is asked for `limit` and the top `limit` points come back:
        the long record fills the cut as it always has."""
        _switches(monkeypatch, mode, feature)

        response = await _search(service)

        assert [req.limit for req in store.requests] == [8]
        assert _shown(response) == [f"long#{i}" for i in range(8)]

    @pytest.mark.parametrize(("mode", "feature"), _ON)
    async def test_on_a_search_fuses_deeper_and_reaches_more_records(
        self, monkeypatch: pytest.MonkeyPatch, service, store: _FakeStore, mode, feature
    ) -> None:
        _switches(monkeypatch, mode, feature)

        response = await _search(service)

        assert [req.limit for req in store.requests] == [24]
        assert _shown(response) == [
            "long#0", "long#1", "long#2", "long#3", "r0#0", "r1#0", "r2#0", "r3#0",
        ]

    @pytest.mark.parametrize(("enabled", "asked", "shown"), [
        # Off, the fused cut is not applied, but the search API still keeps at
        # most `passages_per_record(5)` = 4 passages of any one record.
        ("false", 5, ["long#0", "long#1", "long#2", "long#3"]),
        ("true", 15, ["long#0", "long#1", "long#2", "r0#0", "r1#0"]),
    ])
    async def test_the_search_api_path_is_cut_the_same_way(
        self, monkeypatch: pytest.MonkeyPatch, service, store: _FakeStore, enabled, asked, shown
    ) -> None:
        """`/search` (and the UI search on it) passes `knowledge_search` and
        its default limit of 5."""
        _switches(monkeypatch, None, enabled)

        response = await _search(service, limit=5, knowledge_search=True)

        assert [req.limit for req in store.requests] == [asked]
        assert _shown(response) == shown

    async def test_off_matches_a_search_with_no_cut_at_all(
        self, monkeypatch: pytest.MonkeyPatch, store: _FakeStore
    ) -> None:
        """Byte for byte: the switch off returns the same response as the
        search path with the record cut taken out entirely."""
        _switches(monkeypatch, "true", "false")
        switched_off = await _search(_service(store, _graph()))
        with patch.object(mod, "active_record_cut", return_value=None):
            no_cut = await _search(_service(_FakeStore(), _graph()))

        assert switched_off == no_cut

    async def test_a_search_confined_to_one_record_keeps_the_plain_depth(
        self, monkeypatch: pytest.MonkeyPatch, service, store: _FakeStore
    ) -> None:
        """Block selection inside one fetched record: no other record to reach."""
        _switches(monkeypatch, None, "true")

        response = await _search(service, virtual_record_ids_from_tool=["long", "long"])

        assert [req.limit for req in store.requests] == [8]
        assert _shown(response) == [f"long#{i}" for i in range(8)]

    async def test_a_user_who_reads_one_record_keeps_the_plain_depth(
        self, monkeypatch: pytest.MonkeyPatch, service, store: _FakeStore
    ) -> None:
        _switches(monkeypatch, None, "true")

        response = await _search(service, user="carol", org=ORG_2)

        assert [req.limit for req in store.requests] == [8]
        assert _records(response) == {"elsewhere"}

    async def test_a_scope_of_several_records_is_spread(
        self, monkeypatch: pytest.MonkeyPatch, service, store: _FakeStore
    ) -> None:
        _switches(monkeypatch, None, "true")

        response = await _search(service, limit=6, virtual_record_ids_from_tool=["long", "r5"])

        assert [req.limit for req in store.requests] == [18]
        assert _shown(response) == ["long#0", "long#1", "long#2", "long#3", "long#4", "r5#0"]


class TestPermissions:
    """Two users of one org and a user of another, over one shared store."""

    @pytest.mark.parametrize("container_path", [False, True])
    @pytest.mark.parametrize("enabled", ["false", "true"])
    async def test_nobody_sees_a_record_they_cannot_read(
        self, monkeypatch: pytest.MonkeyPatch, container_path: bool, enabled: str
    ) -> None:
        _switches(monkeypatch, None, enabled)
        for user, org in _READERS:
            store, graph = _FakeStore(), _graph()
            service = _service(store, graph)
            if container_path:
                service._container_filter_enabled = AsyncMock(return_value=True)
                app = "app-1" if org == ORG_1 else "app-2"
                graph.get_accessible_containers = AsyncMock(
                    return_value=AccessibleContainers(app_ids=frozenset({app}))
                )

            response = await _search(service, user=user, org=org, limit=10)

            shown = _records(response)
            assert shown, (user, org)
            assert shown <= _READERS[(user, org)], (user, org)
            assert {r["virtualRecordId"] for r in response["records"]} <= _READERS[(user, org)]
            assert set(response["virtual_to_record_map"]) <= _READERS[(user, org)]
            assert all(req.filter["must"]["orgId"] == org for req in store.requests)

    async def test_an_unreadable_record_ranked_first_does_not_cost_a_reader_slots(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Container path: the store returns `secret` to alice too (same app),
        and the verifier drops it; the cut still fills from what she may read."""
        _switches(monkeypatch, None, "true")
        store, graph = _FakeStore(), _graph()
        service = _service(store, graph)
        service._container_filter_enabled = AsyncMock(return_value=True)
        graph.get_accessible_containers = AsyncMock(
            return_value=AccessibleContainers(app_ids=frozenset({"app-1"}))
        )

        response = await _search(service, limit=10)

        assert "secret" not in _records(response)
        assert len(response["searchResults"]) == 10
        verified = graph.check_access.await_args_list[0].kwargs["virtual_record_ids"]
        assert "secret" in verified

    async def test_the_record_id_filter_is_the_same_with_the_switch_on(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        filters = []
        for enabled in ("false", "true"):
            _switches(monkeypatch, None, enabled)
            store = _FakeStore()
            await _search(_service(store, _graph()), user="bob")
            filters.append(store.requests[0].filter)

        assert filters[0] == filters[1]
        assert filters[0]["must"]["orgId"] == ORG_1
        assert set(filters[0]["must"]["virtualRecordId"]) == {"secret", "r0", "r1"}


class TestTheContainerPath:
    async def test_the_overfetch_headroom_is_dropped_by_record(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A score cut of the over-fetched set would hand back the long record
        each search's cut had spread out."""
        hits = [
            {"score": 1.0 - i / 100, "content": "c", "metadata": {"virtualRecordId": "v0"}}
            for i in range(20)
        ] + [{"score": 0.5, "content": "c", "metadata": {"virtualRecordId": "v1"}}]
        service = _service(_FakeStore(), _graph())
        service.graph_provider.check_access = AsyncMock(
            return_value=AccessCheck(records_by_vrid={"v0": "r0", "v1": "r1"})
        )
        service._execute_parallel_searches = AsyncMock(return_value=hits)
        containers = AccessibleContainers(app_ids=frozenset({"a"}))

        plain, _, _ = await service._search_and_adjudicate(
            ["q"], None, 10, ORG_1, "u1", containers, allow_requery=False, scope_connector_ids=None,
        )
        spread, _, _ = await service._search_and_adjudicate(
            ["q"], None, 10, ORG_1, "u1", containers, allow_requery=False, scope_connector_ids=None,
            record_cut=RecordCut(),
        )

        assert [h["metadata"]["virtualRecordId"] for h in plain] == ["v0"] * 10
        assert len(spread) == 10
        assert "v1" in {h["metadata"]["virtualRecordId"] for h in spread}
        assert service._execute_parallel_searches.await_args_list[1].args[2] == 60
        assert "record_cut" not in service._execute_parallel_searches.await_args_list[1].kwargs

    @pytest.mark.parametrize("enabled", ["false", "true"])
    async def test_unreadable_records_ranked_after_a_readable_one_cost_it_nothing(
        self, monkeypatch: pytest.MonkeyPatch, enabled: str
    ) -> None:
        """One trusted and one untrusted app: a readable record's 20 chunks
        rank first and 60 unreadable records follow. Cut before verification,
        the reserve went to the unreadable records and the readable chunks
        were lost; the cut now runs on what was admitted."""
        _switches(monkeypatch, None, enabled)
        docs = {"mine": _Doc("mine", ORG_1, "app-1"), **{f"u{i}": _Doc(f"u{i}", ORG_1, "app-x") for i in range(60)}}
        corpus = [("mine", 20), *((f"u{i}", 1) for i in range(60))]
        store = _FakeStore(points=_points(corpus, docs))
        service = _service(store, _graph({("alice", ORG_1): {"mine"}}))
        service._container_filter_enabled = AsyncMock(return_value=True)
        service.graph_provider.get_accessible_containers = AsyncMock(return_value=AccessibleContainers(
            app_ids=frozenset({"app-1", "app-x"}), app_ids_trusted=frozenset({"app-1"}),
        ))

        response = await _search(service, limit=20)

        assert _shown(response) == [f"mine#{i}" for i in range(20)]
        assert [req.limit for req in store.requests] == ([81] if enabled == "true" else [27])

    async def test_a_retry_cannot_ask_past_the_cap(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """At the fused depth the first fetch is already at the cap, so a
        shortfall does not trigger a second fetch of the same points."""
        _switches(monkeypatch, None, "true")
        docs = {f"u{i}": _Doc(f"u{i}", ORG_1, "app-x") for i in range(400)}
        store = _FakeStore(points=_points([(f"u{i}", 1) for i in range(400)], docs))
        service = _service(store, _graph({("alice", ORG_1): {"u399"}}))
        service._container_filter_enabled = AsyncMock(return_value=True)
        service.graph_provider.get_accessible_containers = AsyncMock(
            return_value=AccessibleContainers(app_ids=frozenset({"app-x"}))
        )

        await _search(service, limit=100)

        assert [req.limit for req in store.requests] == [300]


def _store_points(*vrids: str) -> list[SearchResult]:
    return [
        SearchResult(
            id=f"{vrid}-{i}", score=1.0 - i / 100,
            payload={"page_content": vrid, "metadata": {"virtualRecordId": vrid}},
        )
        for i, vrid in enumerate(vrids)
    ]


class TestExecuteParallelSearches:
    @staticmethod
    def _service(batches: list[list[SearchResult]]) -> tuple[mod.RetrievalService, AsyncMock]:
        store = AsyncMock()
        store.query_nearest_points = AsyncMock(return_value=batches)
        service = _service(store, _graph())
        return service, store

    @staticmethod
    def _limits(store: AsyncMock) -> list[int]:
        return [req.limit for call in store.query_nearest_points.await_args_list for req in call.kwargs["requests"]]

    @pytest.mark.parametrize(
        ("limit", "asked"), [(4, 12), (100, 300), (200, 300), (300, 300), (400, 400)]
    )
    async def test_the_fused_depth_stops_at_the_overfetch_cap(self, limit: int, asked: int) -> None:
        service, store = self._service([[]])

        await service._execute_parallel_searches(["q"], None, limit, ORG_1, record_cut=RecordCut())

        assert self._limits(store) == [asked]
        assert mod._OVERFETCH_ABSOLUTE_CAP == 300

    async def test_without_a_cut_the_store_is_asked_for_the_limit(self) -> None:
        service, store = self._service([_store_points(*("a" * 10))])

        results = await service._execute_parallel_searches(["q"], None, 4, ORG_1)

        assert self._limits(store) == [4]
        assert [r["metadata"]["point_id"] for r in results] == ["a-0", "a-1", "a-2", "a-3"]

    async def test_each_query_is_cut_by_record_on_its_own(self) -> None:
        service, store = self._service([
            _store_points(*("a" * 10), "b", "c"),
            _store_points(*("d" * 10), "e"),
        ])

        results = await service._execute_parallel_searches(["q1", "q2"], None, 4, ORG_1, record_cut=RecordCut())

        assert [r["metadata"]["point_id"] for r in results] == [
            "a-0", "a-1", "b-10", "c-11", "d-0", "d-1", "d-2", "e-10",
        ]

    async def test_several_collections_are_merged_at_the_fused_depth_then_cut(self) -> None:
        service, store = self._service([_store_points(*("a" * 10))])
        other = [_store_points("b", "c")]
        store.query_nearest_points = AsyncMock(side_effect=[[_store_points(*("a" * 10))], other])
        service.collection_registry.resolve_for_query = AsyncMock(return_value=["one", "two"])

        results = await service._execute_parallel_searches(["q"], None, 4, ORG_1, record_cut=RecordCut())

        assert {r["metadata"]["virtualRecordId"] for r in results} == {"a", "b", "c"}
        assert len(results) == 4

    async def test_exhaustion_is_judged_after_the_cut(self) -> None:
        """A deep batch cut back to `limit` is not exhausted; a store with fewer
        than `limit` points is."""
        service, _ = self._service([_store_points(*("a" * 30))])
        plan = mod._QueryPlan()
        await service._execute_parallel_searches(["q"], None, 10, ORG_1, plan=plan, record_cut=RecordCut())
        assert plan.max_batch == 10

        service, _ = self._service([_store_points(*("a" * 6))])
        plan = mod._QueryPlan()
        await service._execute_parallel_searches(["q"], None, 10, ORG_1, plan=plan, record_cut=RecordCut())
        assert plan.max_batch == 6
