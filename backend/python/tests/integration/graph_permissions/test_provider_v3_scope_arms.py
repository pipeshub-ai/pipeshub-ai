"""v3's scope arms, on a graph shaped the way production actually is.

**Why this module exists.** `get_knowledge_hub_connector_page_v3` builds its
visible set from seven arms, and five of them key on ``connectorId = app.id``:

    arm 2  WHERE dg.connectorId = app.id       -> declared
    arm 3  declared + nested groups (declaredScope), driven by declared
    arm 4  belowDeclared,  driven by declaredScope
    arm 5  WHERE sd.connectorId = app.id       -> seeds
    arm 6  belowSeeds,     driven by seeds

(Arm 1, the App walk, stops at a declared group of this connector; arms 2-4
supply what is below it.)

The shared acceptance fixture cannot satisfy that predicate. Measured directly
off ``fixture_graph.build_fixture()``:

    RecordGroups: 24, with connectorId: 0      (``rg()`` sets connectorName,
                                                never connectorId)
    Records: 75 of 81 carry a connectorId that is not any App id
             ("drive-conn", "confluence-conn", ...)

So for 12 of its 14 apps those five arms are **structurally dead** -- they
cannot match a single node -- and the suite still passes because arm 1 (the App
walk) happens to cover most of the fixture's cases. A real sync does hold the
invariant: every Record and RecordGroup has ``connectorId = App.id``.

This module therefore builds its own small graph with the production convention
so those arms actually run. `test_the_fixture_can_exercise_the_declared_arms`
guards the premise itself, so this module cannot quietly rot into the same hole.

Assertions are against **known values**, not against the other backend: two
engines generated from one set of builders share that set's mistakes, and the
acceptance suite has already been burned by a parity assertion that held while
both sides were wrong.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from .fixture_graph import (
    DRIVE,
    KB,
    ORG,
    USER_U,
    _principals,
    app,
    bt,
    ip,
    nr,
    perm,
    rec,
    rg,
    user_app,
)
from .loaders import load_into_neo4j

APP = "v3-app"
KB_APP = "v3-kb"
RGL = "RECORD_GROUP_LEVEL"

# The global-flatten walk stops at a declared group; this is the marker of that
# stop in the generated query.
DECLARED_STOP = "coalesce(pa.permissionModel, '') = 'RECORD_GROUP_LEVEL'"

# Everything in this connector carries connectorId = the App's own id, which is
# what the real store does and what arms 2-6 require.
GROUP = dict(group_type="DRIVE", connector=DRIVE, connectorId=APP)
ITEM = dict(record_type="FILE", connector_id=APP)

# The set the rules say USER_U may see in this connector.
#
#   v3-open         arm 1: inherits from the App
#   v3-dec          arm 1 (granted) and arm 2 (declared + granted)
#   v3-dec-r1/r2    arm 4: BELONGS_TO a declared group
#   v3-dec-nested   arm 3: a group below a declared group
#   v3-nested-r1    arm 4: BELONGS_TO a group in the declared scope
#   v3-seed         arm 5: granted OPEN node below a gap
#   v3-seed-child   arm 6: inherits from a seed
#   v3-hidden       arm 1 (granted); its CONTENTS are hidden, it is not
#   v3-open-r1      arm 1: the walk passes an undeclared group (null permissionModel)
#   v3-dec-walked   arm 1 + 2: a declaration the walk reaches, never granted
#   v3-dec-walked-r1 arm 4: its BELONGS_TO contents
#   v3-dec-folder   arm 4: BELONGS_TO v3-dec (the walk does not descend v3-dec)
#   v3-dec-attach-granted  arm 5: a granted node below the declared stop
#   v3-dec-restricted  arm 4: RESTRICTED, but the declaration wins
#   v3-dec-r3 / v3-dec-nested-granted  granted AND in the declared scope
#   v3-dec-nested-hidden  arm 3: a nested group (its own flag hides only children)
#   v3-foreign-dec(-r1) / v3-noconn-dec(-r1)  declarations the stop must NOT apply to
#   v3-hide-folder  arm 1: an undeclared group that hides its children
CORRECT_VISIBLE = {
    "v3-open", "v3-dec", "v3-dec-r1", "v3-dec-r2", "v3-dec-nested",
    "v3-nested-r1", "v3-seed", "v3-seed-child", "v3-hidden",
    "v3-open-r1", "v3-dec-walked", "v3-dec-walked-r1", "v3-dec-folder",
    "v3-dec-attach-granted", "v3-dec-restricted", "v3-dec-r3",
    "v3-dec-nested-granted", "v3-dec-nested-hidden",
    "v3-foreign-dec", "v3-foreign-dec-r1", "v3-noconn-dec", "v3-noconn-dec-r1",
    "v3-hide-folder",
}

EXPECTED_VISIBLE = CORRECT_VISIBLE

# Below a declared group, reachable only by hierarchy edges and belonging to no
# group of the declared scope. The walk stops at the declaration, so global
# flatten does not show them (folder browse does).
PRUNE_GAP = {"v3-dec-attach", "v3-dec-via-rec"}

# Declared-group nodes built with a connectorId other than APP, on purpose.
OFF_CONNECTOR = {"v3-foreign-dec", "v3-noconn-dec", "v3-kb-r1", "v3-kb-deleted"}

EXPECTED_HIDDEN = PRUNE_GAP | {
    "v3-dec-r3-child",         # inherits from a granted node that is not a seed
    "v3-dec-nested-granted-child",
    "v3-app-loose",            # BELONGS_TO a non-KB, non-APP_LEVEL app

    "v3-gap",                  # the walk stops here: no inheritance, no grant
    "v3-undeclared",           # reachable by an edge, but nothing opens it
    "v3-undec-r1",             # ... so its contents stay out
    "v3-dec-ungranted",        # declared, but granted to nobody
    "v3-dec-ungranted-r1",
    "v3-hidden-r1",            # below hideChildren
    "v3-hide-folder-r1",       # ... of an undeclared group, by a hierarchy edge
    # Deleted, on each arm that could otherwise admit them.
    "v3-dec-deleted",          # arm 4: BELONGS_TO a declared group
    "v3-nested-deleted",       # arm 3: a deleted group below the declaration
    "v3-nested-deleted-r1",    # ... and its contents
    "v3-seed-deleted",         # arm 5: a granted node below the gap
    "v3-open-deleted",         # arm 1: reached by the walk
    "v3-dec-granted-deleted",  # arm 2: a declared group admitted only by a grant
    "v3-dec-granted-deleted-r1",
    "v3-seed-child-deleted",   # arm 6: inherits from a seed
    "v3-dec-placeholder",      # a placeholder stub is never a result
    # RESTRICTED and ungranted below a folder in the declaration, and in no group
    # of the declared scope: the check refuses it, so browse must not list it.
    "v3-dec-folder-restricted",
    "v3-otherorg-r1",          # another org's record under this connector
    "v3-dec-nested-hidden-r1",  # a record of a nested group that hides its children
}

# A node carrying this connector's id but ANOTHER org's orgId: the arms key on
# the App alone, so this is what a sync writing the wrong org would leak were the
# returned nodes not filtered by org.
OTHER_ORG_NODE = "v3-otherorg-r1"


def _graph() -> tuple[list, list]:
    principal_nodes, principal_edges = _principals()
    nodes = principal_nodes + [
        app(APP, "V3 Connector", connector=DRIVE, app_group="Google Workspace"),

        rg("v3-open", "Open group", **GROUP),
        rec("v3-gap", "Gap folder", **ITEM),
        rec("v3-seed", "Granted below gap", **ITEM),
        rec("v3-seed-child", "Child of the seed", **ITEM),

        rg("v3-dec", "Declared group",
           permissionModel="RECORD_GROUP_LEVEL", **GROUP),
        rec("v3-dec-r1", "Declared item 1", **ITEM),
        rec("v3-dec-r2", "Declared item 2", **ITEM),
        rg("v3-dec-nested", "Nested under the declaration", **GROUP),
        rec("v3-nested-r1", "Nested item", **ITEM),

        rg("v3-undeclared", "Undeclared group", **GROUP),
        rec("v3-undec-r1", "Item in an undeclared group", **ITEM),

        rg("v3-dec-ungranted", "Declared, granted to nobody",
           permissionModel="RECORD_GROUP_LEVEL", **GROUP),
        rec("v3-dec-ungranted-r1", "Item in an ungranted declaration", **ITEM),

        rg("v3-hidden", "Declared group that hides its children",
           permissionModel="RECORD_GROUP_LEVEL", hideChildren=True, **GROUP),
        rec("v3-hidden-r1", "Item below hideChildren", **ITEM),
        rg("v3-hide-folder", "Undeclared group that hides its children",
           hideChildren=True, **GROUP),
        rec("v3-hide-folder-r1", "Inherits below hideChildren", **ITEM),

        # Deleted nodes, one per arm that could otherwise admit them.
        rec("v3-dec-deleted", "Deleted item in a declared group",
            isDeleted=True, **ITEM),
        rg("v3-nested-deleted", "Deleted group below the declaration",
           isDeleted=True, **GROUP),
        rec("v3-nested-deleted-r1", "Item in a deleted nested group", **ITEM),
        rec("v3-seed-deleted", "Deleted grant below the gap",
            isDeleted=True, **ITEM),
        rec("v3-open-deleted", "Deleted record reached by the walk",
            isDeleted=True, **ITEM),
        rg("v3-dec-granted-deleted", "Deleted declared group, granted",
           permissionModel=RGL, isDeleted=True, **GROUP),
        rec("v3-dec-granted-deleted-r1", "Item in a deleted declared group", **ITEM),
        rec("v3-seed-child-deleted", "Deleted child of the seed",
            isDeleted=True, **ITEM),
        rec("v3-dec-placeholder", "Placeholder stub in the declared group",
            isPlaceholder=True, **ITEM),

        # Right connector, wrong org.
        rec(OTHER_ORG_NODE, "Record belonging to another org",
            orgId="org-2", **ITEM),

        # The declared stop. v3-open has no permissionModel, so a bare `=` in
        # the stop is null under NOT and would end the walk right here.
        rec("v3-open-r1", "Record below an undeclared group", **ITEM),
        rg("v3-dec-walked", "Declared group reached by the walk",
           permissionModel=RGL, **GROUP),
        rec("v3-dec-walked-r1", "Record of the walked declaration", **ITEM),
        rec("v3-dec-folder", "Folder in the declared group", **ITEM),
        rec("v3-dec-attach", "Attachment below the folder", **ITEM),
        rg("v3-dec-via-rec", "Group reached through a record", **GROUP),
        rec("v3-dec-attach-granted", "Granted attachment below the folder", **ITEM),
        rec("v3-dec-folder-restricted", "Restricted, outside the declared scope",
            rule="RESTRICTED", **ITEM),
        rec("v3-dec-restricted", "Restricted record in the declaration",
            rule="RESTRICTED", **ITEM),
        rec("v3-dec-r3", "Granted record in the declaration", **ITEM),
        rec("v3-dec-r3-child", "Child of the granted record", **ITEM),
        rg("v3-dec-nested-granted", "Granted group in the declared scope", **GROUP),
        rec("v3-dec-nested-granted-child", "Child of the granted nested group",
            **ITEM),
        rg("v3-dec-nested-hidden", "Nested group that hides its children",
           hideChildren=True, **GROUP),
        rec("v3-dec-nested-hidden-r1", "Record in the hidden nested group", **ITEM),
        # Declarations the stop must not apply to: another connector's, and one
        # with no connectorId at all.
        rg("v3-foreign-dec", "Declared group of another connector",
           group_type="DRIVE", connector=DRIVE, connectorId="other-conn",
           permissionModel=RGL),
        rec("v3-foreign-dec-r1", "Record below the foreign declaration", **ITEM),
        rg("v3-noconn-dec", "Declared group with no connector id",
           group_type="DRIVE", connector=DRIVE, permissionModel=RGL),
        rec("v3-noconn-dec-r1", "Record below the unowned declaration", **ITEM),
        rec("v3-app-loose", "Loose record of a non-KB app", **ITEM),

        # Arm 7: collection items of a KB app.
        app(KB_APP, "V3 KB", connector=KB, app_group="Local Storage"),
        rec("v3-kb-r1", "KB item", connector_id=KB_APP),
        rec("v3-kb-deleted", "Deleted KB item", connector_id=KB_APP, isDeleted=True),
    ]
    edges = principal_edges + [
        user_app(USER_U, APP),

        # Arm 1: the App walk reaches v3-open because it inherits.
        nr(APP, "v3-open"), ip("v3-open", APP),

        # A gap: v3-gap neither inherits nor is granted, so the walk stops at
        # it -- and everything below it is reachable only as a seed.
        nr("v3-open", "v3-gap"),
        nr("v3-gap", "v3-seed"), perm(USER_U, "v3-seed"),
        nr("v3-seed", "v3-seed-child"), ip("v3-seed-child", "v3-seed"),

        # Arms 2/3/4: a declared group, its BELONGS_TO contents, and a nested
        # group that carries the declaration down.
        nr(APP, "v3-dec"), perm(USER_U, "v3-dec"),
        bt("v3-dec-r1", "v3-dec"), bt("v3-dec-r2", "v3-dec"),
        nr("v3-dec", "v3-dec-nested"), bt("v3-nested-r1", "v3-dec-nested"),

        # Neither declared nor openable: contents must stay out.
        nr(APP, "v3-undeclared"), bt("v3-undec-r1", "v3-undeclared"),

        # Declared but granted to nobody: a declaration opens only a group the
        # user can already open.
        nr(APP, "v3-dec-ungranted"),
        bt("v3-dec-ungranted-r1", "v3-dec-ungranted"),

        # Granted, so the group itself is visible; hideChildren keeps its
        # contents out.
        nr(APP, "v3-hidden"), perm(USER_U, "v3-hidden"),
        bt("v3-hidden-r1", "v3-hidden"),
        nr(APP, "v3-hide-folder"), ip("v3-hide-folder", APP),
        nr("v3-hide-folder", "v3-hide-folder-r1"), ip("v3-hide-folder-r1", "v3-hide-folder"),

        # Deleted, attached exactly where a live node would be admitted.
        bt("v3-dec-deleted", "v3-dec"),
        nr("v3-dec", "v3-nested-deleted"),
        bt("v3-nested-deleted-r1", "v3-nested-deleted"),
        nr("v3-gap", "v3-seed-deleted"), perm(USER_U, "v3-seed-deleted"),
        nr("v3-open", "v3-open-deleted"), ip("v3-open-deleted", "v3-open"),
        perm(USER_U, "v3-dec-granted-deleted"),
        bt("v3-dec-granted-deleted-r1", "v3-dec-granted-deleted"),
        nr("v3-seed", "v3-seed-child-deleted"), ip("v3-seed-child-deleted", "v3-seed"),
        nr("v3-dec", "v3-dec-placeholder"), ip("v3-dec-placeholder", "v3-dec"),
        bt("v3-dec-placeholder", "v3-dec"),

        # Wrong org, but granted and carrying this connector's id.
        nr(APP, OTHER_ORG_NODE), perm(USER_U, OTHER_ORG_NODE),
        nr("v3-open", OTHER_ORG_NODE),     # also a child of a group, for browse

        nr("v3-open", "v3-open-r1"), ip("v3-open-r1", "v3-open"),

        # Declared, reached by the walk, never granted: arm 2 admits it only
        # through `dg IN regionA`.
        nr(APP, "v3-dec-walked"), ip("v3-dec-walked", APP),
        bt("v3-dec-walked-r1", "v3-dec-walked"),

        # Below the declared v3-dec. The folder belongs to it; the two gap nodes
        # hang below the folder by hierarchy only.
        nr("v3-dec", "v3-dec-folder"), ip("v3-dec-folder", "v3-dec"),
        bt("v3-dec-folder", "v3-dec"),
        nr("v3-dec-folder", "v3-dec-attach"), ip("v3-dec-attach", "v3-dec-folder"),
        nr("v3-dec-folder", "v3-dec-via-rec"), ip("v3-dec-via-rec", "v3-dec-folder"),
        nr("v3-dec-folder", "v3-dec-attach-granted"),
        nr("v3-dec-folder", "v3-dec-folder-restricted"),
        ip("v3-dec-folder-restricted", "v3-dec-folder"),
        perm(USER_U, "v3-dec-attach-granted"),
        nr("v3-dec", "v3-dec-restricted"), bt("v3-dec-restricted", "v3-dec"),

        # Granted AND already in the declared scope, so not seeds: their
        # hierarchy-only children must stay out. Compared as ids against a list
        # of nodes, `NOT sd IN ...` would be true and seed them.
        bt("v3-dec-r3", "v3-dec"), perm(USER_U, "v3-dec-r3"),
        nr("v3-dec-r3", "v3-dec-r3-child"), ip("v3-dec-r3-child", "v3-dec-r3"),
        nr("v3-dec-nested", "v3-dec-nested-granted"),
        perm(USER_U, "v3-dec-nested-granted"),
        nr("v3-dec-nested-granted", "v3-dec-nested-granted-child"),
        ip("v3-dec-nested-granted-child", "v3-dec-nested-granted"),

        nr("v3-dec", "v3-dec-nested-hidden"),
        bt("v3-dec-nested-hidden-r1", "v3-dec-nested-hidden"),
        # Also its hierarchy child, so browse's own hideChildren guard is load-bearing.
        nr("v3-dec-nested-hidden", "v3-dec-nested-hidden-r1"),

        nr(APP, "v3-foreign-dec"), ip("v3-foreign-dec", APP),
        nr("v3-foreign-dec", "v3-foreign-dec-r1"),
        ip("v3-foreign-dec-r1", "v3-foreign-dec"),
        nr(APP, "v3-noconn-dec"), ip("v3-noconn-dec", APP),
        nr("v3-noconn-dec", "v3-noconn-dec-r1"),
        ip("v3-noconn-dec-r1", "v3-noconn-dec"),

        bt("v3-app-loose", APP),

        user_app(USER_U, KB_APP),
        bt("v3-kb-r1", KB_APP), bt("v3-kb-deleted", KB_APP),
    ]
    return nodes, edges


@pytest.fixture(scope="module")
async def v3_graph(neo4j_provider, neo4j_settings):
    """Neo4j only -- v3 has no Arango implementation."""
    nodes, edges = _graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    return {"nodes": nodes, "edges": edges}


async def _page(provider, *, app_id: str = APP, **overrides) -> dict:
    access = await provider.get_knowledge_hub_access_v3(
        user_key=USER_U, org_id=ORG,
    )
    kwargs = {
        "app_id": app_id, "org_id": ORG,
        "grantee_ids": access["grantee_ids"],
        "gated_app_ids": access["gated_app_ids"],
        "granted_ids": access["by_connector"].get(app_id) or [],
        "limit": 500, "flatten": True, "sort_field": "name", "sort_dir": "ASC",
        "include_total": True,
    }
    kwargs.update(overrides)
    return await provider.get_knowledge_hub_connector_page_v3(**kwargs)


async def _visible(provider, **overrides) -> set[str]:
    return {row["id"] for row in (await _page(provider, **overrides))["rows"]}


async def _page_query(provider, monkeypatch, **overrides) -> str:
    """The page query the provider sends, for assertions on its shape."""
    seen: list[str] = []
    original = provider.client.execute_query

    async def spy(query: str, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        seen.append(query)
        return await original(query, *args, **kwargs)

    monkeypatch.setattr(provider.client, "execute_query", spy)
    await _page(provider, **overrides)
    monkeypatch.setattr(provider.client, "execute_query", original)
    return [q for q in seen if "$kh_limit" in q][-1]


def test_the_fixture_can_exercise_the_declared_arms() -> None:
    """Guard the premise: every node here must satisfy `connectorId = app.id`,
    except the few built off-connector on purpose.

    Without this the module would still pass while testing nothing -- which is
    exactly the state the shared acceptance fixture is in.
    """
    nodes, _ = _graph()
    groups = [n for n in nodes if n["kind"] == "RecordGroup"]
    records = [n for n in nodes if n["kind"] == "Record"]
    assert groups and records
    for node in groups + records:
        if node["id"] in OFF_CONNECTOR:
            assert node["props"].get("connectorId") != APP, node["id"]
        else:
            assert node["props"].get("connectorId") == APP, node["id"]


async def test_the_whole_visible_set(v3_graph, neo4j_provider) -> None:
    """The exact set, so an extra node is a failure and not just a miss."""
    assert await _visible(neo4j_provider) == EXPECTED_VISIBLE


async def test_nothing_out_of_reach_leaks(v3_graph, neo4j_provider) -> None:
    assert (await _visible(neo4j_provider)) & EXPECTED_HIDDEN == set()


async def test_a_declared_groups_contents_are_reached_by_belongs_to(
    v3_graph, neo4j_provider
) -> None:
    """Arms 2 and 4. The items hold no grant and inherit from nothing; the
    RECORD_GROUP_LEVEL declaration on their group is what admits them."""
    visible = await _visible(neo4j_provider)
    assert {"v3-dec-r1", "v3-dec-r2"} <= visible


async def test_a_nested_group_carries_the_declaration_down(
    v3_graph, neo4j_provider
) -> None:
    """Arm 3, then arm 4: the nested group is admitted by the declaration above
    it, and its own BELONGS_TO contents come with it."""
    visible = await _visible(neo4j_provider)
    assert "v3-dec-nested" in visible
    assert "v3-nested-r1" in visible


async def test_a_granted_node_below_a_gap_is_visible(
    v3_graph, neo4j_provider
) -> None:
    """Arm 5. The App walk cannot pass `v3-gap`, so the only way in is the seed."""
    visible = await _visible(neo4j_provider)
    assert "v3-seed" in visible
    assert "v3-gap" not in visible, "the gap itself is not admissible"


async def test_content_below_a_seed_is_visible(v3_graph, neo4j_provider) -> None:
    """Arm 6: an OPEN node inheriting from a seed comes with it."""
    assert "v3-seed-child" in await _visible(neo4j_provider)


async def test_an_undeclared_groups_contents_stay_out(
    v3_graph, neo4j_provider
) -> None:
    """A hierarchy edge from the App is not access: nothing opens this group."""
    visible = await _visible(neo4j_provider)
    assert "v3-undeclared" not in visible
    assert "v3-undec-r1" not in visible


async def test_a_declaration_granted_to_nobody_opens_nothing(
    v3_graph, neo4j_provider
) -> None:
    """A declaration opens only a group the user can already open."""
    visible = await _visible(neo4j_provider)
    assert "v3-dec-ungranted" not in visible
    assert "v3-dec-ungranted-r1" not in visible


async def test_hidechildren_keeps_contents_out_but_shows_the_group(
    v3_graph, neo4j_provider
) -> None:
    """The group is granted, so it is visible; `hideChildren` stops the descent
    and also excludes it from the declared scope."""
    visible = await _visible(neo4j_provider)
    assert "v3-hidden" in visible
    assert "v3-hidden-r1" not in visible


async def test_deleted_nodes_are_excluded_from_every_arm(
    v3_graph, neo4j_provider
) -> None:
    """Each arm carries its own `isDeleted` guard and the listing does not
    re-check it, so one deleted node is planted at every admitting position."""
    access = await neo4j_provider.get_knowledge_hub_access_v3(user_key=USER_U, org_id=ORG)
    # The access step already drops deleted grant targets; pass the grant anyway
    # so arm 2's own guard is what keeps the deleted declaration out.
    granted = [*access["by_connector"][APP], "v3-dec-granted-deleted"]
    visible = await _visible(neo4j_provider, granted_ids=granted)
    for node_id in ("v3-dec-deleted", "v3-nested-deleted",
                    "v3-nested-deleted-r1", "v3-seed-deleted", "v3-open-deleted",
                    "v3-dec-granted-deleted", "v3-dec-granted-deleted-r1",
                    "v3-seed-child-deleted"):
        assert node_id not in visible, node_id


async def test_placeholders_never_surface(v3_graph, neo4j_provider) -> None:
    """Each listing drops placeholders on the node, once: the global flatten, the
    app's direct children, and browsing into a group."""
    assert "v3-dec-placeholder" not in await _visible(neo4j_provider)
    assert "v3-dec-placeholder" not in await _visible(neo4j_provider, flatten=False)
    browsed = await _visible(
        neo4j_provider, start_id="v3-dec", start_type="recordGroup", flatten=False,
    )
    assert "v3-dec-folder" in browsed
    assert "v3-dec-placeholder" not in browsed


async def test_a_node_from_another_org_does_not_leak(
    v3_graph, neo4j_provider
) -> None:
    """This node carries the right connectorId and a direct grant, and belongs
    to a different org. The arms key on the App alone, so the listing and browse
    filter what they return by org."""
    assert OTHER_ORG_NODE not in await _visible(neo4j_provider)
    assert OTHER_ORG_NODE not in await _visible(neo4j_provider, flatten=False)
    assert OTHER_ORG_NODE not in await _visible(
        neo4j_provider, start_id="v3-open", start_type="recordGroup", flatten=False,
    )


async def test_the_total_matches_the_rows(v3_graph, neo4j_provider) -> None:
    """The total counts the whole result, not the page."""
    access = await neo4j_provider.get_knowledge_hub_access_v3(
        user_key=USER_U, org_id=ORG,
    )
    page = await neo4j_provider.get_knowledge_hub_connector_page_v3(
        app_id=APP, org_id=ORG,
        grantee_ids=access["grantee_ids"],
        gated_app_ids=access["gated_app_ids"],
        granted_ids=access["by_connector"].get(APP) or [],
        limit=3, flatten=True, sort_field="name", sort_dir="ASC",
        include_total=True,
    )
    assert page["total"] == len(EXPECTED_VISIBLE)
    assert len(page["rows"]) == 3
    assert sum(page["counts"].values()) == page["total"]


async def test_a_user_with_no_grants_sees_nothing_here(
    v3_graph, neo4j_provider
) -> None:
    """The app is gated to USER_U only, so USER_V gets an empty page rather
    than the connector's contents."""
    from .fixture_graph import USER_V

    access = await neo4j_provider.get_knowledge_hub_access_v3(
        user_key=USER_V, org_id=ORG,
    )
    page = await neo4j_provider.get_knowledge_hub_connector_page_v3(
        app_id=APP, org_id=ORG,
        grantee_ids=access["grantee_ids"],
        gated_app_ids=access["gated_app_ids"],
        granted_ids=access["by_connector"].get(APP) or [],
        limit=500, flatten=True, sort_field="name", sort_dir="ASC",
        include_total=True,
    )
    assert page["rows"] == []


async def test_the_walk_passes_an_undeclared_group(v3_graph, neo4j_provider) -> None:
    """The declared stop reads `permissionModel` under NOT. v3-open has none, so
    a bare `=` would be null there and end the walk at every undeclared group."""
    assert "v3-open-r1" in await _visible(neo4j_provider)


async def test_the_stop_applies_only_to_this_connectors_declarations(
    v3_graph, neo4j_provider
) -> None:
    """Arm 2 never admits another connector's declaration, or one without a
    connectorId, so the walk has to keep descending them."""
    visible = await _visible(neo4j_provider)
    assert {"v3-foreign-dec-r1", "v3-noconn-dec-r1"} <= visible


async def test_a_declaration_the_walk_reaches_opens_its_contents(
    v3_graph, neo4j_provider
) -> None:
    """v3-dec-walked is granted to nobody: arm 2 admits it only because the walk
    reached it (`dg IN regionA`)."""
    visible = await _visible(neo4j_provider)
    assert {"v3-dec-walked", "v3-dec-walked-r1"} <= visible


async def test_restricted_records_in_a_declaration_are_visible(
    v3_graph, neo4j_provider
) -> None:
    """A declaration wins inside its scope."""
    assert "v3-dec-restricted" in await _visible(neo4j_provider)


async def test_granted_nodes_in_the_declared_scope_do_not_seed_their_children(
    v3_graph, neo4j_provider
) -> None:
    visible = await _visible(neo4j_provider)
    assert {"v3-dec-r3", "v3-dec-nested-granted"} <= visible
    assert "v3-dec-r3-child" not in visible
    assert "v3-dec-nested-granted-child" not in visible


async def test_the_walk_stops_at_a_declared_group(v3_graph, neo4j_provider) -> None:
    """What only the walk reached below v3-dec leaves the global flatten; a
    granted node there still comes back as a seed; folder browse, which walks
    hop by hop, still shows all of it."""
    visible = await _visible(neo4j_provider)
    assert PRUNE_GAP.isdisjoint(visible)
    assert "v3-dec-attach-granted" in visible
    browsed = await _visible(
        neo4j_provider, start_id="v3-dec-folder", start_type="record",
        flatten=False,
    )
    assert browsed == PRUNE_GAP | {"v3-dec-attach-granted"}


async def test_browsing_into_a_declared_group_still_walks_it(
    v3_graph, neo4j_provider
) -> None:
    """The stop belongs to the global flatten. In the shared hop rule it would
    empty any browse started at or below a declared group."""
    browsed = await _visible(
        neo4j_provider, start_id="v3-dec", start_type="recordGroup",
        flatten=False,
    )
    assert "v3-dec-folder" in browsed


async def test_browsing_a_declaration_lists_what_the_flatten_lists(
    v3_graph, neo4j_provider
) -> None:
    """In browse too: inside an admitted RECORD_GROUP_LEVEL group the
    declaration wins, so a child that neither inherits nor is granted is listed,
    and a group below it can be opened (a per-hop walk up would refuse it).
    hideChildren still hides, and a declaration nobody is granted opens
    nothing."""
    browsed = await _visible(
        neo4j_provider, start_id="v3-dec", start_type="recordGroup", flatten=False,
    )
    assert {"v3-dec-nested", "v3-dec-restricted", "v3-dec-folder"} <= browsed
    assert not browsed & {"v3-dec-placeholder", "v3-nested-deleted"}
    assert await _visible(
        neo4j_provider, start_id="v3-dec-nested", start_type="recordGroup", flatten=False,
    ) == {"v3-dec-nested-granted"}
    assert await _visible(
        neo4j_provider, start_id="v3-dec-nested-hidden", start_type="recordGroup", flatten=False,
    ) == set()
    assert await _visible(
        neo4j_provider, start_id="v3-dec-ungranted", start_type="recordGroup", flatten=False,
    ) == set()


async def test_browsing_a_declaration_lists_only_what_the_check_admits(
    v3_graph, neo4j_provider
) -> None:
    """The declaration opens its scope as the check defines it: its groups and
    the records belonging to them. A RESTRICTED record hanging below a folder of
    the declaration, in none of its groups, is not listed: opening it would 404."""
    browsed = await _visible(
        neo4j_provider, start_id="v3-dec-folder", start_type="record", flatten=False,
    )
    assert "v3-dec-folder-restricted" not in browsed
    assert not (await neo4j_provider.check_access(
        USER_U, ORG, node_ids=["v3-dec-folder-restricted"],
    )).node_ids


async def test_a_start_below_hide_children_does_not_open(v3_graph, neo4j_provider) -> None:
    """The check admits both (hideChildren is navigation only), but
    browsing is navigation: neither a child by the hierarchy nor a record of the
    group opens as a start."""
    for start in ("v3-hide-folder-r1", "v3-hidden-r1"):
        assert (await neo4j_provider.check_access(USER_U, ORG, node_ids=[start])).node_ids
        scope = (await _page(
            neo4j_provider, start_id=start, start_type="record", flatten=False, include_scope=True,
        ))["scope"]
        assert scope["admitted"] is False, start


async def test_a_start_admitted_by_its_declaration_has_its_trail(
    v3_graph, neo4j_provider
) -> None:
    """v3-dec-nested neither inherits nor is granted, so the walk from the App
    does not reach it; the declaration admits it. Its breadcrumb runs through
    v3-dec, where it is listed, not straight to the App."""
    scope = (await _page(
        neo4j_provider, start_id="v3-dec-nested", start_type="recordGroup",
        flatten=False, include_scope=True,
    ))["scope"]
    assert [crumb["id"] for crumb in scope["breadcrumbs"]] == [APP, "v3-dec", "v3-dec-nested"]
    assert scope["parentNode"]["id"] == "v3-dec"


async def test_collection_items_come_from_belongs_to_the_app(
    v3_graph, neo4j_provider
) -> None:
    """Arm 7: a KB app's items BELONG_TO the app; another app's do not count."""
    assert await _visible(neo4j_provider, app_id=KB_APP) == {"v3-kb-r1"}
    assert "v3-app-loose" not in await _visible(neo4j_provider)


async def test_direct_children_of_the_app(v3_graph, neo4j_provider) -> None:
    """The one-level app listing shares the node listing with the flatten. The
    seed below v3-gap belongs to no group, so it is listed here;
    v3-dec-attach-granted has an open parent and stays under it."""
    assert await _visible(neo4j_provider, flatten=False) == {
        "v3-open", "v3-dec", "v3-hidden", "v3-hide-folder",
        "v3-dec-walked", "v3-foreign-dec", "v3-noconn-dec", "v3-seed",
    }


async def test_the_flatten_query_shape(v3_graph, neo4j_provider, monkeypatch) -> None:
    flatten = await _page_query(neo4j_provider, monkeypatch)
    assert flatten.count(DECLARED_STOP) == 1
    # The arms hand the listing nodes: no seek by id, and no id tested against a
    # list of nodes (always false, so true under NOT).
    assert "{id: vid}" not in flatten
    assert not re.search(
        r"\.id IN (regionA|declared|declaredScope|belowDeclared|seeds)\b", flatten,
    )
    for overrides in (
        {"flatten": False},
        {"start_id": "v3-dec", "start_type": "recordGroup"},
    ):
        query = await _page_query(neo4j_provider, monkeypatch, **overrides)
        assert DECLARED_STOP not in query, overrides
    # Browse walks up from its start: bound in the pattern, the App becomes the
    # planner's starting point and the walk covers the whole connector.
    browse = await _page_query(neo4j_provider, monkeypatch, start_id="v3-dec-folder", start_type="record")
    assert "{1,50} (app)" not in browse and "(top)\n        WHERE top = app" in browse


async def test_the_page_is_cut_through_an_aggregation(
    v3_graph, neo4j_provider, monkeypatch
) -> None:
    """A slice carried into the enrich puts the whole sorted list on every page
    row; Enterprise's pipelined runtime then fails on its memory cap. Community
    cannot show that, so pin the shape for both app listings."""
    browse = {"start_id": "v3-dec", "start_type": "recordGroup"}
    for overrides in ({}, {"flatten": False}, browse, {**browse, "flatten": False}):
        query = await _page_query(neo4j_provider, monkeypatch, **overrides)
        assert "ordered[0..$kh_limit] AS page" not in query, overrides
        assert "collect(pageItem) AS page" in query, overrides
    # Browsing walks inside its CALL: an id list imported into the CALL rode on
    # every row and was charged ~9 GB on a 99k-record group.
    scoped = await _page_query(neo4j_provider, monkeypatch, **browse)
    assert "CALL (app, start, appOpensEverything, fromApp)" in scoped
    assert "{id: vid}" not in scoped


async def test_an_empty_page_keeps_its_zero_total(v3_graph, neo4j_provider) -> None:
    page = await _page(neo4j_provider, filters={"search_query": "no such node anywhere"})
    assert page["rows"] == []
    assert page["total"] == 0


def _sort_map(query: str) -> str:
    return re.search(r"WITH \{ ([^{}]*) \} AS node", query).group(1)


async def test_the_sort_map_reads_name_only_when_needed(
    v3_graph, neo4j_provider, monkeypatch
) -> None:
    """`name` is read for every visible node when it is in this map. Only a name
    sort or a filter on name needs it."""
    by_update = await _page_query(neo4j_provider, monkeypatch, sort_field="updatedAt")
    assert "name:" not in _sort_map(by_update)
    by_name = await _page_query(neo4j_provider, monkeypatch, sort_field="name")
    assert "name:" in _sort_map(by_name)
    searched = await _page_query(
        neo4j_provider, monkeypatch, sort_field="updatedAt",
        filters={"search_query": "declared item"},
    )
    assert "name:" in _sort_map(searched)


async def test_the_sort_map_reads_node_type_only_for_counts(
    v3_graph, neo4j_provider, monkeypatch
) -> None:
    """nodeType is two string reads per visible node; only the per-type counts
    and a node-type filter need it."""
    counted = await _page_query(neo4j_provider, monkeypatch, sort_field="updatedAt")
    assert "nodeType:" in _sort_map(counted)
    later = await _page_query(
        neo4j_provider, monkeypatch, sort_field="updatedAt", include_total=False,
    )
    assert "nodeType:" not in _sort_map(later)
    typed = await _page_query(
        neo4j_provider, monkeypatch, sort_field="updatedAt", include_total=False,
        filters={"node_types": ["recordGroup"]},
    )
    assert "nodeType:" in _sort_map(typed)


async def test_a_node_type_filter_holds_on_later_pages(v3_graph, neo4j_provider) -> None:
    groups = {"node_types": ["recordGroup"]}
    first = await _visible(neo4j_provider, filters=groups)
    later = await _visible(neo4j_provider, filters=groups, include_total=False)
    assert first and later == first
    assert "v3-dec-r1" not in later


async def test_search_with_a_non_name_sort_matches_by_name(
    v3_graph, neo4j_provider
) -> None:
    assert await _visible(
        neo4j_provider, sort_field="updatedAt", sort_dir="DESC",
        filters={"search_query": "declared item"},
    ) == {"v3-dec-r1", "v3-dec-r2"}


@pytest.mark.parametrize("sort_field", ["updatedAt", "sizeInBytes", "name"])
async def test_cursor_paging_covers_the_visible_set(
    v3_graph, neo4j_provider, sort_field
) -> None:
    """Keyset paging through the whole set: every node exactly once."""
    seen: list[str] = []
    after = None
    for _ in range(50):
        rows = (await _page(
            neo4j_provider, limit=3, sort_field=sort_field,
            include_total=after is None, after=after,
        ))["rows"]
        seen += [row["id"] for row in rows]
        if len(rows) < 3:
            break
        last = rows[-1]
        after = {"nullRank": last["nullRank"], "sortKey": last["sortKey"],
                 "id": last["id"]}
    assert len(seen) == len(set(seen))
    assert set(seen) == EXPECTED_VISIBLE


async def test_a_hidden_nested_group_hides_its_contents(
    v3_graph, neo4j_provider
) -> None:
    """The group is listed; its records are not, in the flatten as in browse."""
    assert "v3-dec-nested-hidden" in await _visible(neo4j_provider)
    assert "v3-dec-nested-hidden-r1" not in await _visible(neo4j_provider)
