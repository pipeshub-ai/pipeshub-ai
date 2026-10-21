"""SEARCH_SOURCE_DIVERSITY: the search API's first page, varied across sources.

The rule on its own, then through `search_with_filters` over a fake vector
store: off, a search is exactly what it was; on, no source fills the first
page, the records it pushes down still come back, and a search scoped to one
source or run as a timeline keeps the plain ranking.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import app.modules.retrieval.retrieval_service as mod
from app.agents.actions.knowledge_graph.ops.timeline import timeline_search_response
from app.modules.retrieval.source_diversity import (
    PER_SOURCE_ENV_VAR,
    POOL_ENV_VAR,
    SOURCE_DIVERSITY_ENV_VAR,
    WINDOW_ENV_VAR,
    SourceDiversity,
    active_source_diversity,
    plain_ranking,
    record_source,
    source_diversity_from_env,
)
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
    from collections.abc import Iterator

_KNOBS = (SOURCE_DIVERSITY_ENV_VAR, WINDOW_ENV_VAR, PER_SOURCE_ENV_VAR, POOL_ENV_VAR, "HIGH_ACCURACY_MODE")


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _KNOBS:
        monkeypatch.delenv(name, raising=False)


def _source(record: str) -> str | None:
    return None if record.startswith("?") else record[0]


class TestTheRule:
    rule = SourceDiversity(window=6, per_source=2, pool=50)

    def test_the_excess_is_demoted_below_the_first_page_not_dropped(self) -> None:
        records = ["g1", "g2", "g3", "g4", "s1", "c1", "g5", "s2"]

        order = self.rule.order(records, plain=set(records[:6]), source_of=_source)

        assert order == ["g1", "g2", "s1", "c1", "s2", "g3", "g4"]
        # Every record the plain search returned is still there.
        assert set(records[:6]) <= set(order)

    def test_records_of_one_source_keep_their_order(self) -> None:
        records = ["g1", "s1", "g2", "g3", "s2", "g4", "s3", "s4", "c1"]

        order = self.rule.order(records, plain=set(records[:6]), source_of=_source)

        for source in "gsc":
            assert [r for r in order if r[0] == source] == [r for r in records if r[0] == source and r in order]

    def test_a_record_below_the_plain_results_comes_in_only_on_the_first_page(self) -> None:
        records = ["g1", "g2", "g3", "s1", "g4", "s2", "s3", "s4", "c1"]

        order = self.rule.order(records, plain=set(records[:4]), source_of=_source)

        # A page of four (what the plain search showed): two of each source,
        # then the GitHub record it pushed down. s3, s4 and c1 stay out.
        assert order == ["g1", "g2", "s1", "s2", "g3"]

    def test_one_source_keeps_the_plain_order(self) -> None:
        records = [f"g{i}" for i in range(12)]

        assert self.rule.order(records, plain=set(records[:10]), source_of=_source) == records[:10]

    def test_a_record_of_unknown_source_is_never_held_back(self) -> None:
        records = ["?a", "?b", "?c", "g1", "g2", "g3", "s1"]

        order = self.rule.order(records, plain=set(records[:6]), source_of=_source)

        assert order == ["?a", "?b", "?c", "g1", "g2", "s1", "g3"]

    def test_nothing_to_vary_keeps_the_plain_order(self) -> None:
        records = ["g1", "s1", "c1", "g2", "s2", "c2", "g3"]

        assert self.rule.order(records, plain=set(records), source_of=_source) == records

    def test_plain_records_need_not_lead_the_pool(self) -> None:
        """Several queries: a plain record can rank below pool records another
        query read deeper. It is still returned, and the deeper ones are not."""
        records = ["g1", "g2", "g3", "g4", "g5", "s1", "g6", "c1"]
        plain = {"g1", "g2", "g3", "s1", "c1"}

        order = self.rule.order(records, plain=plain, source_of=_source)

        assert order == ["g1", "g2", "s1", "c1", "g3"]

    def test_with_no_source_over_its_share_the_plain_records_come_back_in_rank_order(self) -> None:
        records = ["g1", "x1", "s1", "x2", "c1", "g2"]
        plain = {"g1", "s1", "c1", "g2"}

        assert self.rule.order(records, plain=plain, source_of=_source) == ["g1", "s1", "c1", "g2"]

    def test_the_pool_never_reads_fewer_than_the_caller_asked(self) -> None:
        assert self.rule.fetch_limit(10) == 50
        assert self.rule.fetch_limit(80) == 80


class TestTheSettings:
    def test_off_unless_set_to_true(self, monkeypatch: pytest.MonkeyPatch) -> None:
        assert active_source_diversity(scope_ids=None, scope_virtual_ids=None) is None
        for value in ("false", "", "0", "no"):
            monkeypatch.setenv(SOURCE_DIVERSITY_ENV_VAR, value)
            assert active_source_diversity(scope_ids=None, scope_virtual_ids=None) is None

    def test_high_accuracy_mode_does_not_turn_it_on(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("HIGH_ACCURACY_MODE", "true")

        assert active_source_diversity(scope_ids=None, scope_virtual_ids=None) is None

    def test_on_with_the_default_knobs(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SOURCE_DIVERSITY_ENV_VAR, "true")

        assert active_source_diversity(scope_ids=None, scope_virtual_ids=None) == SourceDiversity(
            window=10, per_source=4, pool=50,
        )

    def test_the_knobs_are_read_and_clamped(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(WINDOW_ENV_VAR, "8")
        monkeypatch.setenv(PER_SOURCE_ENV_VAR, "20")
        monkeypatch.setenv(POOL_ENV_VAR, "9999")
        assert source_diversity_from_env() == SourceDiversity(window=8, per_source=8, pool=300)

        monkeypatch.setenv(WINDOW_ENV_VAR, "ten")
        monkeypatch.setenv(PER_SOURCE_ENV_VAR, "0")
        monkeypatch.setenv(POOL_ENV_VAR, "-5")
        assert source_diversity_from_env() == SourceDiversity(window=10, per_source=1, pool=1)

    @pytest.mark.parametrize(("scope_ids", "scope_virtual_ids"), [
        (["app-1"], None),
        (["app-1", "app-1"], None),
        ([], None),
        (None, ["v1", "v1"]),
    ])
    def test_a_search_scoped_to_one_source_or_record_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch, scope_ids, scope_virtual_ids
    ) -> None:
        monkeypatch.setenv(SOURCE_DIVERSITY_ENV_VAR, "true")

        assert active_source_diversity(scope_ids=scope_ids, scope_virtual_ids=scope_virtual_ids) is None

    def test_a_scope_of_several_sources_is_varied(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SOURCE_DIVERSITY_ENV_VAR, "true")

        assert active_source_diversity(scope_ids=["app-1", "app-2"], scope_virtual_ids=None) is not None

    def test_plain_ranking_holds_inside_its_block_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv(SOURCE_DIVERSITY_ENV_VAR, "true")

        with plain_ranking():
            assert active_source_diversity(scope_ids=None, scope_virtual_ids=None) is None
        assert active_source_diversity(scope_ids=None, scope_virtual_ids=None) is not None

    def test_a_source_is_the_connector_type(self) -> None:
        assert record_source({"connectorName": "github", "origin": "CONNECTOR"}) == "GITHUB"
        assert record_source({"origin": "UPLOAD"}) == "UPLOAD"
        assert record_source({}) is None
        assert record_source(None) is None


# ---- through search_with_filters ------------------------------------------

ORG = "org-1"
# Best first: seven pull requests, then a Slack thread, a Confluence page,
# more pull requests and a Jira issue.
_CORPUS: list[tuple[str, str]] = [
    *((f"pr{i}", "GITHUB") for i in range(7)),
    ("thread", "SLACK"),
    ("page", "CONFLUENCE"),
    *((f"pr{i}", "GITHUB") for i in range(7, 12)),
    ("issue", "JIRA"),
]
# A second query's own best matches: Slack threads that score below every
# match of the first query.
_CORPUS_B: list[tuple[str, str]] = [(f"chat{i}", "SLACK") for i in range(8)]
# Records the two-query tests below rank by hand.
_HAND: list[tuple[str, str]] = [
    *((f"gh{i}", "GITHUB") for i in range(4)),
    ("shared", "SLACK"), ("chat-a", "SLACK"), ("chat-b", "SLACK"),
    # Readable when checked by id, but missing from the user's record list.
    ("unlisted", "SLACK"),
]
_CONNECTOR = dict(_CORPUS + _CORPUS_B + _HAND)
_APP = {"GITHUB": "app-gh", "SLACK": "app-slack", "CONFLUENCE": "app-wiki", "JIRA": "app-jira"}


def _points(corpus: list[tuple[str, str]] = _CORPUS, top: float = 1.0) -> list[SearchResult]:
    return [
        SearchResult(
            id=f"{vrid}#0",
            score=top - rank / 100,
            payload={
                "page_content": f"{vrid} text",
                "metadata": {"virtualRecordId": vrid, "orgId": ORG, "blockId": "b0"},
                CONNECTOR_IDS_FIELD: [_APP[connector]],
            },
        )
        for rank, (vrid, connector) in enumerate(corpus)
    ]


def _point(vrid: str, score: float) -> SearchResult:
    return SearchResult(
        id=f"{vrid}#0",
        score=score,
        payload={
            "page_content": f"{vrid} text",
            "metadata": {"virtualRecordId": vrid, "orgId": ORG, "blockId": "b0"},
            CONNECTOR_IDS_FIELD: [_APP[_CONNECTOR[vrid]]],
        },
    )


@dataclass
class _FakeStore:
    points: list[SearchResult] = field(default_factory=_points)
    requests: list[HybridSearchRequest] = field(default_factory=list)
    # Each query's own ranking; a query not named here ranks `points`.
    by_query: dict[str, list[SearchResult]] = field(default_factory=dict)

    async def filter_collection(
        self, must: dict[str, Any] | None = None, should: dict[str, Any] | None = None
    ) -> dict[str, dict[str, Any]]:
        return {"must": dict(must or {}), "should": dict(should or {})}

    async def query_nearest_points(
        self, collection_name: str, requests: list[HybridSearchRequest]
    ) -> list[list[SearchResult]]:
        self.requests.extend(requests)
        out = []
        for req in requests:
            must, should = req.filter["must"], req.filter["should"]
            ranked = self.by_query.get(req.text_query or "", self.points)
            if "virtualRecordId" in must:
                allowed = set(must["virtualRecordId"])
                ranked = [p for p in ranked if p.payload["metadata"]["virtualRecordId"] in allowed]
            elif should.get(CONNECTOR_IDS_FIELD):
                apps = set(should[CONNECTOR_IDS_FIELD])
                ranked = [p for p in ranked if set(p.payload[CONNECTOR_IDS_FIELD]) & apps]
            out.append(ranked[: req.limit])
        return out


def _graph() -> AsyncMock:
    graph = AsyncMock()
    readable = set(_CONNECTOR)

    async def accessible(user_id: str, org_id: str, filters: dict | None = None, **_: object) -> dict[str, str]:
        apps = set((filters or {}).get("apps") or [])
        return {
            vrid: f"rec-{vrid}" for vrid in readable
            if vrid != "unlisted" and (not apps or _APP[_CONNECTOR[vrid]] in apps)
        }

    async def records(record_ids: list[str], org_id: str) -> list[dict[str, Any]]:
        out = []
        for rid in record_ids:
            vrid = rid.removeprefix("rec-")
            out.append({
                "_key": rid, "virtualRecordId": vrid, "origin": "CONNECTOR",
                "recordName": vrid, "mimeType": "text/plain",
                "connectorName": _CONNECTOR[vrid], "connectorId": _APP[_CONNECTOR[vrid]],
                "webUrl": f"https://example.test/{vrid}", "orgId": org_id,
            })
        return out

    async def verify(vrids: list[str], user_id: str, org_id: str, **_: object) -> dict[str, str]:
        return {vrid: f"rec-{vrid}" for vrid in vrids if vrid in readable}

    async def user(user_id: str, **_: object) -> dict[str, str]:
        return {"_key": user_id, "email": f"{user_id}@example.test"}

    async def check(user_key: str, org_id: str, *, virtual_record_ids=(), **_: object) -> AccessCheck:
        return AccessCheck(records_by_vrid={v: f"rec-{v}" for v in virtual_record_ids if v in readable})

    graph.check_access = AsyncMock(side_effect=check)

    graph.get_accessible_virtual_record_ids = AsyncMock(side_effect=accessible)
    graph.filter_accessible_virtual_record_ids = AsyncMock(side_effect=verify)
    graph.get_records_by_record_ids = AsyncMock(side_effect=records)
    graph.get_user_by_user_id = AsyncMock(side_effect=user)
    return graph


def _service(store: _FakeStore) -> mod.RetrievalService:
    service = mod.RetrievalService.__new__(mod.RetrievalService)
    service.logger = MagicMock()
    service.config_service = AsyncMock()
    service.graph_provider = _graph()
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
    service._keep_permitted_hits = AsyncMock(
        side_effect=lambda hits, enumerated, *a, **k: enumerated,
    )
    return service


@pytest.fixture(autouse=True)
def _isolated() -> Iterator[None]:
    mod._user_cache.clear()
    with patch(
        "app.agents.actions.knowledge_graph.location.resolve_ancestor_locations",
        AsyncMock(return_value={}),
    ), patch.object(mod, "describe_search_records", AsyncMock()):
        yield
    mod._user_cache.clear()


def _on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(SOURCE_DIVERSITY_ENV_VAR, "true")
    monkeypatch.setenv(WINDOW_ENV_VAR, "6")
    monkeypatch.setenv(PER_SOURCE_ENV_VAR, "3")
    monkeypatch.setenv(POOL_ENV_VAR, "20")


async def _search(
    store: _FakeStore, queries: tuple[str, ...] = ("q",), *, containers: bool = False, **kwargs: object
) -> dict:
    kwargs.setdefault("limit", 6)
    kwargs.setdefault("knowledge_search", True)
    service = _service(store)
    if containers:
        apps = frozenset(_APP.values())
        service._container_filter_enabled = AsyncMock(return_value=True)
        service.graph_provider.get_accessible_containers = AsyncMock(
            return_value=AccessibleContainers(app_ids=apps, app_ids_trusted=apps)
        )
    return await service.search_with_filters(list(queries), "alice", ORG, **kwargs)


def _knobs(monkeypatch: pytest.MonkeyPatch, *, window: int, per_source: int, pool: int) -> None:
    monkeypatch.setenv(SOURCE_DIVERSITY_ENV_VAR, "true")
    monkeypatch.setenv(WINDOW_ENV_VAR, str(window))
    monkeypatch.setenv(PER_SOURCE_ENV_VAR, str(per_source))
    monkeypatch.setenv(POOL_ENV_VAR, str(pool))


def _order(response: dict) -> list[str]:
    seen: list[str] = []
    for hit in response.get("searchResults", []):
        vrid = hit["metadata"]["virtualRecordId"]
        if vrid not in seen:
            seen.append(vrid)
    return seen


class TestTheSearchApi:
    async def test_off_a_search_is_exactly_what_it_was(self) -> None:
        store = _FakeStore()

        response = await _search(store)

        assert [req.limit for req in store.requests] == [6]
        assert _order(response) == [f"pr{i}" for i in range(6)]

    async def test_off_matches_a_search_without_the_rule(self) -> None:
        switched_off = await _search(_FakeStore())
        with patch.object(mod, "active_source_diversity", return_value=None):
            without = await _search(_FakeStore())

        assert switched_off == without

    async def test_on_no_source_fills_the_first_page_and_nothing_is_dropped(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _on(monkeypatch)
        store = _FakeStore()

        response = await _search(store)

        # The plain search as always, then the deeper read for candidates.
        assert [req.limit for req in store.requests] == [6, 20]
        assert _order(response) == [
            "pr0", "pr1", "pr2", "thread", "page", "issue",  # the first page
            "pr3", "pr4", "pr5",                              # pushed down, still there
        ]
        assert {r["virtualRecordId"] for r in response["records"]} == set(_order(response))
        assert set(response["virtual_to_record_map"]) == set(_order(response))

    @pytest.mark.parametrize("containers", [False, True])
    async def test_on_with_no_source_over_its_share_the_response_is_the_plain_one(
        self, monkeypatch: pytest.MonkeyPatch, containers: bool
    ) -> None:
        def store() -> _FakeStore:
            return _FakeStore(points=[
                _point("gh0", 0.99), _point("chat-a", 0.98), _point("gh1", 0.97),
                _point("chat-b", 0.96), _point("gh2", 0.50), _point("gh3", 0.40),
            ])

        plain = await _search(store(), containers=containers, limit=4)
        _knobs(monkeypatch, window=10, per_source=2, pool=6)
        varied = await _search(store(), containers=containers, limit=4)

        assert varied == plain

    async def test_on_every_record_a_query_returns_at_its_own_limit_is_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Two queries (the search API's rewrite runs several): the second
        query's own top matches score below the first query's deeper ones,
        and still come back, with the passages they had."""
        def store() -> _FakeStore:
            return _FakeStore(by_query={"b": _points(_CORPUS_B, top=0.5)})

        plain = await _search(store(), queries=("q", "b"))
        _on(monkeypatch)
        varied = await _search(store(), queries=("q", "b"))

        assert set(_order(plain)) == {*(f"pr{i}" for i in range(6)), *(f"chat{i}" for i in range(6))}
        assert set(_order(varied)) == set(_order(plain))
        assert _order(varied)[:6] == ["pr0", "pr1", "pr2", "chat0", "chat1", "chat2"]
        assert {h["metadata"]["point_id"] for h in varied["searchResults"]} == {
            h["metadata"]["point_id"] for h in plain["searchResults"]
        }

    @pytest.mark.parametrize("queries", [("q",), ("q", "b")])
    async def test_on_with_the_record_cut_the_plain_records_all_come_back(
        self, monkeypatch: pytest.MonkeyPatch, queries: tuple[str, ...]
    ) -> None:
        """The record cut keeps places for lower-scoring records; with the
        deeper pool those records must not be lost."""
        monkeypatch.setenv("FUSION_RECORD_CUT", "true")
        long_pr = [
            SearchResult(
                id=f"pr0#long{block}", score=1.0 - block / 1000,
                payload={
                    "page_content": f"pr0 block {block}",
                    "metadata": {"virtualRecordId": "pr0", "orgId": ORG, "blockId": f"b{block}"},
                    CONNECTOR_IDS_FIELD: ["app-gh"],
                },
            )
            for block in range(12)
        ]

        def store() -> _FakeStore:
            return _FakeStore(
                points=long_pr + _points(top=0.9),
                by_query={"b": _points(_CORPUS_B, top=0.5)},
            )

        plain = await _search(store(), queries=queries)
        _on(monkeypatch)
        varied = await _search(store(), queries=queries)

        assert set(_order(plain)) <= set(_order(varied))
        # The first page is as long as the plain result, up to the window of 6.
        page = _order(varied)[: min(6, len(_order(plain)))]
        sources = [_CONNECTOR[vrid] for vrid in page]
        assert max(sources.count(source) for source in set(sources)) <= 3
        if queries == ("q",):
            # Four plain records, all pull requests: one place goes to Slack.
            assert page == ["pr0", "pr1", "pr2", "thread"]

    @pytest.mark.parametrize("containers", [False, True])
    async def test_on_a_second_query_below_the_first_querys_pool_still_comes_back(
        self, monkeypatch: pytest.MonkeyPatch, containers: bool
    ) -> None:
        """Query A reads four pull requests that outscore everything of query
        B; the plain search reads two of each query, so B's two Slack records
        are in its result and must be in this one."""
        def store() -> _FakeStore:
            return _FakeStore(by_query={
                "a": [_point(f"gh{i}", 0.99 - i / 100) for i in range(4)],
                "b": [_point("chat-a", 0.50), _point("chat-b", 0.40)],
            })

        plain = await _search(store(), ("a", "b"), containers=containers, limit=2)
        _knobs(monkeypatch, window=10, per_source=4, pool=4)
        varied = await _search(store(), ("a", "b"), containers=containers, limit=2)

        assert set(_order(plain)) == {"gh0", "gh1", "chat-a", "chat-b"}
        assert _order(varied) == _order(plain)

    async def test_a_point_two_queries_found_keeps_the_score_the_plain_search_gives_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """`shared` is past query 1's top two and first for query 2. Read
        deeper, query 1 sees it too, at a low score; the plain search stores
        query 2's."""
        def store() -> _FakeStore:
            return _FakeStore(by_query={
                "a": [_point("gh0", 0.99), _point("gh1", 0.98), _point("shared", 0.40)],
                "b": [_point("shared", 0.90), _point("chat-a", 0.70)],
            })

        _knobs(monkeypatch, window=2, per_source=1, pool=4)
        varied = await _search(store(), ("a", "b"), limit=2)

        assert _order(varied)[:2] == ["gh0", "shared"]
        shared = [h for h in varied["searchResults"] if h["metadata"]["virtualRecordId"] == "shared"]
        assert [h["score"] for h in shared] == [0.90]

    async def test_on_a_plain_result_as_deep_as_the_pool_is_varied_without_a_second_read(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _knobs(monkeypatch, window=6, per_source=3, pool=20)
        store = _FakeStore()

        response = await _search(store, limit=20)

        assert [req.limit for req in store.requests] == [20]
        assert _order(response)[:6] == ["pr0", "pr1", "pr2", "thread", "page", "issue"]
        assert set(_order(response)) == {vrid for vrid, _ in _CORPUS}

    async def test_after_the_narrow_access_fallback_candidates_come_from_the_record_id_search(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The container page is replaced by the record-id search; the deeper
        read must not bring back a record only the container read returns."""
        mod.narrow_access_memo.clear()
        _knobs(monkeypatch, window=4, per_source=2, pool=10)
        store = _FakeStore(points=[
            *(_point(f"gh{i}", 0.99 - i / 100) for i in range(4)),
            _point("unlisted", 0.60), _point("chat-a", 0.50),
        ])
        service = _service(store)
        apps = frozenset(_APP.values())
        service._container_filter_enabled = AsyncMock(return_value=True)
        service.graph_provider.get_accessible_containers = AsyncMock(
            return_value=AccessibleContainers(app_ids=apps, app_ids_trusted=apps)
        )
        service._narrow_shortfall = MagicMock(return_value=True)
        try:
            response = await service.search_with_filters(
                ["q"], "alice", ORG, limit=4, knowledge_search=True,
            )
        finally:
            mod.narrow_access_memo.clear()

        assert "unlisted" not in _order(response)
        assert _order(response) == ["gh0", "gh1", "chat-a", "gh2", "gh3"]

    async def test_on_a_search_scoped_to_one_connector_is_left_alone(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        _on(monkeypatch)
        store = _FakeStore()

        response = await _search(store, filter_groups={"apps": ["app-gh"]})

        assert [req.limit for req in store.requests] == [6]
        assert _order(response) == [f"pr{i}" for i in range(6)]

    async def test_on_the_assistant_search_is_left_alone(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _on(monkeypatch)
        store = _FakeStore()

        response = await _search(store, knowledge_search=False)

        assert [req.limit for req in store.requests] == [6]
        assert _order(response) == [f"pr{i}" for i in range(6)]

    async def test_on_a_timeline_period_keeps_the_plain_ranking(self, monkeypatch: pytest.MonkeyPatch) -> None:
        _on(monkeypatch)
        store = _FakeStore()
        with plain_ranking():
            response = await _search(store)

        assert [req.limit for req in store.requests] == [6]
        assert _order(response) == [f"pr{i}" for i in range(6)]


async def test_every_search_of_a_timeline_keeps_the_plain_ranking(monkeypatch: pytest.MonkeyPatch) -> None:
    """The search API's timeline runs its whole-span and period searches with
    the plain ranking, whatever the switch says."""
    _on(monkeypatch)
    seen: list[SourceDiversity | None] = []

    async def search(time_range: dict[str, int] | None, limit: int) -> dict[str, Any]:
        seen.append(active_source_diversity(scope_ids=None, scope_virtual_ids=None))
        return {"status_code": 200, "searchResults": [], "records": [], "virtual_to_record_map": {}}

    await timeline_search_response(
        search, start_ms=1_700_000_000_000, end_ms=1_710_000_000_000, date_field="created",
    )

    assert seen
    assert seen == [None] * len(seen)
    assert active_source_diversity(scope_ids=None, scope_virtual_ids=None) is not None
