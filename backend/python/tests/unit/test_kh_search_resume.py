"""A connector that stops looking before it has a page (a bound on its scan) says where it got to, and the listing goes on from there: no row is dropped or listed twice, and a page with no rows
still has a cursor. A provider that never says so (Neo4j, ArangoDB) gives the pages it always did."""
from __future__ import annotations

import asyncio

import pytest

from app.connectors.sources.localKB.handlers import knowledge_hub_service as khs
from app.connectors.sources.localKB.handlers.kh_merge import key_for
from app.connectors.sources.localKB.handlers.kh_search import SearchPage
from app.connectors.sources.localKB.handlers.knowledge_hub_service import (
    KnowledgeHubService,
)
from app.exceptions.graph_db_exceptions import KnowledgeHubListingNotReady

from .test_kh_search import (
    ORG,
    SECRET,
    USER_ID,
    USER_KEY,
    FakeProvider,
    row,
    search_page,
)


class StallingProvider(FakeProvider):
    """Looks at ``reach`` candidates per call and keeps those in ``readable``; at the bound it says where it got to."""

    def __init__(self, rows_by_connector: dict[str, list[dict]], readable: dict[str, set[str]], reach: int) -> None:
        super().__init__(rows_by_connector)
        self.readable, self.reach = readable, reach

    async def get_knowledge_hub_connector_page_v3(self, *, app_id, **kwargs) -> dict:
        self.calls.append({"app_id": app_id, **kwargs})
        rows = sorted(self.rows_by_connector[app_id], key=lambda r: key_for(r, descending=False))
        after = kwargs.get("after")
        if after is not None:
            edge = key_for(after, descending=False)
            rows = [r for r in rows if key_for(r, descending=False) > edge]
        seen, found = rows[:self.reach], []
        found = [r for r in seen if r["id"] in self.readable.get(app_id, set())][:kwargs["limit"] + 1]
        out = {"rows": found[:kwargs["limit"]], "hasMore": len(found) > kwargs["limit"], "total": None, "counts": None, "scope": None}
        if len(rows) > len(seen) and len(found) <= kwargs["limit"]:
            last = seen[-1]
            out["hasMore"], out["resume"] = True, {"nullRank": last["nullRank"], "sortKey": last["sortKey"], "id": last["id"]}
        return out


def _walk(provider: StallingProvider, limit: int) -> tuple[list[str], list[int]]:
    ids, sizes, token = [], [], None
    for _ in range(200):
        page = asyncio.run(search_page(provider, user_key=USER_KEY, user_id=USER_ID, org_id=ORG, secret=SECRET, limit=limit, cursor_token=token, filters=None))
        ids += [r["id"] for r in page.rows]
        sizes.append(len(page.rows))
        token = page.next_cursor
        if not token:
            return ids, sizes
    raise AssertionError("did not end")


def test_every_readable_row_is_listed_once_when_connectors_stop_scanning_early() -> None:
    c1 = [row(f"a{i:02d}", f"a{i:02d}") for i in range(30)]
    c2 = [row(f"b{i:02d}", f"b{i:02d}") for i in range(30)]
    readable = {"c1": {r["id"] for r in c1 if int(r["id"][1:]) % 7 == 0}, "c2": {r["id"] for r in c2 if int(r["id"][1:]) >= 25}}
    ids, sizes = _walk(StallingProvider({"c1": c1, "c2": c2}, readable, reach=4), limit=3)
    assert sorted(ids) == sorted(readable["c1"] | readable["c2"]) and len(ids) == len(set(ids))
    assert 0 in sizes, "the walk was meant to meet a page with no rows"


def test_a_page_with_no_rows_and_more_to_scan_has_a_cursor() -> None:
    c1 = [row(f"a{i:02d}", f"a{i:02d}") for i in range(20)]
    provider = StallingProvider({"c1": c1}, {"c1": {"a19"}}, reach=5)
    page = asyncio.run(search_page(provider, user_key=USER_KEY, user_id=USER_ID, org_id=ORG, secret=SECRET, limit=3))
    assert page.rows == [] and page.next_cursor


def test_a_provider_that_never_stops_early_gives_the_pages_it_always_did() -> None:
    rows = [row(f"r{i:02d}", f"n{i:02d}") for i in range(10)]
    ids, sizes = _walk(FakeProvider({"c1": rows}), limit=4)
    assert ids == [r["id"] for r in rows] and sizes == [4, 4, 2]


class NotReadyProvider(FakeProvider):
    def __init__(self, rows_by_connector: dict[str, list[dict]], not_ready: set[str]) -> None:
        super().__init__(rows_by_connector)
        self.not_ready = not_ready

    async def get_knowledge_hub_connector_page_v3(self, *, app_id, **kwargs) -> dict:
        if app_id in self.not_ready:
            raise KnowledgeHubListingNotReady
        return await super().get_knowledge_hub_connector_page_v3(app_id=app_id, **kwargs)


def test_a_connector_not_ready_is_left_out_and_named_on_every_page_the_others_are_listed_on() -> None:
    c1 = [row(f"a{i:02d}", f"a{i:02d}") for i in range(9)]
    provider = NotReadyProvider({"c1": c1, "c2": [row("b00", "b00")]}, {"c2"})
    ids, notices, token = [], [], None
    for _ in range(20):
        page = asyncio.run(search_page(provider, user_key=USER_KEY, user_id=USER_ID, org_id=ORG, secret=SECRET, limit=4, cursor_token=token))
        ids += [r["id"] for r in page.rows]
        notices.append(page.not_ready)
        token = page.next_cursor
        if not token:
            break
    assert ids == [r["id"] for r in c1] and len(notices) == 3 and all(n == ["c2"] for n in notices)


def test_when_every_connector_is_not_ready_the_page_is_refused() -> None:
    provider = NotReadyProvider({"c1": [row("a", "a")], "c2": [row("b", "b")]}, {"c1", "c2"})
    with pytest.raises(KnowledgeHubListingNotReady):
        asyncio.run(search_page(provider, user_key=USER_KEY, user_id=USER_ID, org_id=ORG, secret=SECRET, limit=4))


def _page(rows: list[dict], scan_cursor: str | None) -> SearchPage:
    return SearchPage(rows=rows, total=0, counts_by_type={}, start_index=0, end_index=0, next_cursor=scan_cursor or "end", prev_cursor=None, scan_cursor=scan_cursor)


def test_the_service_goes_on_from_empty_pages_within_its_budget_and_returns_the_empty_page_when_it_runs_out(monkeypatch) -> None:
    seen: list[str | None] = []

    async def fetch(token: str | None) -> SearchPage:
        seen.append(token)
        return _page([row("r", "r")], None) if token == "c3" else _page([], f"c{len(seen) + 1}")
    got = asyncio.run(KnowledgeHubService._walk_pages(None, fetch, None, 1))      # type: ignore[arg-type]
    assert [r["id"] for r in got.rows] == ["r"] and seen == [None, "c2", "c3"]
    seen.clear()
    monkeypatch.setattr(khs, "SCAN_BUDGET_S", 0.0)
    got = asyncio.run(KnowledgeHubService._walk_pages(None, fetch, None, 1))      # type: ignore[arg-type]
    assert got.rows == [] and got.next_cursor and seen == [None]
