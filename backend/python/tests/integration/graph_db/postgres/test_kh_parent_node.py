"""The knowledge hub's parent of a node on PostgreSQL, for the same tree as ``../test_kh_parent_node_real_backends.py`` on
Neo4j and ArangoDB: a drive with a folder, a file in the folder, a file under that file and a file directly in the drive.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import logging

import pytest

from app.services.graph_db.interface.graph_db_provider import AccessCheck
from app.services.graph_db.postgres.provider import PostgresGraphProvider

from .pg_support import new_org
from .test_kg_derivation import ctx, edge

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

FOLDER = "text/directory"


async def _tree(p: PostgresGraphProvider, org: str) -> None:
    def record(key: str, name: str, **kw: object) -> dict:
        return {"_key": key, "recordName": name, "connectorId": "app1", "connectorName": "DRIVE WORKSPACE", "orgId": org,
                "recordType": "FILE", "origin": "CONNECTOR", **kw}

    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE WORKSPACE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": "drive", "groupName": "drive", "groupType": "DRIVE", "connectorId": "app1",
                                     "connectorName": "DRIVE WORKSPACE", "orgId": org}], "recordGroups")
        await p.batch_upsert_nodes([record("docs", "docs", mimeType=FOLDER), record("guide", "guide", mimeType="application/pdf"),
                                    record("figure", "figure", mimeType="application/pdf"), record("loose", "loose", mimeType="application/pdf")], "records")
        await p.batch_create_edges([edge("docs", "records", "guide", "records", relationshipType="PARENT_CHILD"),
                                    edge("guide", "records", "figure", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
        await p.batch_create_edges([edge(r, "records", "drive", "recordGroups") for r in ("docs", "guide", "figure", "loose")], "belongsTo")
        # As the processor writes a nested record: it inherits from the record above it, a top one from the group.
        await p.batch_create_edges([edge("guide", "records", "docs", "records"), edge("figure", "records", "guide", "records"),
                                    edge("docs", "records", "drive", "recordGroups"), edge("loose", "records", "drive", "recordGroups")],
                                   "inheritPermissions")


@pytest.fixture
async def tree(pg_db) -> tuple[PostgresGraphProvider, str]:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    await _tree(p, org)
    return p, org


async def _parent(tree: tuple[PostgresGraphProvider, str], node_id: str) -> dict | None:
    p, org = tree
    with ctx(org):
        return await p.get_knowledge_hub_parent_node(node_id, [FOLDER])


async def test_a_record_under_a_folder_has_the_folder_as_its_parent(tree) -> None:
    assert await _parent(tree, "guide") == {"id": "docs", "name": "docs", "nodeType": "folder", "subType": "FILE"}


async def test_a_record_under_a_record_that_is_not_a_folder_has_it_as_a_record_parent(tree) -> None:
    assert await _parent(tree, "figure") == {"id": "guide", "name": "guide", "nodeType": "record", "subType": "FILE"}


async def test_a_record_directly_in_a_group_has_the_group_as_its_parent(tree) -> None:
    assert await _parent(tree, "loose") == {"id": "drive", "name": "drive", "nodeType": "recordGroup", "subType": "DRIVE"}


async def test_a_group_has_its_app_as_parent_and_the_app_has_none(tree) -> None:
    assert await _parent(tree, "drive") == {"id": "app1", "name": "Drive", "nodeType": "app", "subType": "DRIVE WORKSPACE"}
    assert await _parent(tree, "app1") is None


def _allow_every_node(p: PostgresGraphProvider, monkeypatch: pytest.MonkeyPatch) -> None:
    """The breadcrumbs drop what the user cannot see; these tests are about the walk, not access."""
    async def check_access(user_key: str, org_id: str, *, node_ids=(), **_: object) -> AccessCheck:
        return AccessCheck(node_ids=frozenset(node_ids))
    monkeypatch.setattr(p, "check_access", check_access)


async def _breadcrumb_ids(tree: tuple[PostgresGraphProvider, str], node_id: str) -> list[str]:
    p, org = tree
    with ctx(org):
        return [step["id"] for step in await p.get_knowledge_hub_breadcrumbs(node_id, "user-1", org)]


async def _assert_trail(p: PostgresGraphProvider, org: str, monkeypatch: pytest.MonkeyPatch, *above: str) -> None:
    """The breadcrumbs reach "report" from the app through ``above``, the last of them its parent node."""
    _allow_every_node(p, monkeypatch)
    trail = await _breadcrumb_ids((p, org), "report")
    assert trail == ["app1", *above, "report"]
    assert trail[-2] == (await _parent((p, org), "report"))["id"]


async def _groups_and_report(p: PostgresGraphProvider, org: str, *, groups: dict[str, str], home: str | None) -> None:
    """Groups named in ``groups`` (name to id), each under the app, and a top-level record "report", its own group ``home``."""
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "app1", "name": "Drive", "type": "DRIVE WORKSPACE", "orgId": org}], "apps")
        await p.batch_upsert_nodes([{"_key": gid, "groupName": name, "groupType": "DRIVE", "connectorId": "app1",
                                     "connectorName": "DRIVE WORKSPACE", "orgId": org} for name, gid in groups.items()], "recordGroups")
        await p.batch_create_edges([edge(gid, "recordGroups", "app1", "apps") for gid in groups.values()], "belongsTo")
        await p.batch_upsert_nodes([{"_key": "report", "recordName": "report", "connectorId": "app1", "connectorName": "DRIVE WORKSPACE",
                                     "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "mimeType": "application/pdf",
                                     **({"recordGroupId": groups[home]} if home else {})}], "records")


async def _member(p: PostgresGraphProvider, org: str, group_id: str, *, hierarchy: bool, inherit: bool = False) -> None:
    with ctx(org):
        await p.batch_create_edges([edge("report", "records", group_id, "recordGroups")], "belongsTo")
        if inherit:
            await p.batch_create_edges([edge("report", "records", group_id, "recordGroups")], "inheritPermissions")
        if hierarchy:
            await p.batch_create_edges([edge(group_id, "recordGroups", "report", "records", relationshipType="PARENT_CHILD")], "nodeRelations")


def _ordered_ids() -> tuple[str, str]:
    rest = new_org()
    return f"0{rest}", f"f{rest}"


@pytest.mark.parametrize("home_written_first", [True, False], ids=["home-written-first", "shared-written-first"])
@pytest.mark.parametrize("home_sorts_first", [True, False], ids=["home-id-first", "shared-id-first"])
async def test_a_top_level_record_shared_into_a_second_group_has_its_own_group_as_parent(
    pg_db, home_sorts_first: bool, home_written_first: bool,
) -> None:
    # As the processor writes a record shared into a second group: membership and a hierarchy edge from each group.
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    low, high = _ordered_ids()
    home_id, shared_id = (low, high) if home_sorts_first else (high, low)
    await _groups_and_report(p, org, groups={"home": home_id, "0S:someone": shared_id}, home="home")
    for group_id in ((home_id, shared_id) if home_written_first else (shared_id, home_id)):
        await _member(p, org, group_id, hierarchy=True, inherit=group_id == home_id)

    assert await _parent((p, org), "report") == {"id": home_id, "name": "home", "nodeType": "recordGroup", "subType": "DRIVE"}


async def test_a_record_in_a_group_without_a_hierarchy_edge_has_the_group_that_has_one_as_parent(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    low, high = _ordered_ids()
    await _groups_and_report(p, org, groups={"member-only": low, "listed": high}, home=None)
    await _member(p, org, low, hierarchy=False)
    await _member(p, org, high, hierarchy=True)

    assert await _parent((p, org), "report") == {"id": high, "name": "listed", "nodeType": "recordGroup", "subType": "DRIVE"}
    await _assert_trail(p, org, monkeypatch, high)


async def test_a_record_under_a_folder_and_shared_into_a_group_has_the_folder_as_parent(tree) -> None:
    p, org = tree
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "0S", "groupName": "0S:someone", "groupType": "DRIVE", "connectorId": "app1",
                                     "connectorName": "DRIVE WORKSPACE", "orgId": org}], "recordGroups")
        await p.batch_create_edges([edge("guide", "records", "0S", "recordGroups")], "belongsTo")
        await p.batch_create_edges([edge("0S", "recordGroups", "guide", "records", relationshipType="PARENT_CHILD")], "nodeRelations")

    assert await _parent(tree, "guide") == {"id": "docs", "name": "docs", "nodeType": "folder", "subType": "FILE"}


async def test_a_record_with_only_a_membership_has_that_group_as_parent(pg_db) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    await _groups_and_report(p, org, groups={"member-only": "g-member"}, home=None)
    await _member(p, org, "g-member", hierarchy=False)

    assert await _parent((p, org), "report") == {"id": "g-member", "name": "member-only", "nodeType": "recordGroup", "subType": "DRIVE"}


def _group_parent(group_id: str, name: str) -> dict:
    return {"id": group_id, "name": name, "nodeType": "recordGroup", "subType": "DRIVE"}


@pytest.mark.parametrize("home_sorts_first", [True, False], ids=["home-id-first", "listed-id-first"])
async def test_the_records_own_group_wins_over_a_group_with_the_hierarchy_edge(pg_db, monkeypatch: pytest.MonkeyPatch, home_sorts_first: bool) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    low, high = _ordered_ids()
    home_id, listed_id = (low, high) if home_sorts_first else (high, low)
    await _groups_and_report(p, org, groups={"home": home_id, "listed": listed_id}, home="home")
    await _member(p, org, home_id, hierarchy=False, inherit=True)
    await _member(p, org, listed_id, hierarchy=True)

    assert await _parent((p, org), "report") == _group_parent(home_id, "home")
    await _assert_trail(p, org, monkeypatch, home_id)


async def test_the_records_own_group_wins_over_a_lower_id_record_it_belongs_to(pg_db) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    low, high = _ordered_ids()
    await _groups_and_report(p, org, groups={"home": high}, home="home")
    await _member(p, org, high, hierarchy=False, inherit=True)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": low, "recordName": "project", "connectorId": "app1", "connectorName": "DRIVE WORKSPACE",
                                     "orgId": org, "recordType": "FILE", "origin": "CONNECTOR", "mimeType": FOLDER}], "records")
        await p.batch_create_edges([edge("report", "records", low, "records")], "belongsTo")

    assert await _parent((p, org), "report") == _group_parent(high, "home")


@pytest.mark.parametrize("low_written_first", [True, False], ids=["low-written-first", "high-written-first"])
async def test_a_record_whose_own_group_it_no_longer_belongs_to_has_the_lowest_listed_group(
    pg_db, monkeypatch: pytest.MonkeyPatch, low_written_first: bool,
) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    low, high = _ordered_ids()
    left = f"5{low[1:]}"
    await _groups_and_report(p, org, groups={"left": left, "low": low, "high": high}, home="left")
    with ctx(org):
        await p.batch_create_edges([edge(left, "recordGroups", "report", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
    for group_id in ((low, high) if low_written_first else (high, low)):
        await _member(p, org, group_id, hierarchy=True)

    assert await _parent((p, org), "report") == _group_parent(low, "low")
    await _assert_trail(p, org, monkeypatch, low)


@pytest.mark.parametrize("low_written_first", [True, False], ids=["low-written-first", "high-written-first"])
@pytest.mark.parametrize("own_group", [True, False], ids=["own-group-high", "no-own-group"])
async def test_a_record_in_several_groups_without_any_hierarchy_edge_has_its_own_group_else_the_lowest(
    pg_db, monkeypatch: pytest.MonkeyPatch, own_group: bool, low_written_first: bool,
) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    low, high = _ordered_ids()
    await _groups_and_report(p, org, groups={"low": low, "high": high}, home="high" if own_group else None)
    for group_id in ((low, high) if low_written_first else (high, low)):
        await _member(p, org, group_id, hierarchy=False)

    expected = _group_parent(high, "high") if own_group else _group_parent(low, "low")
    assert await _parent((p, org), "report") == expected
    await _assert_trail(p, org, monkeypatch, expected["id"])


async def test_a_group_hierarchy_edge_without_membership_is_not_a_parent(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    low, high = _ordered_ids()
    await _groups_and_report(p, org, groups={"ghost": low, "home": high}, home=None)
    await _member(p, org, high, hierarchy=False)
    with ctx(org):
        await p.batch_create_edges([edge(low, "recordGroups", "report", "records", relationshipType="PARENT_CHILD")], "nodeRelations")

    assert await _parent((p, org), "report") == _group_parent(high, "home")
    await _assert_trail(p, org, monkeypatch, high)


async def test_a_record_with_two_record_parents_has_the_lowest_id_parent(pg_db, monkeypatch: pytest.MonkeyPatch) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    low, high = _ordered_ids()
    await _groups_and_report(p, org, groups={"drive": "drive"}, home="drive")
    await _member(p, org, "drive", hierarchy=False)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": key, "recordName": name, "connectorId": "app1", "connectorName": "DRIVE WORKSPACE", "orgId": org,
                                     "recordType": "FILE", "origin": "CONNECTOR", "mimeType": FOLDER}
                                    for key, name in ((high, "folder-high"), (low, "folder-low"))], "records")
        for folder in (high, low):
            await p.batch_create_edges([edge(folder, "records", "report", "records", relationshipType="PARENT_CHILD")], "nodeRelations")
            await p.batch_create_edges([edge(folder, "records", "drive", "recordGroups")], "belongsTo")

    assert await _parent((p, org), "report") == {"id": low, "name": "folder-low", "nodeType": "folder", "subType": "FILE"}
    await _assert_trail(p, org, monkeypatch, "drive", low)


async def test_a_record_with_only_inherit_edges_to_several_groups_has_the_lowest(pg_db) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    low, high = _ordered_ids()
    await _groups_and_report(p, org, groups={"low": low, "high": high}, home=None)
    with ctx(org):
        for group_id in (high, low):
            await p.batch_create_edges([edge("report", "records", group_id, "recordGroups")], "inheritPermissions")

    assert await _parent((p, org), "report") == _group_parent(low, "low")


@pytest.mark.parametrize("home_written_first", [True, False], ids=["home-written-first", "shared-written-first"])
@pytest.mark.parametrize("home_sorts_first", [True, False], ids=["home-id-first", "shared-id-first"])
async def test_the_breadcrumbs_of_a_shared_top_level_record_go_through_its_own_group(
    pg_db, monkeypatch: pytest.MonkeyPatch, home_sorts_first: bool, home_written_first: bool,
) -> None:
    p, org = PostgresGraphProvider(logging.getLogger("kh-parent"), pg_db), new_org()
    _allow_every_node(p, monkeypatch)
    low, high = _ordered_ids()
    home_id, shared_id = (low, high) if home_sorts_first else (high, low)
    await _groups_and_report(p, org, groups={"home": home_id, "0S:someone": shared_id}, home="home")
    for group_id in ((home_id, shared_id) if home_written_first else (shared_id, home_id)):
        await _member(p, org, group_id, hierarchy=True, inherit=group_id == home_id)

    trail = await _breadcrumb_ids((p, org), "report")

    assert trail == ["app1", home_id, "report"]
    assert trail[-2] == (await _parent((p, org), "report"))["id"]


async def test_the_breadcrumbs_of_a_record_under_a_folder_shared_into_a_group_go_through_the_folder(
    tree, monkeypatch: pytest.MonkeyPatch,
) -> None:
    p, org = tree
    _allow_every_node(p, monkeypatch)
    with ctx(org):
        await p.batch_upsert_nodes([{"_key": "0S", "groupName": "0S:someone", "groupType": "DRIVE", "connectorId": "app1",
                                     "connectorName": "DRIVE WORKSPACE", "orgId": org}], "recordGroups")
        await p.batch_create_edges([edge(g, "recordGroups", "app1", "apps") for g in ("drive", "0S")], "belongsTo")
        await p.batch_create_edges([edge("guide", "records", "0S", "recordGroups")], "belongsTo")
        await p.batch_create_edges([edge("0S", "recordGroups", "guide", "records", relationshipType="PARENT_CHILD")], "nodeRelations")

    assert await _breadcrumb_ids(tree, "guide") == ["app1", "drive", "docs", "guide"]
