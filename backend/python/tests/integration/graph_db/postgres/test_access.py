"""The access check on PostgreSQL, rule by rule, against a real database: which nodes a user may open, as ``Neo4jProvider`` decides it.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

import pytest

from app.core.org_context import MissingOrgContext, OrgContext, bind, pop, push
from app.services.graph_db.interface.graph_db_provider import (
    PermissionVerificationUnavailableError,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = pytest.mark.integration

APP = "app1"


def _org() -> str:
    return uuid.uuid4().hex[:24]


class World:
    """A small graph written through the provider, in the ambient org."""

    def __init__(self, p, org: str) -> None:
        self.p, self.org = p, org

    async def nodes(self, coll: str, *docs: dict) -> None:
        await self.p.batch_upsert_nodes([{"orgId": self.org, **d} for d in docs], coll)

    async def edge(self, coll: str, f: tuple[str, str], t: tuple[str, str], **doc: str) -> None:
        await self.p.batch_create_edges([{"from_collection": f[0], "from_id": f[1], "to_collection": t[0], "to_id": t[1], **doc}], coll)

    async def child(self, parent: tuple[str, str], child: tuple[str, str], *, inherits: bool = True, rel: str = "PARENT_CHILD") -> None:
        await self.edge("nodeRelations", parent, child, relationshipType=rel)
        if inherits:
            await self.edge("inheritPermissions", child, parent)

    async def member(self, who: str, group: str, coll: str = "groups", type_: str = "USER") -> None:
        await self.edge("permission", ("users", who), (coll, group), type=type_, role="READER")

    async def allowed(self, user: str, *ids: str, org: str | None = None, **kw) -> set[str]:
        return set((await self.p.check_access(user, org or self.org, node_ids=list(ids), **kw)).node_ids)


@pytest.fixture
async def w(pg_provider, pg_db, org_ctx) -> AsyncIterator[World]:
    org = _org()
    with org_ctx(org):
        world = World(pg_provider, org)
        await world.nodes("users", {"_key": "u"}, {"_key": "other"})
        await world.nodes("apps", {"_key": APP, "type": "CONNECTOR", "isActive": True})
        await world.edge("permission", ("users", "u"), ("apps", APP), type="USER", role="READER")      # the connector gate
        yield world
    with org_ctx(org):
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.edges WHERE org_id = $1", org), write=True)
        await pg_db.run(lambda tx: tx.execute("DELETE FROM graph.nodes WHERE org_id = $1", org), write=True)


def rec(key: str, **kw: object) -> dict:
    return {"_key": key, "connectorId": APP, "recordName": key, **kw}


async def test_a_user_with_no_gate_or_no_grant_opens_nothing(w: World) -> None:
    await w.nodes("records", rec("r"))
    await w.child(("apps", APP), ("records", "r"))
    assert await w.allowed("other", "r", APP) == set()           # no path from the user to the connector
    assert await w.allowed("", "r") == set()                      # no identity
    assert await w.allowed("nobody", "r") == set()                # not a node of the graph


async def test_the_app_and_what_inherits_from_it_open_and_what_does_not_inherit_stays_shut(w: World) -> None:
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP, "groupName": "g"})
    await w.nodes("records", rec("inh"), rec("loose"), rec("deep"))
    await w.child(("apps", APP), ("recordGroups", "g"))
    await w.child(("recordGroups", "g"), ("records", "inh"))
    await w.child(("recordGroups", "g"), ("records", "loose"), inherits=False)
    await w.child(("records", "inh"), ("records", "deep"), rel="ATTACHMENT")
    assert await w.allowed("u", APP, "g", "inh", "deep", "loose") == {APP, "g", "inh", "deep"}


async def test_the_access_rule_decides_what_inheriting_and_a_grant_each_open(w: World) -> None:
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.nodes("records", rec("strict", accessRule="STRICT"), rec("restricted", accessRule="RESTRICTED"), rec("unknown", accessRule="WEIRD"),
                  rec("granted_restricted", accessRule="RESTRICTED"))
    await w.child(("apps", APP), ("recordGroups", "g"))
    for k in ("strict", "restricted", "unknown", "granted_restricted"):
        await w.child(("recordGroups", "g"), ("records", k))
    await w.edge("permission", ("users", "u"), ("records", "granted_restricted"), type="USER", role="READER")
    assert await w.allowed("u", "strict", "restricted", "unknown", "granted_restricted") == {"strict", "granted_restricted"}


async def test_group_role_and_team_membership_expand_and_only_a_user_edge_is_membership(w: World) -> None:
    await w.nodes("groups", {"_key": "grp"}, {"_key": "notmine"})
    await w.nodes("roles", {"_key": "rol", "connectorId": APP})
    await w.nodes("teams", {"_key": "tm"})
    await w.nodes("records", rec("viaGroup"), rec("viaRole"), rec("viaTeam"), rec("viaWrongEdge"), rec("viaNonMember"))
    await w.member("u", "grp")
    await w.member("u", "rol", "roles")
    await w.member("u", "tm", "teams")
    await w.member("u", "notmine", type_="GROUP")                 # an edge of another type is not membership
    for who, k in (("groups", "grp"), ("roles", "rol"), ("teams", "tm")):
        await w.edge("permission", (who, k), ("records", {"groups": "viaGroup", "roles": "viaRole", "teams": "viaTeam"}[who]), type=who[:-1].upper(), role="READER")
    await w.edge("permission", ("groups", "notmine"), ("records", "viaWrongEdge"), type="GROUP", role="READER")
    await w.edge("permission", ("groups", "nobody-here"), ("records", "viaNonMember"), type="GROUP", role="READER")
    assert await w.allowed("u", "viaGroup", "viaRole", "viaTeam", "viaWrongEdge", "viaNonMember") == {"viaGroup", "viaRole", "viaTeam"}
    access = await w.p.get_knowledge_hub_access_v3("u", w.org)
    assert set(access["grantee_ids"]) == {"u", "grp", "rol", "tm"} and access["gated_app_ids"] == [APP]


async def test_an_organization_grant_counts_only_with_an_organization_type(w: World) -> None:
    await w.nodes("organizations", {"_key": "o1"})
    await w.nodes("records", rec("orgShared"), rec("orgOther"))
    await w.edge("belongsTo", ("users", "u"), ("organizations", "o1"), entityType="ORGANIZATION")
    await w.edge("permission", ("organizations", "o1"), ("records", "orgShared"), type="ORG", role="READER")
    await w.edge("permission", ("organizations", "o1"), ("records", "orgOther"), type="USER", role="READER")
    assert await w.allowed("u", "orgShared", "orgOther") == {"orgShared"}


async def test_deleted_nodes_and_stubs_are_not_opened_except_a_stub_asked_by_id(w: World) -> None:
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.nodes("records", rec("gone", isDeleted=True, virtualRecordId="v1"), rec("stub", isPlaceholder=True, virtualRecordId="v2"),
                  rec("real", virtualRecordId="v3"), rec("under_gone"))
    await w.child(("apps", APP), ("recordGroups", "g"))
    for k in ("gone", "stub", "real"):
        await w.child(("recordGroups", "g"), ("records", k))
    await w.child(("records", "gone"), ("records", "under_gone"))
    got = await w.p.check_access("u", w.org, node_ids=["gone", "stub", "real", "under_gone"], virtual_record_ids=["v1", "v2", "v3"])
    assert set(got.node_ids) == {"stub", "real"} and got.placeholder_ids == {"stub"}      # a deleted node is never opened, nor is what hangs only under it
    assert got.records_by_vrid == {"v3": "real"}                   # a stub reached only by its virtual record id is not cited


async def test_the_virtual_record_lookup_admits_what_the_plain_statement_admitted(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The vetted read of graph_0004 and the plain statement answer alike, whichever side a record falls on: deleted, stub (asked or not), another
    org's claim, no virtual record id, two records for one id."""
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.nodes("records", rec("gone", isDeleted=True, virtualRecordId="v1"), rec("stub", isPlaceholder=True, virtualRecordId="v2"),
                  rec("real", virtualRecordId="v3"), rec("twin", virtualRecordId="v3"), rec("asked_stub", isPlaceholder=True, virtualRecordId="v6"),
                  rec("foreign", virtualRecordId="v4", orgId="someone-else"), rec("nov"), rec("shut", virtualRecordId="v7", accessRule="RESTRICTED"))
    await w.nodes("records", {**rec("claimless", virtualRecordId="v8"), "orgId": None})
    for k in ("gone", "stub", "real", "twin", "asked_stub", "foreign", "nov", "shut", "claimless"):
        await w.child(("recordGroups", "g"), ("records", k))
    await w.child(("apps", APP), ("recordGroups", "g"))
    asks = [{"node_ids": ["asked_stub"], "virtual_record_ids": ["v1", "v2", "v3", "v4", "v5", "v6", "v7", "v8"]}, {"node_ids": [], "virtual_record_ids": ["v3"]},
            {"node_ids": ["real"], "virtual_record_ids": ["nope"]}, {"node_ids": ["stub"], "virtual_record_ids": ["v2"]}]
    new = [await w.p.check_access("u", w.org, **a) for a in asks]
    monkeypatch.setattr("app.services.graph_db.postgres.access._VRID_PAGE", 0)          # a page can never be short: every ask takes the plain statement
    old = [await w.p.check_access("u", w.org, **a) for a in asks]
    for n, o, a in zip(new, old, asks, strict=True):
        assert (set(n.node_ids), n.placeholder_ids, n.records_by_vrid.keys()) == (set(o.node_ids), o.placeholder_ids, o.records_by_vrid.keys()), a
    assert set(new[0].records_by_vrid) == {"v3"} and new[0].records_by_vrid["v3"] in {"real", "twin"}      # only a live, readable record is cited by its virtual record id


async def test_a_flag_the_database_cannot_read_fails_the_check_as_the_cast_did(w: World, monkeypatch: pytest.MonkeyPatch) -> None:
    """A virtual record whose ``isDeleted`` is no boolean must not read as live: both the vetted read and the statement refuse, alike."""
    await w.nodes("recordGroups", {"_key": "g", "connectorId": APP})
    await w.nodes("records", rec("odd", virtualRecordId="v1", isDeleted="garbage"), rec("fine", virtualRecordId="v2"))
    await w.child(("apps", APP), ("recordGroups", "g"))
    for k in ("odd", "fine"):
        await w.child(("recordGroups", "g"), ("records", k))
    for page in (5000, 0):                                   # the vetted read, then the plain statement
        monkeypatch.setattr("app.services.graph_db.postgres.access._VRID_PAGE", page)
        with pytest.raises(PermissionVerificationUnavailableError):
            await w.p.check_access("u", w.org, virtual_record_ids=["v1"])


async def test_a_record_of_a_knowledge_base_opens_by_membership_of_its_own_app_only(w: World) -> None:
    await w.nodes("apps", {"_key": "kb1", "type": "KB"}, {"_key": "kb2", "type": "KB"})
    await w.edge("permission", ("users", "u"), ("apps", "kb1"), type="USER", role="READER")
    await w.edge("permission", ("users", "u"), ("apps", "kb2"), type="USER", role="READER")
    await w.nodes("records", {"_key": "in1", "connectorId": "kb1"}, {"_key": "in2", "connectorId": "kb2"}, {"_key": "cross", "connectorId": "kb1"})
    await w.edge("belongsTo", ("records", "in1"), ("apps", "kb1"))
    await w.edge("belongsTo", ("records", "in2"), ("apps", "kb2"))
    await w.edge("belongsTo", ("records", "cross"), ("apps", "kb2"))      # filed in another knowledge base than the one it names
    assert await w.allowed("u", "in1", "in2", "cross") == {"in1", "in2"}


async def test_a_grant_is_a_seed_only_when_it_is_open_live_and_of_the_connector(w: World) -> None:
    await w.nodes("records", rec("open"), rec("emptyRule", accessRule=""), rec("restricted", accessRule="RESTRICTED"), rec("dead", isDeleted=True),
                  {"_key": "elsewhere", "connectorId": "app2"})
    await w.nodes("apps", {"_key": "app2", "type": "CONNECTOR"})
    for k in ("open", "emptyRule", "restricted", "dead", "elsewhere"):
        await w.edge("permission", ("users", "u"), ("records", k), type="USER", role="READER")
    assert await w.allowed("u", "open", "emptyRule", "restricted", "dead", "elsewhere") == {"open"}


async def test_below_a_seed_every_hop_must_be_open_and_inherit(w: World) -> None:
    await w.nodes("records", rec("seed"), rec("kid"), rec("kidNoInherit"), rec("kidStrict", accessRule="STRICT"), rec("grandkid"))
    await w.edge("permission", ("users", "u"), ("records", "seed"), type="USER", role="READER")
    await w.child(("records", "seed"), ("records", "kid"))
    await w.child(("records", "seed"), ("records", "kidNoInherit"), inherits=False)
    await w.child(("records", "seed"), ("records", "kidStrict"))
    await w.child(("records", "kid"), ("records", "grandkid"))
    assert await w.allowed("u", "seed", "kid", "kidNoInherit", "kidStrict", "grandkid") == {"seed", "kid", "grandkid"}


async def test_a_declared_scope_opens_what_hangs_in_it_when_the_declaration_is_granted(w: World) -> None:
    await w.nodes("recordGroups", {"_key": "decl", "connectorId": APP, "permissionModel": "RECORD_GROUP_LEVEL"}, {"_key": "sub", "connectorId": APP},
                  {"_key": "decl2", "connectorId": APP, "permissionModel": "RECORD_GROUP_LEVEL"})
    await w.nodes("records", rec("inDecl"), rec("inSub"), rec("underOther"))
    await w.child(("apps", APP), ("recordGroups", "decl"), inherits=False)
    await w.child(("recordGroups", "decl"), ("recordGroups", "sub"), inherits=False)
    await w.edge("belongsTo", ("records", "inDecl"), ("recordGroups", "decl"))
    await w.edge("belongsTo", ("records", "inSub"), ("recordGroups", "sub"))
    await w.edge("belongsTo", ("records", "underOther"), ("recordGroups", "decl2"))
    await w.edge("permission", ("users", "u"), ("recordGroups", "decl"), type="USER", role="READER")
    assert await w.allowed("u", "inDecl", "inSub", "underOther", "sub") == {"inDecl", "inSub", "sub"}


async def test_a_source_account_the_user_authenticated_as_adds_its_grants_inside_that_connector(w: World) -> None:
    await w.nodes("users", {"_key": "src"}, {"_key": "notauser"})
    await w.nodes("groups", {"_key": "srcgrp"})
    await w.nodes("records", rec("viaSrc"), rec("viaBadge"), rec("viaBogus"), {"_key": "foreign", "connectorId": "app2"})
    await w.nodes("apps", {"_key": "app2", "type": "CONNECTOR"})
    await w.edge("authenticatedAs", ("users", "u"), ("users", "src"), connectorId=APP)
    await w.member("src", "srcgrp")
    await w.edge("permission", ("users", "src"), ("records", "viaSrc"), type="USER", role="READER")
    await w.edge("permission", ("groups", "srcgrp"), ("records", "viaBadge"), type="GROUP", role="READER")
    await w.edge("permission", ("users", "src"), ("records", "foreign"), type="USER", role="READER")      # inside that connector only
    await w.edge("authenticatedAs", ("users", "other"), ("groups", "srcgrp"), connectorId=APP)          # a link to something that is not a user
    assert await w.allowed("u", "viaSrc", "viaBadge", "foreign") == {"viaSrc", "viaBadge"}
    assert await w.allowed("other", "viaBadge") == set()


async def test_only_principals_can_be_grantees_of_the_gate(w: World) -> None:
    """An edge out of a node of another collection that shares a grantee's id is not the grantee's."""
    await w.nodes("apps", {"_key": "gated2", "type": "CONNECTOR"})
    await w.nodes("records", {"_key": "u", "connectorId": "gated2"}, {"_key": "r2", "connectorId": "gated2"})        # a record whose id is the user's
    await w.edge("permission", ("records", "u"), ("apps", "gated2"), type="USER", role="READER")
    await w.edge("permission", ("records", "u"), ("records", "r2"), type="USER", role="READER")
    access = await w.p.get_knowledge_hub_access_v3("u", w.org)
    assert access["gated_app_ids"] == [APP] and "r2" not in access["by_connector"].get(APP, [])
    assert await w.allowed("u", "gated2", "r2") == set()


async def test_a_chat_attachment_outside_every_app_opens_only_by_a_grant_on_the_record(w: World) -> None:
    await w.nodes("records", {"_key": "att", "connectorId": "gone-app", "origin": "UPLOAD"}, {"_key": "att2", "connectorId": "gone-app", "origin": "UPLOAD"},
                  {"_key": "conn", "connectorId": "gone-app", "origin": "CONNECTOR"})
    await w.edge("permission", ("users", "u"), ("records", "att"), type="USER", role="OWNER")
    await w.edge("permission", ("users", "u"), ("records", "conn"), type="USER", role="OWNER")
    assert await w.allowed("u", "att", "att2", "conn") == {"att"}


async def test_another_org_is_nobodys_and_a_node_of_another_org_is_not_there(w: World) -> None:
    await w.nodes("records", rec("r"))
    await w.child(("apps", APP), ("records", "r"))
    assert await w.allowed("u", "r") == {"r"}
    assert await w.allowed("u", "r", org=_org()) == set()                  # a different org asked of the ambient one
    stranger = _org()
    token = push(None)
    try:
        with bind(OrgContext(stranger, None, "job", "job:test", "t")):         # the same ids under another org see nothing
            assert (await w.p.check_access("u", stranger, node_ids=["r", APP])).node_ids == frozenset()
    finally:
        pop(token)
    await w.p.update_node("r", "records", {"orgId": _org()})                # a record whose own orgId names another org is not this org's
    assert await w.allowed("u", "r") == set()


async def test_a_call_without_an_org_fails_instead_of_denying(pg_provider) -> None:
    with pytest.raises(PermissionVerificationUnavailableError):      # check_access never answers "nothing" for a graph that could not answer
        await pg_provider.check_access("u", "o", node_ids=["r"])
    with pytest.raises(MissingOrgContext):
        await pg_provider.get_knowledge_hub_access_v3("u", "o")


async def test_an_access_with_a_grant_probe_is_refused_rather_than_answered_without_its_grants(w: World) -> None:
    from app.services.graph_db.postgres.base import NotImplementedInPostgres
    await w.nodes("records", rec("r"))
    await w.child(("apps", APP), ("records", "r"))
    with pytest.raises(NotImplementedInPostgres):
        await w.p.check_access("u", w.org, node_ids=["r"], access={"grantee_ids": ["u"], "gated_app_ids": [APP], "by_connector": {}, "probe": {"connector_id": APP, "grantees": ["u"]}})


async def test_a_null_in_a_document_is_a_missing_field_for_the_rule_and_the_flags(w: World, pg_db) -> None:
    """A stored JSON null reads as absent (the SQL arms coalesce, Neo4j has no null property): a granted record with a null rule is an OPEN seed."""
    await w.nodes("records", rec("nullRule"), rec("nullDeleted"), rec("nullOrigin"))
    for k in ("nullRule", "nullDeleted", "nullOrigin"):
        await w.edge("permission", ("users", "u"), ("records", k), type="USER", role="READER")

    async def set_null(tx, key: str, field: str) -> None:
        await tx.execute("UPDATE graph.nodes SET doc = jsonb_set(doc, ARRAY[$3::text], 'null'::jsonb) WHERE org_id = $1 AND coll = 'records' AND id = $2", w.org, key, field)
    await pg_db.run(lambda tx: set_null(tx, "nullRule", "accessRule"), write=True)
    await pg_db.run(lambda tx: set_null(tx, "nullDeleted", "isDeleted"), write=True)
    await pg_db.run(lambda tx: set_null(tx, "nullOrigin", "origin"), write=True)
    assert await w.allowed("u", "nullRule", "nullDeleted", "nullOrigin") == {"nullRule", "nullDeleted", "nullOrigin"}
