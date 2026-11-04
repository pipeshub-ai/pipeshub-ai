"""The pure derivation of the typed graph (``kg.model``): what a node's rows are, from the documents and edges around it, with no database."""
from __future__ import annotations

import pytest

from app.services.graph_db.postgres.kg.model import (
    LINK_GROUP,
    LINK_HIERARCHY,
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
    assert folder["folder"] is True and folder["parent_sk"] is None and folder["group_sk"] is None
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


def test_a_node_of_another_organisation_is_marked_alien() -> None:
    nodes = {APP: {}, REC: {"connectorId": "a", "orgId": "someone-else"}}
    snap = world(nodes, [])
    assert derive({REC}, snap).records[snap.sk[REC]]["alien"] is True


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
    assert [flag(v) for v in (True, False, None, 0, 1, {"a": None}, "")] == [True, False, False, False, True, True, False]
    assert [sql_true(v) for v in (True, "TRUE", " yes ", 1, 1.0, 2, "no", False, None)] == [True, True, True, True, True, False, False, False, False]
    assert app_id_of(APP, {}) == "a" and app_id_of(REC, {"connectorId": "x"}) == "x" and app_id_of(REC, {"connectorId": ""}) is None and app_id_of(REC, {}) is None
