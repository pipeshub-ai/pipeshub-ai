"""The Drive connector's audience groups on a real graph: reading a group's members, and the cut-listing write.

On a cut user listing the connector keeps each audience group's stored members, so it reads them with
``get_users_with_permission_to_node(group.id, "groups")``. That lookup is shared with records, where it also walks
inherited permissions. These tests pin, on Neo4j and ArangoDB, that for a group it returns exactly the members: not
a user who only holds a record the group is granted on, and not one reached through an inherit edge from that record.
Then they run the cut-listing write on the real store: a member the listing leaves out stays, and one it shows
suspended is removed.

Runs on Neo4j and ArangoDB (backend-matrix). Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL,
ARANGO_IT_PASSWORD.
"""

from __future__ import annotations

import contextlib
import logging
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.google.drive.team.connector import (
    ALL_MEMBERS_GROUP_ID,
    DOMAIN_MEMBERS_GROUP_PREFIX,
    GoogleDriveTeamConnector,
)
from app.models.entities import AppUser, AppUserGroup
from app.models.permission import EntityType, Permission, PermissionType
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)
from tests.integration.test_link_and_domain_shares_e2e import DOMAIN, _file, _user

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

ARANGO_DB = "drive_audience_groups_it"
NAMES = ("owner", "colleague", "suspended", "outsider")

logger = logging.getLogger("drive-audience-groups-it")


@dataclass
class _Env:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    org_id: str
    connector_id: str
    users: dict[str, dict]


async def _remove(graph: IGraphDBProvider, connector_id: str, user_keys: list[str], org_id: str) -> None:
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.connectorId = $c OR n.id IN $ids DETACH DELETE n",
            parameters={"c": connector_id, "ids": [connector_id, org_id, *user_keys]},
        )
        return
    handles = [f"users/{k}" for k in user_keys] + [f"apps/{connector_id}"]
    for collection in (CollectionNames.RECORDS.value, CollectionNames.GROUPS.value):
        rows = await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.connectorId == @c RETURN d._id", {"c": connector_id}
        )
        handles += list(rows or [])
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.connectorId == @c REMOVE d IN {collection}", {"c": connector_id}
        )
    for collection, keys in (
        (CollectionNames.USERS.value, user_keys), (CollectionNames.APPS.value, [connector_id]),
        (CollectionNames.ORGS.value, [org_id]),
    ):
        await graph.http_client.execute_aql(
            f"FOR k IN @keys REMOVE k IN {collection} OPTIONS {{ignoreErrors: true}}", {"keys": keys}
        )
    for edge in (
        CollectionNames.PERMISSION.value, CollectionNames.USER_APP_RELATION.value,
        CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.BELONGS_TO.value,
    ):
        await graph.http_client.execute_aql(
            f"FOR e IN {edge} FILTER e._from IN @h OR e._to IN @h REMOVE e IN {edge}", {"h": handles}
        )


@pytest.fixture(params=["neo4j", "arango"])
async def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (
                connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB)
            )
        except Exception as exc:
            backend_unavailable(request.param, exc)
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)

        run = uuid.uuid4().hex[:10]
        org_id, connector_id = f"org-audience-{run}", f"drive-audience-{run}"
        users = {name: _user(run, name, org_id) for name in NAMES}
        cleanup.push_async_callback(_remove, graph, connector_id, [u["id"] for u in users.values()], org_id)
        now = get_epoch_timestamp_in_ms()
        assert await graph.batch_upsert_nodes(
            [{
                "id": connector_id, "name": "Google Drive", "type": Connectors.GOOGLE_DRIVE.value,
                "appGroup": "Google Workspace", "scope": "team", "isActive": True, "orgId": org_id,
                "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.APPS.value,
        )
        assert await graph.batch_upsert_nodes(list(users.values()), collection=CollectionNames.USERS.value)
        assert await graph.batch_create_edges(
            [
                {
                    "from_id": u["id"], "from_collection": CollectionNames.USERS.value,
                    "to_id": connector_id, "to_collection": CollectionNames.APPS.value,
                    "syncState": "COMPLETED", "lastSyncUpdate": now, "createdAtTimestamp": now, "updatedAtTimestamp": now,
                }
                for u in users.values()
            ],
            collection=CollectionNames.USER_APP_RELATION.value,
        )
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id
        processor.messaging_producer = AsyncMock()
        processor.messaging_producer.send_messages.side_effect = lambda _topic, messages: [True] * len(messages)
        yield _Env(graph, processor, org_id, connector_id, users)


def _app_user(env: _Env, name: str, *, active: bool = True) -> AppUser:
    user = env.users[name]
    return AppUser(
        app_name=Connectors.GOOGLE_DRIVE_WORKSPACE, connector_id=env.connector_id, source_user_id=f"src-{name}",
        email=user["email"], full_name=name, is_active=active, org_id=env.org_id,
    )


async def _members(env: _Env, group_id: str) -> set[str]:
    groups = {g.source_user_group_id: g for g in await env.processor.get_all_user_groups(env.connector_id, raise_on_error=True)}
    users = await env.processor.get_users_with_permission_to_node(
        groups[group_id].id, CollectionNames.GROUPS.value, raise_on_error=True
    )
    return {(u.email or "").lower() for u in users}


def _emails(env: _Env, *names: str) -> set[str]:
    return {env.users[n]["email"].lower() for n in names}


async def test_a_groups_members_are_read_back_exactly(env: _Env) -> None:
    group = AppUserGroup(
        source_user_group_id=ALL_MEMBERS_GROUP_ID, app_name=Connectors.GOOGLE_DRIVE_WORKSPACE,
        connector_id=env.connector_id, name="Workspace members",
    )
    await env.processor.on_new_user_groups([(group, [_app_user(env, "owner"), _app_user(env, "colleague")])])
    # A file the group is granted on, which the outsider also holds and inherits from a folder the suspended user holds.
    folder, file = _file(env, "folder"), _file(env, "file.txt")
    await env.processor.on_new_records([
        (folder, [Permission(type=PermissionType.READ, entity_type=EntityType.USER, email=env.users["suspended"]["email"])]),
        (file, [
            Permission(type=PermissionType.READ, entity_type=EntityType.GROUP, external_id=ALL_MEMBERS_GROUP_ID),
            Permission(type=PermissionType.READ, entity_type=EntityType.USER, email=env.users["outsider"]["email"]),
        ]),
    ])
    assert await env.graph.batch_create_edges(
        [{
            "from_id": file.id, "from_collection": CollectionNames.RECORDS.value,
            "to_id": folder.id, "to_collection": CollectionNames.RECORDS.value,
            "createdAtTimestamp": get_epoch_timestamp_in_ms(), "updatedAtTimestamp": get_epoch_timestamp_in_ms(),
        }],
        collection=CollectionNames.INHERIT_PERMISSIONS.value,
    )
    holders = await env.processor.get_users_with_permission_to_node(file.id, CollectionNames.RECORDS.value, raise_on_error=True)
    assert _emails(env, "outsider", "suspended") <= {(u.email or "").lower() for u in holders}, (
        "control: the file's lookup reaches the outsider directly and the suspended user through the folder"
    )

    assert await _members(env, ALL_MEMBERS_GROUP_ID) == _emails(env, "owner", "colleague")


async def test_a_cut_listing_keeps_a_member_it_leaves_out_and_removes_one_it_shows_suspended(env: _Env) -> None:
    connector = object.__new__(GoogleDriveTeamConnector)
    connector.logger = logger
    connector.data_entities_processor = env.processor
    connector.connector_id = env.connector_id
    connector.connector_name = Connectors.GOOGLE_DRIVE_WORKSPACE
    connector.drive_delta_sync_point = MagicMock()
    connector.drive_delta_sync_point.read_sync_point = AsyncMock(return_value={})
    connector.drive_delta_sync_point.update_sync_point = AsyncMock()
    connector._sync_ledger = MagicMock()
    connector._account_domains = {DOMAIN}
    connector._reach_candidates = {}
    connector._run_started_at = get_epoch_timestamp_in_ms()
    connector._alias_domains = {}

    # A complete listing writes the groups exactly.
    connector.synced_users = [_app_user(env, n) for n in ("owner", "colleague", "suspended")]
    connector._inactive_emails, connector._listing_cut = set(), False
    await connector._write_audience_groups()
    everyone = _emails(env, "owner", "colleague", "suspended")
    assert await _members(env, ALL_MEMBERS_GROUP_ID) == everyone
    assert await _members(env, DOMAIN_MEMBERS_GROUP_PREFIX + DOMAIN) == everyone

    # A cut listing leaves the colleague out and shows the suspended user unable to sign in.
    connector.synced_users = [_app_user(env, "owner"), _app_user(env, "suspended", active=False)]
    connector._inactive_emails, connector._listing_cut = _emails(env, "suspended"), True
    await connector._write_audience_groups()

    assert not connector._sync_ledger.note_incomplete.called
    for group_id in (ALL_MEMBERS_GROUP_ID, DOMAIN_MEMBERS_GROUP_PREFIX + DOMAIN):
        assert await _members(env, group_id) == _emails(env, "owner", "colleague"), group_id
