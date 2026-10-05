"""Breadcrumbs follow placement and never name a node the query refused.

Each case is a small graph reduced to the flags the browse query returns.
"""

from app.utils.kh_breadcrumbs import browse_scope, build_trail


def node(node_id, node_type="record", *, admitted=True, own_group=None, internal=False):
    groups = [own_group] if isinstance(own_group, str) else list(own_group or ())
    return {
        "id": node_id, "name": node_id.upper(), "nodeType": node_type, "subType": None,
        "isInternal": internal, "admitted": admitted, "ownGroups": groups,
    }


def lists(parent, child, ok=True):
    return {"parentId": parent, "childId": child, "lists": ok}


def trail(nodes, edges, start, via=None):
    return [c["id"] for c in build_trail(nodes, edges, start, via)]


def two_parents():
    """r6 lists under folder f1 and under the internal Shared-with-Me group."""
    nodes = [node("app", "app"), node("rgd", "recordGroup"), node("f1"),
             node("inbox", "recordGroup", internal=True), node("r6", own_group="rgd")]
    edges = [lists("app", "rgd"), lists("rgd", "f1"), lists("f1", "r6"),
             lists("app", "inbox"), lists("inbox", "r6")]
    return nodes, edges


def test_accessible_parents_form_the_trail() -> None:
    """A nested group's trail is its hierarchy."""
    nodes = [node("app", "app"), node("rg1", "recordGroup"), node("rg2", "recordGroup")]
    edges = [lists("app", "rg1"), lists("rg1", "rg2")]
    assert trail(nodes, edges, "rg2") == ["app", "rg1", "rg2"]


def test_a_chain_top_climbs_to_its_own_group_and_never_names_the_gap() -> None:
    """r6's parent r3 is inaccessible, so r6 sits under its own group."""
    nodes = [node("app", "app"), node("rg1", "recordGroup"),
             node("r3", admitted=False, own_group="rg1"), node("r6", own_group="rg1")]
    edges = [lists("app", "rg1"), lists("rg1", "r3", ok=False), lists("r3", "r6")]
    crumbs = build_trail(nodes, edges, "r6")
    assert [c["id"] for c in crumbs] == ["app", "rg1", "r6"]
    assert "r3" not in repr(crumbs).lower()


def test_a_chain_top_skips_an_own_group_it_cannot_open() -> None:
    """A node with several own groups climbs to one it can actually open.

    `r6` belongs to another user's inbox, which it may not open, and to `rgd`,
    which it may. Picking a group before asking whether it opens drops `rgd`
    and falls through to the App, which the App step exists to avoid.
    """
    nodes = [node("app", "app"), node("rgd", "recordGroup"),
             node("ainbox", "recordGroup", admitted=False, internal=True),
             node("r6", own_group=["ainbox", "rgd"])]
    edges = [lists("app", "rgd"), lists("app", "ainbox")]
    assert trail(nodes, edges, "r6") == ["app", "rgd", "r6"]


def test_the_drive_group_wins_over_an_internal_one_among_own_groups() -> None:
    """Non-internal before internal governs the own-group hop too, not only listing parents.

    Both groups open, and the internal one sorts first by id, so an id-ordered
    pick would name Shared with Me instead of the drive location.
    """
    nodes = [node("app", "app"), node("rgd", "recordGroup"),
             node("ainbox", "recordGroup", internal=True),
             node("r6", own_group=["ainbox", "rgd"])]
    edges = [lists("app", "rgd"), lists("app", "ainbox")]
    assert trail(nodes, edges, "r6") == ["app", "rgd", "r6"]


def test_descendants_of_a_chain_top_nest_under_it() -> None:
    """r7 inherits from the chain-top r6."""
    nodes = [node("app", "app"), node("rg1", "recordGroup"), node("r3", admitted=False),
             node("r6", own_group="rg1"), node("r7", own_group="rg1")]
    edges = [lists("app", "rg1"), lists("rg1", "r3", ok=False),
             lists("r3", "r6", ok=False), lists("r6", "r7")]
    assert trail(nodes, edges, "r7") == ["app", "rg1", "r6", "r7"]


def test_an_openable_parent_the_node_does_not_list_under_is_skipped() -> None:
    """Opening a parent is not enough: the child must actually list under it.

    `fa` sorts first and the user may open it, but r6 does not list there (a
    STRICT node below a seed-only parent, say), so the trail goes through `fb`.
    """
    nodes = [node("app", "app"), node("rg1", "recordGroup"),
             node("fa"), node("fb"), node("r6")]
    edges = [lists("app", "rg1"), lists("rg1", "fa"), lists("rg1", "fb"),
             lists("fa", "r6", ok=False), lists("fb", "r6")]
    assert trail(nodes, edges, "r6") == ["app", "rg1", "fb", "r6"]


def test_an_unreachable_own_group_falls_back_to_the_app() -> None:
    """App › R6."""
    nodes = [node("app", "app"), node("rg2", "recordGroup", admitted=False),
             node("r6", own_group="rg2")]
    edges = [lists("app", "rg2", ok=False), lists("rg2", "r6")]
    assert trail(nodes, edges, "r6") == ["app", "r6"]


def test_an_own_group_the_query_never_returned_is_unreachable() -> None:
    """Malformed data falls back to the App rather than naming a group whose
    access was never checked."""
    nodes = [node("app", "app"), node("r6", own_group="rg-elsewhere")]
    assert trail(nodes, [], "r6") == ["app", "r6"]


def test_via_parent_picks_the_navigated_trail() -> None:
    """The parent the user navigated through picks between two valid trails."""
    nodes, edges = two_parents()
    assert trail(nodes, edges, "r6", via="inbox") == ["app", "inbox", "r6"]
    assert trail(nodes, edges, "r6", via="f1") == ["app", "rgd", "f1", "r6"]


def test_without_via_the_drive_location_beats_shared_with_me() -> None:
    nodes, edges = two_parents()
    assert trail(nodes, edges, "r6") == ["app", "rgd", "f1", "r6"]


def test_a_via_parent_the_node_does_not_list_under_is_ignored() -> None:
    nodes, edges = two_parents()
    assert trail(nodes, edges, "r6", via="rgd") == ["app", "rgd", "f1", "r6"]
    assert trail(nodes, edges, "r6", via="nowhere") == ["app", "rgd", "f1", "r6"]


def test_via_parent_applies_to_the_first_step_only() -> None:
    """f1 has two parents too, but via names r6's parent, not f1's."""
    nodes = [node("app", "app"), node("rga", "recordGroup"),
             node("rgb", "recordGroup", internal=True), node("f1"), node("r6")]
    edges = [lists("app", "rga"), lists("app", "rgb"), lists("rga", "f1"),
             lists("rgb", "f1"), lists("f1", "r6")]
    assert trail(nodes, edges, "r6", via="rgb") == ["app", "rga", "f1", "r6"]


def test_an_app_is_its_own_trail() -> None:
    scope = browse_scope("app", True, [node("app", "app")], [])
    assert [c["id"] for c in scope["breadcrumbs"]] == ["app"]
    assert scope["currentNode"]["id"] == "app"
    assert scope["parentNode"] is None


def test_the_parent_node_is_the_last_step_up() -> None:
    nodes = [node("app", "app"), node("rg1", "recordGroup"), node("r6", own_group="rg1")]
    scope = browse_scope("r6", True, nodes, [lists("app", "rg1")])
    assert scope["parentNode"]["id"] == "rg1"
    assert scope["currentNode"] == {"id": "r6", "name": "R6", "nodeType": "record", "subType": None}


def test_an_inadmissible_start_carries_nothing_but_the_verdict() -> None:
    """No name, type or trail that could confirm the node exists."""
    nodes = [node("app", "app"), node("r3", admitted=False)]
    scope = browse_scope("r3", False, nodes, [lists("app", "r3", ok=False)])
    assert scope == {"admitted": False, "nodeId": "r3"}


def test_a_cycle_in_the_flags_terminates() -> None:
    edges = [lists("a", "b"), lists("b", "a")]
    assert trail([node("a"), node("b")], edges, "a") == ["b", "a"]
