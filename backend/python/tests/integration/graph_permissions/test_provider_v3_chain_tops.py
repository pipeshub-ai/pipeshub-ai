"""Chain-tops in browse: a node shared on its own below a gap is listed under its
own record group when the user can open it, otherwise directly under the App.

One graph, production-shaped (every node carries the App's id as connectorId),
holds each case as its own subtree under the App. Every assertion is against
known values and runs on both backends: two engines built from one set of
builders can share a mistake, so they are not each other's oracle here.

Notation in the graph's comments: ``[O,inh]`` OPEN and inheriting from its parent,
``[O,noinh]`` OPEN without, ``[R!]`` RESTRICTED, ``g:U`` a grant to the user.
"""

from __future__ import annotations

from typing import Any

import pytest

from .fixture_graph import DRIVE, GROUP_G, ORG, USER_U, _principals, app, bt, ip, nr, perm, rec, rg, user_app
from .loaders import arango_shape, load_into_arango, load_into_neo4j

APP = "ct-app"
RGL = "RECORD_GROUP_LEVEL"
GROUP = dict(group_type="DRIVE", connector=DRIVE, connectorId=APP)
ITEM = dict(record_type="FILE", connector_id=APP)


def _graph() -> tuple[list, list]:
    principal_nodes, principal_edges = _principals()
    nodes = principal_nodes + [
        app(APP, "Chain-top connector", connector=DRIVE, app_group="Google Workspace"),
        # ac21: App → RG1[O,noinh] → R3[O,g:U]
        rg("ac21-rg1", "AC21 group", **GROUP), rec("ac21-r3", "AC21 shared", **ITEM),
        # ac43: App → RG1[R!,inh] → R3[O,g:U]
        rg("ac43-rg1", "AC43 group", rule="RESTRICTED", **GROUP), rec("ac43-r3", "AC43 shared", **ITEM),
        # nv07: App → RG1[O,inh] → RG2[O,noinh] → R6[O,g:U]
        rg("nv07-rg1", "NV07 open group", **GROUP), rg("nv07-rg2", "NV07 closed group", **GROUP),
        rec("nv07-r6", "NV07 shared", **ITEM),
        # nv08: App → RG1[O,inh] → R6[O,inh] → R10[O,noinh] → R11[O,g:U]
        rg("nv08-rg1", "NV08 group", **GROUP), rec("nv08-r6", "NV08 page", **ITEM),
        rec("nv08-r10", "NV08 closed page", **ITEM), rec("nv08-r11", "NV08 shared", **ITEM),
        # nv10: App → RG1[O,inh] → RG2[O,inh] → R3[O,noinh] → R6[O,g:U]
        rg("nv10-rg1", "NV10 outer group", **GROUP), rg("nv10-rg2", "NV10 inner group", **GROUP),
        rec("nv10-r3", "NV10 closed page", **ITEM), rec("nv10-r6", "NV10 shared", **ITEM),
        # nv11: App → RG0[O,noinh] → RG1[O,g:U] → R3[O,noinh] → R6[O,g:U]
        rg("nv11-rg0", "NV11 closed group", **GROUP), rg("nv11-rg1", "NV11 granted group", **GROUP),
        rec("nv11-r3", "NV11 closed page", **ITEM), rec("nv11-r6", "NV11 shared", **ITEM),
        # nv12: App → RG1[O,noinh] → R6[O,g:U] → R10[O,noinh] → R11[O,g:U]
        rg("nv12-rg1", "NV12 closed group", **GROUP), rec("nv12-r6", "NV12 shared top", **ITEM),
        rec("nv12-r10", "NV12 closed page", **ITEM), rec("nv12-r11", "NV12 shared below", **ITEM),
        # nv16: App → RG1[O,inh] → RG2[O,noinh] → RG3[O,g:U] → R7[O,inh]
        rg("nv16-rg1", "NV16 open group", **GROUP), rg("nv16-rg2", "NV16 closed group", **GROUP),
        rg("nv16-rg3", "NV16 shared group", **GROUP), rec("nv16-r7", "NV16 item", **ITEM),
        # nv21: App → RGd[O,noinh] → R6[O,g:group]
        rg("nv21-rgd", "NV21 drive", **GROUP), rec("nv21-r6", "NV21 shared with a group", **ITEM),
        # A granted node under an open parent is listed there, nowhere else.
        rg("op-g", "Open parent group", **GROUP), rec("op-f", "Open parent folder", **ITEM),
        rec("op-r", "Granted under an open parent", **ITEM),
        # Two hierarchy parents, one open (Shared with Me).
        rg("sw-inbox", "Shared with me", isInternal=True, **GROUP),
        rg("sw-drive", "Someone else's drive", **GROUP), rec("sw-r", "Shared both ways", **ITEM),
        # No parent and no group.
        rec("orphan-r", "Orphan shared", **ITEM),
        # Own group outside the App.
        rg("fl-group", "Floating group", **GROUP), rg("fl-x", "Closed parent", **GROUP),
        rec("fl-r", "Own group outside the App", **ITEM),
        # Beneath a group that hides its children, and in such a group.
        rg("hd-g", "Hiding group", hideChildren=True, **GROUP), rg("hd-x", "Closed below hiding", **GROUP),
        rec("hd-r", "Granted beneath hiding", **ITEM), rec("hd-m", "Granted in the hiding group", **ITEM),
        # A declaration the user holds: its granted records are under it.
        rg("dc-g", "Granted declaration", permissionModel=RGL, **GROUP),
        rec("dc-r", "Granted in the declaration", **ITEM),
        rec("dc-loose", "Granted, belongs to it, no hierarchy edge", **ITEM),
        # Outlook's shape: a message in a colleague's mail folder, sent to the user.
        rg("ol-box", "Colleague mailbox", **GROUP),
        rg("ol-folder", "Colleague inbox", permissionModel=RGL, **GROUP),
        rec("ol-msg", "Message to the user", record_type="MAIL", connector_id=APP),
        # STRICT and RESTRICTED grants below a gap open nothing.
        rg("sr-x", "Closed group", **GROUP),
        rec("sr-strict", "Strict grant below a gap", rule="STRICT", **ITEM),
        rec("sr-restricted", "Restricted grant below a gap", rule="RESTRICTED", **ITEM),
        # Deleted, placeholder and other-org grants below a gap.
        rg("ex-x", "Closed group", **GROUP),
        rec("ex-deleted", "Deleted grant", isDeleted=True, **ITEM),
        rec("ex-placeholder", "Placeholder grant", isPlaceholder=True, **ITEM),
        rec("ex-otherorg", "Other org grant", orgId="org-2", **ITEM),
        # A granted direct child of the App, listed once.
        rec("dir-r", "Granted child of the App", **ITEM),
    ]
    edges = principal_edges + [
        user_app(USER_U, APP),

        nr(APP, "ac21-rg1"), nr("ac21-rg1", "ac21-r3"), ip("ac21-r3", "ac21-rg1"),
        bt("ac21-r3", "ac21-rg1"), perm(USER_U, "ac21-r3"),

        nr(APP, "ac43-rg1"), ip("ac43-rg1", APP), nr("ac43-rg1", "ac43-r3"), ip("ac43-r3", "ac43-rg1"),
        bt("ac43-r3", "ac43-rg1"), perm(USER_U, "ac43-r3"),

        nr(APP, "nv07-rg1"), ip("nv07-rg1", APP), nr("nv07-rg1", "nv07-rg2"),
        nr("nv07-rg2", "nv07-r6"), ip("nv07-r6", "nv07-rg2"), bt("nv07-r6", "nv07-rg2"),
        perm(USER_U, "nv07-r6"),

        nr(APP, "nv08-rg1"), ip("nv08-rg1", APP),
        nr("nv08-rg1", "nv08-r6"), ip("nv08-r6", "nv08-rg1"), bt("nv08-r6", "nv08-rg1"),
        nr("nv08-r6", "nv08-r10"), bt("nv08-r10", "nv08-rg1"),
        nr("nv08-r10", "nv08-r11"), ip("nv08-r11", "nv08-r10"), bt("nv08-r11", "nv08-rg1"),
        perm(USER_U, "nv08-r11"),

        nr(APP, "nv10-rg1"), ip("nv10-rg1", APP), nr("nv10-rg1", "nv10-rg2"), ip("nv10-rg2", "nv10-rg1"),
        nr("nv10-rg2", "nv10-r3"), bt("nv10-r3", "nv10-rg2"),
        nr("nv10-r3", "nv10-r6"), ip("nv10-r6", "nv10-r3"), bt("nv10-r6", "nv10-rg2"),
        perm(USER_U, "nv10-r6"),

        nr(APP, "nv11-rg0"), nr("nv11-rg0", "nv11-rg1"), perm(USER_U, "nv11-rg1"),
        nr("nv11-rg1", "nv11-r3"), bt("nv11-r3", "nv11-rg1"),
        nr("nv11-r3", "nv11-r6"), ip("nv11-r6", "nv11-r3"), bt("nv11-r6", "nv11-rg1"),
        perm(USER_U, "nv11-r6"),

        nr(APP, "nv12-rg1"), nr("nv12-rg1", "nv12-r6"), ip("nv12-r6", "nv12-rg1"), bt("nv12-r6", "nv12-rg1"),
        perm(USER_U, "nv12-r6"),
        nr("nv12-r6", "nv12-r10"), bt("nv12-r10", "nv12-rg1"),
        nr("nv12-r10", "nv12-r11"), ip("nv12-r11", "nv12-r10"), bt("nv12-r11", "nv12-rg1"),
        perm(USER_U, "nv12-r11"),

        nr(APP, "nv16-rg1"), ip("nv16-rg1", APP), nr("nv16-rg1", "nv16-rg2"),
        nr("nv16-rg2", "nv16-rg3"), perm(USER_U, "nv16-rg3"),
        nr("nv16-rg3", "nv16-r7"), ip("nv16-r7", "nv16-rg3"), bt("nv16-r7", "nv16-rg3"),

        nr(APP, "nv21-rgd"), nr("nv21-rgd", "nv21-r6"), ip("nv21-r6", "nv21-rgd"), bt("nv21-r6", "nv21-rgd"),
        perm(GROUP_G, "nv21-r6", grant_type="GROUP"),

        nr(APP, "op-g"), ip("op-g", APP), nr("op-g", "op-f"), ip("op-f", "op-g"), bt("op-f", "op-g"),
        nr("op-f", "op-r"), ip("op-r", "op-f"), bt("op-r", "op-g"), perm(USER_U, "op-r"),

        nr(APP, "sw-inbox"), perm(USER_U, "sw-inbox", role="OWNER"),
        nr(APP, "sw-drive"), nr("sw-drive", "sw-r"), ip("sw-r", "sw-drive"), bt("sw-r", "sw-drive"),
        nr("sw-inbox", "sw-r"), bt("sw-r", "sw-inbox"), perm(USER_U, "sw-r"),

        perm(USER_U, "orphan-r"),

        nr(APP, "fl-x"), nr("fl-x", "fl-r"), ip("fl-r", "fl-x"), bt("fl-r", "fl-group"), perm(USER_U, "fl-r"),

        nr(APP, "hd-g"), ip("hd-g", APP), nr("hd-g", "hd-x"),
        nr("hd-x", "hd-r"), ip("hd-r", "hd-x"), bt("hd-r", "hd-x"), perm(USER_U, "hd-r"),
        nr("hd-x", "hd-m"), bt("hd-m", "hd-g"), perm(USER_U, "hd-m"),

        nr(APP, "dc-g"), perm(USER_U, "dc-g"),
        nr("dc-g", "dc-r"), ip("dc-r", "dc-g"), bt("dc-r", "dc-g"), perm(USER_U, "dc-r"),
        bt("dc-loose", "dc-g"), perm(USER_U, "dc-loose"),

        nr(APP, "ol-box"), nr("ol-box", "ol-folder"),
        nr("ol-folder", "ol-msg"), ip("ol-msg", "ol-folder"), bt("ol-msg", "ol-folder"),
        perm(USER_U, "ol-msg"),

        nr(APP, "sr-x"), nr("sr-x", "sr-strict"), bt("sr-strict", "sr-x"), perm(USER_U, "sr-strict"),
        nr("sr-x", "sr-restricted"), ip("sr-restricted", "sr-x"), bt("sr-restricted", "sr-x"),
        perm(USER_U, "sr-restricted"),

        nr(APP, "ex-x"),
        nr("ex-x", "ex-deleted"), bt("ex-deleted", "ex-x"), perm(USER_U, "ex-deleted"),
        nr("ex-x", "ex-placeholder"), bt("ex-placeholder", "ex-x"), perm(USER_U, "ex-placeholder"),
        nr("ex-x", "ex-otherorg"), bt("ex-otherorg", "ex-x"), perm(USER_U, "ex-otherorg"),

        nr(APP, "dir-r"), perm(USER_U, "dir-r"),
    ]
    return nodes, edges


# What USER_U sees when browsing the App: the direct children the walk lists,
# then the chain-tops whose own group cannot be opened.
APP_CHILDREN = {
    "nv07-rg1", "nv08-rg1", "nv10-rg1", "nv16-rg1", "op-g", "sw-inbox", "hd-g", "dc-g", "dir-r",
}
APP_CHAIN_TOPS = {
    "ac21-r3", "ac43-r3", "nv07-r6", "nv11-rg1", "nv12-r6", "nv12-r11", "nv16-rg3", "nv21-r6",
    "orphan-r", "ol-msg",
}
# Visible, but never listed under the App.
NEVER_AT_APP = {
    "nv08-r11", "nv10-r6", "nv11-r6", "op-r", "sw-r", "fl-r", "dc-r", "dc-loose", "nv16-r7",
}
# Hidden from everything.
HIDDEN = {
    "ac21-rg1", "ac43-rg1", "nv07-rg2", "nv08-r10", "nv10-r3", "nv11-rg0", "nv11-r3", "nv12-rg1",
    "nv12-r10", "nv16-rg2", "nv21-rgd", "sw-drive", "fl-x", "fl-group", "hd-x", "hd-r", "hd-m",
    "ol-box", "ol-folder", "sr-x", "sr-strict", "sr-restricted", "ex-x", "ex-deleted",
    "ex-placeholder", "ex-otherorg",
}
# Visible in the flatten under the App, though browse lists them nowhere: their own
# group is not under the App, so the App fallback does not apply and no openable
# parent exists. The one known exception to "browse lists a row at its parentId".
UNPLACED = {"fl-r"}


@pytest.fixture(scope="module")
async def ct_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> dict:
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))
    return {"nodes": nodes}


@pytest.fixture(params=["neo4j", "arango"])
def provider(request, neo4j_provider, arango_provider, ct_graph):
    return neo4j_provider if request.param == "neo4j" else arango_provider


async def _page(provider, **overrides: Any) -> dict:  # noqa: ANN401
    access = await provider.get_knowledge_hub_access_v3(USER_U, ORG)
    kwargs = {
        "app_id": APP, "org_id": ORG,
        "grantee_ids": access["grantee_ids"],
        "gated_app_ids": access["gated_app_ids"],
        "grants_by_connector": access["by_connector"],
        "limit": 500, "flatten": False, "sort_field": "name", "sort_dir": "ASC",
        "include_total": True,
    }
    kwargs.update(overrides)
    return await provider.get_knowledge_hub_connector_page_v3(**kwargs)


async def _browse(provider, start_id: str, start_type: str = "recordGroup", **overrides: Any) -> dict:  # noqa: ANN401
    return await _page(provider, start_id=start_id, start_type=start_type, include_scope=True, **overrides)


def _ids(page: dict) -> list[str]:
    return [row["id"] for row in page["rows"]]


def _parents(page: dict) -> dict[str, str]:
    return {row["id"]: row["parentId"] for row in page["rows"]}


def _trail(page: dict) -> list[str]:
    return [crumb["id"] for crumb in page["scope"]["breadcrumbs"]]


async def test_the_app_lists_its_children_and_the_chain_tops_whose_group_is_closed(provider) -> None:
    page = await _page(provider)
    assert set(_ids(page)) == APP_CHILDREN | APP_CHAIN_TOPS
    assert len(_ids(page)) == len(set(_ids(page))), "a node is listed once"
    assert page["total"] == len(APP_CHILDREN | APP_CHAIN_TOPS)
    assert set(_parents(page).values()) == {APP}


async def test_nothing_hidden_or_placed_elsewhere_reaches_the_app(provider) -> None:
    listed = set(_ids(await _page(provider)))
    assert listed & (NEVER_AT_APP | HIDDEN) == set()


async def test_ac21_ac43_nv07_chain_top_under_the_app(provider) -> None:
    for node in ("ac21-r3", "ac43-r3", "nv07-r6"):
        crumbs = _trail(await _browse(provider, node, "record"))
        assert crumbs == [APP, node], node
    assert set(_ids(await _browse(provider, "nv07-rg1"))) == set(), "RG1 lists neither RG2 nor R6"


async def test_nv08_own_group_not_the_nearest_accessible_ancestor(provider) -> None:
    group = await _browse(provider, "nv08-rg1")
    assert set(_ids(group)) == {"nv08-r6", "nv08-r11"}
    assert _parents(group)["nv08-r11"] == "nv08-rg1"
    assert set(_ids(await _browse(provider, "nv08-r6", "record"))) == set()
    assert _trail(await _browse(provider, "nv08-r11", "record")) == [APP, "nv08-rg1", "nv08-r11"]


async def test_nv10_the_innermost_own_group(provider) -> None:
    assert _ids(await _browse(provider, "nv10-rg2")) == ["nv10-r6"]
    assert "nv10-r6" not in _ids(await _browse(provider, "nv10-rg1"))
    assert _trail(await _browse(provider, "nv10-r6", "record")) == [APP, "nv10-rg1", "nv10-rg2", "nv10-r6"]


async def test_nv11_own_group_open_only_by_its_grant(provider) -> None:
    group = await _browse(provider, "nv11-rg1")
    assert _ids(group) == ["nv11-r6"]
    assert _parents(group) == {"nv11-r6": "nv11-rg1"}
    assert _trail(group) == [APP, "nv11-rg1"]
    assert _trail(await _browse(provider, "nv11-r6", "record")) == [APP, "nv11-rg1", "nv11-r6"]


async def test_nv12_a_chain_top_inside_another_chain_tops_subtree(provider) -> None:
    assert set(_ids(await _browse(provider, "nv12-r6", "record"))) == set(), "R6 does not list R11"
    assert _trail(await _browse(provider, "nv12-r11", "record")) == [APP, "nv12-r11"]


async def test_nv16_a_group_below_a_gap(provider) -> None:
    assert set(_ids(await _browse(provider, "nv16-rg1"))) == set()
    assert _ids(await _browse(provider, "nv16-rg3")) == ["nv16-r7"]


async def test_granted_under_an_open_parent_stays_under_it(provider) -> None:
    assert _ids(await _browse(provider, "op-f", "record")) == ["op-r"]
    assert "op-r" not in _ids(await _browse(provider, "op-g"))


async def test_two_parents_one_open_lists_only_under_the_open_one(provider) -> None:
    inbox = await _browse(provider, "sw-inbox")
    assert _ids(inbox) == ["sw-r"]
    assert _parents(inbox) == {"sw-r": "sw-inbox"}


async def test_a_declaration_lists_its_granted_records_once(provider) -> None:
    page = await _browse(provider, "dc-g")
    assert sorted(_ids(page)) == ["dc-loose", "dc-r"]
    assert set(_parents(page).values()) == {"dc-g"}


async def test_the_flatten_names_the_placement_parent(provider) -> None:
    """Every flatten row's parentId is where browse lists it."""
    parents = _parents(await _page(provider, flatten=True))
    expected = {
        "ac21-r3": APP, "nv07-r6": APP, "nv12-r11": APP, "nv16-rg3": APP, "ol-msg": APP,
        "nv08-r11": "nv08-rg1", "nv10-r6": "nv10-rg2", "nv11-r6": "nv11-rg1", "nv11-rg1": APP,
        "op-r": "op-f", "sw-r": "sw-inbox", "dc-loose": "dc-g",
    }
    assert {node: parents.get(node) for node in expected} == expected


async def test_every_flatten_row_is_listed_at_its_parent_and_its_trail_agrees(provider) -> None:
    """Over the whole graph: browsing an item's parentId lists it, and following
    parentIds up to the App gives its breadcrumbs."""
    flatten = await _page(provider, flatten=True)
    parent_of = {row["id"]: (row["parentId"], row["parentType"]) for row in flatten["rows"]}
    assert set(parent_of) & HIDDEN == set()
    for node, (parent_id, parent_type) in sorted(parent_of.items()):
        if node in UNPLACED:
            continue
        listing = await _page(provider) if parent_type == "app" else await _browse(
            provider, parent_id, "recordGroup" if parent_type == "recordGroup" else "record",
        )
        assert _parents(listing).get(node) == parent_id, f"{node} not listed at {parent_id}"
        chain = [node]
        while chain[-1] != APP:
            chain.append(parent_of[chain[-1]][0])
        start_type = "recordGroup" if any(
            n["id"] == node and n["kind"] == "RecordGroup" for n in _graph()[0]) else "record"
        assert _trail(await _browse(provider, node, start_type)) == chain[::-1], node


@pytest.mark.parametrize(("sort_field", "sort_dir"), [
    ("name", "ASC"), ("name", "DESC"), ("updatedAt", "DESC"), ("createdAt", "ASC"),
])
async def test_paging_the_app_with_chain_tops_mixed_in(provider, sort_field, sort_dir) -> None:
    """Three rows a page, to the end and back: chain-tops sort and page with the
    App's own children, and the total counts both."""
    whole = _ids(await _page(provider, sort_field=sort_field, sort_dir=sort_dir))
    assert set(whole) & APP_CHAIN_TOPS == APP_CHAIN_TOPS
    pages: list[list[dict]] = []
    after = None
    while True:
        page = await _page(provider, limit=3, sort_field=sort_field, sort_dir=sort_dir,
                           after=after, include_total=after is None)
        if after is None:
            assert page["total"] == len(whole)
        pages.append(page["rows"])
        if not page["hasMore"]:
            break
        last = page["rows"][-1]
        after = {"id": last["id"], "sortKey": last["sortKey"], "nullRank": last["nullRank"]}
    assert [row["id"] for rows in pages for row in rows] == whole
    for index in range(len(pages) - 1, 0, -1):
        first = pages[index][0]
        back = await _page(provider, limit=3, sort_field=sort_field, sort_dir=sort_dir, direction="prev",
                           after={"id": first["id"], "sortKey": first["sortKey"], "nullRank": first["nullRank"]},
                           include_total=False)
        assert _ids(back) == [row["id"] for row in pages[index - 1]]


async def test_a_deleted_own_group_lists_its_chain_top_nowhere(neo4j_provider, neo4j_settings, ct_graph) -> None:
    """No App fallback for a deleted group. Neo4j only: Arango
    removes a group rather than flagging it deleted, so it has no such state."""
    nodes = [
        rg("dg-group", "Deleted group", isDeleted=True, **GROUP), rg("dg-x", "Closed parent", **GROUP),
        rec("dg-r", "Own group deleted", **ITEM),
    ]
    edges = [nr(APP, "dg-group"), nr(APP, "dg-x"), nr("dg-x", "dg-r"), ip("dg-r", "dg-x"),
             bt("dg-r", "dg-group"), perm(USER_U, "dg-r")]
    await load_into_neo4j(neo4j_settings, nodes, edges)
    assert "dg-r" not in _ids(await _page(neo4j_provider))
