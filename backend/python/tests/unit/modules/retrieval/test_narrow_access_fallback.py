"""The container search moves to the record-id filter when a user reads too
little of their containers, and never returns more than the access check admits.

The container filter (``connectorIds IN gated apps``) is a superset of what a
user may read; every hit is decided by ``check_access``. For a user who reads
a small share of a large connector, most candidates are denied and the page
cannot be filled within the over-fetch cap. ``SEARCH_NARROW_ACCESS_FALLBACK``
moves that search to the record-id filter, whose hits go through the same
check, and remembers the scope for a few minutes.

The differential tests run the same inputs through the record-id path (the
exact reference: ``get_accessible_containers`` declines) and through the
container path, over a fake vector store that honours the filters the way
Qdrant, OpenSearch and Redis do, and compare what comes back.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.retrieval_service as mod
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
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

ORG = "org-1"
OTHER_ORG = "org-2"
APPS = ("app-1", "app-2")


@dataclass
class _Corpus:
    """Records with 1-4 points each, ranked by one global score for every query."""

    points: list[SearchResult]
    vrids: list[str]


def _corpus(seed: int, records: int = 240) -> _Corpus:
    rnd = random.Random(seed)
    vrids = [f"v{i}" for i in range(records)]
    rnd.shuffle(vrids)
    points: list[SearchResult] = []
    for vrid in vrids:
        app = APPS[int(vrid[1:]) % len(APPS)]
        for block in range(rnd.randint(1, 4)):
            points.append(SearchResult(
                id=f"{vrid}#{block}",
                score=1.0 - len(points) / 10_000,
                payload={
                    "page_content": f"{vrid} block {block}",
                    "metadata": {"virtualRecordId": vrid, "orgId": ORG},
                    CONNECTOR_IDS_FIELD: [app],
                },
            ))
    # Another org's record that would outrank everything: no filter shape may reach it.
    points.insert(0, SearchResult(
        id="foreign#0", score=2.0,
        payload={"page_content": "foreign", "metadata": {"virtualRecordId": "foreign", "orgId": OTHER_ORG},
                 CONNECTOR_IDS_FIELD: [APPS[0]]},
    ))
    return _Corpus(points=points, vrids=sorted(vrids))


@dataclass
class _FakeStore:
    """Applies `must` and `should` as every backend does, then cuts at `limit`."""

    points: list[SearchResult]
    requests: list[HybridSearchRequest] = field(default_factory=list)

    async def filter_collection(
        self, must: dict[str, Any] | None = None, should: dict[str, Any] | None = None
    ) -> dict[str, dict[str, Any]]:
        return {"must": dict(must or {}), "should": dict(should or {})}

    @staticmethod
    def _matches(point: SearchResult, flt: dict[str, Any]) -> bool:
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

    def used_record_ids(self) -> list[bool]:
        """Per request, whether it carried the record-id filter."""
        return ["virtualRecordId" in req.filter["must"] for req in self.requests]


@dataclass
class _Access:
    """What the graph says. ``readable`` is the access check's answer;
    ``enumerated`` is the (cached) record-id enumeration, which may be stale or
    over-admit, as the real one does for RESTRICTED pages."""

    readable: set[str]
    enumerated: set[str]
    enumerate_fails: bool = False


def _graph(access: _Access) -> AsyncMock:
    graph = AsyncMock()

    async def enumerate_(user_id: str, org_id: str, **_: object) -> dict[str, str]:
        if access.enumerate_fails:
            raise PermissionVerificationUnavailableError("graph down")
        return {vrid: f"rec-{vrid}" for vrid in access.enumerated} if org_id == ORG else {}

    async def check(
        user_key: str, org_id: str, *, node_ids: Iterable[str] = (),
        virtual_record_ids: Iterable[str] = (), **_: object,
    ) -> AccessCheck:
        readable = access.readable if org_id == ORG else set()
        nodes = frozenset(n for n in node_ids if n.removeprefix("rec-") in readable)
        return AccessCheck(
            node_ids=nodes,
            records_by_vrid={v: f"rec-{v}" for v in virtual_record_ids if v in readable},
            node_ids_in_scope=nodes,
        )

    async def records(record_ids: list[str], org_id: str) -> list[dict[str, Any]]:
        return [{
            "_key": rid, "virtualRecordId": rid.removeprefix("rec-"), "origin": "CONNECTOR",
            "recordName": f"{rid}.txt", "mimeType": "text/plain", "connectorName": "X",
            "webUrl": f"https://example.test/{rid}", "orgId": org_id,
        } for rid in record_ids]

    async def user(user_id: str, **_: object) -> dict[str, str]:
        return {"_key": user_id, "email": f"{user_id}@example.test"}

    graph.get_accessible_virtual_record_ids = AsyncMock(side_effect=enumerate_)
    graph.check_access = AsyncMock(side_effect=check)
    graph.get_records_by_record_ids = AsyncMock(side_effect=records)
    graph.get_user_by_user_id = AsyncMock(side_effect=user)
    graph.get_accessible_containers = AsyncMock(
        return_value=AccessibleContainers(app_ids=frozenset(APPS))
    )
    return graph


def _service(store: _FakeStore, graph: AsyncMock, *, containers: bool = True) -> mod.RetrievalService:
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
    if not containers:
        # The exact reference: the graph declines containers, so the search
        # takes the record-id path, as it does on every unsupported filter.
        graph.get_accessible_containers = AsyncMock(
            return_value=AccessibleContainers(fallback_reason="reference")
        )
    return service


@pytest.fixture(autouse=True)
def _isolated(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.delenv("SEARCH_NARROW_ACCESS_FALLBACK", raising=False)
    monkeypatch.delenv("FUSION_RECORD_CUT", raising=False)
    monkeypatch.delenv("HIGH_ACCURACY_MODE", raising=False)
    mod._user_cache.clear()
    mod.narrow_access_memo.clear()
    with patch(
        "app.agents.actions.knowledge_graph.location.resolve_ancestor_locations",
        AsyncMock(return_value={}),
    ):
        yield
    mod._user_cache.clear()
    mod.narrow_access_memo.clear()


async def _search(service: mod.RetrievalService, limit: int, user: str = "alice", **kwargs: object) -> dict:
    return await service.search_with_filters(["q"], user, ORG, limit=limit, **kwargs)


def _shown(response: dict) -> list[str]:
    return [hit["metadata"]["point_id"] for hit in response.get("searchResults", [])]


def _vrids(response: dict) -> set[str]:
    return {hit["metadata"]["virtualRecordId"] for hit in response.get("searchResults", [])}


async def _both(corpus: _Corpus, access: _Access, limit: int) -> tuple[dict, dict, _FakeStore]:
    """The same search through the record-id reference and the container path."""
    reference = await _search(_service(_FakeStore(list(corpus.points)), _graph(access), containers=False), limit)
    mod.narrow_access_memo.clear()
    store = _FakeStore(list(corpus.points))
    candidate = await _search(_service(store, _graph(access)), limit)
    return reference, candidate, store


_SHARES = (1.0, 0.95, 0.8, 0.5, 0.2, 0.05, 0.01)


class TestDifferential:
    """Same corpus, same user, same query: container path against the record-id path."""

    @pytest.mark.parametrize("seed", range(6))
    @pytest.mark.parametrize("share", _SHARES)
    @pytest.mark.parametrize("limit", (5, 20, 50))
    async def test_the_container_path_returns_what_the_record_id_path_returns(
        self, seed: int, share: float, limit: int
    ) -> None:
        corpus = _corpus(seed)
        rnd = random.Random(seed * 1000 + int(share * 100))
        readable = set(rnd.sample(corpus.vrids, max(1, int(len(corpus.vrids) * share))))

        reference, candidate, _ = await _both(corpus, _Access(readable, set(readable)), limit)

        assert _vrids(candidate) <= readable
        assert _shown(candidate) == _shown(reference)

    @pytest.mark.parametrize("seed", range(4))
    @pytest.mark.parametrize("share", _SHARES)
    async def test_nothing_outside_the_access_check_even_when_the_enumeration_over_admits(
        self, seed: int, share: float
    ) -> None:
        """Revoked access the cached enumeration still lists, and RESTRICTED pages
        it admits to every member of the space: neither path may show them."""
        corpus = _corpus(seed)
        rnd = random.Random(seed)
        readable = set(rnd.sample(corpus.vrids, max(1, int(len(corpus.vrids) * share))))
        stale = set(rnd.sample(sorted(set(corpus.vrids) - readable), min(10, len(set(corpus.vrids) - readable))))

        reference, candidate, _ = await _both(corpus, _Access(readable, readable | stale), 20)

        assert _vrids(reference) <= readable
        assert _vrids(candidate) <= readable
        assert not (_vrids(candidate) | _vrids(reference)) & stale

    async def test_a_narrow_user_is_served_in_full(self) -> None:
        """1% readable: the container path alone returns a fraction of the page;
        with the move it returns the record-id path's page."""
        corpus = _corpus(3, records=2000)
        readable = set(random.Random(3).sample(corpus.vrids, 20))

        reference, candidate, store = await _both(corpus, _Access(readable, set(readable)), 10)

        assert len(_shown(reference)) == 10
        assert _shown(candidate) == _shown(reference)
        assert store.used_record_ids() == [False, True]

    async def test_with_the_move_off_a_narrow_user_is_short_changed(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """What the setting fixes: the container path's single wider retry cannot
        reach a 1% share within the cap."""
        monkeypatch.setenv("SEARCH_NARROW_ACCESS_FALLBACK", "false")
        corpus = _corpus(3, records=2000)
        readable = set(random.Random(3).sample(corpus.vrids, 20))

        reference, candidate, store = await _both(corpus, _Access(readable, set(readable)), 10)

        assert len(_shown(candidate)) < len(_shown(reference))
        assert _vrids(candidate) <= readable
        assert not any(store.used_record_ids())


class TestWhenTheContainerSearchStands:
    async def test_a_user_who_reads_everything_never_enumerates(self) -> None:
        corpus = _corpus(1)
        graph = _graph(_Access(set(corpus.vrids), set(corpus.vrids)))
        store = _FakeStore(list(corpus.points))

        await _search(_service(store, graph), 20)

        graph.get_accessible_virtual_record_ids.assert_not_awaited()
        assert store.used_record_ids() == [False]
        assert not mod.narrow_access_memo._entries

    async def test_an_exhausted_corpus_is_not_moved(self) -> None:
        """Fewer points than asked for: the record-id filter selects a subset of
        the same points, so moving could not add anything."""
        corpus = _corpus(1, records=6)
        readable = set(corpus.vrids[:1])
        graph = _graph(_Access(readable, set(readable)))
        store = _FakeStore(list(corpus.points))

        await _search(_service(store, graph), 20)

        assert store.used_record_ids() == [False]

    async def test_a_tool_scoped_search_is_not_moved(self) -> None:
        """A tool's or an entity filter's id list already narrows the search; it
        keeps the container path's no-retry rule rather than loading the map."""
        corpus = _corpus(2, records=400)
        readable = set(corpus.vrids[:4])
        graph = _graph(_Access(readable, set(readable)))
        store = _FakeStore(list(corpus.points))

        await _search(_service(store, graph), 5, virtual_record_ids_from_tool=corpus.vrids[:300])

        graph.get_accessible_virtual_record_ids.assert_not_awaited()
        assert not mod.narrow_access_memo._entries


class TestTheMemo:
    async def test_the_next_search_goes_straight_to_the_record_ids(self) -> None:
        corpus = _corpus(4, records=1000)
        readable = set(random.Random(4).sample(corpus.vrids, 10))
        store = _FakeStore(list(corpus.points))
        service = _service(store, _graph(_Access(readable, set(readable))))

        first = await _search(service, 10)
        store.requests.clear()
        second = await _search(service, 10)

        assert store.used_record_ids() == [True]
        assert _shown(second) == _shown(first)

    async def test_the_memo_never_widens_access(self) -> None:
        """A remembered scope whose access is then revoked: the record-id path's
        own check still drops the revoked records, even while the cached
        enumeration lists them."""
        corpus = _corpus(5, records=1000)
        readable = set(random.Random(5).sample(corpus.vrids, 10))
        access = _Access(readable, set(readable))
        store = _FakeStore(list(corpus.points))
        service = _service(store, _graph(access))
        await _search(service, 10)

        revoked = set(sorted(readable)[:5])
        access.readable = readable - revoked
        response = await _search(service, 10)

        assert _vrids(response) <= access.readable
        assert not _vrids(response) & revoked

    async def test_the_memo_is_per_user_and_scope(self) -> None:
        corpus = _corpus(6, records=1000)
        readable = set(random.Random(6).sample(corpus.vrids, 10))
        store = _FakeStore(list(corpus.points))
        service = _service(store, _graph(_Access(readable, set(readable))))
        await _search(service, 10)

        key = mod.narrow_access_memo.key
        assert mod.narrow_access_memo.is_narrow(key(ORG, "alice", {}))
        assert not mod.narrow_access_memo.is_narrow(key(ORG, "bob", {}))
        assert not mod.narrow_access_memo.is_narrow(key(ORG, "alice", {"apps": ["app-1"]}))

    async def test_the_memo_is_ignored_when_the_setting_is_off(self, monkeypatch: pytest.MonkeyPatch) -> None:
        corpus = _corpus(7, records=1000)
        readable = set(random.Random(7).sample(corpus.vrids, 10))
        store = _FakeStore(list(corpus.points))
        service = _service(store, _graph(_Access(readable, set(readable))))
        await _search(service, 10)
        monkeypatch.setenv("SEARCH_NARROW_ACCESS_FALLBACK", "off")
        store.requests.clear()

        await _search(service, 10)

        assert not any(store.used_record_ids())

    def test_entries_expire(self) -> None:
        memo = mod._NarrowAccessMemo()
        key = memo.key(ORG, "alice", None)
        memo.mark(key, now=100.0)

        assert memo.is_narrow(key, now=100.0 + mod.NARROW_ACCESS_MEMO_TTL_SECONDS - 1)
        assert not memo.is_narrow(key, now=100.0 + mod.NARROW_ACCESS_MEMO_TTL_SECONDS)
        assert not memo._entries

    def test_the_memo_is_bounded(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(mod, "_NARROW_ACCESS_MEMO_MAX_ENTRIES", 3)
        memo = mod._NarrowAccessMemo()
        for i in range(5):
            memo.mark(memo.key(ORG, f"u{i}", None), now=float(i))

        assert len(memo._entries) == 3
        assert memo.is_narrow(memo.key(ORG, "u4", None), now=5.0)
        assert not memo.is_narrow(memo.key(ORG, "u0", None), now=5.0)


class TestFailures:
    async def test_a_graph_outage_during_the_move_is_reported_not_emptied(self) -> None:
        corpus = _corpus(8, records=1000)
        readable = set(random.Random(8).sample(corpus.vrids, 10))
        access = _Access(readable, set(readable), enumerate_fails=True)

        response = await _search(_service(_FakeStore(list(corpus.points)), _graph(access)), 10)

        assert response["status"] == mod.Status.PERMISSION_CHECK_UNAVAILABLE.value
        assert not response["searchResults"]


class TestTheRule:
    @pytest.mark.parametrize(
        ("surviving", "raw", "fetch", "max_batch", "denied", "allow", "moved"),
        [
            (40, 40, 40, 40, 0, True, False),     # nothing denied: same points either way
            (5, 10, 40, 10, 5, True, False),      # corpus exhausted
            (5, 40, 40, 40, 35, False, False),    # tool-scoped search
            (5, 40, 40, 40, 35, True, True),      # short page
            (25, 40, 40, 40, 15, True, True),     # full page, 62% admitted
            (36, 40, 40, 40, 4, True, False),     # full page, 90% admitted
            (0, 40, 40, 40, 40, True, True),      # everything denied
        ],
    )
    def test_when_a_search_moves(self, surviving, raw, fetch, max_batch, denied, allow, moved) -> None:
        assert mod.RetrievalService._narrow_shortfall(
            surviving=surviving, limit=20, raw=raw, fetch_limit=fetch,
            max_batch=max_batch, denied=denied, allow_requery=allow,
        ) is moved

    @pytest.mark.parametrize(("value", "on"), [
        (None, True), ("true", True), ("1", True), ("anything", True),
        ("false", False), ("0", False), ("off", False), ("No", False), (" FALSE ", False),
    ])
    def test_the_setting(self, monkeypatch: pytest.MonkeyPatch, value: str | None, on: bool) -> None:
        if value is None:
            monkeypatch.delenv("SEARCH_NARROW_ACCESS_FALLBACK", raising=False)
        else:
            monkeypatch.setenv("SEARCH_NARROW_ACCESS_FALLBACK", value)
        assert mod.narrow_access_fallback_enabled() is on
