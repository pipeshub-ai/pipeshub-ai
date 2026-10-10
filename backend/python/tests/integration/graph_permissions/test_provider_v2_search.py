"""Global search end to end: real connector pages, real merge, real cursors.

The unit tests drove `kh_search` against a fake provider, which proves the
orchestration and nothing about the queries it orchestrates. This runs it
against both real engines on the acceptance graph, where every connector is a
page of its own and the merge orders rows that came from eleven of them.

`search_page` takes the access context from the provider itself, so unlike the
rest of the harness these tests pass no grantee or gated-App list by hand —
which makes this the first thing to exercise a request the way the service will.

The graph is the acceptance fixture with every record, group and role naming its
App as `connectorId`, as a synced store does: a connector's page reads the
user's grants, its declared groups and the parents it may name by that id
(`test_provider_v3_scope_arms.py` measures what the unshaped fixture leaves dead).
"""

import pytest

from app.connectors.sources.localKB.handlers.kh_search import search_page
from app.utils.kh_cursor import CursorError

from .fixture_graph import build_fixture
from .loaders import load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

USER = "user-u"
ORG = "org-1"
SECRET = "kh-integration-secret"


def production_shaped() -> tuple[list[dict], list[dict]]:
    nodes, edges = build_fixture()
    kinds = {n["id"]: n["kind"] for n in nodes}
    parents: dict[str, set[str]] = {}
    for edge in edges:
        if edge["type"] == "NODE_RELATION":
            parents.setdefault(edge["to"], set()).add(edge["from"])
        elif edge["type"] == "BELONGS_TO":
            parents.setdefault(edge["from"], set()).add(edge["to"])

    def apps_above(node_id: str) -> set[str]:
        found: set[str] = set()
        seen, todo = {node_id}, [node_id]
        while todo:
            for parent in parents.get(todo.pop(), ()):
                if kinds[parent] == "App":
                    found.add(parent)
                elif parent not in seen:
                    seen.add(parent)
                    todo.append(parent)
        return found

    for node in nodes:
        if node["kind"] in ("Record", "RecordGroup"):
            (node["props"]["connectorId"],) = apps_above(node["id"])
    # A role is a connector's own entity: it names the connector it grants in.
    by_id = {n["id"]: n for n in nodes}
    for edge in edges:
        if edge["type"] == "PERMISSION" and kinds[edge["from"]] == "Role":
            by_id[edge["from"]]["props"]["connectorId"] = by_id[edge["to"]]["props"]["connectorId"]
    return nodes, edges


@pytest.fixture(scope="module")
async def loaded_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings):
    nodes, edges = production_shaped()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, nodes, edges)
    return {"nodes": nodes, "edges": edges}


@pytest.fixture(params=["neo4j", "arango"])
def provider(request, neo4j_provider, arango_provider):
    """Each test twice, once per backend, with both stores loaded either way."""
    return neo4j_provider if request.param == "neo4j" else arango_provider


async def _page(prov, **kwargs):
    return await search_page(
        prov, user_key=USER, user_id=USER, org_id=ORG, secret=SECRET, **kwargs
    )


def _ids(page) -> list[str]:
    return [row["id"] for row in page.rows]


async def _walk(prov, limit: int, **kwargs) -> list[list[str]]:
    """Every page, forward, as id lists."""
    pages: list[list[str]] = []
    page = await _page(prov, limit=limit, **kwargs)
    pages.append(_ids(page))
    for _ in range(50):
        if not page.next_cursor:
            break
        page = await _page(prov, limit=limit, cursor_token=page.next_cursor, **kwargs)
        pages.append(_ids(page))
    else:
        pytest.fail(f"paging never finished: {pages}")
    return pages


async def test_indexing_status_filters_across_partition_kinds(
    loaded_graph, provider
) -> None:
    """One status list, applied in a connector's page and a collection's.

    The fixture spreads the statuses on purpose so every clause of the test is
    load-bearing:

    * `pl-r6` FAILED, in a connector group -> in;
    * `kb-r3` QUEUED and `kb-r4` FAILED, in the collection -> in, which is what
      makes this "across partition kinds" rather than one kind twice;
    * `pl-r11` COMPLETED -> out **by the list**, so a filter that ignored the
      list and returned every indexed record would fail here;
    * `kb-f1` carries no status -> out.

    `kb-f1` is named "Folder 1" but is `record_type="FILE"`, which matters: the
    filter also gates on `nodeType == "record"`, so a genuinely folder-typed
    node would be excluded twice over and the no-status clause would go
    untested.

    The assertion is a set, not a containment check -- asserting only that the
    three expected ids are present would hold just as well if nothing were
    filtered at all.
    """
    page = await _page(
        provider, limit=100, filters={"indexing_status": ["FAILED", "QUEUED"]}
    )
    assert set(_ids(page)) == {"pl-r6", "kb-r3", "kb-r4"}, sorted(_ids(page))


async def test_a_global_search_returns_each_node_once(loaded_graph, provider) -> None:
    """`swm-x` has two parents and belongs in the page once.

    The drive parent wins over Shared with Me, and the same node reached
    from both sides must not produce two rows. An App is the page's partition,
    never a row of it: it is named as the parent of what hangs off it.
    """
    whole = await _page(provider, limit=500)
    ids = _ids(whole)
    assert len(ids) == len(set(ids)), "a node was returned twice"

    placed = {row["id"]: row["parentId"] for row in whole.rows}
    assert placed["swm-x"] == "swm-f1", placed["swm-x"]
    assert "pl-app" not in placed and placed["pl-rg1"] == "pl-app"
    assert "pl-r6" in placed, "a granted node below a gap is reachable"
    assert not {"gate-rg1", "gate-r3", "kb2-r1"} & set(placed), "ungated content must stay out"


async def test_paging_reproduces_the_single_page_order(loaded_graph, provider) -> None:
    """Pages of three, merged across every connector, equal one large page exactly."""
    whole = _ids(await _page(provider, limit=500))
    assert len(whole) > 10, whole

    paged = [row_id for page in await _walk(provider, 3) for row_id in page]
    assert paged == whole, f"paged={paged}\nwhole={whole}"


async def test_every_page_but_the_last_is_full(loaded_graph, provider) -> None:
    """A short page mid-walk means a connector ran dry and was not refilled."""
    pages = await _walk(provider, 4)
    assert all(len(page) == 4 for page in pages[:-1]), [len(p) for p in pages]
    assert 0 < len(pages[-1]) <= 4


async def test_previous_pages_walk_back_through_the_same_pages(
    loaded_graph, provider
) -> None:
    """Across connectors: going back re-queries them all, and still lands
    on exactly the pages the user came from."""
    forward = await _walk(provider, 3)
    assert len(forward) >= 3, forward

    page = await _page(provider, limit=3)
    for _ in range(len(forward) - 1):
        page = await _page(provider, limit=3, cursor_token=page.next_cursor)

    back: list[list[str]] = []
    while page.prev_cursor:
        page = await _page(provider, limit=3, cursor_token=page.prev_cursor)
        back.append(_ids(page))
    assert back == list(reversed(forward[:-1])), f"back={back}\nforward={forward}"


async def test_indices_follow_the_page_position(loaded_graph, provider) -> None:
    """`startIndex`/`endIndex` count items, not pages."""
    first = await _page(provider, limit=3)
    second = await _page(provider, limit=3, cursor_token=first.next_cursor)
    assert (first.start_index, first.end_index) == (1, 3)
    assert (second.start_index, second.end_index) == (4, 6)

    back = await _page(provider, limit=3, cursor_token=second.prev_cursor)
    assert (back.start_index, back.end_index) == (1, 3)
    assert _ids(back) == _ids(first)


async def test_the_total_and_counts_describe_the_whole_result(
    loaded_graph, provider
) -> None:
    """Counted once over every connector's page, then carried."""
    whole = await _page(provider, limit=500)
    first = await _page(provider, limit=3)
    assert first.total == len(whole.rows)
    assert sum(first.counts_by_type.values()) == first.total
    assert first.counts_by_type["recordGroup"] >= 1 and "app" not in first.counts_by_type

    second = await _page(provider, limit=3, cursor_token=first.next_cursor)
    assert (second.total, second.counts_by_type) == (first.total, first.counts_by_type)


async def test_a_filter_applies_in_every_partition(loaded_graph, provider) -> None:
    """The term matches the node, never the path to it, on both engines."""
    whole = await _page(provider, limit=500)
    expected = {
        row["id"] for row in whole.rows
        if "granted" in (row.get("name") or "").lower()
    }
    assert len(expected) > 1, expected

    filtered = await _page(provider, limit=500, filters={"search_query": "GRANTED"})
    assert set(_ids(filtered)) == expected
    assert filtered.total == len(expected)


async def test_another_users_cursor_is_refused(loaded_graph, provider) -> None:
    """It raises, so the caller answers 400 rather than a first page."""
    first = await _page(provider, limit=2)
    with pytest.raises(CursorError):
        await search_page(
            provider, user_key="user-v", user_id="user-v", org_id=ORG,
            limit=2, cursor_token=first.next_cursor, secret=SECRET,
        )


async def test_both_backends_return_the_same_pages(
    loaded_graph, neo4j_provider, arango_provider
) -> None:
    """One search, one order, one set of page boundaries — on either store."""
    cypher = await _walk(neo4j_provider, 3)
    aql = await _walk(arango_provider, 3)
    assert cypher == aql, f"neo4j={cypher}\narango={aql}"
