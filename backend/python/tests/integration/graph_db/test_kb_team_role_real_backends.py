"""KB role derived from the team->KB share edge, on a real Neo4j and a real ArangoDB.

Covers the edge-role rule and the legacy fallback (member's team role capped at
WRITER) in ``get_user_kb_permission`` and ``list_user_knowledge_bases``, and the
backfill of role-less team->KB edges (READER only), the record-access-details and
records-listing KB-team branches, and the later-joining-member regression.

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \\
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/graph_db/test_kb_team_role_real_backends.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider

from ._backends import arango_env as load_arango_env
from ._backends import neo4j_env as load_neo4j_env
from ._backends import unavailable

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "kb_team_role_it"

logger = logging.getLogger("kb-team-role-it")


@pytest.fixture(params=["neo4j", "arango"])
async def backend(request, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[tuple[object, str]]:
    org_id = f"org-it-{uuid.uuid4().hex[:10]}"
    if request.param == "neo4j":
        neo4j_env = load_neo4j_env()
        monkeypatch.setenv("NEO4J_URI", neo4j_env.uri)
        monkeypatch.setenv("NEO4J_USERNAME", neo4j_env.user)
        monkeypatch.setenv("NEO4J_PASSWORD", neo4j_env.password)
        monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
        provider = Neo4jProvider(logger, MagicMock())
        try:
            if not await asyncio.wait_for(provider.connect(), timeout=60):
                raise ConnectionError("connect returned False")
        except Exception as exc:
            unavailable(f"Neo4j not available at {neo4j_env.uri}: {exc}")
        await provider.ensure_schema()
        try:
            yield provider, org_id
        finally:
            await provider.client.execute_query(
                "MATCH (n) WHERE n.orgId STARTS WITH $org DETACH DELETE n", parameters={"org": org_id}
            )
            await provider.disconnect()
        return

    arango_env = load_arango_env()
    config_service = MagicMock()
    config_service.get_config = AsyncMock(return_value={
        "url": arango_env.url, "username": arango_env.user, "password": arango_env.password, "db": ARANGO_DB,
    })
    provider = ArangoHTTPProvider(logger, config_service)
    try:
        if not await asyncio.wait_for(provider.connect(), timeout=60):
            raise ConnectionError("connect returned False")
        await provider.ensure_schema()
    except Exception as exc:
        unavailable(f"ArangoDB not available at {arango_env.url}: {exc}")
    try:
        yield provider, org_id
    finally:
        keys = f"{org_id}-"
        for collection in (
            CollectionNames.PERMISSION.value,
        ):
            await provider.http_client.execute_aql(
                f"FOR e IN {collection} FILTER CONTAINS(e._from, @k) OR CONTAINS(e._to, @k) REMOVE e IN {collection}",
                {"k": keys},
            )
        for collection in (CollectionNames.USERS.value, CollectionNames.TEAMS.value, CollectionNames.APPS.value):
            await provider.http_client.execute_aql(
                f"FOR d IN {collection} FILTER STARTS_WITH(d.orgId, @org) REMOVE d IN {collection}", {"org": org_id}
            )


class _Graph:
    """Seeds users, teams, one KB and PERMISSION edges through the provider's own write paths."""

    def __init__(self, provider, org_id: str) -> None:
        self.p = provider
        self.org = org_id

    def key(self, name: str) -> str:
        return f"{self.org}-{name}"

    async def user(self, name: str) -> str:
        k = self.key(name)
        await self.p.batch_upsert_nodes(
            [{"id": k, "userId": k, "orgId": self.org, "email": f"{k}@example.com", "isActive": True}],
            CollectionNames.USERS.value,
        )
        return k

    async def team(self, name: str, *, team_id: str | None = None) -> str:
        k = team_id or self.key(name)
        await self.p.batch_upsert_nodes([{"id": k, "name": name, "orgId": self.org}], CollectionNames.TEAMS.value)
        return k

    async def kb(self, name: str) -> str:
        k = self.key(name)
        await self.p.batch_upsert_nodes(
            [{"id": k, "name": name, "orgId": self.org, "type": "KB", "appGroup": "Knowledge Base",
              "scope": "team", "isActive": True, "createdBy": "someone-else", "isHidden": False,
              "createdAtTimestamp": 1, "updatedAtTimestamp": 1}],
            CollectionNames.APPS.value,
        )
        return k

    async def edge(self, src: str, src_col: str, dst: str, dst_col: str, kind: str, role: str | None) -> None:
        edge = {
            "from_id": src, "from_collection": src_col, "to_id": dst, "to_collection": dst_col,
            "type": kind, "createdAtTimestamp": 1, "updatedAtTimestamp": 1,
        }
        if role is not None:
            edge["role"] = role
        await self.p.batch_create_edges([edge], CollectionNames.PERMISSION.value)

    async def member(self, user: str, team: str, role: str) -> None:
        await self.edge(user, CollectionNames.USERS.value, team, CollectionNames.TEAMS.value, "USER", role)

    async def team_kb(self, team: str, kb: str, role: str | None) -> None:
        await self.edge(team, CollectionNames.TEAMS.value, kb, CollectionNames.APPS.value, "TEAM", role)

    async def direct(self, user: str, kb: str, role: str) -> None:
        await self.edge(user, CollectionNames.USERS.value, kb, CollectionNames.APPS.value, "USER", role)

    async def edge_role(self, team: str, kb: str) -> str | None:
        if isinstance(self.p, Neo4jProvider):
            rows = await self.p.client.execute_query(
                "MATCH (:Teams {id: $t})-[e:PERMISSION {type: 'TEAM'}]->(:App {id: $k}) RETURN e.role AS role",
                parameters={"t": team, "k": kb},
            )
            return rows[0]["role"] if rows else None
        rows = await self.p.http_client.execute_aql(
            "FOR e IN permission FILTER e._from == @f AND e._to == @t AND e.type == 'TEAM' RETURN e.role",
            {"f": f"teams/{team}", "t": f"apps/{kb}"},
        )
        return rows[0] if rows else None

    async def record_in(self, kb: str, name: str = "rec") -> str:
        rid = self.key(name)
        await self.p.batch_upsert_nodes(
            [{"id": rid, "orgId": self.org, "recordName": name, "recordType": "FILE", "origin": "UPLOAD",
              "connectorName": Connectors.KNOWLEDGE_BASE.value, "connectorId": kb, "externalRecordId": rid,
              "isDeleted": False, "mimeType": "text/plain",
              "createdAtTimestamp": 1, "updatedAtTimestamp": 1}],
            CollectionNames.RECORDS.value,
        )
        await self.p.batch_create_edges(
            [{"from_id": rid, "from_collection": CollectionNames.RECORDS.value,
              "to_id": kb, "to_collection": CollectionNames.APPS.value,
              "entityType": "KB", "createdAtTimestamp": 1, "updatedAtTimestamp": 1}],
            CollectionNames.BELONGS_TO.value,
        )
        return rid

    async def record_access_role(self, user: str, record: str) -> str | None:
        details = await self.p.check_record_access_with_details(user, self.org, record)
        return details["permissions"][0]["relationship"] if details else None

    async def records_listing_role(self, user: str, record: str) -> str | None:
        args = [user, self.org, 0, 50]
        if isinstance(self.p, ArangoHTTPProvider):
            args += [None] * 8 + ["createdAtTimestamp", "desc", "all"]
        records = (await self.p.list_all_records(*args))[0]
        row = next((r for r in records if r["id"] == record), None)
        return row["permission"]["role"] if row else None

    async def path7_role(self, user: str, record: str) -> str | None:
        """Role from the generic record permission fragment (path 7 included)."""
        if isinstance(self.p, Neo4jProvider):
            frag = self.p._get_permission_role_cypher("record", "record", "u")
            rows = await self.p.client.execute_query(
                f"MATCH (record:Record {{id: $r}}) MATCH (u:User {{id: $u}}) {frag} RETURN permission_role",
                parameters={"r": record, "u": user},
            )
            return rows[0]["permission_role"] if rows else None
        frag = self.p._get_permission_role_aql("record", "record", "u")
        rows = await self.p.http_client.execute_aql(
            f"FOR record IN records FILTER record._key == @r FOR u IN users FILTER u._key == @u {frag} RETURN FIRST(permission_role)",
            {"r": record, "u": user},
        )
        return rows[0] if rows else None

    async def record_group_in(self, record: str, name: str = "rg") -> str:
        rg = self.key(name)
        await self.p.batch_upsert_nodes(
            [{"id": rg, "orgId": self.org, "groupName": name, "groupType": "SLACK_CHANNEL",
              "connectorName": "SLACK", "connectorId": self.key("conn"),
              "createdAtTimestamp": 1, "updatedAtTimestamp": 1}],
            CollectionNames.RECORD_GROUPS.value,
        )
        await self.p.batch_create_edges(
            [{"from_id": record, "from_collection": CollectionNames.RECORDS.value,
              "to_id": rg, "to_collection": CollectionNames.RECORD_GROUPS.value,
              "createdAtTimestamp": 1, "updatedAtTimestamp": 1}],
            CollectionNames.INHERIT_PERMISSIONS.value,
        )
        return rg

    async def listed_role(self, user: str, kb: str) -> str | None:
        result = await self.p.list_user_knowledge_bases(user, self.org, 0, 50)
        kbs = result[0] if isinstance(result, tuple) else result["knowledge_bases"]
        return next((k["userRole"] for k in kbs if k["id"] == kb), None)


@pytest.fixture
def g(backend) -> _Graph:
    provider, org_id = backend
    return _Graph(provider, org_id)


class TestEdgeRoleWins:
    async def test_team_owner_gets_the_edge_role(self, g: _Graph) -> None:
        """PI-16"""
        user, team, kb = await g.user("u"), await g.team("t"), await g.kb("kb")
        await g.member(user, team, "OWNER")
        await g.team_kb(team, kb, "READER")

        assert await g.p.get_user_kb_permission(kb, user) == "READER"
        assert await g.listed_role(user, kb) == "READER"

    async def test_all_org_team_owner_gets_the_edge_role(self, g: _Graph) -> None:
        """PI-17"""
        user, kb = await g.user("oldest"), await g.kb("project-kb")
        all_team = await g.team("All", team_id=f"all_{g.org}")
        await g.member(user, all_team, "OWNER")
        await g.team_kb(all_team, kb, "READER")

        assert await g.p.get_user_kb_permission(kb, user) == "READER"
        assert await g.listed_role(user, kb) == "READER"


class TestLegacyRolelessEdge:
    async def test_member_role_capped_at_writer(self, g: _Graph) -> None:
        """PH01-14"""
        owner, writer, reader = await g.user("o"), await g.user("w"), await g.user("r")
        team, kb = await g.team("t"), await g.kb("kb")
        await g.member(owner, team, "OWNER")
        await g.member(writer, team, "WRITER")
        await g.member(reader, team, "READER")
        await g.team_kb(team, kb, None)

        assert await g.p.get_user_kb_permission(kb, owner) == "WRITER"
        assert await g.p.get_user_kb_permission(kb, writer) == "WRITER"
        assert await g.p.get_user_kb_permission(kb, reader) == "READER"
        assert await g.listed_role(owner, kb) == "WRITER"
        assert await g.listed_role(reader, kb) == "READER"

    async def test_direct_edge_still_wins_when_higher(self, g: _Graph) -> None:
        """PH01-14"""
        user, team, kb = await g.user("u"), await g.team("t"), await g.kb("kb")
        await g.member(user, team, "READER")
        await g.team_kb(team, kb, None)
        await g.direct(user, kb, "WRITER")

        assert await g.p.get_user_kb_permission(kb, user) == "WRITER"
        assert await g.listed_role(user, kb) == "WRITER"

    async def test_team_derived_role_wins_over_lower_direct_edge(self, g: _Graph) -> None:
        user, team, kb = await g.user("u"), await g.team("t"), await g.kb("kb")
        await g.member(user, team, "WRITER")
        await g.team_kb(team, kb, "WRITER")
        await g.direct(user, kb, "READER")

        assert await g.p.get_user_kb_permission(kb, user) == "WRITER"
        assert await g.listed_role(user, kb) == "WRITER"


class TestBackfill:
    async def test_stamps_only_uniform_reader_teams(self, g: _Graph) -> None:
        """PH01-16"""
        owner, reader = await g.user("o"), await g.user("r")
        solo_owner, all_reader, mixed = await g.team("solo"), await g.team("readers"), await g.team("mixed")
        kb_s, kb_r, kb_m = await g.kb("kb-s"), await g.kb("kb-r"), await g.kb("kb-m")
        await g.member(owner, solo_owner, "OWNER")
        await g.member(reader, all_reader, "READER")
        await g.member(owner, mixed, "OWNER")
        await g.member(reader, mixed, "READER")
        await g.team_kb(solo_owner, kb_s, None)
        await g.team_kb(all_reader, kb_r, None)
        await g.team_kb(mixed, kb_m, None)

        first = await g.p.backfill_kb_team_edge_roles()

        assert first["stamped"] == 1
        assert first["remaining_role_less"] == 2
        assert await g.edge_role(all_reader, kb_r) == "READER"
        assert await g.edge_role(solo_owner, kb_s) in (None, "")
        assert await g.edge_role(mixed, kb_m) in (None, "")
        assert await g.p.get_user_kb_permission(kb_m, owner) == "WRITER"
        assert await g.p.get_user_kb_permission(kb_m, reader) == "READER"

        second = await g.p.backfill_kb_team_edge_roles()

        assert second["stamped"] == 0
        assert second["remaining_role_less"] == 2
        assert await g.edge_role(all_reader, kb_r) == "READER"

    async def test_single_owner_team_then_reader_joins_stays_reader(self, g: _Graph) -> None:
        """S5a-T1: a stamped role would be a grant to future members, so an OWNER-only team is not stamped."""
        owner, later_reader = await g.user("o"), await g.user("late")
        team, kb = await g.team("t"), await g.kb("kb")
        await g.member(owner, team, "OWNER")
        await g.team_kb(team, kb, None)

        await g.p.backfill_kb_team_edge_roles()
        await g.member(later_reader, team, "READER")

        assert await g.edge_role(team, kb) in (None, "")
        assert await g.p.get_user_kb_permission(kb, owner) == "WRITER"
        assert await g.p.get_user_kb_permission(kb, later_reader) == "READER"
        assert await g.listed_role(later_reader, kb) == "READER"

    async def test_inactive_members_do_not_count(self, g: _Graph) -> None:
        reader, gone = await g.user("r"), await g.user("gone")
        team, kb = await g.team("t"), await g.kb("kb")
        await g.p.batch_upsert_nodes(
            [{"id": gone, "userId": gone, "orgId": g.org, "isActive": False}], CollectionNames.USERS.value
        )
        await g.member(reader, team, "READER")
        await g.member(gone, team, "OWNER")
        await g.team_kb(team, kb, None)

        await g.p.backfill_kb_team_edge_roles()

        assert await g.edge_role(team, kb) == "READER"

    async def test_member_without_role_and_empty_team_are_not_stamped(self, g: _Graph) -> None:
        reader, norole = await g.user("r"), await g.user("n")
        team, empty, kb = await g.team("t"), await g.team("empty"), await g.kb("kb")
        await g.member(reader, team, "READER")
        await g.edge(norole, CollectionNames.USERS.value, team, CollectionNames.TEAMS.value, "USER", None)
        await g.team_kb(team, kb, None)
        await g.team_kb(empty, kb, None)

        await g.p.backfill_kb_team_edge_roles()

        assert await g.edge_role(team, kb) in (None, "")
        assert await g.edge_role(empty, kb) in (None, "")


class TestOtherKbDerivedSites:
    async def test_record_access_details_uses_edge_role(self, g: _Graph) -> None:
        owner = await g.user("o")
        team, kb = await g.team("t"), await g.kb("kb")
        await g.member(owner, team, "OWNER")
        await g.team_kb(team, kb, "READER")
        record = await g.record_in(kb)

        assert await g.record_access_role(owner, record) == "READER"

    async def test_record_access_details_legacy_edge_caps_at_writer(self, g: _Graph) -> None:
        owner, reader = await g.user("o"), await g.user("r")
        team, kb = await g.team("t"), await g.kb("kb")
        await g.member(owner, team, "OWNER")
        await g.member(reader, team, "READER")
        await g.team_kb(team, kb, None)
        record = await g.record_in(kb)

        assert await g.record_access_role(owner, record) == "WRITER"
        assert await g.record_access_role(reader, record) == "READER"

    async def test_records_listing_uses_edge_role(self, g: _Graph) -> None:
        owner = await g.user("o")
        team, kb = await g.team("t"), await g.kb("kb")
        await g.member(owner, team, "OWNER")
        await g.team_kb(team, kb, "READER")
        record = await g.record_in(kb)

        assert await g.records_listing_role(owner, record) == "READER"

    async def test_records_listing_legacy_edge_caps_at_writer(self, g: _Graph) -> None:
        owner, reader = await g.user("o"), await g.user("r")
        team, kb = await g.team("t"), await g.kb("kb")
        await g.member(owner, team, "OWNER")
        await g.member(reader, team, "READER")
        await g.team_kb(team, kb, None)
        record = await g.record_in(kb)

        assert await g.records_listing_role(owner, record) == "WRITER"
        assert await g.records_listing_role(reader, record) == "READER"


class TestGenericRecordPermissionPath7:
    async def test_team_owner_gets_edge_role_on_kb_record(self, g: _Graph) -> None:
        owner = await g.user("o")
        team, kb = await g.team("t"), await g.kb("kb")
        await g.member(owner, team, "OWNER")
        await g.team_kb(team, kb, "READER")
        record = await g.record_in(kb)

        assert await g.path7_role(owner, record) == "READER"

    async def test_legacy_roleless_edge_caps_owner_at_writer(self, g: _Graph) -> None:
        owner, reader = await g.user("o"), await g.user("r")
        team, kb = await g.team("t"), await g.kb("kb")
        await g.member(owner, team, "OWNER")
        await g.member(reader, team, "READER")
        await g.team_kb(team, kb, None)
        record = await g.record_in(kb)

        assert await g.path7_role(owner, record) == "WRITER"
        assert await g.path7_role(reader, record) == "READER"

    async def test_record_group_team_path_keeps_member_role(self, g: _Graph) -> None:
        owner = await g.user("o")
        team = await g.team("t")
        record = g.key("conn-rec")
        await g.p.batch_upsert_nodes(
            [{"id": record, "orgId": g.org, "recordName": "x", "recordType": "FILE", "origin": "CONNECTOR",
              "connectorName": "SLACK", "connectorId": g.key("conn"), "externalRecordId": record,
              "isDeleted": False, "createdAtTimestamp": 1, "updatedAtTimestamp": 1}],
            CollectionNames.RECORDS.value,
        )
        rg = await g.record_group_in(record)
        await g.member(owner, team, "OWNER")
        await g.edge(team, CollectionNames.TEAMS.value, rg, CollectionNames.RECORD_GROUPS.value, "TEAM", "READER")

        assert await g.path7_role(owner, record) == "OWNER"


def _fail_on_second_edge(g: _Graph, grants: list[dict]) -> None:
    """Make the second grant fail inside the provider's write, whatever the backend."""
    if isinstance(g.p, Neo4jProvider):
        # A map is not a storable property value, so the statement aborts at the second row.
        grants[1] = {**grants[1], "role": {"not": "a primitive"}}
        return
    real = g.p.batch_create_edges
    calls = {"n": 0}

    async def flaky(edges, collection, transaction=None):
        calls["n"] += 1
        if calls["n"] == 2:
            raise RuntimeError("injected failure on the second edge")
        return await real(edges, collection, transaction=transaction)

    g.p.batch_create_edges = flaky


class TestAtomicPrincipalGrants:
    async def test_grants_persist_with_per_edge_roles(self, g: _Graph) -> None:
        user, t1, t2, kb = await g.user("u"), await g.team("t1"), await g.team("t2"), await g.kb("kb")

        result = await g.p.create_kb_principal_permissions(kb, [
            {"principalType": "user", "principalId": user, "role": "OWNER"},
            {"principalType": "team", "principalId": t1, "role": "WRITER"},
            {"principalType": "team", "principalId": t2, "role": "READER"},
        ])

        assert result["success"] is True and result["grantedCount"] == 3
        assert await g.edge_role(t1, kb) == "WRITER"
        assert await g.edge_role(t2, kb) == "READER"
        assert await g.p.get_kb_permissions(kb, [user], [t1, t2]) == {
            "users": {user: "OWNER"}, "teams": {t1: "WRITER", t2: "READER"},
        }

    async def test_failure_on_second_edge_persists_nothing(self, g: _Graph) -> None:
        t1, t2, kb = await g.team("t1"), await g.team("t2"), await g.kb("kb")
        grants = [
            {"principalType": "team", "principalId": t1, "role": "WRITER"},
            {"principalType": "team", "principalId": t2, "role": "READER"},
        ]
        _fail_on_second_edge(g, grants)

        result = await g.p.create_kb_principal_permissions(kb, grants)

        assert result["success"] is False
        assert await g.edge_role(t1, kb) is None
        assert await g.edge_role(t2, kb) is None

    async def test_failure_in_the_team_part_leaves_no_user_edge(self, g: _Graph) -> None:
        user, team, kb = await g.user("u"), await g.team("t"), await g.kb("kb")
        grants = [
            {"principalType": "user", "principalId": user, "role": "WRITER"},
            {"principalType": "team", "principalId": team, "role": "READER"},
        ]
        _fail_on_second_edge(g, grants)

        result = await g.p.create_kb_principal_permissions(kb, grants)

        assert result["success"] is False
        assert await g.p.get_kb_permissions(kb, [user], [team]) == {"users": {}, "teams": {}}

    async def test_unknown_kb_is_refused(self, g: _Graph) -> None:
        t1 = await g.team("t1")

        result = await g.p.create_kb_principal_permissions(
            g.key("missing-kb"), [{"principalType": "team", "principalId": t1, "role": "READER"}]
        )

        assert result["success"] is False and result["code"] == 404


class TestTeamRoleUpdate:
    async def _owned_kb_with_team(self, g: _Graph, role: str = "READER") -> tuple[str, str, str]:
        owner, team, kb = await g.user("o"), await g.team("t"), await g.kb("kb")
        await g.direct(owner, kb, "OWNER")
        await g.team_kb(team, kb, role)
        return owner, team, kb

    async def test_update_changes_team_edge_role(self, g: _Graph) -> None:
        owner, team, kb = await self._owned_kb_with_team(g)

        result = await g.p.update_kb_permission(kb, owner, [], [team], "COMMENTER")

        assert result["success"] is True
        assert result["updated_teams"] == 1
        assert result["updates_detail"]["teams"][team] == {"old_role": "READER", "new_role": "COMMENTER"}
        assert await g.edge_role(team, kb) == "COMMENTER"

    async def test_list_permissions_reports_team_role(self, g: _Graph) -> None:
        owner, team, kb = await self._owned_kb_with_team(g, "WRITER")

        listed = await g.p.list_kb_permissions(kb)

        assert next(p for p in listed if p["type"] == "TEAM")["role"] == "WRITER"

    async def test_service_rejects_owner_and_leaves_the_edge_unchanged(self, g: _Graph) -> None:
        from app.connectors.sources.localKB.handlers.kb_service import (
            KnowledgeBaseService,
        )

        owner, team, kb = await self._owned_kb_with_team(g)
        service = KnowledgeBaseService(logger, g.p, MagicMock())

        result = await service.update_kb_permission(kb, owner, [], [team], "OWNER")

        assert result["success"] is False and result["code"] == 400
        assert await g.edge_role(team, kb) == "READER"

    async def test_service_refuses_a_team_from_another_org_and_changes_nothing(self, g: _Graph) -> None:
        from app.connectors.sources.localKB.handlers.kb_service import (
            KnowledgeBaseService,
        )

        owner, team, kb = await self._owned_kb_with_team(g)
        foreign = g.key("t-foreign")
        await g.p.batch_upsert_nodes(
            [{"id": foreign, "name": "foreign", "orgId": f"{g.org}-other"}], CollectionNames.TEAMS.value
        )
        await g.team_kb(foreign, kb, "READER")
        service = KnowledgeBaseService(logger, g.p, MagicMock())

        result = await service.update_kb_permission(kb, owner, [], [foreign], "WRITER")

        assert result["success"] is False and result["code"] == 404
        assert await g.edge_role(foreign, kb) == "READER"
        assert await g.edge_role(team, kb) == "READER"


class TestCreateKeepsAnOwner:
    async def _service(self, g: _Graph, monkeypatch: pytest.MonkeyPatch):
        from app.connectors.sources.localKB.handlers import kb_service as module

        monkeypatch.setattr(module, "notify_kb_records_changed", AsyncMock())
        return module.KnowledgeBaseService(logger, g.p, MagicMock())

    async def test_resharing_cannot_demote_the_last_owner(self, g: _Graph, monkeypatch: pytest.MonkeyPatch) -> None:
        owner, kb = await g.user("o"), await g.kb("kb")
        await g.direct(owner, kb, "OWNER")
        service = await self._service(g, monkeypatch)

        result = await service.create_kb_permissions(
            kb, owner, [], [], "", principals=[{"principalType": "user", "principalId": owner, "role": "WRITER"}]
        )

        assert result["success"] is False and result["code"] == 400
        assert await g.p.get_kb_permissions(kb, [owner], []) == {"users": {owner: "OWNER"}, "teams": {}}

    async def test_resharing_may_demote_an_owner_when_another_remains(self, g: _Graph, monkeypatch: pytest.MonkeyPatch) -> None:
        o1, o2, kb = await g.user("o1"), await g.user("o2"), await g.kb("kb")
        await g.direct(o1, kb, "OWNER")
        await g.direct(o2, kb, "OWNER")
        service = await self._service(g, monkeypatch)

        result = await service.create_kb_permissions(
            kb, o1, [], [], "", principals=[{"principalType": "user", "principalId": o2, "role": "READER"}]
        )

        assert result["success"] is True
        assert await g.p.get_kb_permissions(kb, [o1, o2], []) == {"users": {o1: "OWNER", o2: "READER"}, "teams": {}}

    async def test_handing_ownership_over_in_one_share_is_allowed(self, g: _Graph, monkeypatch: pytest.MonkeyPatch) -> None:
        o1, o2, kb = await g.user("o1"), await g.user("o2"), await g.kb("kb")
        await g.direct(o1, kb, "OWNER")
        service = await self._service(g, monkeypatch)

        result = await service.create_kb_permissions(kb, o1, [], [], "", principals=[
            {"principalType": "user", "principalId": o2, "role": "OWNER"},
            {"principalType": "user", "principalId": o1, "role": "READER"},
        ])

        assert result["success"] is True
        assert await g.p.get_kb_permissions(kb, [o1, o2], []) == {"users": {o1: "READER", o2: "OWNER"}, "teams": {}}
