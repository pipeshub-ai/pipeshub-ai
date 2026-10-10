"""A re-sync writes onto what it stored before, on both backends.

* N4MISC-01: an RSS full sync keeps the articles that have left the feed; the
  sweep after it must not take their edges.
* SERVICENOW-08 / N4MISC-02: a group read again is written onto the stored node,
  the lookup by external id returns the oldest copy, and the merge of existing
  copies moves their members and grants onto that copy.
* BOOKSTACK-06: clearing a user's role memberships of one connector leaves the
  roles of every other connector.
* STORAGE-RSS-01: a rewritten record keeps its creation time.

Driven through the processor and the providers, as test_write_path.py is.
"""

import logging
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames, Connectors
from app.config.constants.neo4j import collection_to_label
from app.connectors.core.sync.sync_runner import _sweep_stale_sync_edges
from app.connectors.sources.rss.connector import RSSConnector
from app.models.entities import AppRole, AppUserGroup
from app.models.permission import EntityType, Permission, PermissionType

from .processor_harness import build_processor
from .test_write_path import (
    ORG,
    _ArangoBackend,
    _group,
    _Neo4jBackend,
    _record,
    _seed_app,
    _seed_user,
    _suffix,
)

pytestmark = pytest.mark.integration

GENERATION = 1_760_000_000_000
GROUPS, ROLES, USERS = CollectionNames.GROUPS.value, CollectionNames.ROLES.value, CollectionNames.USERS.value
RECORDS, RECORD_GROUPS = CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value
PERMISSION = CollectionNames.PERMISSION.value


class _Arango(_ArangoBackend):
    async def group_ids(self, connector_id: str, external_id: str) -> list[str]:
        return await self._aql(
            f"FOR g IN {GROUPS} FILTER g.connectorId == @c AND g.externalGroupId == @e SORT g._key RETURN g._key",
            {"c": connector_id, "e": external_id},
        )

    async def edge_count(self, edge_collection: str, from_collection: str, from_id: str,
                         to_collection: str, to_id: str) -> int:
        rows = await self._aql(
            f"RETURN LENGTH(FOR e IN {edge_collection} FILTER e._from == @from AND e._to == @to RETURN 1)",
            {"from": f"{from_collection}/{from_id}", "to": f"{to_collection}/{to_id}"},
        )
        return rows[0]

    async def created_at(self, collection: str, key: str) -> int | None:
        rows = await self._aql(f"FOR n IN {collection} FILTER n._key == @k RETURN n.createdAtTimestamp", {"k": key})
        return rows[0] if rows else None


class _Neo4j(_Neo4jBackend):
    async def group_ids(self, connector_id: str, external_id: str) -> list[str]:
        rows = await self._cypher(
            "MATCH (g:Group {connectorId: $c, externalGroupId: $e}) RETURN g.id AS id ORDER BY id",
            {"c": connector_id, "e": external_id},
        )
        return [r["id"] for r in rows]

    async def edge_count(self, edge_collection: str, from_collection: str, from_id: str,
                         to_collection: str, to_id: str) -> int:
        from app.config.constants.neo4j import edge_collection_to_relationship

        rows = await self._cypher(
            f"MATCH (:{collection_to_label(from_collection)} {{id: $from}})"
            f"-[r:{edge_collection_to_relationship(edge_collection)}]->"
            f"(:{collection_to_label(to_collection)} {{id: $to}}) RETURN count(r) AS n",
            {"from": from_id, "to": to_id},
        )
        return rows[0]["n"]

    async def created_at(self, collection: str, key: str) -> int | None:
        rows = await self._cypher(
            f"MATCH (n:{collection_to_label(collection)} {{id: $k}}) RETURN n.createdAtTimestamp AS v", {"k": key}
        )
        return rows[0]["v"] if rows else None


@pytest.fixture(params=["arango", "neo4j"])
def backend(request, arango_provider, arango_settings, neo4j_provider, neo4j_settings) -> _Arango | _Neo4j:
    if request.param == "arango":
        return _Arango(arango_provider, arango_settings)
    return _Neo4j(neo4j_provider, neo4j_settings)


def _org_group(connector_id: str, external_id: str, *, created_at: int | None = None) -> AppUserGroup:
    extra = {} if created_at is None else {"created_at": created_at}
    return AppUserGroup(
        app_name=Connectors.SERVICENOW, connector_id=connector_id, source_user_group_id=external_id,
        name=f"COMPANY_{external_id}", org_id=ORG, **extra,
    )


def _grant(from_collection: str, from_id: str, to_collection: str, to_id: str) -> dict:
    entity = EntityType.GROUP if from_collection == GROUPS else EntityType.USER
    return Permission(external_id=from_id, type=PermissionType.READ, entity_type=entity).to_arango_permission(
        from_id, from_collection, to_id, to_collection
    )


# --- N4MISC-01 ------------------------------------------------------------------


def _feed(*guids: str) -> MagicMock:
    feed = MagicMock()
    feed.entries = [{"title": g, "link": f"https://news.example.com/{g}", "id": g} for g in guids]
    feed.feed = {"title": "News"}
    return feed


async def test_an_rss_full_sync_keeps_the_articles_that_left_the_feed(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    processor.ensure_team_app_edge = AsyncMock()
    connector_id = f"rss-{_suffix()}"
    await _seed_app(backend, connector_id)
    feed_url = f"https://news.example.com/{_suffix()}/rss"
    connector = RSSConnector(
        logger=logging.getLogger("rss"), data_entities_processor=processor,
        data_store_provider=processor.data_store_provider, config_service=AsyncMock(),
        connector_id=connector_id, scope="team", created_by="creator",
    )
    connector.feed_urls = [feed_url]
    connector.session = MagicMock()
    connector._resolve_entry_text = AsyncMock(side_effect=lambda entry, url: f"text of {url}")
    connector._fetch_and_parse_feed = AsyncMock(return_value=_feed("aged-out", "still-listed"))
    await connector.run_sync()
    aged = await processor.get_record_by_external_id(connector_id, "aged-out")
    group = await processor.get_record_group_by_external_id(connector_id, feed_url)
    assert aged is not None and group is not None

    _, ok = await backend.provider.mark_connector_sync_edges(connector_id, GENERATION)
    assert ok
    connector._fetch_and_parse_feed = AsyncMock(return_value=_feed("still-listed"))
    connector.stored_access_kept = None
    await connector.run_sync()
    await _sweep_stale_sync_edges(connector, connector_id, backend.provider, logging.getLogger("t"), GENERATION, None)

    relations = CollectionNames.NODE_RELATIONS.value
    assert await backend.edge_exists(relations, RECORD_GROUPS, group.id, RECORDS, aged.id)
    assert await backend.edge_exists(CollectionNames.BELONGS_TO.value, RECORDS, aged.id, RECORD_GROUPS, group.id)
    assert await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, RECORDS, aged.id, RECORD_GROUPS, group.id)


# --- SERVICENOW-08 / N4MISC-02 --------------------------------------------------


async def test_a_group_read_again_is_written_onto_the_stored_one(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"sn-{_suffix()}"

    await processor.batch_upsert_user_groups([_org_group(connector_id, "acme")])
    first = await backend.group_ids(connector_id, "acme")
    await processor.batch_upsert_user_groups([_org_group(connector_id, "acme")])

    assert len(first) == 1
    assert await backend.group_ids(connector_id, "acme") == first


async def test_the_lookup_returns_the_oldest_copy_whatever_its_id(backend) -> None:
    connector_id = f"sn-{_suffix()}"
    older = _org_group(connector_id, "acme", created_at=1_000).model_copy(update={"id": f"z-{uuid.uuid4()}"})
    newer = _org_group(connector_id, "acme", created_at=2_000).model_copy(update={"id": f"a-{uuid.uuid4()}"})
    await backend.provider.batch_upsert_user_groups([newer, older])

    found = await backend.provider.get_user_group_by_external_id(connector_id, "acme")

    assert found is not None and found.id == older.id


async def test_the_copies_of_a_group_are_merged_onto_the_oldest(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    # The merge covers every ServiceNow connector in the database: fold other tests' copies first.
    await backend.provider.merge_duplicate_user_groups([Connectors.SERVICENOW.value])
    connector_id = f"sn-{_suffix()}"
    await _seed_app(backend, connector_id)
    keeper = _org_group(connector_id, "acme", created_at=1_000)
    copy = _org_group(connector_id, "acme", created_at=2_000)
    alone = _org_group(connector_id, "globex", created_at=2_000)
    await backend.provider.batch_upsert_user_groups([keeper, copy, alone])
    kb = _group(f"kb-{_suffix()}", inherit=False, connector_id=connector_id)
    await processor.on_new_record_groups([(kb, [])])
    only_on_copy = await _seed_user(backend, f"a-{_suffix()}@example.com")
    on_both = await _seed_user(backend, f"b-{_suffix()}@example.com")
    await backend.provider.batch_create_edges(
        [
            _grant(USERS, only_on_copy, GROUPS, copy.id),
            _grant(USERS, on_both, GROUPS, copy.id),
            _grant(USERS, on_both, GROUPS, keeper.id),
            _grant(GROUPS, copy.id, RECORD_GROUPS, kb.id),
        ],
        collection=PERMISSION,
    )

    result = await backend.provider.merge_duplicate_user_groups([Connectors.SERVICENOW.value])

    assert result["groups"] == 1 and result["removed"] == 1
    assert await backend.group_ids(connector_id, "acme") == [keeper.id]
    assert await backend.group_ids(connector_id, "globex") == [alone.id]
    assert await backend.edge_count(PERMISSION, USERS, only_on_copy, GROUPS, keeper.id) == 1
    assert await backend.edge_count(PERMISSION, USERS, on_both, GROUPS, keeper.id) == 1
    assert await backend.edge_count(PERMISSION, GROUPS, keeper.id, RECORD_GROUPS, kb.id) == 1
    assert not await backend.edge_exists(PERMISSION, USERS, only_on_copy, GROUPS, copy.id)
    again = await backend.provider.merge_duplicate_user_groups([Connectors.SERVICENOW.value])
    assert again == {"groups": 0, "removed": 0, "moved": 0}


async def test_the_merge_leaves_other_connectors_groups_alone(backend) -> None:
    connector_id = f"conf-{_suffix()}"
    copies = [
        AppUserGroup(app_name=Connectors.CONFLUENCE, connector_id=connector_id, source_user_group_id="team",
                     name="team", org_id=ORG, created_at=at)
        for at in (1_000, 2_000)
    ]
    await backend.provider.batch_upsert_user_groups(copies)

    await backend.provider.merge_duplicate_user_groups([Connectors.SERVICENOW.value])

    assert len(await backend.group_ids(connector_id, "team")) == 2


# --- BOOKSTACK-06 ---------------------------------------------------------------


async def test_a_users_roles_of_other_connectors_survive_a_scoped_clear(backend) -> None:
    bookstack, jira = f"bs-{_suffix()}", f"jira-{_suffix()}"
    roles = [
        AppRole(app_name=Connectors.BOOKSTACK, connector_id=bookstack, source_role_id="1", name="Editor", org_id=ORG),
        AppRole(app_name=Connectors.JIRA, connector_id=jira, source_role_id="10", name="Developers", org_id=ORG),
    ]
    await backend.provider.batch_upsert_app_roles(roles)
    user_id = await _seed_user(backend, f"u-{_suffix()}@example.com")
    await backend.provider.batch_create_edges(
        [_grant(USERS, user_id, ROLES, role.id) for role in roles], collection=PERMISSION,
    )

    await backend.provider.delete_edges_between_collections(
        user_id, USERS, PERMISSION, ROLES, to_connector_id=bookstack,
    )

    assert not await backend.edge_exists(PERMISSION, USERS, user_id, ROLES, roles[0].id)
    assert await backend.edge_exists(PERMISSION, USERS, user_id, ROLES, roles[1].id)


# --- STORAGE-RSS-01 -------------------------------------------------------------


async def test_a_rewritten_record_keeps_its_creation_time(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group = _group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)
    group.created_at = 1_000
    await processor.on_new_record_groups([(group, [])])
    first = _record(f"page-{_suffix()}", connector_id=connector_id, group_external=group.external_group_id)
    first.created_at = 1_000
    await processor.on_new_records([(first, [])])

    again = _record(first.external_record_id, connector_id=connector_id, group_external=group.external_group_id)
    again.created_at = 9_000
    await processor.on_new_records([(again, [])])
    regrouped = _group(group.external_group_id, inherit=True, connector_id=connector_id)
    regrouped.created_at = 9_000
    await processor.on_new_record_groups([(regrouped, [])])

    stored = await processor.get_record_by_external_id(connector_id, first.external_record_id)
    assert await backend.created_at(RECORDS, stored.id) == 1_000
    assert await backend.created_at(RECORD_GROUPS, group.id) == 1_000
