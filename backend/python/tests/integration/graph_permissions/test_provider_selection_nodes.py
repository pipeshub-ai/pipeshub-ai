"""`get_selection_nodes` — what a search selection below app level covers —
on both backends.

The walk is structural: nested record groups under a selected group, and every
record under a selected folder or record along PARENT_CHILD/ATTACHMENT edges.
Deleted nodes cut the walk, links are not hierarchy, another org's node and an
id of the wrong kind contribute nothing, and a node reached twice is returned
once. Permissions are not applied here; `check_access` decides every hit.
"""

from __future__ import annotations

import pytest

from .fixture_graph import ATTACHMENT, DRIVE, ORG, app, bt, nr, rec, rg
from .loaders import arango_shape, load_into_arango, load_into_neo4j

pytestmark = pytest.mark.integration

APP = "sel-app"
FOLDER_MIME = "text/directory"

GROUP, NESTED, DEEPER, SIBLING = "sel-g1", "sel-g1a", "sel-g1a-x", "sel-g2"
DELETED_GROUP, BELOW_DELETED_GROUP, OTHER_ORG_GROUP = "sel-g-del", "sel-g-del-x", "sel-g-org2"

FOLDER, SUBFOLDER = "sel-f", "sel-f-sub"
FILE_A, FILE_B, ATTACHED, TWO_PARENTS = "sel-f-a", "sel-f-sub-b", "sel-f-a-att", "sel-two"
DELETED, BELOW_DELETED, LINKED, ELSEWHERE, OTHER_ORG = (
    "sel-f-del", "sel-f-del-c", "sel-linked", "sel-other", "sel-org2-r",
)


def _file(node_id: str, **props) -> dict:
    return rec(node_id, node_id, connector_id=APP, virtualRecordId=f"v-{node_id}", **props)


def _folder(node_id: str) -> dict:
    return rec(node_id, node_id, connector_id=APP, mimeType=FOLDER_MIME)


def _group(node_id: str, **props) -> dict:
    return rg(node_id, node_id, group_type="DRIVE", connector=DRIVE, connectorId=APP, **props)


def _selection_graph() -> tuple[list[dict], list[dict]]:
    other_org_group = _group(OTHER_ORG_GROUP)
    other_org_group["props"]["orgId"] = "org-2"
    other_org_record = _file(OTHER_ORG)
    other_org_record["props"]["orgId"] = "org-2"
    nodes = [
        app(APP, "Selection drive", connector=DRIVE, app_group="Google Workspace"),
        _group(GROUP), _group(NESTED), _group(DEEPER), _group(SIBLING),
        _group(DELETED_GROUP, isDeleted=True), _group(BELOW_DELETED_GROUP), other_org_group,
        _folder(FOLDER), _folder(SUBFOLDER),
        _file(FILE_A), _file(FILE_B), _file(ATTACHED), _file(TWO_PARENTS),
        _file(DELETED, isDeleted=True), _file(BELOW_DELETED), _file(LINKED),
        _file(ELSEWHERE), other_org_record,
    ]
    edges = [
        nr(APP, GROUP), nr(APP, SIBLING), nr(GROUP, NESTED), nr(NESTED, DEEPER),
        nr(GROUP, DELETED_GROUP), nr(DELETED_GROUP, BELOW_DELETED_GROUP),
        nr(GROUP, FOLDER), bt(FOLDER, GROUP),
        nr(FOLDER, FILE_A), nr(FOLDER, SUBFOLDER), nr(SUBFOLDER, FILE_B),
        nr(FILE_A, ATTACHED, ATTACHMENT),
        nr(FOLDER, TWO_PARENTS), nr(SUBFOLDER, TWO_PARENTS),
        nr(FOLDER, DELETED), nr(DELETED, BELOW_DELETED),
        nr(FILE_A, LINKED, "LINKED_TO"),
        nr(SIBLING, ELSEWHERE), bt(ELSEWHERE, SIBLING),
    ]
    return nodes, edges


@pytest.fixture(scope="module")
async def selection_graph(neo4j_provider, arango_provider, neo4j_settings, arango_settings) -> None:
    nodes, edges = _selection_graph()
    await load_into_neo4j(neo4j_settings, nodes, edges)
    await load_into_arango(arango_settings, *arango_shape(nodes, edges))


@pytest.fixture(params=["neo4j", "arango"])
def provider(request: pytest.FixtureRequest) -> object:
    return request.getfixturevalue(f"{request.param}_provider")


async def _nodes(provider, *, groups=(), records=(), exact=(), limit=1000, org=ORG) -> dict:
    return await provider.get_selection_nodes(
        org, group_ids=list(groups), record_ids=list(records),
        exact_record_ids=list(exact), limit=limit,
    )


def _ids(rows) -> set[str]:
    return {row["id"] for row in rows}


async def test_a_group_brings_the_groups_nested_in_it(selection_graph, provider) -> None:
    found = await _nodes(provider, groups=[GROUP])
    assert _ids(found["groups"]) == {GROUP, NESTED, DEEPER}
    assert {row["connectorId"] for row in found["groups"]} == {APP}
    assert found["records"] == []


async def test_a_nested_group_does_not_bring_its_parent(selection_graph, provider) -> None:
    assert _ids((await _nodes(provider, groups=[NESTED]))["groups"]) == {NESTED, DEEPER}


async def test_a_deleted_group_and_what_is_below_it_stay_out(selection_graph, provider) -> None:
    found = _ids((await _nodes(provider, groups=[GROUP, DELETED_GROUP]))["groups"])
    assert not {DELETED_GROUP, BELOW_DELETED_GROUP} & found


async def test_a_folder_brings_everything_under_it(selection_graph, provider) -> None:
    """Children, grandchildren and attachments, and the folders on the way
    (which hold no content); the deleted record cuts its branch, and a link is
    not a path."""
    found = await _nodes(provider, records=[FOLDER])
    assert _ids(found["records"]) == {FOLDER, SUBFOLDER, FILE_A, FILE_B, ATTACHED, TWO_PARENTS}
    vrids = {row["id"]: row["vrid"] for row in found["records"]}
    assert vrids[FILE_B] == f"v-{FILE_B}"
    assert vrids[FOLDER] is None and vrids[SUBFOLDER] is None
    assert {row["connectorId"] for row in found["records"]} == {APP}


async def test_a_record_reached_twice_is_returned_once(selection_graph, provider) -> None:
    rows = (await _nodes(provider, records=[FOLDER, SUBFOLDER]))["records"]
    assert sorted(row["id"] for row in rows).count(TWO_PARENTS) == 1


async def test_a_record_with_children_brings_them(selection_graph, provider) -> None:
    assert _ids((await _nodes(provider, records=[FILE_A]))["records"]) == {FILE_A, ATTACHED}


async def test_an_exact_record_comes_alone(selection_graph, provider) -> None:
    assert _ids((await _nodes(provider, exact=[FILE_A]))["records"]) == {FILE_A}


async def test_an_exact_folder_comes_alone_and_without_content(selection_graph, provider) -> None:
    rows = (await _nodes(provider, exact=[FOLDER]))["records"]
    assert [(row["id"], row["vrid"]) for row in rows] == [(FOLDER, None)]


async def test_subtree_and_exact_are_one_set(selection_graph, provider) -> None:
    found = await _nodes(provider, records=[SUBFOLDER], exact=[FILE_A, FILE_B])
    assert _ids(found["records"]) == {SUBFOLDER, FILE_A, FILE_B, TWO_PARENTS}


async def test_an_id_of_the_wrong_kind_contributes_nothing(selection_graph, provider) -> None:
    found = await _nodes(provider, groups=[FOLDER, APP], records=[GROUP, APP], exact=[GROUP])
    assert found == {"groups": [], "records": []}


async def test_another_orgs_nodes_contribute_nothing(selection_graph, provider) -> None:
    found = await _nodes(provider, groups=[OTHER_ORG_GROUP], records=[OTHER_ORG], exact=[OTHER_ORG])
    assert found == {"groups": [], "records": []}
    in_their_org = await _nodes(provider, records=[OTHER_ORG], org="org-2")
    assert _ids(in_their_org["records"]) == {OTHER_ORG}


async def test_an_unknown_id_contributes_nothing(selection_graph, provider) -> None:
    assert await _nodes(provider, groups=["nope"], records=["nope"], exact=["nope"]) == {
        "groups": [], "records": [],
    }


async def test_one_more_than_the_limit_tells_over_from_at(selection_graph, provider) -> None:
    assert len((await _nodes(provider, records=[FOLDER], limit=6))["records"]) == 6
    assert len((await _nodes(provider, records=[FOLDER], limit=5))["records"]) == 6
    assert len((await _nodes(provider, records=[FOLDER], limit=2))["records"]) == 3
    assert len((await _nodes(provider, groups=[GROUP], limit=1))["groups"]) == 2


async def test_the_access_check_reports_a_records_groups(selection_graph, provider) -> None:
    """`check_access` rows carry the groups a record belongs to, which is how a
    selected group admits its records. The App is APP_LEVEL-free and ungranted
    here, so the rows are read straight from the stage that builds them."""
    access = {"gated_app_ids": [APP], "grantee_ids": [], "by_connector": {APP: [FOLDER, ELSEWHERE]}}
    rows = await provider._kh_v3_accessible_rows(
        "no-user", ORG, [FOLDER, ELSEWHERE], [], access=access, transaction=None,
    )
    assert {row["id"]: sorted(row["groupIds"]) for row in rows} == {
        FOLDER: [GROUP], ELSEWHERE: [SIBLING],
    }
