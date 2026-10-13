"""Who an organization-wide share reaches, against a real Neo4j and a real ArangoDB.

The queries that decide access are Cypher and AQL strings, so a unit test with
a mocked driver cannot tell whether they even parse. These run them for real on
both backends, over the same seeded graph:

* a record shared with the whole org the way a connector writes it ("ORG"), and
  (Neo4j only) the way a service account's chat upload does ("ORGANIZATION"):
  reachable. ArangoDB's permission-edge schema does not accept that second
  type, so the chat upload cannot write it there;
* a record carrying only a domain-typed organization edge, or only a leftover
  "anyone" node: not reachable. PipesHub decided those shares grant nothing;
* a knowledge base's sharing list: an inactive (deleted) user is left out.

Needs Docker services, and skips cleanly when they are not reachable:

  docker run -d --name neo4j-it -p 17687:7687 -e NEO4J_AUTH=neo4j/ensure-it-pass neo4j:5.26.0
  docker run -d --name arango-it -p 18529:8529 -e ARANGO_ROOT_PASSWORD=ensure-it-pass arangodb:3.12.4

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""

import asyncio
import contextlib
import logging
import os
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors, ProgressStatus
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "org_share_access_it"

logger = logging.getLogger("org-share-access-it")


@dataclass
class _Env:
    graph: IGraphDBProvider
    suffix: str
    org_id: str
    user_key: str
    user_id: str
    connector_id: str
    kb_id: str
    records: dict[str, tuple[str, str]] = field(default_factory=dict)
    anyone_ids: list[str] = field(default_factory=list)
    record_group_ids: list[str] = field(default_factory=list)
    user_keys: list[str] = field(default_factory=list)


async def _connect_neo4j(monkeypatch: pytest.MonkeyPatch) -> IGraphDBProvider:
    monkeypatch.setenv("NEO4J_URI", NEO4J_URI)
    monkeypatch.setenv("NEO4J_USERNAME", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", NEO4J_PASSWORD)
    monkeypatch.setenv("NEO4J_DATABASE", "neo4j")
    provider = Neo4jProvider(logger, MagicMock())
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("Neo4jProvider.connect returned False")
    return provider


async def _connect_arango() -> IGraphDBProvider:
    config_service = MagicMock()
    config_service.get_config = AsyncMock(
        return_value={"url": ARANGO_URL, "username": "root", "password": ARANGO_PASSWORD, "db": ARANGO_DB}
    )
    provider = ArangoHTTPProvider(logger, config_service)
    if not await asyncio.wait_for(provider.connect(), timeout=60):
        raise ConnectionError("ArangoHTTPProvider.connect returned False")
    return provider


async def _remove_test_data(env: _Env) -> None:
    graph = env.graph
    record_ids = [record_id for record_id, _ in env.records.values()]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.id IN $ids OR n.connectorId = $c OR n.orgId = $o OR n.organization = $o "
            "DETACH DELETE n",
            parameters={
                "ids": [
                    *record_ids, *env.user_keys, *env.record_group_ids,
                    env.org_id, env.connector_id, env.kb_id, *env.anyone_ids,
                ],
                "c": env.connector_id,
                "o": env.org_id,
            },
        )
        return
    for edges in (
        CollectionNames.PERMISSION.value,
        CollectionNames.BELONGS_TO.value,
        CollectionNames.USER_APP_RELATION.value,
        CollectionNames.INHERIT_PERMISSIONS.value,
    ):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER CONTAINS(e._from, @s) OR CONTAINS(e._to, @s) REMOVE e IN {edges}",
            {"s": env.suffix},
        )
    for collection, keys in (
        (CollectionNames.RECORDS.value, record_ids),
        (CollectionNames.RECORD_GROUPS.value, env.record_group_ids),
        (CollectionNames.USERS.value, env.user_keys),
        (CollectionNames.ORGS.value, [env.org_id]),
        (CollectionNames.APPS.value, [env.connector_id, env.kb_id]),
        (CollectionNames.ANYONE.value, env.anyone_ids),
    ):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @keys REMOVE d IN {collection}",
            {"keys": keys},
        )


def _edge(frm: str, frm_col: str, to: str, to_col: str, **extra: object) -> dict:
    now = get_epoch_timestamp_in_ms()
    return {
        "from_id": frm, "from_collection": frm_col, "to_id": to, "to_collection": to_col,
        "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra,
    }


async def _add_user(env: _Env, key: str, *, active: bool) -> None:
    now = get_epoch_timestamp_in_ms()
    await env.graph.batch_upsert_nodes(
        [{
            "id": key, "userId": f"uid-{key}", "orgId": env.org_id, "email": f"{key}@example.com",
            "fullName": key, "isActive": active, "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }],
        collection=CollectionNames.USERS.value,
    )
    env.user_keys.append(key)
    await env.graph.batch_create_edges(
        [_edge(key, CollectionNames.USERS.value, env.org_id, CollectionNames.ORGS.value,
               entityType="ORGANIZATION")],
        collection=CollectionNames.BELONGS_TO.value,
    )


async def _add_record(env: _Env, label: str) -> str:
    now = get_epoch_timestamp_in_ms()
    record_id = f"rec-{label}-{env.suffix}"
    virtual_id = f"vrec-{label}-{env.suffix}"
    await env.graph.batch_upsert_nodes(
        [{
            "id": record_id, "orgId": env.org_id, "recordName": f"{label} note",
            "externalRecordId": f"ext-{record_id}", "recordType": "FILE", "origin": "CONNECTOR",
            "connectorName": "WEB", "connectorId": env.connector_id, "version": 0,
            "virtualRecordId": virtual_id, "indexingStatus": ProgressStatus.COMPLETED.value,
            "isDeleted": False, "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }],
        collection=CollectionNames.RECORDS.value,
    )
    env.records[label] = (record_id, virtual_id)
    return record_id


async def _share_from_org(env: _Env, record_id: str, edge_type: str) -> None:
    await env.graph.batch_create_edges(
        [_edge(env.org_id, CollectionNames.ORGS.value, record_id, CollectionNames.RECORDS.value,
               type=edge_type, role="READER")],
        collection=CollectionNames.PERMISSION.value,
    )


@pytest.fixture(params=["neo4j", "arango"])
async def env(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_Env]:
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (_connect_neo4j(monkeypatch) if request.param == "neo4j" else _connect_arango())
        except Exception as exc:
            # Only an unreachable server skips (and backend-matrix fails on any skip).
            pytest.skip(f"{request.param} not available: {exc}")
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        # Outside the try: a schema that cannot be set up on a reachable server is a failure.
        assert await graph.ensure_schema() is not False, f"ensure_schema failed on {request.param}"

        suffix = uuid.uuid4().hex[:10]
        environment = _Env(
            graph=graph, suffix=suffix, org_id=f"org-osa-{suffix}", user_key=f"user-osa-{suffix}",
            user_id=f"uid-user-osa-{suffix}", connector_id=f"web-osa-{suffix}", kb_id=f"kb-osa-{suffix}",
        )
        cleanup.push_async_callback(_remove_test_data, environment)

        now = get_epoch_timestamp_in_ms()
        await graph.batch_upsert_nodes(
            [{"id": environment.org_id, "name": "Org", "accountType": "enterprise", "isActive": True}],
            collection=CollectionNames.ORGS.value,
        )
        await graph.batch_upsert_nodes(
            [{
                "id": environment.connector_id, "name": "Web", "type": "Web", "appGroup": "Web",
                "scope": "team", "orgId": environment.org_id, "isActive": True,
                # Without it the container filter declines to narrow this connector at all.
                "vectorMembershipBackfilled": True,
                "createdAtTimestamp": now, "updatedAtTimestamp": now,
            }],
            collection=CollectionNames.APPS.value,
        )
        await _add_user(environment, environment.user_key, active=True)
        await graph.batch_create_edges(
            [_edge(environment.user_key, CollectionNames.USERS.value, environment.connector_id,
                   CollectionNames.APPS.value, syncState="COMPLETED", lastSyncUpdate=now)],
            collection=CollectionNames.USER_APP_RELATION.value,
        )

        await _share_from_org(environment, await _add_record(environment, "org"), "ORG")
        if request.param == "neo4j":
            await _share_from_org(environment, await _add_record(environment, "chat"), "ORGANIZATION")
        await _share_from_org(environment, await _add_record(environment, "domain"), "DOMAIN")
        anyone_record = await _add_record(environment, "anyone")
        anyone_id = f"anyone_{anyone_record}"
        await graph.batch_upsert_nodes(
            [{
                "id": anyone_id, "type": "anyone", "file_key": anyone_record,
                "organization": environment.org_id, "role": "READER", "active": True,
            }],
            collection=CollectionNames.ANYONE.value,
        )
        environment.anyone_ids.append(anyone_id)
        yield environment


def _org_shared(env: _Env) -> list[str]:
    return [label for label in ("org", "chat") if label in env.records]


async def test_opening_a_record_follows_org_shares_only(env: _Env) -> None:
    for label in _org_shared(env):
        record_id, _ = env.records[label]
        access = await env.graph.check_record_access_with_details(env.user_id, env.org_id, record_id)
        assert access is not None, f"a record shared with the whole org ({label}) must open"
    for label in ("domain", "anyone"):
        record_id, _ = env.records[label]
        access = await env.graph.check_record_access_with_details(env.user_id, env.org_id, record_id)
        assert access is None, f"a {label} share must not open the record, got {access}"


async def test_search_and_chat_retrieve_org_shares_only(env: _Env) -> None:
    found = await env.graph._get_virtual_ids_for_connector(
        env.user_id, env.org_id, env.connector_id, None, raise_on_error=True,
    )
    expected = {env.records[label][1]: env.records[label][0] for label in _org_shared(env)}
    assert found == expected


async def test_the_per_record_check_follows_org_shares_only(env: _Env) -> None:
    granted = await env.graph._check_record_permissions(env.records["org"][0], env.user_key)
    assert granted.get("permission") == "READER", f"an org share must grant READER, got {granted}"
    for label in ("domain", "anyone"):
        refused = await env.graph._check_record_permissions(env.records[label][0], env.user_key)
        assert refused.get("permission") is None, f"a {label} share must grant nothing, got {refused}"


async def test_a_knowledge_bases_sharing_list_leaves_out_inactive_users(env: _Env) -> None:
    now = get_epoch_timestamp_in_ms()
    await env.graph.batch_upsert_nodes(
        [{
            "id": env.kb_id, "name": "KB", "type": Connectors.KNOWLEDGE_BASE.value, "appGroup": "Local Storage", "scope": "team",
            "orgId": env.org_id, "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }],
        collection=CollectionNames.APPS.value,
    )
    deleted_key = f"deleted-osa-{env.suffix}"
    await _add_user(env, deleted_key, active=False)
    await env.graph.batch_create_edges(
        [_edge(key, CollectionNames.USERS.value, env.kb_id, CollectionNames.APPS.value, type="USER", role="READER")
         for key in (env.user_key, deleted_key)],
        collection=CollectionNames.PERMISSION.value,
    )

    listed = {p.get("id") for p in await env.graph.list_kb_permissions(env.kb_id)}

    assert env.user_key in listed, f"an active user shared the knowledge base must be listed: {listed}"
    assert deleted_key not in listed, f"a deleted (inactive) user must not be listed: {listed}"


async def _add_record_group(env: _Env, label: str) -> str:
    now = get_epoch_timestamp_in_ms()
    group_id = f"rg-{label}-{env.suffix}"
    await env.graph.batch_upsert_nodes(
        [{
            "id": group_id, "groupName": f"{label} group", "externalGroupId": f"ext-{group_id}",
            "groupType": "PROJECT", "connectorName": "WEB", "connectorId": env.connector_id,
            "orgId": env.org_id, "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }],
        collection=CollectionNames.RECORD_GROUPS.value,
    )
    env.record_group_ids.append(group_id)
    await env.graph.batch_create_edges(
        [_edge(env.org_id, CollectionNames.ORGS.value, group_id, CollectionNames.RECORD_GROUPS.value,
               type="ORG" if label == "org" else "DOMAIN", role="READER")],
        collection=CollectionNames.PERMISSION.value,
    )
    return group_id


async def test_the_container_filter_follows_org_shares_only(env: _Env) -> None:
    """The filter chat builds when containers are searched, not records one by one."""
    org_group = await _add_record_group(env, "org")
    domain_group = await _add_record_group(env, "domain")

    containers = await env.graph.get_accessible_containers(env.user_id, env.org_id)

    assert containers.fallback_reason is None, containers.fallback_reason
    assert org_group in containers.record_group_ids, "a record group shared with the whole org must be searchable"
    assert domain_group not in containers.record_group_ids, "a domain-typed org edge must not open a record group"
    direct = set(containers.direct_records) | set(containers.direct_records.values())
    assert env.records["org"][0] in direct, "a record shared with the whole org must be searchable"
    assert env.records["domain"][0] not in direct, "a domain-typed org edge must not open a record"


async def _inherit_from(env: _Env, record_id: str, group_id: str) -> None:
    await env.graph.batch_create_edges(
        [_edge(record_id, CollectionNames.RECORDS.value, group_id, CollectionNames.RECORD_GROUPS.value)],
        collection=CollectionNames.INHERIT_PERMISSIONS.value,
    )


async def test_a_shared_record_group_grants_only_the_records_inside_it(env: _Env) -> None:
    """A role on a record group reaches the records that inherit from it, and no others."""
    org_group = await _add_record_group(env, "org")
    inside = await _add_record(env, "inside-org-group")
    outside = await _add_record(env, "outside")
    await _inherit_from(env, inside, org_group)

    granted = await env.graph._check_record_permissions(inside, env.user_key)
    refused = await env.graph._check_record_permissions(outside, env.user_key)

    assert granted.get("permission") == "READER", f"a record in an org-shared group must open, got {granted}"
    assert refused.get("permission") is None, f"an org-shared group must not open a record outside it, got {refused}"


async def test_a_users_record_group_role_grants_only_the_records_inside_it(env: _Env) -> None:
    now = get_epoch_timestamp_in_ms()
    group_id = f"rg-user-{env.suffix}"
    await env.graph.batch_upsert_nodes(
        [{
            "id": group_id, "groupName": "user group", "externalGroupId": f"ext-{group_id}",
            "groupType": "PROJECT", "connectorName": "WEB", "connectorId": env.connector_id,
            "orgId": env.org_id, "createdAtTimestamp": now, "updatedAtTimestamp": now,
        }],
        collection=CollectionNames.RECORD_GROUPS.value,
    )
    env.record_group_ids.append(group_id)
    await env.graph.batch_create_edges(
        [_edge(env.user_key, CollectionNames.USERS.value, group_id, CollectionNames.RECORD_GROUPS.value,
               type="USER", role="WRITER")],
        collection=CollectionNames.PERMISSION.value,
    )
    inside = await _add_record(env, "inside-user-group")
    outside = await _add_record(env, "outside-user-group")
    await _inherit_from(env, inside, group_id)

    granted = await env.graph._check_record_permissions(inside, env.user_key)
    refused = await env.graph._check_record_permissions(outside, env.user_key)

    assert granted.get("permission") == "WRITER", f"a record in the user's group must open, got {granted}"
    assert refused.get("permission") is None, f"the user's group must not open a record outside it, got {refused}"
