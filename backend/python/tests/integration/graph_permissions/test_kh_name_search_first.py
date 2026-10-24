"""A name search lists exactly what the full listing lists, filtered by name.

A global search with a name starts from the nodes whose name matches and walks
up from each to decide whether the user sees it, instead of listing everything
visible in the connector and filtering last. These tests hold it to the full
listing on both stores: the same rows, the same order, the same total, page by
page, for every word in the graph's names. The full listing is reached by
raising the match count over the name-first cap, which is the provider's own
fallback.

Three graphs: the scope-arms graph (every arm of the listing, with nodes whose
connectorId is another connector's or none), the acceptance graph as synced
(every node names its App) and the acceptance graph as built, where most nodes
name no App at all, so only the walks can place them.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import aiohttp
import pytest

from app.connectors.sources.localKB.handlers.kh_search import search_page
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

from . import test_hidden_above_declared as hidden
from .conftest import _wipe_neo4j
from .fixture_graph import ORG, USER_U, USER_V, build_fixture
from .loaders import arango_shape, load_into_arango, load_into_neo4j
from .test_provider_v2_search import production_shaped
from .test_provider_v3_listing_parity import PARENT_FIELDS
from .test_provider_v3_scope_arms import APP, KB_APP, _graph

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.connectors.sources.localKB.handlers.kh_search import SearchPage

pytestmark = pytest.mark.integration

SECRET = "kh-name-first-secret"


def _words(nodes: list[dict]) -> list[str]:
    names = " ".join(
        str(n["props"].get(key) or "")
        for n in nodes if n["kind"] in ("Record", "RecordGroup")
        for key in ("name", "recordName", "groupName")
    )
    words = sorted({w for w in re.findall(r"[a-z0-9]+", names.lower()) if len(w) >= 2})
    # A term inside a word and one that matches nothing.
    return [*words, "ecl", "zz-no-such-name"]


def _without_parent(rows: list[dict], multi_parent: set[str]) -> list[dict]:
    """A node with two hierarchy parents names one of them, the backend's pick."""
    return [{k: v for k, v in r.items() if r["id"] not in multi_parent or k not in PARENT_FIELDS}
            for r in rows]


def _multi_parent(edges: list[dict]) -> set[str]:
    parents: dict[str, set[str]] = {}
    for edge in edges:
        if edge["type"] == "NODE_RELATION":
            parents.setdefault(edge["to"], set()).add(edge["from"])
    return {n for n, p in parents.items() if len(p) > 1}


@pytest.fixture
def full_listing(monkeypatch) -> Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]]:
    """Run a coroutine with the name-first path switched off."""
    async def run(coro_fn: Callable[[], Awaitable[Any]]) -> Any:  # noqa: ANN401
        with monkeypatch.context() as patch:
            patch.setattr(IGraphDBProvider, "_KH_NAME_FIRST_MAX", -1)
            return await coro_fn()
    return run


@pytest.fixture(params=["neo4j", "arango"])
def provider(request, neo4j_provider, arango_provider) -> IGraphDBProvider:
    return neo4j_provider if request.param == "neo4j" else arango_provider


def _graphs() -> dict[str, tuple[list, list]]:
    return {"arms": _graph(), "synced": production_shaped(), "as-built": build_fixture(), "hidden": hidden._graph()}


async def _truncate_arango(settings: dict) -> None:
    base = f"{settings['url'].rstrip('/')}/_db/{settings['db']}/_api/collection"
    auth = aiohttp.BasicAuth(settings["username"], settings["password"])
    async with aiohttp.ClientSession(auth=auth) as session:
        async with session.get(f"{base}?excludeSystem=true") as resp:
            names = [c["name"] for c in (await resp.json())["result"]]
        for name in names:
            async with session.put(f"{base}/{name}/truncate") as resp:
                assert resp.status == 200, await resp.text()


@pytest.fixture(scope="module")
def load_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> Callable[[str], Awaitable[dict]]:
    """Load one of the graphs into both stores, replacing the one there."""
    loaded: dict[str, Any] = {}

    async def load(name: str) -> dict:
        if loaded.get("name") != name:
            nodes, edges = _graphs()[name]
            await _wipe_neo4j(neo4j_settings)
            await _truncate_arango(arango_settings)
            await load_into_neo4j(neo4j_settings, nodes, edges)
            await load_into_arango(arango_settings, *(arango_shape(nodes, edges) if name == "arms" else (nodes, edges)))
            loaded.update(name=name, graph={"nodes": nodes, "multi_parent": _multi_parent(edges)})
        return loaded["graph"]

    return load


# --------------------------------------------------------- one connector page

@pytest.fixture
async def arms_graph(load_graph) -> dict:
    return await load_graph("arms")


async def _connector_page(prov: IGraphDBProvider, app_id: str, user: str = USER_U, org: str = ORG,
                          **overrides: Any) -> dict:  # noqa: ANN401
    access = await prov.get_knowledge_hub_access_v3(user, org)
    kwargs = {
        "app_id": app_id, "org_id": org,
        "grantee_ids": access["grantee_ids"], "gated_app_ids": access["gated_app_ids"],
        "grants_by_connector": access["by_connector"],
        "limit": 500, "flatten": True, "sort_field": "name", "sort_dir": "ASC",
        "include_total": True,
    }
    kwargs.update(overrides)
    return await prov.get_knowledge_hub_connector_page_v3(**kwargs)


@pytest.mark.parametrize("app_id", [APP, KB_APP])
async def test_a_name_search_is_the_full_listing_filtered(arms_graph, provider, full_listing, app_id) -> None:
    everything = await _connector_page(provider, app_id)
    multi = arms_graph["multi_parent"]
    for term in _words(arms_graph["nodes"]):
        filters = {"search_query": term}
        got = await _connector_page(provider, app_id, filters=filters)
        want = await full_listing(lambda f=filters: _connector_page(provider, app_id, filters=f))
        expected_ids = [r["id"] for r in everything["rows"] if term in (r["name"] or "").lower()]
        assert [r["id"] for r in got["rows"]] == expected_ids, term
        assert _without_parent(got["rows"], multi) == _without_parent(want["rows"], multi), term
        assert (got["total"], got.get("counts")) == (want["total"], want.get("counts")), term


async def test_a_hidden_group_hides_its_declared_group_from_a_name_search(load_graph, provider, full_listing) -> None:
    """A granted declared group below a group that hides its children stays out."""
    graph = await load_graph("hidden")
    page = {"user": hidden.USER, "org": hidden.ORG}
    for term in _words(graph["nodes"]):
        kwargs = {**page, "filters": {"search_query": term}}
        got = await _connector_page(provider, hidden.APP, **kwargs)
        want = await full_listing(lambda k=kwargs: _connector_page(provider, hidden.APP, **k))
        assert [r["id"] for r in got["rows"]] == [r["id"] for r in want["rows"]], term
    code = await _connector_page(provider, hidden.APP, **page, filters={"search_query": "code"})
    assert code["rows"] == []


@pytest.mark.parametrize(("sort_field", "sort_dir"), [("name", "DESC"), ("updatedAt", "DESC"), ("nodeType", "ASC")])
async def test_a_name_search_keeps_every_sort(arms_graph, provider, full_listing, sort_field, sort_dir) -> None:
    for term in ("declared", "item", "group", "ecl"):
        kwargs = {"filters": {"search_query": term}, "sort_field": sort_field, "sort_dir": sort_dir}
        got = await _connector_page(provider, APP, **kwargs)
        want = await full_listing(lambda k=kwargs: _connector_page(provider, APP, **k))
        assert [r["id"] for r in got["rows"]] == [r["id"] for r in want["rows"]], term


async def test_a_name_search_with_other_filters(arms_graph, provider, full_listing) -> None:
    for filters in ({"search_query": "item", "node_types": ["record"]},
                    {"search_query": "group", "node_types": ["recordGroup"]},
                    {"search_query": "declared", "record_types": ["FILE"]}):
        got = await _connector_page(provider, APP, filters=filters)
        want = await full_listing(lambda f=filters: _connector_page(provider, APP, filters=f))
        assert [r["id"] for r in got["rows"]] == [r["id"] for r in want["rows"]], filters
        assert got["total"] == want["total"], filters


async def test_a_name_search_pages_by_cursor_like_the_full_listing(arms_graph, provider, full_listing) -> None:
    """Pages of two, resumed from each page's last row: no gap, no repeat."""
    async def walk() -> list[str]:
        seen: list[str] = []
        after = None
        for _ in range(30):
            page = await _connector_page(provider, APP, limit=2, after=after,
                                         filters={"search_query": "e"}, include_total=after is None)
            seen.extend(r["id"] for r in page["rows"])
            if not page["hasMore"]:
                return seen
            last = page["rows"][-1]
            after = {"nullRank": last["nullRank"], "sortKey": last["sortKey"], "id": last["id"]}
        pytest.fail("paging never finished")

    got = await walk()
    want = await full_listing(walk)
    assert got == want
    assert len(got) == len(set(got)) > 4


# ----------------------------------------------------------- global search

@pytest.fixture(params=["synced", "as-built"])
async def acceptance_graph(request, load_graph) -> dict:
    return await load_graph(request.param)


async def _search(prov: IGraphDBProvider, user: str, term: str, limit: int, token: str | None = None) -> "SearchPage":
    return await search_page(
        prov, user_key=user, user_id=user, org_id=ORG, secret=SECRET, limit=limit,
        filters={"search_query": term}, cursor_token=token,
    )


async def _all_pages(prov: IGraphDBProvider, user: str, term: str, limit: int) -> tuple[list[list[str]], int | None]:
    page = await _search(prov, user, term, limit)
    pages, total = [[r["id"] for r in page.rows]], page.total
    for _ in range(100):
        if not page.next_cursor:
            return pages, total
        page = await _search(prov, user, term, limit, page.next_cursor)
        pages.append([r["id"] for r in page.rows])
    pytest.fail("paging never finished")


@pytest.mark.parametrize("user", [USER_U, USER_V])
async def test_a_global_name_search_pages_like_the_full_listing(acceptance_graph, provider, full_listing, user) -> None:
    for term in _words(acceptance_graph["nodes"]):
        got = await _all_pages(provider, user, term, 3)
        want = await full_listing(lambda t=term: _all_pages(provider, user, t, 3))
        assert got == want, term
        flat = [i for page in got[0] for i in page]
        assert len(flat) == len(set(flat)), term
