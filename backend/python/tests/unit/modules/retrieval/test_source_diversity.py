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

        order = self.rule.order(records, shown=6, source_of=_source)

        assert order == ["g1", "g2", "s1", "c1", "s2", "g3", "g4"]
        # Every record the plain search returned is still there.
        assert set(records[:6]) <= set(order)

    def test_records_of_one_source_keep_their_order(self) -> None:
        records = ["g1", "s1", "g2", "g3", "s2", "g4", "s3", "s4", "c1"]

        order = self.rule.order(records, shown=6, source_of=_source)

        for source in "gsc":
            assert [r for r in order if r[0] == source] == [r for r in records if r[0] == source and r in order]

    def test_a_record_below_the_plain_results_comes_in_only_on_the_first_page(self) -> None:
        records = ["g1", "g2", "g3", "s1", "g4", "s2", "s3", "s4", "c1"]

        order = self.rule.order(records, shown=4, source_of=_source)

        # A page of four (what the plain search showed): two of each source,
        # then the GitHub record it pushed down. s3, s4 and c1 stay out.
        assert order == ["g1", "g2", "s1", "s2", "g3"]

    def test_one_source_keeps_the_plain_order(self) -> None:
        records = [f"g{i}" for i in range(12)]

        assert self.rule.order(records, shown=10, source_of=_source) == records[:10]

    def test_a_record_of_unknown_source_is_never_held_back(self) -> None:
        records = ["?a", "?b", "?c", "g1", "g2", "g3", "s1"]

        order = self.rule.order(records, shown=6, source_of=_source)

        assert order == ["?a", "?b", "?c", "g1", "g2", "s1", "g3"]

    def test_nothing_to_vary_keeps_the_plain_order(self) -> None:
        records = ["g1", "s1", "c1", "g2", "s2", "c2", "g3"]

        assert self.rule.order(records, shown=7, source_of=_source) == records

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
_CONNECTOR = dict(_CORPUS)
_APP = {"GITHUB": "app-gh", "SLACK": "app-slack", "CONFLUENCE": "app-wiki", "JIRA": "app-jira"}


def _points() -> list[SearchResult]:
    return [
        SearchResult(
            id=f"{vrid}#0",
            score=1.0 - rank / 100,
            payload={
                "page_content": f"{vrid} text",
                "metadata": {"virtualRecordId": vrid, "orgId": ORG, "blockId": "b0"},
                CONNECTOR_IDS_FIELD: [_APP[connector]],
            },
        )
        for rank, (vrid, connector) in enumerate(_CORPUS)
    ]


@dataclass
class _FakeStore:
    points: list[SearchResult] = field(default_factory=_points)
    requests: list[HybridSearchRequest] = field(default_factory=list)

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
            allowed = set(req.filter["must"].get("virtualRecordId") or [])
            out.append([p for p in self.points if p.payload["metadata"]["virtualRecordId"] in allowed][: req.limit])
        return out


def _graph() -> AsyncMock:
    graph = AsyncMock()
    readable = {vrid for vrid, _ in _CORPUS}

    async def accessible(user_id: str, org_id: str, filters: dict | None = None, **_: object) -> dict[str, str]:
        apps = set((filters or {}).get("apps") or [])
        return {
            vrid: f"rec-{vrid}" for vrid in readable
            if not apps or _APP[_CONNECTOR[vrid]] in apps
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


async def _search(store: _FakeStore, **kwargs: object) -> dict:
    kwargs.setdefault("limit", 6)
    kwargs.setdefault("knowledge_search", True)
    return await _service(store).search_with_filters(["q"], "alice", ORG, **kwargs)


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

        assert [req.limit for req in store.requests] == [20]
        assert _order(response) == [
            "pr0", "pr1", "pr2", "thread", "page", "issue",  # the first page
            "pr3", "pr4", "pr5",                              # pushed down, still there
        ]
        assert {r["virtualRecordId"] for r in response["records"]} == set(_order(response))
        assert set(response["virtual_to_record_map"]) == set(_order(response))

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
