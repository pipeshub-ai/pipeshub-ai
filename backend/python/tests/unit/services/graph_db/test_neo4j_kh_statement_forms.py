"""The text rewrites a knowledge-hub statement goes through before it is sent.

Three pure functions decide what Neo4j is asked: lists moved inside one map
parameter, lists a statement does not read left out, and a grant tested from
the node instead of a list of the user's grants. A slip in any of them changes
a permission query silently, so their output is pinned here.
"""

import pytest

from app.models.permission import ORG_SHARE_PERMISSION_TYPES
from app.services.graph_db.neo4j import kh_scope
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

# An organization's edge is a grant only when it is an org-wide share.
ORG_SHARE_TYPES = "[" + ", ".join(f"'{t}'" for t in ORG_SHARE_PERMISSION_TYPES) + "]"


def test_lists_move_inside_one_map_and_longer_names_win() -> None:
    query = "MATCH (n) WHERE n.id IN $ids AND n.g IN $ids_granted AND n.o = $org RETURN n"
    out = kh_scope.lists_in_map(query, ("ids", "ids_granted"))
    assert out == "MATCH (n) WHERE n.id IN $kh_lists.ids AND n.g IN $kh_lists.ids_granted AND n.o = $org RETURN n"


def test_no_list_leaves_the_statement_as_it_is() -> None:
    query = "MATCH (n {id: $id}) RETURN n"
    assert kh_scope.lists_in_map(query, ()) == query


def test_only_the_lists_a_statement_reads_are_sent() -> None:
    query = "UNWIND $kh_page AS item MATCH (n {id: item.id}) WHERE n.x IN $gatedAppIds RETURN n"
    names = ("gatedAppIds", "grantedIds", "kh_page", "kh_placed")
    assert Neo4jProvider._kh_lists_read(query, names) == ("gatedAppIds", "kh_page")


def test_a_name_that_is_a_prefix_of_another_is_not_taken_for_it() -> None:
    assert Neo4jProvider._kh_lists_read("RETURN $granteeIds", ("grant", "granteeIds")) == ("granteeIds",)


def test_a_grant_test_is_read_from_the_node() -> None:
    out = Neo4jProvider._kh_grants_by_probe("MATCH (ca) WHERE ca.id IN $grantedIds RETURN ca")
    assert out == (
        "MATCH (ca) WHERE ((ca:Record OR ca:RecordGroup) AND ca.connectorId = $kh_conn"
        " AND NOT coalesce(ca.isDeleted, false)"
        " AND EXISTS { (ca)<-[kh_gp0:PERMISSION]-(kh_gr0)"
        " WHERE (kh_gr0:User OR kh_gr0:Group OR kh_gr0:Role OR kh_gr0:Teams"
        " OR (kh_gr0:Organization AND kh_gp0.type IN ['ORG', 'ORGANIZATION']))"
        " AND kh_gr0.id IN $kh_grantees }) RETURN ca"
    )


def test_each_grant_test_takes_its_own_grantee_variable() -> None:
    # Fragments nest (a test inside an EXISTS inside a quantified pattern): a
    # reused name would bind to the outer one. The edge is named for the same reason.
    out = Neo4jProvider._kh_grants_by_probe(
        "WHERE a.id IN $grantedIds OR EXISTS { MATCH (b) WHERE b.id IN $grantedIds } OR NOT start.id IN $grantedIds"
    )
    assert "$grantedIds" not in out
    for i, node in enumerate(("a", "b", "start")):
        assert f"({node})<-[kh_gp{i}:PERMISSION]-(kh_gr{i}) WHERE (kh_gr{i}:User OR" in out
        assert f"OR (kh_gr{i}:Organization AND kh_gp{i}.type IN {ORG_SHARE_TYPES}))" in out
        assert f"AND kh_gr{i}.id IN $kh_grantees" in out


def test_a_grant_test_of_another_shape_is_refused_not_dropped() -> None:
    with pytest.raises(ValueError):
        Neo4jProvider._kh_grants_by_probe("WHERE any(g IN $grantedIds WHERE g = n.id)")


def test_the_probe_names_what_makes_a_grant() -> None:
    """The node test must agree with the list it replaces: a record or record
    group of the connector, not deleted, granted by one of the grantees; and an
    organization's edge only when it is an org-wide share."""
    grants = Neo4jProvider._kh_v3_connector_grants_cypher()
    assert "(kh_grantee)-[kh_ge:PERMISSION]->(kh_g:Record|RecordGroup)" in grants
    assert "kh_g.connectorId = $connector_id AND NOT coalesce(kh_g.isDeleted, false)" in grants
    assert f"AND (NOT kh_grantee:Organization OR kh_ge.type IN {ORG_SHARE_TYPES})" in grants
    probe = Neo4jProvider._kh_grants_by_probe("x.id IN $grantedIds")
    assert "(x:Record OR x:RecordGroup)" in probe
    assert "x.connectorId = $kh_conn AND NOT coalesce(x.isDeleted, false)" in probe
    assert f"OR (kh_gr0:Organization AND kh_gp0.type IN {ORG_SHARE_TYPES}))" in probe
    # No other PERMISSION edge reaches the grant list from a grantee that can be
    # an organization: the linked source account's are a user's and its badges'.
    assert grants.count(":PERMISSION]->(") == grants.count("(kh_src)-[kh_e:PERMISSION]->(") + grants.count(
        "(kh_badge)-[:PERMISSION]->("
    ) + 1


def test_the_grantees_of_the_probe_are_the_grantees_of_the_grant_list() -> None:
    """Two statements derive who holds grants for a user in a connector: the grant
    list and the grantee ids the node-side test compares against. They must name
    the same badges and the same linked account."""
    import inspect

    grants = Neo4jProvider._kh_v3_connector_grants_cypher()
    grantees = inspect.getsource(Neo4jProvider._kh_v3_connector_grantees)
    mine = "(kh_b.connectorId IS NULL OR kh_b.connectorId = $connector_id)"
    assert mine in grants and mine in grantees
    for fragment in (
        "(u)-[:PERMISSION {{type: 'USER'}}]->(kh_b:Group|Role|Teams)",
        "(u)-[:BELONGS_TO]->(kh_org:Organization)",
        "(u)-[kh_link:AUTHENTICATED_AS]->(kh_src:User)",
        "WHERE kh_link.connectorId = $connector_id",
        "(kh_src)-[kh_e:PERMISSION]->(kh_b)",
    ):
        assert fragment in grantees, fragment
        assert fragment.replace("{{", "{").replace("}}", "}") in grants, fragment
    # The source account's organization grants nothing in either.
    assert "(kh_src)-[:BELONGS_TO]" not in grants and "(kh_src)-[:BELONGS_TO]" not in grantees
