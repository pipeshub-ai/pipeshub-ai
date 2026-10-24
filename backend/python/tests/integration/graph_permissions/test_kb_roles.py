"""The role a user holds on a collection, on both backends: the highest of the
direct grant and the team grants wins, a team grant carries the user's role in
the team, and a retired role reads as READER. And
deleting a collection folder, which that role gates."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from .fixture_graph import KB, _node, app, bt, nr, perm, rec, rg
from .loaders import EDGE_TARGETS, NODE_TARGETS, load_into_arango, load_into_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

ORG = "org-kbr"
USER = "kbr-u"

# name -> (direct role, [(role in team, role on the team->collection edge)], extra props)
COLLECTIONS: dict[str, tuple[str | None, list[tuple[str, str]], dict]] = {
    "reader-direct-writer-team": ("READER", [("WRITER", "READER")], {}),
    "writer-direct-reader-team": ("WRITER", [("READER", "OWNER")], {}),
    "organizer-direct": ("ORGANIZER", [], {}),
    "Alpha team only": (None, [("COMMENTER", "OWNER")], {}),
    "hidden": ("OWNER", [], {"isHidden": True}),
    "other org": ("OWNER", [], {"orgId": "org-other"}),
}


def _org(node: dict) -> dict:
    if "orgId" in node["props"] and node["props"]["orgId"] != "org-other":
        node["props"]["orgId"] = ORG
    return node


def _graph() -> tuple[list[dict], list[dict]]:
    nodes = [_org(_node("User", USER, userId="kbr-m", email="kbr@example.com", fullName="KB Roles", isActive=True))]
    edges = []
    for i, (name, (direct, teams, extra)) in enumerate(COLLECTIONS.items()):
        kb = f"kbr-{i}"
        nodes.append(_org(app(kb, name, connector=KB, app_group="Local Storage", scope="personal", **extra)))
        if direct:
            edges.append(perm(USER, kb, role=direct))
        for t, (member_role, edge_role) in enumerate(teams):
            team = f"{kb}-team-{t}"
            nodes.append(_org(_node("Teams", team, name=team)))
            edges += [perm(USER, team, role=member_role), perm(team, kb, grant_type="TEAM", role=edge_role)]
    return nodes, edges


class _Neo4j:
    def __init__(self, provider, settings: dict) -> None:
        self.provider, self.settings = provider, settings

    async def load(self, nodes: list[dict], edges: list[dict]) -> None:
        await load_into_neo4j(self.settings, nodes, edges)

    async def drop(self) -> None:
        await self.provider.client.execute_query("MATCH (n) WHERE n.id STARTS WITH 'kbr-' DETACH DELETE n")

    async def item_role(self, node_type: str, kind: str, node_id: str) -> str | None:
        call = self.provider._get_permission_role_cypher(node_type, "node", "u")
        rows = await self.provider.client.execute_query(
            f"MATCH (node:{kind} {{id: $id}}) MATCH (u:User {{id: $user}}) {call} RETURN permission_role",
            parameters={"id": node_id, "user": USER, "org_id": ORG},
        )
        return rows[0]["permission_role"] if rows else None

    async def records_left(self, ids: list[str]) -> list[str]:
        rows = await self.provider.client.execute_query(
            "MATCH (r:Record) WHERE r.id IN $ids RETURN r.id AS id ORDER BY id", parameters={"ids": ids},
        )
        return [r["id"] for r in rows]


class _Arango:
    EDGES = ("permission", "belongsTo", "nodeRelations", "recordLinks", "userAppRelation",
             "inheritPermissions", "isOfType")

    def __init__(self, provider, settings: dict) -> None:
        self.provider, self.settings = provider, settings
        self.kinds: dict[str, str] = {}

    async def _aql(self, query: str, bind: dict) -> list:
        return await self.provider.http_client.execute_aql(query, bind) or []

    async def load(self, nodes: list[dict], edges: list[dict]) -> None:
        """The loader resolves edge ends among the nodes it is given, so edges to
        nodes loaded earlier are inserted here."""
        self.kinds.update({n["id"]: n["kind"] for n in nodes})
        await load_into_arango(self.settings, nodes, [])
        for edge in edges:
            doc = {"_from": f"{NODE_TARGETS[self.kinds[edge['from']]]}/{edge['from']}",
                   "_to": f"{NODE_TARGETS[self.kinds[edge['to']]]}/{edge['to']}", **edge["props"]}
            await self._aql("INSERT @doc IN @@c", {"doc": doc, "@c": EDGE_TARGETS[edge["type"]]})

    async def drop(self) -> None:
        for edges in self.EDGES:
            await self._aql(
                "FOR e IN @@c FILTER CONTAINS(e._from, '/kbr-') OR CONTAINS(e._to, '/kbr-') REMOVE e IN @@c",
                {"@c": edges},
            )
        for collection in set(NODE_TARGETS.values()):
            await self._aql("FOR d IN @@c FILTER STARTS_WITH(d._key, 'kbr-') REMOVE d IN @@c", {"@c": collection})

    async def item_role(self, node_type: str, kind: str, node_id: str) -> str | None:
        fragment = self.provider._get_permission_role_aql(node_type, "node", "u")
        rows = await self._aql(
            f"LET node = DOCUMENT(@node) LET u = DOCUMENT('users', @user) {fragment} "
            "RETURN IS_ARRAY(permission_role) ? FIRST(permission_role) : permission_role",
            {"node": f"{NODE_TARGETS[kind]}/{node_id}", "user": USER},
        )
        return rows[0] if rows else None

    async def records_left(self, ids: list[str]) -> list[str]:
        return await self._aql("FOR r IN records FILTER r._key IN @ids SORT r._key RETURN r._key", {"ids": ids})


@pytest.fixture(params=["neo4j", "arango"])
async def store(request, neo4j_provider, neo4j_settings, arango_provider, arango_settings) -> AsyncIterator:
    """User ``kbr-u`` with the grants above, one team per team grant; removed afterwards."""
    backend = (_Arango(arango_provider, arango_settings) if request.param == "arango"
               else _Neo4j(neo4j_provider, neo4j_settings))
    await backend.drop()
    await backend.load(*_graph())
    yield backend
    await backend.drop()


def _kb_id(name: str) -> str:
    return f"kbr-{list(COLLECTIONS).index(name)}"


async def _listed(provider, **kwargs) -> tuple[dict[str, str], int, dict]:
    kbs, total, filters = await provider.list_user_knowledge_bases(USER, ORG, skip=0, limit=50, **kwargs)
    return {kb["name"]: kb["userRole"] for kb in kbs}, total, filters


async def test_the_listing_shows_each_collection_with_the_best_role(store) -> None:
    roles, total, filters = await _listed(store.provider)
    assert roles == {
        "reader-direct-writer-team": "WRITER",
        "writer-direct-reader-team": "WRITER",
        "organizer-direct": "READER",
        "Alpha team only": "READER",
    }
    assert total == 4
    assert sorted(filters["permissions"]) == ["READER", "WRITER"]


async def test_the_listing_sorts_by_role(store) -> None:
    """Arango sorted on a variable of the role subquery and returned nothing."""
    kbs, total, _ = await store.provider.list_user_knowledge_bases(
        USER, ORG, skip=0, limit=50, sort_by="userRole", sort_order="desc")
    assert total == 4
    assert [kb["userRole"] for kb in kbs] == ["WRITER", "WRITER", "READER", "READER"]


async def test_search_finds_a_collection_shared_only_through_a_team(store) -> None:
    roles, total, _ = await _listed(store.provider, search="alpha")
    assert roles == {"Alpha team only": "READER"} and total == 1


async def test_the_role_filter_uses_the_role_as_read(store) -> None:
    roles, total, _ = await _listed(store.provider, permissions=["READER"])
    assert set(roles) == {"organizer-direct", "Alpha team only"} and total == 2
    roles, total, _ = await _listed(store.provider, permissions=["ORGANIZER", "COMMENTER"])
    assert roles == {} and total == 0


@pytest.mark.parametrize(("name", "expected"), [
    ("reader-direct-writer-team", "WRITER"),
    ("writer-direct-reader-team", "WRITER"),
    ("organizer-direct", "READER"),
    ("Alpha team only", "READER"),
])
async def test_the_single_collection_role_matches_the_listing(store, name, expected) -> None:
    assert await store.provider.get_user_kb_permission(_kb_id(name), USER) == expected


async def test_the_member_list_shows_a_retired_role_as_reader(store) -> None:
    members = await store.provider.list_kb_permissions(_kb_id("organizer-direct"))
    assert [(m["id"], m["role"]) for m in members if m["type"] == "USER"] == [(USER, "READER")]


async def test_the_owner_count_counts_users_only(store) -> None:
    """A team edge carrying OWNER (a legacy shape) is no owner: counting it could
    let the last real owner be demoted."""
    assert await store.provider.count_kb_owners(_kb_id("writer-direct-reader-team")) == 0
    assert await store.provider.count_kb_owners(_kb_id("hidden")) == 1


@pytest.mark.parametrize(("name", "expected"), [
    ("reader-direct-writer-team", "WRITER"),
    ("writer-direct-reader-team", "WRITER"),
    ("organizer-direct", "READER"),
    ("Alpha team only", "READER"),
])
async def test_the_item_role_on_a_collection_matches_the_listing(store, name, expected) -> None:
    assert await store.item_role("kb", "App", _kb_id(name)) == expected


@pytest.mark.parametrize(("kind", "role"), [
    ("Record", "FILEORGANIZER"), ("Record", "COMMENTER"), ("RecordGroup", "ORGANIZER"), ("RecordGroup", "OTHERS"),
])
async def test_the_item_role_on_a_record_or_group_reads_a_retired_role_as_reader(store, kind, role) -> None:
    item = (rec("kbr-item", "Item", connector_id="kbr-0") if kind == "Record"
            else rg("kbr-item", "Item", group_type="KB", connector=KB))
    await store.load([_org(item)], [perm(USER, "kbr-item", role=role)])
    node_type = "record" if kind == "Record" else "recordGroup"
    assert await store.item_role(node_type, kind, "kbr-item") == "READER"


async def _folder_with_a_file(store, kb_id: str) -> None:
    upload = {"connector_id": kb_id, "origin": "UPLOAD"}
    await store.load(
        [_org(rec("kbr-folder", "Folder", virtualRecordId="kbr-vf", **upload)),
         _org(rec("kbr-file", "File", virtualRecordId="kbr-vc", **upload))],
        [bt("kbr-folder", kb_id), bt("kbr-file", kb_id),
         nr(kb_id, "kbr-folder"), nr("kbr-folder", "kbr-file")],
    )


async def test_a_collection_folder_is_deleted_with_its_contents(store) -> None:
    await _folder_with_a_file(store, _kb_id("writer-direct-reader-team"))

    result = await store.provider.delete_record("kbr-folder", "kbr-m", ORG)

    assert result["success"] is True
    assert await store.records_left(["kbr-folder", "kbr-file"]) == []
    deleted = sorted(p["recordId"] for p in result["eventData"]["payloads"])
    assert deleted == ["kbr-file", "kbr-folder"]


async def test_a_retired_role_cannot_delete_a_collection_folder(store) -> None:
    await _folder_with_a_file(store, _kb_id("organizer-direct"))

    result = await store.provider.delete_record("kbr-folder", "kbr-m", ORG)

    assert result["code"] == 403
    assert await store.records_left(["kbr-folder", "kbr-file"]) == ["kbr-file", "kbr-folder"]


async def test_a_root_item_has_no_parent_folder(store) -> None:
    """The collection itself is no parent folder; a nested item's is."""
    await _folder_with_a_file(store, _kb_id("writer-direct-reader-team"))
    assert await store.provider.get_record_parent_info("kbr-folder") is None
    assert await store.provider.get_record_parent_info("kbr-file") == {"id": "kbr-folder", "type": "record"}


async def test_a_role_change_needs_no_direct_owner_edge(store) -> None:
    """The service checks the requester, counting an OWNER role held through a
    team, and the provider writes. A provider re-check for a direct OWNER edge
    would refuse a team owner after a share had inserted its new members."""
    kb = _kb_id("Alpha team only")
    member = "kbr-member"
    await store.load(
        [_org(_node("User", member, userId="kbr-mm", email="member@example.com", fullName="Member"))],
        [perm(member, kb, role="READER")],
    )

    result = await store.provider.update_kb_permission(kb, USER, [member], [], "WRITER")

    assert result["success"] is True and result["updated_users"] == 1
    assert (await store.provider.get_kb_permissions(kb_id=kb, user_ids=[member]))["users"] == {member: "WRITER"}
