"""The pure derivation of the typed graph (``kg.model``): what a node's rows are, from the documents and edges around it, with no database."""
from __future__ import annotations

import pytest

from app.services.graph_db.postgres.kg.model import (
    LINK_GROUP,
    LINK_HIERARCHY,
    PRINCIPALS,
    UAR,
    GEdge,
    Snapshot,
    app_id_of,
    derive,
    edge_ends,
    flag,
    listing_ts,
    sql_true,
)


def edge(coll: str, fc: str, fid: str, tc: str, tid: str, **doc: object) -> GEdge:
    return GEdge(coll, fc, fid, tc, tid, dict(doc))


def world(nodes: dict, edges: list[GEdge], *, org: str = "o") -> Snapshot:
    keys = list(nodes)
    sk = {k: i + 1 for i, k in enumerate(keys)}
    to: dict = {}
    frm: dict = {}
    for e in edges:
        to.setdefault(e.to, []).append(e)
        frm.setdefault(e.frm, []).append(e)
    parents = {e.frm for e in edges if e.coll == "nodeRelations" and e.to in nodes}
    return Snapshot(nodes, to, frm, sk, parents, org)


APP, G, REC, CHILD, USER, TEAM = ("apps", "a"), ("recordGroups", "g"), ("records", "r"), ("records", "c"), ("users", "u"), ("teams", "t")


def basic() -> Snapshot:
    nodes = {APP: {}, G: {"connectorId": "a", "orgId": "o", "hideChildren": "yes"}, REC: {"connectorId": "a", "orgId": "o", "recordName": "R", "mimeType": "application/vnd.folder"},
             CHILD: {"connectorId": "a", "orgId": "o", "recordName": "Kid", "isDeleted": 0}, USER: {}, TEAM: {}}
    edges = [edge("nodeRelations", *REC, *CHILD, relationshipType="PARENT_CHILD"), edge("belongsTo", *CHILD, *G),
             edge("permission", *USER, *CHILD, type="USER", role="READER"), edge("permission", *USER, *TEAM, type="USER"),
             edge("userAppRelation", *USER, *APP)]
    return world(nodes, edges)


def test_a_record_has_its_app_group_parent_and_flags() -> None:
    snap = basic()
    d = derive({CHILD, REC}, snap)
    kid, folder = d.records[snap.sk[CHILD]], d.records[snap.sk[REC]]
    assert (kid["app_sk"], kid["group_sk"], kid["parent_sk"], kid["name"], kid["granted"], kid["deleted"]) == (snap.sk[APP], snap.sk[G], snap.sk[REC], "Kid", True, False)
    assert folder["parent_sk"] is None and folder["group_sk"] is None
    assert snap.leaf(CHILD) and not snap.leaf(REC)


def test_a_second_parent_and_a_second_group_are_links() -> None:
    nodes = {APP: {}, G: {"connectorId": "a"}, ("recordGroups", "g2"): {"connectorId": "a"}, REC: {"connectorId": "a"}, ("records", "p2"): {"connectorId": "a"}, CHILD: {"connectorId": "a"}}
    edges = [edge("nodeRelations", *REC, *CHILD, relationshipType="PARENT_CHILD"), edge("nodeRelations", "records", "p2", *CHILD, relationshipType="ATTACHMENT"),
             edge("belongsTo", *CHILD, *G), edge("belongsTo", *CHILD, "recordGroups", "g2"), edge("nodeRelations", *REC, *CHILD, relationshipType="BOOKMARK")]
    snap = world(nodes, edges)
    d = derive({CHILD}, snap)
    row = d.records[snap.sk[CHILD]]
    primary_parent = min(snap.sk[REC], snap.sk[("records", "p2")])
    primary_group = min(snap.sk[G], snap.sk[("recordGroups", "g2")])
    assert (row["parent_sk"], row["group_sk"]) == (primary_parent, primary_group)
    assert d.links == {(max(snap.sk[REC], snap.sk[("records", "p2")]), LINK_HIERARCHY, snap.sk[CHILD]), (max(snap.sk[G], snap.sk[("recordGroups", "g2")]), LINK_GROUP, snap.sk[CHILD])}


def test_grants_are_read_from_both_ends_and_the_membership_is_a_closure_row() -> None:
    snap = basic()
    d = derive({USER, CHILD, APP}, snap)
    assert d.grants[(snap.sk[USER], snap.sk[CHILD], "USER")] == ("READER", snap.sk[APP], True)
    assert d.grants[(snap.sk[USER], snap.sk[APP], UAR)] == (None, snap.sk[APP], False)
    assert (snap.sk[USER], snap.sk[TEAM], 0) in d.closure and d.closure[(snap.sk[USER], snap.sk[TEAM], 0)] == "membership"
    assert d.closure[(snap.sk[USER], snap.sk[USER], 0)] == "self"
    assert not any(k[1] == snap.sk[TEAM] for k in d.grants)


def test_a_grant_from_a_node_that_does_not_exist_is_not_read() -> None:
    nodes = {APP: {}, REC: {"connectorId": "a"}}
    snap = world(nodes, [edge("permission", "users", "ghost", *REC, type="USER")])
    snap.sk[("users", "ghost")] = 99
    assert derive({REC}, snap).grants == {}
    assert derive({REC}, snap).records[snap.sk[REC]]["granted"] is False


def test_a_node_that_is_gone_is_reported_and_derives_no_row() -> None:
    snap = basic()
    del snap.nodes[CHILD]
    d = derive({CHILD}, snap)
    assert d.gone == {snap.sk[CHILD]} and d.records == {} and snap.sk[CHILD] in d.link_children and snap.sk[CHILD] in d.grant_nodes


@pytest.mark.parametrize(("coll", "frm", "to", "doc", "ends"), [
    ("nodeRelations", ("records", "p"), ("records", "c"), {"relationshipType": "PARENT_CHILD"}, {("records", "c")}),
    ("nodeRelations", ("records", "p"), ("records", "c"), {"relationshipType": "BOOKMARK"}, set()),
    ("belongsTo", ("records", "c"), ("recordGroups", "g"), {}, {("records", "c")}),
    ("permission", ("users", "u"), ("records", "r"), {}, {("records", "r")}),
    ("permission", ("users", "u"), ("teams", "t"), {}, {("users", "u")}),
    ("permission", ("users", "u"), ("blocks", "b"), {}, {("users", "u")}),
    ("userAppRelation", ("users", "u"), ("apps", "a"), {}, {("apps", "a")}),
    ("authenticatedAs", ("users", "u"), ("users", "acct"), {}, {("users", "u")}),
    ("isOfType", ("records", "r"), ("files", "r"), {}, set()),
])
def test_the_ends_an_edge_changes(coll: str, frm: tuple, to: tuple, doc: dict, ends: set) -> None:
    assert edge_ends(coll, frm, to, doc) == ends


def test_numbers_and_flags_are_read_the_way_the_walk_reads_them() -> None:
    assert listing_ts({"s": "12"}, "s", "i") == 12 and listing_ts({"i": 7}, "s", "i") == 7 and listing_ts({}, "s", "i") == 0
    assert listing_ts({"s": True}, "s", "i") is None and listing_ts({"s": "x"}, "s", "i") is None and listing_ts({"s": 2 ** 70}, "s", "i") is None
    assert [flag(v) for v in (True, False, None, 0, 1, {"a": None}, "", "false", "False", "true", "t", 2)] == [True, False, False, False, True, False, False, False, False, True, True, False]
    assert [sql_true(v) for v in (True, "TRUE", " yes ", 1, 1.0, 2, "no", False, None, "1")] == [True, True, True, True, False, False, False, False, False, True]
    assert app_id_of(APP, {}) == "a" and app_id_of(REC, {"connectorId": "x"}) == "x" and app_id_of(REC, {"connectorId": ""}) is None and app_id_of(REC, {}) is None


# ---- the closure and the grants are only what the walk of the graph admits ------------------------------------------------------------------------------
def closure_of(nodes: dict, edges: list[GEdge], user: tuple = USER, *, org: str = "o") -> dict:
    snap = world(nodes, edges, org=org)
    d = derive({user}, snap)
    names = {v: k for k, v in snap.sk.items()}
    return {(names[u][1], names[p][1], "-" if a == 0 else names[a][1]): via for (u, p, a), via in d.closure.items()}


def test_a_source_account_must_be_a_user_not_any_node() -> None:
    """A user linked to a team in a connector gets nothing from it: the walk reads the link only to a user."""
    nodes = {APP: {"orgId": "o"}, USER: {}, TEAM: {}}
    got = closure_of(nodes, [edge("authenticatedAs", *USER, *TEAM, connectorId="a")])
    assert got == {("u", "u", "-"): "self"}


def test_a_connector_of_another_organisation_is_no_link() -> None:
    """A badge member through an app of another organisation reaches nothing: the walk gates only on the organisation's own connectors."""
    acct, group = ("users", "acct"), ("groups", "grp")
    nodes = {APP: {"orgId": "someone-else"}, USER: {}, acct: {}, group: {}}
    edges = [edge("authenticatedAs", *USER, *acct, connectorId="a"), edge("permission", *acct, *group, type="USER")]
    assert closure_of(nodes, edges) == {("u", "u", "-"): "self"}
    nodes[APP] = {"orgId": "o"}
    assert closure_of(nodes, edges) == {("u", "u", "-"): "self", ("u", "acct", "a"): "link", ("u", "grp", "a"): "link_badge"}


def test_a_source_account_that_is_not_a_node_has_no_link_and_no_badges() -> None:
    nodes = {APP: {"orgId": "o"}, USER: {}}
    edges = [edge("authenticatedAs", *USER, "users", "gone", connectorId="a"), edge("permission", "users", "gone", *TEAM, type="USER")]
    assert closure_of(nodes, edges) == {("u", "u", "-"): "self"}


def test_a_connector_that_is_missing_or_named_by_no_text_is_no_link() -> None:
    acct = ("users", "acct")
    nodes = {APP: {"orgId": "o"}, USER: {}, acct: {}}
    for doc in ({}, {"connectorId": 7}, {"connectorId": "nope"}):
        assert closure_of(nodes, [edge("authenticatedAs", *USER, *acct, **doc)]) == {("u", "u", "-"): "self"}


def test_a_membership_needs_a_user_typed_permission_and_an_existing_group_and_an_organisation_that_exists() -> None:
    nodes = {USER: {}, TEAM: {}, ("organizations", "org"): {}}
    assert closure_of(nodes, [edge("permission", *USER, *TEAM, type="TEAM")]) == {("u", "u", "-"): "self"}                # another type is no membership
    assert closure_of(nodes, [edge("permission", *USER, "teams", "gone", type="USER")]) == {("u", "u", "-"): "self"}      # a group that does not exist
    assert closure_of(nodes, [edge("belongsTo", *USER, "organizations", "gone")]) == {("u", "u", "-"): "self"}            # an organisation that does not exist
    assert closure_of(nodes, [edge("permission", *USER, *TEAM, type="USER"), edge("belongsTo", *USER, "organizations", "org")]) == {
        ("u", "u", "-"): "self", ("u", "t", "-"): "membership", ("u", "org", "-"): "org"}


def test_only_a_principal_grants() -> None:
    """A record that points a permission edge at another record grants nothing and does not make it granted."""
    nodes = {APP: {}, REC: {"connectorId": "a"}, CHILD: {"connectorId": "a"}}
    snap = world(nodes, [edge("permission", *REC, *CHILD, type="USER", role="OWNER"), edge("userAppRelation", *REC, *APP)])
    d = derive({CHILD, APP}, snap)
    assert d.grants == {} and d.records[snap.sk[CHILD]]["granted"] is False
    assert set(PRINCIPALS) == {"users", "groups", "roles", "teams", "organizations"}


def test_the_primary_parent_is_a_parent_child_one_in_the_same_connector_then_the_lowest_sk() -> None:
    other_app = ("apps", "b")
    att, foreign, pc = ("records", "att"), ("records", "foreign"), ("records", "pc")
    nodes = {APP: {}, other_app: {}, att: {"connectorId": "a"}, foreign: {"connectorId": "b"}, pc: {"connectorId": "a"}, CHILD: {"connectorId": "a"}}
    edges = [edge("nodeRelations", *att, *CHILD, relationshipType="ATTACHMENT"), edge("nodeRelations", *foreign, *CHILD, relationshipType="PARENT_CHILD"),
             edge("nodeRelations", *pc, *CHILD, relationshipType="PARENT_CHILD")]
    snap = world(nodes, edges)
    d = derive({CHILD}, snap)
    assert d.records[snap.sk[CHILD]]["parent_sk"] == snap.sk[pc]
    assert d.links == {(snap.sk[att], LINK_HIERARCHY, snap.sk[CHILD]), (snap.sk[foreign], LINK_HIERARCHY, snap.sk[CHILD])}


def test_the_primary_group_is_one_of_the_records_own_connector_first() -> None:
    other = ("apps", "b")
    own, foreign = ("recordGroups", "own"), ("recordGroups", "foreign")
    nodes = {APP: {}, other: {}, foreign: {"connectorId": "b"}, own: {"connectorId": "a"}, CHILD: {"connectorId": "a"}}   # the foreign group has the lower sk
    snap = world(nodes, [edge("belongsTo", *CHILD, *foreign), edge("belongsTo", *CHILD, *own)])
    assert snap.sk[foreign] < snap.sk[own] and derive({CHILD}, snap).records[snap.sk[CHILD]]["group_sk"] == snap.sk[own]


def test_a_record_row_has_exactly_the_columns_the_writer_takes() -> None:
    from app.services.graph_db.postgres.kg.writers import RECORD_COLS
    snap = basic()
    row = derive({CHILD}, snap).records[snap.sk[CHILD]]
    assert set(row) == set(RECORD_COLS) - {"sk"}


def test_a_snapshot_without_its_organisation_is_refused() -> None:
    """The gate on a connector compares its organisation with the snapshot's: an empty one would let every connector through."""
    snap = world({APP: {"orgId": "o"}, USER: {}}, [], org="")
    with pytest.raises(ValueError, match="organisation"):
        derive({USER}, snap)


@pytest.mark.parametrize("source", [("apps", "a"), ("recordGroups", "g"), ("records", "c")])
def test_an_app_a_group_of_records_or_a_record_is_no_grant_source(source: tuple) -> None:
    """A knowledge base is an app: its node, like a record group or a record, can point a permission edge at a record and grants nothing."""
    nodes = {APP: {"type": "KB"}, G: {"connectorId": "a"}, CHILD: {"connectorId": "a"}, REC: {"connectorId": "a"}}
    snap = world(nodes, [edge("permission", *source, *REC, type="USER", role="OWNER")])
    d = derive({REC, source}, snap)
    assert d.grants == {} and d.records[snap.sk[REC]]["granted"] is False
