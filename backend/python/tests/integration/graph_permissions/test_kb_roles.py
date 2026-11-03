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

pytestmark = pytest.mark.integration

ORG = "org-kbr"
USER = "kbr-u"

# name -> (direct role, [(role in team, role on the team->collection edge)], extra props)
COLLECTIONS: dict[str, tuple[str | None, list[tuple[str, str]], dict]] = {
    "reader-direct-writer-team": ("READER", [("WRITER", "READER")], {}),
    "writer-direct-reader-team": ("WRITER", [("READER", "OWNER")], {}),
    "organizer-direct": ("ORGANIZER", [], {}),
    "commenter-direct": ("COMMENTER", [], {}),
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
        "reader-direct-writer-team": "READER",
        "writer-direct-reader-team": "WRITER",
        "organizer-direct": "READER",
        "commenter-direct": "READER",
        "Alpha team only": "WRITER",
    }
    assert total == 5
    assert sorted(filters["permissions"]) == ["READER", "WRITER"]


async def test_the_listing_sorts_by_role(store) -> None:
    """Arango sorted on a variable of the role subquery and returned nothing."""
    kbs, total, _ = await store.provider.list_user_knowledge_bases(
        USER, ORG, skip=0, limit=50, sort_by="userRole", sort_order="desc")
    assert total == 5
    assert [kb["userRole"] for kb in kbs] == ["WRITER", "WRITER", "READER", "READER", "READER"]


async def test_search_finds_a_collection_shared_only_through_a_team(store) -> None:
    roles, total, _ = await _listed(store.provider, search="alpha")
    assert roles == {"Alpha team only": "WRITER"} and total == 1


async def test_the_role_filter_uses_the_role_as_read(store) -> None:
    roles, total, _ = await _listed(store.provider, permissions=["READER"])
    assert set(roles) == {"reader-direct-writer-team", "organizer-direct", "commenter-direct"} and total == 3
    roles, total, _ = await _listed(store.provider, permissions=["ORGANIZER", "COMMENTER"])
    assert roles == {} and total == 0


@pytest.mark.parametrize(("name", "expected"), [
    ("reader-direct-writer-team", "READER"),
    ("writer-direct-reader-team", "WRITER"),
    ("organizer-direct", "READER"),
    ("commenter-direct", "READER"),
    ("Alpha team only", "WRITER"),
])
async def test_the_single_collection_role_matches_the_listing(store, name, expected) -> None:
    assert await store.provider.get_user_kb_permission(_kb_id(name), USER) == expected


async def _load_odd(store, nodes: list[dict], edges: list[dict]) -> bool:
    """Load edges carrying a role no writer stores. ArangoDB's permissions schema refuses them,
    which is its guarantee: that is asserted here and False returned, so the caller stops."""
    if not isinstance(store, _Arango):
        await store.load(nodes, edges)
        return True
    with pytest.raises(Exception, match="permissions schema"):
        await store.load(nodes, edges)
    return False


def _odd_graph(role: str, team_reader: bool) -> tuple[list[dict], list[dict]]:
    """``kbr-odd`` (direct ``role``, a record in it, optionally a READER team grant) and ``kbr-ok`` (direct READER)."""
    nodes, edges = [], []
    for kb, name in (("kbr-odd", "odd"), ("kbr-ok", "ok")):
        nodes += [_org(app(kb, name, connector=KB, app_group="Local Storage", scope="personal")),
                  _org(rec(f"{kb}-r", f"{name} record", origin="UPLOAD", connector_id=kb, virtualRecordId=f"{kb}-v"))]
        edges += [bt(f"{kb}-r", kb), perm(USER, kb, role="READER" if kb == "kbr-ok" else role)]
    if team_reader:
        nodes.append(_org(_node("Teams", "kbr-odd-team", name="kbr-odd-team")))
        edges += [perm(USER, "kbr-odd-team", role="READER"),
                  perm("kbr-odd-team", "kbr-odd", grant_type="TEAM", role="READER")]
    return nodes, edges


@pytest.mark.parametrize("role", ["ADMIN", "EDITOR", "owner"])
@pytest.mark.parametrize("team_reader", [False, True])
async def test_an_unrecognised_direct_role_is_no_access(store, role, team_reader) -> None:
    """A direct edge with a role no writer stores neither wins nor lists the
    collection; a team grant beside it still gives its own role."""
    if not await _load_odd(store, *_odd_graph(role, team_reader)):
        return
    expected = "READER" if team_reader else None
    assert await store.provider.get_user_kb_permission("kbr-odd", USER) == expected
    roles, _, _ = await _listed(store.provider)
    if expected is None:
        assert "odd" not in roles
    else:
        assert roles["odd"] == expected


@pytest.mark.parametrize("role", ["ADMIN", "EDITOR", "owner"])
async def test_an_unrecognised_direct_role_opens_no_collection_or_record(store, role) -> None:
    """The access paths that read the direct edge themselves agree with the role lookup."""
    if not await _load_odd(store, *_odd_graph(role, False)):
        return
    provider = store.provider

    accessible = await provider._get_accessible_kb_ids("kbr-m")
    assert "kbr-ok" in accessible and "kbr-odd" not in accessible
    gated = {a["id"] for a in await provider.get_gated_apps(USER, ORG)}
    assert "kbr-ok" in gated and "kbr-odd" not in gated
    assert await provider.check_record_access_with_details("kbr-m", ORG, "kbr-odd-r") is None
    assert await provider.check_record_access_with_details("kbr-m", ORG, "kbr-ok-r")
    context = await provider.get_entity_access_context("kbr-m", ORG)
    assert "kbr-odd" not in {a["id"] for a in context["apps"]}


async def test_the_hub_ranks_a_team_reader_above_an_unrecognised_direct_role(store) -> None:
    if not await _load_odd(store, *_odd_graph("ADMIN", True)):
        return

    context = await store.provider.get_knowledge_hub_context_permissions(
        USER, ORG, "kbr-odd", parent_type="app")

    assert (context["role"], context["canManagePermissions"]) == ("READER", False), context


async def test_a_connector_app_keeps_the_hub_role_of_its_direct_edge(store) -> None:
    """Only a knowledge base holds direct roles to the KB rule; no writer stores EDITOR on a
    connector app, but the hub reads one as it always did."""
    if not await _load_odd(store, [_org(app("kbr-conn", "conn", connector="DRIVE", app_group="Google Workspace"))],
                           [perm(USER, "kbr-conn", role="EDITOR")]):
        return

    context = await store.provider.get_knowledge_hub_context_permissions(
        USER, ORG, "kbr-conn", parent_type="app")

    assert context["role"] == "EDITOR", context


async def test_the_member_list_shows_a_retired_role_as_reader(store) -> None:
    members = await store.provider.list_kb_permissions(_kb_id("organizer-direct"))
    assert [(m["id"], m["role"]) for m in members if m["type"] == "USER"] == [(USER, "READER")]


async def test_the_owner_count_counts_users_only(store) -> None:
    """A team edge carrying OWNER (a legacy shape) is no owner: counting it could
    let the last real owner be demoted."""
    assert await store.provider.count_kb_owners(_kb_id("writer-direct-reader-team")) == 0
    assert await store.provider.count_kb_owners(_kb_id("hidden")) == 1


@pytest.mark.parametrize(("name", "expected"), [
    ("reader-direct-writer-team", "READER"),
    ("writer-direct-reader-team", "WRITER"),
    ("organizer-direct", "READER"),
    ("commenter-direct", "READER"),
    ("Alpha team only", "WRITER"),
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
