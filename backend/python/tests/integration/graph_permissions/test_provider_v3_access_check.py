"""The shared batch access check on the v3 scope-arms graph, on both backends.

`check_access` must give the knowledge-hub listing's answer for every
node, with three deliberate differences: hideChildren is navigation only, links
are not hierarchy, and the node's org must be the request's. The
graph is the scope-arms fixture plus a link-only path and shared virtual record
ids, so every arm and every deliberate difference is exercised. The oracle is
Neo4j's listing, and each backend's check is held to it.
"""

from __future__ import annotations

import pytest

from .fixture_graph import ORG, USER_U, USER_V, bt, ip, nr, perm, rec, rg
from .loaders import arango_shape, load_into_arango, load_into_neo4j
from .test_provider_v3_scope_arms import (
    APP,
    EXPECTED_VISIBLE,
    GROUP,
    KB_APP,
    OTHER_ORG_NODE,
    _graph,
    _visible,
)

ITEM = {"record_type": "FILE", "connector_id": APP}

# The listing hides these below a hideChildren group; the access check does not.
BELOW_HIDE_CHILDREN = {"v3-hidden-r1", "v3-hide-folder-r1", "v3-dec-nested-hidden-r1"}
# Granted and STRICT, reachable from an accessible node only through a
# link. Links are stored apart from the hierarchy, so neither side walks it.
LINK_ONLY = "v3-linked-strict"
# A node is judged under the App its own connectorId names. These two hang under
# APP but name another connector or none -- fixture-only shapes -- so the check
# fails closed where the listing, walking from APP, shows them.
NAMES_NO_GATED_APP = {"v3-foreign-dec", "v3-noconn-dec"}


USER_U_ID = "user-u-id"
# Chat attachments live outside every App: only a grant on the record opens one.
ATTACHMENTS = {"connector_id": f"attachments_{ORG}", "origin": "UPLOAD"}
OWN_ATTACHMENT, OTHERS_ATTACHMENT = "v3-att-own", "v3-att-other"
LOOSE_CONNECTOR_RECORD = "v3-loose-connector"      # origin CONNECTOR, names no App
UPLOAD_UNDER_AN_APP = "v3-upload-in-app"            # an upload of an App, granted, no path
# Belongs to an undeclared group the walk reaches, with no hierarchy edge of its
# own: membership opens only a declared group's records.
UNDECLARED_MEMBER = "v3-open-member"
NO_ORIGIN_RECORD = "v3-no-origin"                   # no origin, names no App
# Two routes up to one ancestor; each record inherits along one route only. An
# upward walk that visits each vertex once, whatever the path, loses whichever
# record it first walks up the failing route.
VIA_LEFT, VIA_RIGHT = "v3-dag-via-left", "v3-dag-via-right"
# A granted, declared group whose org is not the request's (or is missing),
# with a record below it that inherits from it and belongs to nothing. The group
# is admitted from the App, so it is no seed and opens nothing below it; a seed
# re-check that also tested the seed's org would miss that and admit the record.
OFF_ORG_SEEDS = {"v3-orgless-dec": None, "v3-org2-dec": "org-2"}
BELOW_OFF_ORG_SEEDS = {f"{group}-r1" for group in OFF_ORG_SEEDS}


def _access_graph() -> tuple[list, list]:
    nodes, edges = _graph()
    by_id = {n["id"]: n for n in nodes}
    by_id[USER_U]["props"]["userId"] = USER_U_ID
    for node_id, vrid in (("v3-open-r1", "vr-shared"), ("v3-dec-r1", "vr-shared"),
                          ("v3-undec-r1", "vr-hidden"), (OTHER_ORG_NODE, "vr-other-org"),
                          ("v3-dec-deleted", "vr-deleted")):
        by_id[node_id]["props"]["virtualRecordId"] = vrid
    by_id["v3-open-r1"]["props"]["indexingStatus"] = "COMPLETED"
    by_id["v3-dec-r1"]["props"]["indexingStatus"] = "QUEUED"
    nodes = [
        *nodes,
        rec(LINK_ONLY, "Strict record behind a link", rule="STRICT", **ITEM),
        rec(OWN_ATTACHMENT, "My attachment", virtualRecordId="vr-att",
            indexingStatus="COMPLETED", **ATTACHMENTS),
        rec(OTHERS_ATTACHMENT, "Someone else's attachment", **ATTACHMENTS),
        rec(LOOSE_CONNECTOR_RECORD, "Connector record with no App",
            connector_id="no-such-app"),
        rec(NO_ORIGIN_RECORD, "Record with no origin", connector_id="no-such-app", origin=None),
        rec(UPLOAD_UNDER_AN_APP, "Granted upload with no path", origin="UPLOAD", rule="RESTRICTED", **ITEM),
        rec(UNDECLARED_MEMBER, "Member of an undeclared group only", **ITEM),
        *(rg(f"v3-dag-{side}", f"Route {side}", **GROUP) for side in ("top", "left", "right")),
        rec(VIA_LEFT, "Inherits on the left", **ITEM),
        rec(VIA_RIGHT, "Inherits on the right", **ITEM),
        *(n for group, org in OFF_ORG_SEEDS.items() for n in (
            rg(group, f"Declared group in org {org}", permissionModel="RECORD_GROUP_LEVEL", orgId=org, **GROUP),
            rec(f"{group}-r1", "Inherits from it, belongs to nothing", **ITEM))),
    ]
    edges = [*edges, nr("v3-open", LINK_ONLY, relationship_type="LINKED_TO"),
             nr("v3-open-r1", LINK_ONLY, relationship_type="LINKED_TO"),
             nr("v3-open-r1", "v3-dec-r1", relationship_type="BLOCKS"),
             perm(USER_U, LINK_ONLY), perm(USER_U, OWN_ATTACHMENT),
             perm(USER_U, LOOSE_CONNECTOR_RECORD), perm(USER_U, NO_ORIGIN_RECORD),
             perm(USER_V, OTHERS_ATTACHMENT), perm(USER_U, UPLOAD_UNDER_AN_APP),
             bt(UNDECLARED_MEMBER, "v3-open"),
             nr(APP, "v3-dag-top"), ip("v3-dag-top", APP),
             *(e for side in ("left", "right")
               for e in (nr("v3-dag-top", f"v3-dag-{side}"), ip(f"v3-dag-{side}", "v3-dag-top"),
                         nr(f"v3-dag-{side}", VIA_LEFT), nr(f"v3-dag-{side}", VIA_RIGHT))),
             ip(VIA_LEFT, "v3-dag-left"), ip(VIA_RIGHT, "v3-dag-right"),
             *(e for group in OFF_ORG_SEEDS for e in (
                 nr(APP, group), perm(USER_U, group),
                 nr(group, f"{group}-r1"), ip(f"{group}-r1", group)))]
    return nodes, edges


@pytest.fixture(scope="module")
async def access_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> dict:
    nodes, edges = _access_graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))
    return {"nodes": nodes, "edges": edges}


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


def _node_ids(graph) -> list[str]:
    return [n["id"] for n in graph["nodes"] if n["kind"] in ("Record", "RecordGroup", "App")]


async def _check(provider, ids) -> set[str]:
    return set((await provider.check_access(USER_U, ORG, node_ids=ids)).node_ids)


async def test_the_check_is_the_listing_up_to_decisions_81_56_82(
    access_graph, neo4j_provider, provider
) -> None:
    """Every node of the graph at once. The listing's own answer is the oracle, so
    a new listing arm the check lacks (or the reverse) fails here."""
    listing = await _visible(neo4j_provider) | await _visible(neo4j_provider, app_id=KB_APP)
    assert LINK_ONLY not in listing                  # a link is not hierarchy
    expected = ((listing | BELOW_HIDE_CHILDREN | {APP, KB_APP, OWN_ATTACHMENT})
                - NAMES_NO_GATED_APP)
    assert await _check(provider, _node_ids(access_graph)) == expected


async def test_hide_children_does_not_hide_access(access_graph, neo4j_provider, provider) -> None:
    assert "v3-hidden-r1" not in await _visible(neo4j_provider)
    assert await _check(provider, ["v3-hidden-r1"]) == {"v3-hidden-r1"}


async def test_a_link_is_not_a_path(access_graph, neo4j_provider, provider) -> None:
    """A STRICT node needs an accessible hierarchy path; a LINKED_TO edge from an
    accessible node is not one, whatever grant the node holds. Neither the check
    nor the knowledge hub walks it."""
    assert await _check(provider, [LINK_ONLY]) == set()
    assert LINK_ONLY not in await _visible(neo4j_provider)


async def test_a_link_is_stored_apart_from_the_hierarchy(access_graph, neo4j_provider) -> None:
    rows = await neo4j_provider.client.execute_query(
        "MATCH ({id: 'v3-open'})-[r]->({id: $id}) RETURN type(r) AS t, r.relationshipType AS rt",
        parameters={"id": LINK_ONLY},
    )
    assert rows == [{"t": "RECORD_LINK", "rt": "LINKED_TO"}]


async def test_a_link_is_stored_apart_from_the_hierarchy_on_arango(access_graph, arango_provider) -> None:
    query = "FOR e IN @@edges FILTER e._to == @to RETURN [PARSE_IDENTIFIER(e._from).key, e.relationshipType]"
    bind = {"to": f"records/{LINK_ONLY}"}
    links = await arango_provider.http_client.execute_aql(query, {**bind, "@edges": "recordLinks"})
    hierarchy = await arango_provider.http_client.execute_aql(query, {**bind, "@edges": "nodeRelations"})
    assert sorted(links) == [["v3-open", "LINKED_TO"], ["v3-open-r1", "LINKED_TO"]]
    assert hierarchy == []


async def test_linked_records_are_read_from_the_link_edges_and_checked(access_graph, provider) -> None:
    """The navigator's related records: links on RECORD_LINK, and only those the
    user may access."""
    linked = await provider.get_linked_records("v3-open-r1", ORG, USER_U, ["BLOCKS", "LINKED_TO"])
    assert {r["id"]: r["relationshipType"] for r in linked} == {"v3-dec-r1": "BLOCKS"}


async def test_another_orgs_node_is_denied(access_graph, provider) -> None:
    assert OTHER_ORG_NODE not in EXPECTED_VISIBLE
    assert await _check(provider, [OTHER_ORG_NODE]) == set()


async def test_a_seed_from_another_org_opens_nothing_below_it(access_graph, neo4j_provider, provider) -> None:
    listing = await _visible(neo4j_provider)
    assert not (set(OFF_ORG_SEEDS) | BELOW_OFF_ORG_SEEDS) & listing
    assert await _check(provider, [*OFF_ORG_SEEDS, *BELOW_OFF_ORG_SEEDS]) == set()


async def test_each_route_up_is_tried(access_graph, provider) -> None:
    assert await _check(provider, [VIA_LEFT, VIA_RIGHT]) == {VIA_LEFT, VIA_RIGHT}


async def test_an_empty_org_matches_nothing(access_graph, provider) -> None:
    """A node's org is compared with the request's; no org is no match,
    even where the store compares a missing value equal to it."""
    check = await provider.check_access(
        USER_U, "", node_ids=[APP, "v3-open-r1", OWN_ATTACHMENT], virtual_record_ids=["vr-shared"],
    )
    assert check.node_ids == frozenset() and check.records_by_vrid == {}


async def test_membership_opens_only_a_declared_group(access_graph, provider) -> None:
    assert await _check(provider, [UNDECLARED_MEMBER, "v3-open"]) == {"v3-open"}


async def test_record_details_follow_the_check(access_graph, provider) -> None:
    """Record details and content are gated by the batch check:
    details come back exactly for the records the check admits."""
    records = [n["id"] for n in access_graph["nodes"] if n["kind"] == "Record"]
    admitted = await _check(provider, records)
    assert admitted and set(records) - admitted
    for record_id in records:
        details = await provider.check_record_access_with_details(USER_U_ID, ORG, record_id)
        assert (details is not None) == (record_id in admitted), record_id


async def test_details_of_a_record_admitted_without_a_role_read_as_reader(access_graph, provider) -> None:
    """Connector items carry no role: a record the check admits
    through inheritance alone is described as read access."""
    details = await provider.check_record_access_with_details(USER_U_ID, ORG, "v3-open-r1")
    assert details is not None
    assert [(p["accessType"], p["relationship"]) for p in details["permissions"]] == [("CONNECTOR", "READER")]


async def test_deleted_placeholder_and_unknown_ids_are_denied(access_graph, provider) -> None:
    ids = ["v3-dec-deleted", "v3-dec-placeholder", "no-such-node", "v3-seed-deleted"]
    assert await _check(provider, ids) == set()


async def test_batch_shape(access_graph, provider) -> None:
    assert await _check(provider, []) == set()
    assert await _check(provider, ["v3-dec-r1", "v3-dec-r1", "", "v3-undec-r1"]) == {"v3-dec-r1"}


async def test_the_apps_themselves(access_graph, provider) -> None:
    assert await _check(provider, [APP, KB_APP]) == {APP, KB_APP}


async def test_a_virtual_record_id_resolves_to_its_smallest_accessible_record(
    access_graph, provider
) -> None:
    resolved = (await provider.check_access(
        USER_U, ORG, virtual_record_ids=["vr-shared", "vr-hidden", "vr-other-org", "vr-deleted", "vr-none"],
    )).records_by_vrid
    assert resolved == {"vr-shared": "v3-dec-r1"}      # "v3-dec-r1" < "v3-open-r1"


async def test_an_access_context_can_be_passed_in(access_graph, provider) -> None:
    access = await provider.get_knowledge_hub_access_v3(user_key=USER_U, org_id=ORG)
    ids = ["v3-dec-r1", "v3-undec-r1", "v3-seed"]
    assert (await provider.check_access(USER_U, ORG, node_ids=ids, access=access)).node_ids == {
        "v3-dec-r1", "v3-seed",
    }


async def test_a_record_outside_every_app_opens_only_to_its_own_grant(access_graph, provider) -> None:
    """A chat attachment has no hierarchy. A record naming no App that is not
    known to be an attachment is bad data, and fails closed even with a grant.
    Someone else's attachment is granted to them, not to this user; an upload
    of an App is judged by the App's hierarchy, whatever it is granted."""
    ids = [OWN_ATTACHMENT, OTHERS_ATTACHMENT, LOOSE_CONNECTOR_RECORD, NO_ORIGIN_RECORD, UPLOAD_UNDER_AN_APP]
    assert await _check(provider, ids) == {OWN_ATTACHMENT}


async def test_the_search_verifier_cites_a_finished_accessible_record(access_graph, provider) -> None:
    """Both vr-shared copies are accessible, but v3-dec-r1 is still indexing, so
    the other copy is cited; the attachment resolves through its own grant."""
    granted = (await provider.check_access(
        USER_U, ORG, virtual_record_ids=["vr-shared", "vr-att", "vr-hidden", "vr-other-org"],
        indexed_only=True,
    )).records_by_vrid
    assert granted == {"vr-shared": "v3-open-r1", "vr-att": OWN_ATTACHMENT}


async def test_the_search_verifier_keeps_to_the_scope(access_graph, provider) -> None:
    granted = (await provider.check_access(
        USER_U, ORG, virtual_record_ids=["vr-shared", "vr-att"],
        indexed_only=True, connector_ids=frozenset({APP}),
    )).records_by_vrid
    assert granted == {"vr-shared": "v3-open-r1"}


async def test_the_search_verifier_knows_no_stranger(access_graph, provider) -> None:
    assert (await provider.check_access(
        "no-such-user", ORG, virtual_record_ids=["vr-shared"], indexed_only=True,
    )).records_by_vrid == {}
