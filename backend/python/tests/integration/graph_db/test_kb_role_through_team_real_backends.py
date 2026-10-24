"""A team never makes its members owners of what it is granted.

The role a member holds through a team comes from the team's grant (the role on the
team -> target PERMISSION edge). A grant that carries no role, which is every grant
the share dialog writes, gives each member their team role capped at WRITER. Both
providers read the member's role in the team instead, so a team OWNER, including the
oldest user of every org through the "All" team, became OWNER of every knowledge base
shared with the team: they could share it on, change its members' roles and delete it.

Runs in backend-matrix on both graph jobs. Environment: NEO4J_IT_URI,
NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "kb_role_through_team_it"
# Ranked above OWNER by the hub's record and record-group role maps.
UNKNOWN_ROLE = "ADMIN"
MEMBER_ROLES = ("OWNER", "WRITER", "READER")

logger = logging.getLogger("kb-role-through-team-it")


@dataclass
class _Member:
    key: str
    user_id: str


@dataclass
class _Org:
    graph: IGraphDBProvider
    service: KnowledgeBaseService
    org_id: str
    owner: _Member
    members: dict[str, _Member]  # team role -> member
    shared_kb: str  # shared through the service: the team edge carries no role
    granted_kb: str  # the team edge carries role READER
    shared_folder: str
    granted_folder: str
    legacy_group: str  # a record group a role-less team edge points at
    odd_member: _Member  # holds a team role the product never writes
    odd_folder: str  # in shared_kb; a team edge with a role the product never writes points at it
    odd_roles_stored: bool  # ArangoDB's permissions schema refuses them; Neo4j edges have no schema


async def _remove(graph: IGraphDBProvider, ids: dict[str, list[str]]) -> None:
    for collection, keys in ids.items():
        if keys:
            with contextlib.suppress(Exception):
                await graph.delete_nodes_and_edges(keys, collection)


def _edge(src: str, src_coll: str, dst: str, dst_coll: str, now: int, **extra: object) -> dict:
    return {"from_id": src, "from_collection": src_coll, "to_id": dst, "to_collection": dst_coll,
            "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}


@pytest.fixture(params=["neo4j", "arango"])
async def org(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Org]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j"
                else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id = f"org-kbteam-{run}"
        owner = _Member(f"ckey-kbteam-{run}", f"creator-kbteam-{run}")
        members = {role: _Member(f"{role.lower()}key-kbteam-{run}", f"{role.lower()}-kbteam-{run}")
                   for role in MEMBER_ROLES}
        odd_member = _Member(f"oddkey-kbteam-{run}", f"odd-kbteam-{run}")
        team_key = f"team-kbteam-{run}"
        group_key = f"rg-kbteam-{run}"
        users, teams, apps, groups = (CollectionNames.USERS.value, CollectionNames.TEAMS.value,
                                      CollectionNames.APPS.value, CollectionNames.RECORD_GROUPS.value)
        seeded: dict[str, list[str]] = {
            CollectionNames.ORGS.value: [org_id],
            users: [owner.key, odd_member.key, *(m.key for m in members.values())],
            teams: [team_key],
            groups: [group_key],
            apps: [],
            CollectionNames.RECORDS.value: [],
        }
        cleanup.push_async_callback(_remove, graph, seeded)
        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes(
            [{"_key": org_id, "accountType": "enterprise", "isActive": True, "name": "kb team"}],
            CollectionNames.ORGS.value,
        )
        assert await graph.batch_upsert_nodes(
            [{"_key": m.key, "userId": m.user_id, "orgId": org_id, "email": f"{m.user_id}@example.com",
              "isActive": True} for m in (owner, odd_member, *members.values())],
            users,
        )
        assert await graph.batch_upsert_nodes(
            [{"_key": team_key, "name": f"team {run}", "orgId": org_id, "createdBy": members["OWNER"].key,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            teams,
        )
        # The edges PUT /api/v1/teams/:id writes (entity.py update_team).
        assert await graph.batch_create_edges(
            [_edge(m.key, users, team_key, teams, now, type="USER", role=role) for role, m in members.items()],
            CollectionNames.PERMISSION.value,
        )

        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id

        async def processor_for_kb(_kb_id: str) -> DataSourceEntitiesProcessor:
            return processor

        service = KnowledgeBaseService(logger, graph, MagicMock(), processor_for_kb=processor_for_kb)

        kbs: dict[str, str] = {}
        folders: dict[str, str] = {}
        for label in ("shared", "granted"):
            created = await service.create_knowledge_base(
                user_id=owner.user_id, org_id=org_id, name=f"kbteam {label} {run}")
            assert created and created.get("success") is not False, created
            kbs[label] = created["id"]
            seeded[apps].append(created["id"])
            folder = await service.create_folder_in_kb(created["id"], "docs", owner.user_id, org_id)
            assert folder and folder.get("success") is not False, folder
            folders[label] = folder["id"]
            seeded[CollectionNames.RECORDS.value].append(folder["id"])

        shared = await service.create_kb_permissions(
            kbs["shared"], owner.user_id, user_ids=[], team_ids=[team_key], role="READER",
        )
        assert shared and shared.get("success") is not False, shared
        assert await graph.batch_create_edges(
            [_edge(team_key, teams, kbs["granted"], apps, now, type="TEAM", role="READER")],
            CollectionNames.PERMISSION.value,
        )

        odd_folder = await service.create_folder_in_kb(kbs["shared"], "odd", owner.user_id, org_id)
        assert odd_folder and odd_folder.get("success") is not False, odd_folder
        seeded[CollectionNames.RECORDS.value].append(odd_folder["id"])

        # A knowledge base from before the KB apps migration: its team edge points at a record group.
        assert await graph.batch_upsert_nodes(
            [{"_key": group_key, "orgId": org_id, "groupName": "legacy kb", "externalGroupId": group_key,
              "groupType": Connectors.KNOWLEDGE_BASE.value, "connectorName": Connectors.KNOWLEDGE_BASE.value,
              "connectorId": kbs["shared"], "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            groups,
        )
        assert await graph.batch_create_edges(
            [_edge(team_key, teams, group_key, groups, now, type="TEAM"),
             # A role-less team edge on a single record of the READER-granted collection.
             _edge(team_key, teams, folders["granted"], CollectionNames.RECORDS.value, now, type="TEAM")],
            CollectionNames.PERMISSION.value,
        )
        try:
            odd_roles_stored = bool(await graph.batch_create_edges(
                [_edge(odd_member.key, users, team_key, teams, now, type="USER", role=UNKNOWN_ROLE),
                 _edge(team_key, teams, odd_folder["id"], CollectionNames.RECORDS.value, now, type="TEAM",
                       role=UNKNOWN_ROLE)],
                CollectionNames.PERMISSION.value,
            ))
        except Exception:
            odd_roles_stored = False

        yield _Org(graph, service, org_id, owner, members, kbs["shared"], kbs["granted"],
                   folders["shared"], folders["granted"], group_key, odd_member, odd_folder["id"],
                   odd_roles_stored)


# Team role -> the role the team's grant gives them.
ROLE_LESS_GRANT = {"OWNER": "WRITER", "WRITER": "WRITER", "READER": "READER"}
READER_GRANT = {"OWNER": "READER", "WRITER": "READER", "READER": "READER"}


async def test_a_role_less_team_grant_never_gives_owner(org: _Org) -> None:
    roles = {r: await org.graph.get_user_kb_permission(org.shared_kb, m.key) for r, m in org.members.items()}

    assert roles == ROLE_LESS_GRANT


async def test_the_role_on_the_team_grant_decides(org: _Org) -> None:
    roles = {r: await org.graph.get_user_kb_permission(org.granted_kb, m.key) for r, m in org.members.items()}

    assert roles == READER_GRANT


async def test_the_kb_list_shows_the_role_the_team_grant_gives(org: _Org) -> None:
    for team_role, member in org.members.items():
        result = await org.service.list_user_knowledge_bases(
            user_id=member.user_id, org_id=org.org_id, page=1, limit=50, search=None,
        )
        assert isinstance(result, dict) and "knowledgeBases" in result, result
        listed = {kb["id"]: kb["userRole"] for kb in result["knowledgeBases"]}
        assert listed == {org.shared_kb: ROLE_LESS_GRANT[team_role], org.granted_kb: READER_GRANT[team_role]}, (
            f"team {team_role}")


async def test_the_kb_page_shows_the_role_the_team_grant_gives(org: _Org) -> None:
    team_owner = org.members["OWNER"]

    shared = await org.service.get_knowledge_base(org.shared_kb, team_owner.user_id)
    granted = await org.service.get_knowledge_base(org.granted_kb, team_owner.user_id)

    assert (shared or {}).get("userRole") == "WRITER", shared
    assert (granted or {}).get("userRole") == "READER", granted


async def test_a_team_owner_cannot_share_the_kb_on(org: _Org) -> None:
    team_owner = org.members["OWNER"]

    refused = await org.service.create_kb_permissions(
        org.shared_kb, team_owner.user_id, user_ids=[org.members["READER"].user_id], team_ids=[], role="OWNER",
    )

    assert refused.get("success") is False and refused.get("code") == 403, refused
    assert await org.graph.get_user_kb_permission(org.shared_kb, org.members["READER"].key) == "READER"


async def test_a_team_owner_cannot_write_to_a_kb_granted_for_reading(org: _Org) -> None:
    team_owner = org.members["OWNER"]

    refused = await org.service.create_folder_in_kb(org.granted_kb, "not mine", team_owner.user_id, org.org_id)

    assert refused.get("success") is False and refused.get("code") == 403, refused


async def test_record_access_reads_the_role_the_team_grant_gives(org: _Org) -> None:
    for team_role, member in org.members.items():
        access = await org.graph.check_record_access_with_details(member.user_id, org.org_id, org.shared_folder)
        assert access, f"team {team_role} lost access"
        assert access["permissions"][0]["relationship"] == ROLE_LESS_GRANT[team_role], (team_role, access)


async def test_hub_permissions_read_the_role_the_team_grant_gives(org: _Org) -> None:
    team_owner = org.members["OWNER"].key

    async def permissions(parent_id: str, parent_type: str) -> dict:
        return await org.graph.get_knowledge_hub_context_permissions(
            team_owner, org.org_id, parent_id, parent_type=parent_type)

    on_shared = await permissions(org.shared_kb, "app")
    on_granted = await permissions(org.granted_kb, "app")
    on_group = await permissions(org.legacy_group, "recordGroup")
    on_record = await permissions(org.granted_folder, "folder")

    assert (on_shared["role"], on_shared["canManagePermissions"]) == ("WRITER", False), on_shared
    assert (on_granted["role"], on_granted["canUpload"]) == ("READER", False), on_granted
    assert (on_group["role"], on_group["canManagePermissions"]) == ("WRITER", False), on_group
    assert (on_record["role"], on_record["canManagePermissions"]) == ("WRITER", False), on_record


async def test_the_kb_list_counts_and_filters_by_the_role_the_team_grant_gives(org: _Org) -> None:
    team_owner = org.members["OWNER"]

    writable = await org.service.list_user_knowledge_bases(
        user_id=team_owner.user_id, org_id=org.org_id, page=1, limit=50, search=None, permissions=["WRITER"],
    )
    owned = await org.service.list_user_knowledge_bases(
        user_id=team_owner.user_id, org_id=org.org_id, page=1, limit=50, search=None, permissions=["OWNER"],
    )

    assert [kb["id"] for kb in writable["knowledgeBases"]] == [org.shared_kb], writable
    assert writable["pagination"]["totalCount"] == 1, writable["pagination"]
    assert set(writable["filters"]["available"]["permissions"]) == {"WRITER", "READER"}, writable["filters"]
    assert owned["knowledgeBases"] == [] and owned["pagination"]["totalCount"] == 0, owned


async def test_a_role_the_product_never_writes_gives_no_role(org: _Org) -> None:
    if not org.odd_roles_stored:
        assert isinstance(org.graph, ArangoHTTPProvider), "only ArangoDB's schema refuses an unknown role"
        return
    assert await org.graph.get_user_kb_permission(org.shared_kb, org.odd_member.key) is None
    listed = await org.service.list_user_knowledge_bases(
        user_id=org.odd_member.user_id, org_id=org.org_id, page=1, limit=50, search=None,
    )
    # The grant's own role still reaches every member, whatever their team role.
    assert {kb["id"]: kb["userRole"] for kb in listed["knowledgeBases"]} == {org.granted_kb: "READER"}, listed
    assert listed["pagination"]["totalCount"] == 1, listed["pagination"]
    assert listed["filters"]["available"]["permissions"] == ["READER"], listed["filters"]

    on_record = await org.graph.get_knowledge_hub_context_permissions(
        org.members["OWNER"].key, org.org_id, org.odd_folder, parent_type="folder")
    assert (on_record["role"], on_record["canManagePermissions"]) == ("WRITER", False), on_record
