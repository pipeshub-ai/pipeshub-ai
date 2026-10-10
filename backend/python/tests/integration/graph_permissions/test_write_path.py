"""Scenarios built by the real write path, asserted in a real database.

Every other module here loads a hand-built fixture. This one drives
``DataSourceEntitiesProcessor`` — the same code a connector sync runs — so the
graph under test is one the write path can actually produce. A hand-built
fixture proves the query is self-consistent; this proves the two halves agree.

Covers the edges the traversal depends on: RecordGroup -> App
inheritance and its removal when a group stops inheriting; record -> parent
*record* inheritance; the App -> group and group -> record hierarchy the
traversal descends, including its deliberate *absence* for a nested record,
which is what stops the walk reaching one while skipping the restrictions
above it; and the re-parenting of survivors on both delete paths.

Every case runs against both backends, because the two providers do not
interpret the same call identically. Arango writes an edge straight through the
document API without checking its endpoints; Neo4j MATCHes both endpoints first
and silently writes nothing when either is missing. Neither raises. The same
asymmetry applies to deletes, where Neo4j resolves node *labels* from the
from/to collection arguments that Arango uses only to build ``_from``/``_to``
handles — so a wrong collection name there is a silent no-op on Neo4j and a
working delete on Arango.
"""

import uuid

import aiohttp
import pytest
from neo4j import AsyncGraphDatabase

from app.config.constants.arangodb import (
    AccessRule,
    CollectionNames,
    Connectors,
    OriginTypes,
)
from app.config.constants.neo4j import (
    collection_to_label,
    edge_collection_to_relationship,
)
from app.models.entities import (
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType

from .processor_harness import build_processor

pytestmark = pytest.mark.integration

ORG = "org-write-path"
TS = 1700000000000


def _suffix() -> str:
    return uuid.uuid4().hex[:8]


class _ArangoBackend:
    name = "arango"

    def __init__(self, provider, settings: dict) -> None:
        self.provider = provider
        self._settings = settings

    async def _aql(self, query: str, bind: dict) -> list:
        auth = aiohttp.BasicAuth(self._settings["username"], self._settings["password"])
        url = f"{self._settings['url'].rstrip('/')}/_db/{self._settings['db']}/_api/cursor"
        async with aiohttp.ClientSession(auth=auth) as session:
            async with session.post(url, json={"query": query, "bindVars": bind}) as resp:
                body = await resp.json()
                assert resp.status in (200, 201), body
                return body["result"]

    async def edge_exists(
        self, edge_collection: str, from_collection: str, from_id: str,
        to_collection: str, to_id: str,
    ) -> bool:
        rows = await self._aql(
            f"RETURN LENGTH(FOR e IN {edge_collection} "
            "FILTER e._from == @from AND e._to == @to LIMIT 1 RETURN 1) > 0",
            {"from": f"{from_collection}/{from_id}", "to": f"{to_collection}/{to_id}"},
        )
        return rows[0]

    async def sweep_tags(self, edge_collection: str, node_ids: list[str]) -> list:
        """The sweep tags on the edges touching these "collection/key" ids; a key
        stored as null counts (FVG-03), so an untagged edge must not carry it."""
        return await self._aql(
            f"FOR e IN {edge_collection} FILTER (e._from IN @ids OR e._to IN @ids) "
            "AND HAS(e, 'pendingSweep') RETURN e.pendingSweep",
            {"ids": node_ids},
        )

    async def add_node_relation(self, from_id: str, to_id: str, props: dict) -> None:
        """A record -> record edge in the hierarchy collection, as legacy data
        left it: the write path stores no link there."""
        await self._aql(
            f"INSERT MERGE(@props, {{_from: @from, _to: @to}}) INTO {CollectionNames.NODE_RELATIONS.value}",
            {"props": props, "from": f"{CollectionNames.RECORDS.value}/{from_id}",
             "to": f"{CollectionNames.RECORDS.value}/{to_id}"},
        )

    async def remove_edge(
        self, edge_collection: str, from_collection: str, from_id: str,
        to_collection: str, to_id: str,
    ) -> None:
        await self._aql(
            f"FOR e IN {edge_collection} FILTER e._from == @from AND e._to == @to REMOVE e IN {edge_collection}",
            {"from": f"{from_collection}/{from_id}", "to": f"{to_collection}/{to_id}"},
        )

    async def access_rule(self, collection: str, key: str) -> list[dict]:
        return await self._aql(
            f"FOR n IN {collection} FILTER n._key == @key "
            "RETURN {accessRule: n.accessRule}",
            {"key": key},
        )

    async def reachable_from_app(self, app_id: str, grantees: list[str]) -> set[str]:
        from .test_aql_parity import _TRAVERSAL
        from .test_qpp_semantics import HIERARCHY_TYPES

        rows = await self._aql(_TRAVERSAL, {
            "seeds": [app_id],
            "seedCollection": CollectionNames.APPS.value,
            "types": HIERARCHY_TYPES,
            "grantees": grantees,
            "allowStrict": True,
            "skipChecks": False,
            "maxDepth": 50,
        })
        return set(rows)


class _Neo4jBackend:
    name = "neo4j"

    def __init__(self, provider, settings: dict) -> None:
        self.provider = provider
        self._settings = settings

    async def _cypher(self, query: str, params: dict) -> list[dict]:
        driver = AsyncGraphDatabase.driver(
            self._settings["uri"],
            auth=(self._settings["username"], self._settings["password"]),
        )
        try:
            async with driver.session(database=self._settings["database"]) as session:
                result = await session.run(query, params)
                return [record.data() async for record in result]
        finally:
            await driver.close()

    async def edge_exists(
        self, edge_collection: str, from_collection: str, from_id: str,
        to_collection: str, to_id: str,
    ) -> bool:
        # Labels and relationship types come from the production maps, so the
        # assertion reads the graph exactly as the provider wrote it.
        rel = edge_collection_to_relationship(edge_collection)
        from_label = collection_to_label(from_collection)
        to_label = collection_to_label(to_collection)
        rows = await self._cypher(
            f"MATCH (:{from_label} {{id: $from}})-[r:{rel}]->(:{to_label} {{id: $to}}) "
            "RETURN count(r) AS n",
            {"from": from_id, "to": to_id},
        )
        return rows[0]["n"] > 0

    async def sweep_tags(self, edge_collection: str, node_ids: list[str]) -> list:
        rel = edge_collection_to_relationship(edge_collection)
        keys = [i.split("/", 1)[1] for i in node_ids]
        rows = await self._cypher(
            f"MATCH (n)-[r:{rel}]-() WHERE n.id IN $keys AND r.pendingSweep IS NOT NULL "
            "RETURN DISTINCT elementId(r) AS id, r.pendingSweep AS tag",
            {"keys": keys},
        )
        return [r["tag"] for r in rows]

    async def add_node_relation(self, from_id: str, to_id: str, props: dict) -> None:
        await self._cypher(
            "MATCH (a:Record {id: $from}), (b:Record {id: $to}) "
            "CREATE (a)-[r:NODE_RELATION]->(b) SET r += $props",
            {"from": from_id, "to": to_id, "props": props},
        )

    async def remove_edge(
        self, edge_collection: str, from_collection: str, from_id: str,
        to_collection: str, to_id: str,
    ) -> None:
        rel = edge_collection_to_relationship(edge_collection)
        await self._cypher(
            f"MATCH (:{collection_to_label(from_collection)} {{id: $from}})-[r:{rel}]->"
            f"(:{collection_to_label(to_collection)} {{id: $to}}) DELETE r",
            {"from": from_id, "to": to_id},
        )

    async def access_rule(self, collection: str, key: str) -> list[dict]:
        label = collection_to_label(collection)
        return await self._cypher(
            f"MATCH (n:{label} {{id: $key}}) "
            "RETURN n.accessRule AS accessRule",
            {"key": key},
        )

    async def reachable_from_app(self, app_id: str, grantees: list[str]) -> set[str]:
        from .test_qpp_semantics import HIERARCHY_TYPES, PER_HOP_RULE

        rows = await self._cypher(
            f"MATCH (root:App {{id: $app}}) "
            f"      ((p)-[r:NODE_RELATION]->(c) WHERE {PER_HOP_RULE})+ (n) "
            "RETURN DISTINCT n.id AS id",
            {"app": app_id, "types": HIERARCHY_TYPES, "grantees": grantees,
             "allowStrict": True, "skipChecks": False},
        )
        return {r["id"] for r in rows}


@pytest.fixture(params=["arango", "neo4j"])
def backend(request, arango_provider, arango_settings, neo4j_provider, neo4j_settings):
    if request.param == "arango":
        return _ArangoBackend(arango_provider, arango_settings)
    return _Neo4jBackend(neo4j_provider, neo4j_settings)


async def _seed_org(backend) -> None:
    """The Organization node every record group belongs to.

    Same reason as the App: Neo4j's batch_create_edges MATCHes both endpoints,
    so without this node it drops the group -> org edge while Arango writes the
    same edge dangling, and the two backends disagree for a reason unrelated to
    the code under test. `orgId` is deliberately absent -- the orgs validator
    does not declare it and runs with additionalProperties false.
    """
    await backend.provider.batch_upsert_nodes(
        [{
            "_key": ORG,
            "accountType": "enterprise",
            "isActive": True,
            "name": "Test Org",
            "createdAtTimestamp": TS,
            "updatedAtTimestamp": TS,
        }],
        collection=CollectionNames.ORGS.value,
    )


async def _seed_app(backend, connector_id: str) -> None:
    """Create the App node connector registration would already have written.

    Without it Neo4j drops every RecordGroup -> App edge (its batch_create_edges
    MATCHes both endpoints) while Arango writes the same edge dangling, so the
    two backends would disagree for a reason unrelated to the code under test.
    `_key` rather than `id`: the strict Arango validator forbids unknown fields,
    and batch_upsert_nodes renames it for Neo4j.
    """
    await _seed_org(backend)
    await backend.provider.batch_upsert_nodes(
        [{
            "_key": connector_id,
            "orgId": ORG,
            "name": "Confluence",
            "type": Connectors.CONFLUENCE.value,
            "appGroup": "Atlassian",
            "scope": "team",
            "isActive": True,
            "createdAtTimestamp": TS,
        }],
        collection=CollectionNames.APPS.value,
    )


async def _seed_user(backend, email: str) -> str:
    """A user node the processor can resolve a grant against.

    `_handle_record_permissions` looks a USER grant up by email and silently
    skips when no user exists, so without this the grant would never become an
    edge and the assertions below would pass for the wrong reason.
    """
    user_id = f"user-{_suffix()}"
    await backend.provider.batch_upsert_nodes(
        [{"_key": user_id, "email": email, "orgId": ORG,
          "isActive": True, "createdAtTimestamp": TS, "updatedAtTimestamp": TS}],
        collection=CollectionNames.USERS.value,
    )
    return user_id


def _group(external_id: str, *, inherit: bool, connector_id: str,
           rule: AccessRule = AccessRule.RESTRICTED) -> RecordGroup:
    return RecordGroup(
        org_id=ORG,
        name=f"Space {external_id}",
        external_group_id=external_id,
        connector_name=Connectors.CONFLUENCE,
        connector_id=connector_id,
        group_type=RecordGroupType.CONFLUENCE_SPACES,
        inherit_permissions=inherit,
        access_rule=rule,
    )


def _record(external_id: str, *, connector_id: str, parent_external: str | None = None,
            group_external: str | None = None, inherit: bool = True,
            rule: AccessRule = AccessRule.STRICT) -> WebpageRecord:
    # CONFLUENCE_PAGE is in RECORD_TYPE_COLLECTION_MAPPING, so both providers
    # call to_arango_record() for the type document — which only the subclasses
    # implement. A bare Record raises AttributeError inside batch_upsert_records.
    return WebpageRecord(
        org_id=ORG,
        record_name=f"Page {external_id}",
        external_record_id=external_id,
        record_type=RecordType.CONFLUENCE_PAGE,
        parent_external_record_id=parent_external,
        parent_record_type=RecordType.CONFLUENCE_PAGE if parent_external else None,
        external_record_group_id=group_external,
        record_group_type=RecordGroupType.CONFLUENCE_SPACES if group_external else None,
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.CONFLUENCE,
        connector_id=connector_id,
        inherit_permissions=inherit,
        access_rule=rule,
    )


async def test_top_level_group_inherits_from_its_app(backend) -> None:
    """The edge the Confluence model depends on, and which did not exist before."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group = _group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)

    await processor.on_new_record_groups([(group, [])])

    assert await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.RECORD_GROUPS.value, group.id,
        CollectionNames.APPS.value, connector_id,
    ), "a top-level group with inherit_permissions=True must inherit from its App"


async def test_group_that_stops_inheriting_loses_the_edge(backend) -> None:
    """The reconcile gap: a stale edge would widen access on re-sync."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    external_id = f"space-{_suffix()}"

    first = _group(external_id, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(first, [])])
    # Without this the test passes whenever the edge never existed — including
    # if the processor stopped reusing the stored group id, which is the only
    # reason the assertion below addresses the same vertex twice.
    assert await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.RECORD_GROUPS.value, first.id,
        CollectionNames.APPS.value, connector_id,
    ), "control: the group must inherit from its App before inheritance is turned off"

    stopped = _group(external_id, inherit=False, connector_id=connector_id)
    await processor.on_new_record_groups([(stopped, [])])

    assert not await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.RECORD_GROUPS.value, stopped.id,
        CollectionNames.APPS.value, connector_id,
    ), "turning inheritance off must remove the edge, not leave it behind"


async def test_access_rule_persists_through_the_write_path(backend) -> None:
    """accessRule survives the processor, not just the model."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group = _group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)

    await processor.on_new_record_groups([(group, [])])

    stored = await backend.access_rule(CollectionNames.RECORD_GROUPS.value, group.id)
    assert stored == [{"accessRule": "RESTRICTED"}], stored


async def test_nested_record_inherits_from_its_parent_record(backend) -> None:
    """Inheritance follows the hierarchy, not a shortcut to the record group.

    Without this a restriction part way down a page tree cannot take effect,
    because every descendant inherits straight past it from the group.
    """
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    parent_external = f"page-{_suffix()}"
    child_external = f"page-{_suffix()}"

    parent = _record(parent_external, connector_id=connector_id)
    await processor.on_new_records([(parent, [])])

    child = _record(child_external, connector_id=connector_id,
                    parent_external=parent_external)
    await processor.on_new_records([(child, [])])

    assert await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.RECORDS.value, child.id,
        CollectionNames.RECORDS.value, parent.id,
    ), "a nested record must inherit from the record directly above it"


async def test_top_level_group_hangs_off_its_app(backend) -> None:
    """The traversal descends NODE_RELATION from the App; BELONGS_TO runs the
    other way and cannot be walked downwards."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group = _group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)

    await processor.on_new_record_groups([(group, [])])

    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.APPS.value, connector_id,
        CollectionNames.RECORD_GROUPS.value, group.id,
    ), "a top-level group must be reachable downwards from its App"
    # The upward edges the read side never sees, because the hand-built fixture
    # omits both. A collection was broken for exactly this reason: the query
    # required an edge no writer emits, and nothing compared the two.
    assert await backend.edge_exists(
        CollectionNames.BELONGS_TO.value,
        CollectionNames.RECORD_GROUPS.value, group.id,
        CollectionNames.APPS.value, connector_id,
    ), "a top-level group must also belong to its App"
    assert await backend.edge_exists(
        CollectionNames.BELONGS_TO.value,
        CollectionNames.RECORD_GROUPS.value, group.id,
        CollectionNames.ORGS.value, ORG,
    ), "every record group belongs to its org"


async def test_top_level_record_hangs_off_its_record_group(backend) -> None:
    """Without this edge the root pass cannot reach a record at all."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group_external = f"space-{_suffix()}"
    group = _group(group_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(group, [])])

    record = _record(f"page-{_suffix()}", connector_id=connector_id,
                     group_external=group_external)
    await processor.on_new_records([(record, [])])

    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, group.id,
        CollectionNames.RECORDS.value, record.id,
    ), "a record with no parent record must hang off its record group"
    # Placement reads this edge to find a chain-top's own group, so a record
    # that hangs off a group without belonging to it would list correctly and
    # then be placed under the App.
    assert await backend.edge_exists(
        CollectionNames.BELONGS_TO.value,
        CollectionNames.RECORDS.value, record.id,
        CollectionNames.RECORD_GROUPS.value, group.id,
    ), "a record must belong to the record group it hangs off"


async def test_nested_record_gets_no_edge_from_the_group(backend) -> None:
    """The bypass guard: a hierarchy edge straight from the group would
    let the traversal reach a nested record while skipping every restriction
    between them, which is the whole reason inheritance follows the parent."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group_external = f"space-{_suffix()}"
    group = _group(group_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(group, [])])

    parent_external = f"page-{_suffix()}"
    parent = _record(parent_external, connector_id=connector_id,
                     group_external=group_external)
    await processor.on_new_records([(parent, [])])

    child = _record(f"page-{_suffix()}", connector_id=connector_id,
                    parent_external=parent_external, group_external=group_external)
    await processor.on_new_records([(child, [])])

    assert not await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, group.id,
        CollectionNames.RECORDS.value, child.id,
    ), "a nested record must be reached through its parent, not from the group"


async def _tree(backend, processor):
    """A group holding a parent page with one child page under it."""
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group_external = f"space-{_suffix()}"
    group = _group(group_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(group, [])])

    parent_external = f"page-{_suffix()}"
    parent = _record(parent_external, connector_id=connector_id,
                     group_external=group_external)
    await processor.on_new_records([(parent, [])])

    child = _record(f"page-{_suffix()}", connector_id=connector_id,
                    parent_external=parent_external, group_external=group_external)
    await processor.on_new_records([(child, [])])
    return connector_id, group, parent, child


async def _assert_reparented(backend, group, child) -> None:
    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, group.id,
        CollectionNames.RECORDS.value, child.id,
    ), "a survivor must hang off its record group once its parent is gone"
    assert await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.RECORDS.value, child.id,
        CollectionNames.RECORD_GROUPS.value, group.id,
    ), "a survivor with no inheritance edge is invisible without a direct grant"


async def test_cascade_delete_reparents_the_survivor(backend) -> None:
    """The edge sweep takes the survivor's only inheritance edge with it,
    because a nested record inherits from its parent record."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)

    await processor.on_records_deleted_cascade(
        [parent.id], connector_id, cascade_children=False,
    )

    await _assert_reparented(backend, group, child)


async def test_single_delete_reparents_the_survivor(backend) -> None:
    """The same gap on the per-record path connectors hit most often."""
    processor, _ = build_processor(backend.provider, ORG)
    _connector_id, group, parent, child = await _tree(backend, processor)

    await processor.on_record_deleted(parent.id)

    await _assert_reparented(backend, group, child)
    assert not await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORDS.value, parent.id,
        CollectionNames.RECORDS.value, child.id,
    ), "the edge from the deleted parent must not be left dangling"


async def test_a_record_deleted_by_external_id_takes_its_attachment_and_reparents_its_child(backend) -> None:
    """The path Outlook deletes a mail through: the attachment goes with the
    record instead of being orphaned, and the child page is re-pointed."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    attachment = FileRecord(
        org_id=ORG,
        record_name="spec.pdf",
        external_record_id=f"att-{_suffix()}",
        record_type=RecordType.FILE,
        parent_external_record_id=parent.external_record_id,
        parent_record_type=RecordType.CONFLUENCE_PAGE,
        external_record_group_id=group.external_group_id,
        record_group_type=RecordGroupType.CONFLUENCE_SPACES,
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.CONFLUENCE,
        connector_id=connector_id,
        is_file=True,
        extension="pdf",
    )
    await processor.on_new_records([(attachment, [])])

    await processor.delete_record_by_external_id(connector_id, parent.external_record_id, "user-x")

    assert await backend.access_rule(CollectionNames.RECORDS.value, parent.id) == []
    assert await backend.access_rule(CollectionNames.RECORDS.value, attachment.id) == []
    await _assert_reparented(backend, group, child)


async def _tree_with_own_acl_child(backend, processor):
    """As `_tree`, but the child keeps its own ACL: it does not inherit."""
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group_external = f"space-{_suffix()}"
    group = _group(group_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(group, [])])
    parent_external = f"page-{_suffix()}"
    parent = _record(parent_external, connector_id=connector_id, group_external=group_external)
    await processor.on_new_records([(parent, [])])
    child = _record(f"page-{_suffix()}", connector_id=connector_id, inherit=False,
                    parent_external=parent_external, group_external=group_external)
    await processor.on_new_records([(child, [])])
    return connector_id, group, parent, child


async def _assert_reparented_without_inheriting(backend, group, child) -> None:
    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, group.id,
        CollectionNames.RECORDS.value, child.id,
    ), "the survivor must still hang off its record group"
    assert not await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.RECORDS.value, child.id,
        CollectionNames.RECORD_GROUPS.value, group.id,
    ), "a survivor with its own ACL must not gain the group's readers"


async def test_single_delete_does_not_make_an_own_acl_survivor_inherit(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    _connector_id, group, parent, child = await _tree_with_own_acl_child(backend, processor)

    await processor.on_record_deleted(parent.id)

    await _assert_reparented_without_inheriting(backend, group, child)


async def test_cascade_delete_does_not_make_an_own_acl_survivor_inherit(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree_with_own_acl_child(backend, processor)

    await processor.on_records_deleted_cascade([parent.id], connector_id, cascade_children=False)

    await _assert_reparented_without_inheriting(backend, group, child)


async def test_a_restriction_reaches_the_node_without_a_content_edit(backend) -> None:
    """The revision is unchanged, so the record is not re-upserted; the rule
    alone must still follow the source."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group_external = f"space-{_suffix()}"
    await processor.on_new_record_groups(
        [(_group(group_external, inherit=True, connector_id=connector_id), [])],
    )
    external_id = f"page-{_suffix()}"
    page = _record(external_id, connector_id=connector_id, group_external=group_external,
                   rule=AccessRule.OPEN)
    page.external_revision_id = "rev-1"
    await processor.on_new_records([(page, [])])

    again = _record(external_id, connector_id=connector_id, group_external=group_external,
                    rule=AccessRule.RESTRICTED)
    again.external_revision_id = "rev-1"
    await processor.on_new_records([(again, [])])

    stored = await backend.access_rule(CollectionNames.RECORDS.value, page.id)
    assert stored == [{"accessRule": "RESTRICTED"}], stored


async def test_a_placeholder_parent_hides_its_children_until_the_parent_syncs(backend) -> None:
    """A child synced before its parent inherits through a stand-in
    whose permissions nobody has read; the stand-in is RESTRICTED, so the child
    stays hidden until the real parent arrives with its own rule."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    viewer = await _seed_user(backend, f"u-{_suffix()}@example.com")
    group_external = f"space-{_suffix()}"
    await processor.on_new_record_groups(
        [(_group(group_external, inherit=True, connector_id=connector_id, rule=AccessRule.STRICT), [])],
    )

    parent_external = f"page-{_suffix()}"
    child = _record(f"page-{_suffix()}", connector_id=connector_id,
                    parent_external=parent_external, group_external=group_external,
                    rule=AccessRule.OPEN)
    await processor.on_new_records([(child, [])])

    stub = await backend.provider.get_record_by_external_id(connector_id, parent_external)
    assert stub is not None and stub.is_placeholder
    assert await backend.access_rule(CollectionNames.RECORDS.value, stub.id) == [{"accessRule": "RESTRICTED"}]
    assert child.id not in await backend.reachable_from_app(connector_id, [viewer])

    real_parent = _record(parent_external, connector_id=connector_id,
                          group_external=group_external, rule=AccessRule.OPEN)
    real_parent.external_revision_id = "rev-real"
    await processor.on_new_records([(real_parent, [])])

    assert await backend.access_rule(CollectionNames.RECORDS.value, stub.id) == [{"accessRule": "OPEN"}]
    assert child.id in await backend.reachable_from_app(connector_id, [viewer])


async def test_a_restricted_page_needs_a_grant_as_well_as_inheritance(backend) -> None:
    """RESTRICTED end to end, on a graph the write path produced.

    The space is strict but *not* restricted, so both users clear the group hop
    and the assertions isolate the record-level rule. The two pages differ only
    in the restriction flag and the grant: both inherit from the same space.
    """
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)

    granted_email = f"u-{_suffix()}@example.com"
    granted_user = await _seed_user(backend, granted_email)
    other_user = await _seed_user(backend, f"v-{_suffix()}@example.com")

    group_external = f"space-{_suffix()}"
    group = _group(group_external, inherit=True, connector_id=connector_id,
                   rule=AccessRule.STRICT)
    await processor.on_new_record_groups([(group, [])])

    restricted = _record(f"page-{_suffix()}", connector_id=connector_id,
                         group_external=group_external, rule=AccessRule.RESTRICTED)
    await processor.on_new_records([(restricted, [Permission(
        email=granted_email, type=PermissionType.READ, entity_type=EntityType.USER,
    )])])

    open_page = _record(f"page-{_suffix()}", connector_id=connector_id,
                        group_external=group_external)
    await processor.on_new_records([(open_page, [])])

    seen_by_granted = await backend.reachable_from_app(connector_id, [granted_user])
    seen_by_other = await backend.reachable_from_app(connector_id, [other_user])

    # Guards the guard: if the open page were invisible to both, the assertion
    # below would hold for the wrong reason.
    assert open_page.id in seen_by_granted, "the granted user must see the open page"
    assert open_page.id in seen_by_other, (
        "an unrestricted page must be reachable through inheritance alone"
    )
    assert restricted.id in seen_by_granted, (
        "the granted user must see the restricted page"
    )
    assert restricted.id not in seen_by_other, (
        "inheritance alone must never reveal a restricted page"
    )


async def test_a_record_that_also_takes_its_group_is_read_past_a_restricted_parent(backend) -> None:
    """JC-02: a Jira story under a secured epic is read by everyone who can
    browse the project, and still listed under the epic for the epic's holders.

    The epic is RESTRICTED and granted to one user. The story sets
    ``inherit_permissions_from_group``; a control story under the same epic
    does not, and stays behind it.
    """
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    holder_email = f"h-{_suffix()}@example.com"
    holder = await _seed_user(backend, holder_email)
    browser = await _seed_user(backend, f"b-{_suffix()}@example.com")

    group_external = f"proj-{_suffix()}"
    group = _group(group_external, inherit=True, connector_id=connector_id, rule=AccessRule.STRICT)
    await processor.on_new_record_groups([(group, [])])

    epic_external = f"epic-{_suffix()}"
    epic = _record(epic_external, connector_id=connector_id,
                   group_external=group_external, rule=AccessRule.RESTRICTED)
    await processor.on_new_records([(epic, [Permission(
        email=holder_email, type=PermissionType.READ, entity_type=EntityType.USER,
    )])])

    story = _record(f"story-{_suffix()}", connector_id=connector_id,
                    parent_external=epic_external, group_external=group_external)
    story.inherit_permissions_from_group = True
    control = _record(f"story-{_suffix()}", connector_id=connector_id,
                      parent_external=epic_external, group_external=group_external)
    await processor.on_new_records([(story, []), (control, [])])

    records, groups = CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value
    assert await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, groups, group.id, records, story.id)
    assert await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, records, epic.id, records, story.id)
    assert await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, records, story.id, groups, group.id)
    assert await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, records, story.id, records, epic.id)

    seen_by_holder = await backend.reachable_from_app(connector_id, [holder])
    seen_by_browser = await backend.reachable_from_app(connector_id, [browser])
    assert epic.id in seen_by_holder and epic.id not in seen_by_browser
    assert control.id in seen_by_holder, "control: a story that inherits only the epic follows it"
    assert control.id not in seen_by_browser, "control: the epic's level hides a story that inherits only it"
    assert story.id in seen_by_browser, "the project's audience reads a story whose epic hides it"
    assert story.id in seen_by_holder


async def test_a_record_that_gains_a_parent_loses_its_group_edge(backend) -> None:
    """A stale group edge, by the route the negative test above cannot reach.

    That test creates the record already nested, so the group edge is never
    written. Here it is written correctly at root and the record is only then
    re-parented — and nothing removed it, leaving the traversal able to reach
    the record from the group while skipping every restriction in between.
    """
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group_external = f"space-{_suffix()}"
    group = _group(group_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(group, [])])

    parent_external = f"page-{_suffix()}"
    await processor.on_new_records([(
        _record(parent_external, connector_id=connector_id,
                group_external=group_external), [])])

    child_external = f"page-{_suffix()}"
    at_root = _record(child_external, connector_id=connector_id,
                      group_external=group_external)
    await processor.on_new_records([(at_root, [])])
    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, group.id,
        CollectionNames.RECORDS.value, at_root.id,
    ), "control: a root record hangs off its group before it is re-parented"

    nested = _record(child_external, connector_id=connector_id,
                     parent_external=parent_external, group_external=group_external)
    await processor.on_new_records([(nested, [])])

    assert not await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, group.id,
        CollectionNames.RECORDS.value, at_root.id,
    ), "a record that gains a parent must stop hanging off its group"


async def test_a_record_re_synced_at_root_keeps_its_group_edge(backend) -> None:
    """The mirror image, and why the stale-parent delete is scoped to records.

    An unscoped delete matched every incoming PARENT_CHILD edge, including the
    group's — written moments earlier in the same sync — leaving the record
    with no hierarchy parent at all and unreachable from the App.
    """
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group_external = f"space-{_suffix()}"
    group = _group(group_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(group, [])])

    parent_external = f"page-{_suffix()}"
    parent = _record(parent_external, connector_id=connector_id,
                     group_external=group_external)
    await processor.on_new_records([(parent, [])])

    child_external = f"page-{_suffix()}"
    nested = _record(child_external, connector_id=connector_id,
                     parent_external=parent_external, group_external=group_external)
    await processor.on_new_records([(nested, [])])

    promoted = _record(child_external, connector_id=connector_id,
                       group_external=group_external)
    await processor.on_new_records([(promoted, [])])

    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, group.id,
        CollectionNames.RECORDS.value, nested.id,
    ), "a record re-synced at root must hang off its group"
    assert not await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORDS.value, parent.id,
        CollectionNames.RECORDS.value, nested.id,
    ), "and must lose the edge from the parent it no longer has"


async def test_a_re_parented_record_stops_inheriting_from_its_old_parent(backend) -> None:
    """A record inherits from its parent record, so a stale inheritance edge
    keeps granting access through a parent it has left."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group_external = f"space-{_suffix()}"
    group = _group(group_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(group, [])])

    first_external = f"page-{_suffix()}"
    first = _record(first_external, connector_id=connector_id,
                    group_external=group_external)
    second_external = f"page-{_suffix()}"
    second = _record(second_external, connector_id=connector_id,
                     group_external=group_external)
    await processor.on_new_records([(first, []), (second, [])])

    child_external = f"page-{_suffix()}"
    child = _record(child_external, connector_id=connector_id,
                    parent_external=first_external, group_external=group_external)
    await processor.on_new_records([(child, [])])
    assert await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.RECORDS.value, child.id,
        CollectionNames.RECORDS.value, first.id,
    ), "control: the record must inherit from its first parent"

    moved = _record(child_external, connector_id=connector_id,
                    parent_external=second_external, group_external=group_external)
    await processor.on_new_records([(moved, [])])

    assert await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.RECORDS.value, child.id,
        CollectionNames.RECORDS.value, second.id,
    ), "it must inherit from the parent it moved to"
    assert not await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.RECORDS.value, child.id,
        CollectionNames.RECORDS.value, first.id,
    ), "and must stop inheriting from the one it left"


async def test_a_group_that_gains_a_parent_stops_hanging_off_the_app(backend) -> None:
    """The App edges belong to a top-level group only.

    Left behind, they let the traversal reach the group straight from the App,
    never passing the parent group's own checks.
    """
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)

    parent_external = f"space-{_suffix()}"
    parent_group = _group(parent_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(parent_group, [])])

    child_external = f"space-{_suffix()}"
    top_level = _group(child_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(top_level, [])])
    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.APPS.value, connector_id,
        CollectionNames.RECORD_GROUPS.value, top_level.id,
    ), "control: a top-level group hangs off its App"

    nested = _group(child_external, inherit=True, connector_id=connector_id)
    nested.parent_external_group_id = parent_external
    await processor.on_new_record_groups([(nested, [])])

    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, parent_group.id,
        CollectionNames.RECORD_GROUPS.value, top_level.id,
    ), "the group must hang off its new parent"
    assert not await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.APPS.value, connector_id,
        CollectionNames.RECORD_GROUPS.value, top_level.id,
    ), "and must no longer hang off the App directly"
    assert not await backend.edge_exists(
        CollectionNames.INHERIT_PERMISSIONS.value,
        CollectionNames.RECORD_GROUPS.value, top_level.id,
        CollectionNames.APPS.value, connector_id,
    ), "nor inherit from the App over its parent group's head"


async def _edges_to_parent(backend, child_id: str, parent_id: str) -> list[bool]:
    """[membership, hierarchy, inheritance] between a group and a parent group."""
    groups = CollectionNames.RECORD_GROUPS.value
    return [
        await backend.edge_exists(CollectionNames.BELONGS_TO.value, groups, child_id, groups, parent_id),
        await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, groups, parent_id, groups, child_id),
        await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, groups, child_id, groups, parent_id),
    ]


async def test_a_group_that_moves_leaves_its_old_parent(backend) -> None:
    """Left behind, the old edges list the group under both parents and let it
    inherit from both: the old parent's readers keep seeing it."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    old_external, new_external = f"space-{_suffix()}", f"space-{_suffix()}"
    old_parent = _group(old_external, inherit=True, connector_id=connector_id)
    new_parent = _group(new_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(old_parent, []), (new_parent, [])])

    child_external = f"space-{_suffix()}"
    child = _group(child_external, inherit=True, connector_id=connector_id)
    child.parent_external_group_id = old_external
    await processor.on_new_record_groups([(child, [])])
    assert await _edges_to_parent(backend, child.id, old_parent.id) == [True, True, True], "control"

    moved = _group(child_external, inherit=True, connector_id=connector_id)
    moved.parent_external_group_id = new_external
    await processor.on_new_record_groups([(moved, [])])

    assert await _edges_to_parent(backend, child.id, old_parent.id) == [False, False, False]
    assert await _edges_to_parent(backend, child.id, new_parent.id) == [True, True, True]


async def test_a_group_that_loses_its_parent_hangs_off_the_app_again(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    parent_external = f"space-{_suffix()}"
    parent = _group(parent_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(parent, [])])
    child_external = f"space-{_suffix()}"
    child = _group(child_external, inherit=True, connector_id=connector_id)
    child.parent_external_group_id = parent_external
    await processor.on_new_record_groups([(child, [])])

    top_level = _group(child_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(top_level, [])])

    assert await _edges_to_parent(backend, child.id, parent.id) == [False, False, False]
    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.APPS.value, connector_id,
        CollectionNames.RECORD_GROUPS.value, child.id,
    ), "a top-level group hangs off its App"


async def test_deleting_a_record_leaves_no_shared_with_me_edge(backend) -> None:
    """Shared with Me gives a record a second hierarchy parent, and that id is
    never stored on the record document.

    So a delete that cleans only the record group's edge — the one it *can*
    find, from `recordGroupId` — leaves the Shared with Me edge pointing at a
    vertex that no longer exists. Arango removes the document without touching
    its edges, so nothing downstream catches it and a traversal from the inbox
    crosses into a null vertex.
    """
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)

    drive_external = f"space-{_suffix()}"
    drive = _group(drive_external, inherit=True, connector_id=connector_id)
    inbox_external = f"space-{_suffix()}"
    inbox = _group(inbox_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(drive, []), (inbox, [])])

    record = _record(f"page-{_suffix()}", connector_id=connector_id,
                     group_external=drive_external)
    record.shared_with_me_record_group_ids = [inbox_external]
    await processor.on_new_records([(record, [])])

    assert await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, inbox.id,
        CollectionNames.RECORDS.value, record.id,
    ), "control: Shared with Me must be a real second hierarchy parent"

    await processor.on_record_deleted(record.id)

    assert not await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, inbox.id,
        CollectionNames.RECORDS.value, record.id,
    ), "the second hierarchy edge must not outlive the record it pointed at"
    assert not await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value,
        CollectionNames.RECORD_GROUPS.value, drive.id,
        CollectionNames.RECORDS.value, record.id,
    ), "nor the record group's own edge"


async def test_unknown_group_grants_are_kept_and_empty_ones_removed(backend) -> None:
    """A connector that cannot read a group's grants sends None, and the
    stored grants stay; one that reads "no grants" sends [], and they go."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    email = f"member-{_suffix()}@example.com"
    user_id = await _seed_user(backend, email)
    group = _group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)
    grant = Permission(email=email, type=PermissionType.READ, entity_type=EntityType.USER)
    await processor.on_new_record_groups([(group, [grant])])

    async def granted() -> bool:
        return await backend.edge_exists(
            CollectionNames.PERMISSION.value,
            CollectionNames.USERS.value, user_id,
            CollectionNames.RECORD_GROUPS.value, group.id,
        )

    assert await granted()
    await processor.on_new_record_groups([(group, None)])
    assert await granted(), "an unknown grant list must not wipe the stored grants"
    await processor.on_new_record_groups([(group, [])])
    assert not await granted(), "an empty grant list removes the stored grants"


async def test_a_parent_given_by_internal_id_gets_every_parent_edge(backend) -> None:
    """SharePoint names a drive's site by internal id. Every parent edge is
    written, with or without grants, not only the membership."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    site = _group(f"site-{_suffix()}", inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(site, [])])
    drive = _group(f"drive-{_suffix()}", inherit=True, connector_id=connector_id)
    drive.parent_record_group_id = site.id

    await processor.on_new_record_groups([(drive, [])])

    groups = CollectionNames.RECORD_GROUPS.value
    assert await backend.edge_exists(CollectionNames.BELONGS_TO.value, groups, drive.id, groups, site.id)
    assert await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, groups, site.id, groups, drive.id)
    assert await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, groups, drive.id, groups, site.id)
    assert not await backend.edge_exists(
        CollectionNames.NODE_RELATIONS.value, CollectionNames.APPS.value, connector_id, groups, drive.id,
    ), "a group with a parent does not hang off the App"


async def test_a_deleted_group_takes_its_child_groups_and_their_records(backend) -> None:
    """Nothing is left pointing at the deleted group."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    nested = _group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)
    nested.parent_external_group_id = group.external_group_id
    await processor.on_new_record_groups([(nested, [])])
    nested_page = _record(f"page-{_suffix()}", connector_id=connector_id,
                          group_external=nested.external_group_id)
    await processor.on_new_records([(nested_page, [])])
    other_group = _group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(other_group, [])])
    bystander = _record(f"page-{_suffix()}", connector_id=connector_id,
                        group_external=other_group.external_group_id)
    await processor.on_new_records([(bystander, [])])

    assert await processor.on_record_group_deleted(group.external_group_id, connector_id)

    records, groups = CollectionNames.RECORDS.value, CollectionNames.RECORD_GROUPS.value
    for label, record in (("parent", parent), ("child", child), ("nested page", nested_page)):
        assert await backend.access_rule(records, record.id) == [], label
    for gone in (group, nested):
        assert await backend.access_rule(groups, gone.id) == [], gone.name
    assert await backend.access_rule(records, bystander.id) != []
    assert await backend.access_rule(groups, other_group.id) != []


def _kb_item(external_id: str, kb_id: str, *, folder: bool = False, parent: str | None = None) -> FileRecord:
    return FileRecord(
        org_id=ORG,
        record_name=f"Item {external_id}",
        external_record_id=external_id,
        record_type=RecordType.FILE,
        parent_external_record_id=parent,
        version=1,
        origin=OriginTypes.UPLOAD,
        connector_name=Connectors.KNOWLEDGE_BASE,
        connector_id=kb_id,
        mime_type="text/directory" if folder else "application/pdf",
        is_file=not folder,
        extension=None if folder else "pdf",
        inherit_permissions=True,
    )


async def test_an_item_moved_from_the_collection_root_into_a_folder_leaves_the_root(backend) -> None:
    """An item that kept its App edge would list at the root and in the folder."""
    processor, _ = build_processor(backend.provider, ORG)
    kb_id = f"kb-{_suffix()}"
    await _seed_org(backend)
    await backend.provider.batch_upsert_nodes(
        [{"_key": kb_id, "orgId": ORG, "name": "Collection", "type": Connectors.KNOWLEDGE_BASE.value,
          "appGroup": "Local Storage", "scope": "personal", "isActive": True, "createdAtTimestamp": TS}],
        collection=CollectionNames.APPS.value,
    )
    folder = _kb_item(f"folder-{_suffix()}", kb_id, folder=True)
    item = _kb_item(f"file-{_suffix()}", kb_id)
    await processor.on_new_records([(folder, []), (item, [])])

    apps, records, relations = CollectionNames.APPS.value, CollectionNames.RECORDS.value, CollectionNames.NODE_RELATIONS.value
    assert await backend.edge_exists(relations, apps, kb_id, records, item.id)

    moved = _kb_item(item.external_record_id, kb_id, parent=folder.id)
    await processor.on_records_moved([(item.external_record_id, moved, [])])

    assert await backend.edge_exists(relations, records, folder.id, records, item.id)
    assert not await backend.edge_exists(relations, apps, kb_id, records, item.id), \
        "the item must leave the collection root"


async def test_a_share_that_arrives_by_permission_update_gets_its_hierarchy_edge(backend) -> None:
    """A record already in its drive is shared with another user later: the
    permission-update path must add the Shared with Me hierarchy edge as well as
    the membership, or the record never appears under that user's Shared with Me."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    drive_external, inbox_external = f"space-{_suffix()}", f"space-{_suffix()}"
    drive = _group(drive_external, inherit=True, connector_id=connector_id)
    inbox = _group(inbox_external, inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(drive, []), (inbox, [])])
    record = _record(f"page-{_suffix()}", connector_id=connector_id, group_external=drive_external)
    await processor.on_new_records([(record, [])])

    shared = _record(record.external_record_id, connector_id=connector_id, group_external=drive_external)
    shared.id = record.id
    shared.shared_with_me_record_group_ids = [inbox_external]
    await processor.on_updated_record_permissions(shared, [])

    groups, records = CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value
    assert await backend.edge_exists(CollectionNames.BELONGS_TO.value, records, record.id, groups, inbox.id)
    assert await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, groups, inbox.id, records, record.id)


async def test_a_group_created_from_a_record_is_placed_under_its_app_without_inheriting(backend) -> None:
    """A group the connector never announced, created because a record
    names it, is reachable from its App but opens to nobody through it. Once
    announced under a parent, it leaves the App entirely."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group_external = f"space-{_suffix()}"
    record = _record(f"page-{_suffix()}", connector_id=connector_id, group_external=group_external)
    await processor.on_new_records([(record, [])])
    group = await backend.provider.get_record_group_by_external_id(connector_id, group_external)
    assert group is not None and group.org_id == ORG

    groups, apps = CollectionNames.RECORD_GROUPS.value, CollectionNames.APPS.value
    assert await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, apps, connector_id, groups, group.id)
    assert await backend.edge_exists(CollectionNames.BELONGS_TO.value, groups, group.id, apps, connector_id)
    assert await backend.edge_exists(CollectionNames.BELONGS_TO.value, groups, group.id, CollectionNames.ORGS.value, ORG)
    assert not await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, groups, group.id, apps, connector_id)

    parent = _group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(parent, [])])
    announced = _group(group_external, inherit=True, connector_id=connector_id)
    announced.parent_external_group_id = parent.external_group_id
    await processor.on_new_record_groups([(announced, [])])

    assert not await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, apps, connector_id, groups, group.id)
    assert not await backend.edge_exists(CollectionNames.BELONGS_TO.value, groups, group.id, apps, connector_id)
    assert await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, groups, parent.id, groups, group.id)


async def test_a_placeholder_parent_group_hangs_off_the_app_until_the_real_one_syncs(backend) -> None:
    """The stub for a parent group not synced yet is placed under the App,
    closed and without inheritance, and the real group replaces it in place."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    parent_external = f"space-{_suffix()}"
    child = _group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)
    child.parent_external_group_id = parent_external
    await processor.on_new_record_groups([(child, [])])

    stub = await backend.provider.get_record_group_by_external_id(connector_id, parent_external)
    groups, apps = CollectionNames.RECORD_GROUPS.value, CollectionNames.APPS.value
    assert stub is not None and stub.org_id == ORG
    assert await backend.access_rule(groups, stub.id) == [{"accessRule": AccessRule.RESTRICTED.value}]
    assert await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, apps, connector_id, groups, stub.id)
    assert await backend.edge_exists(CollectionNames.BELONGS_TO.value, groups, stub.id, apps, connector_id)
    assert not await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, groups, stub.id, apps, connector_id)
    assert await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, groups, stub.id, groups, child.id)

    real = _group(parent_external, inherit=True, connector_id=connector_id, rule=AccessRule.OPEN)
    await processor.on_new_record_groups([(real, [])])

    synced = await backend.provider.get_record_group_by_external_id(connector_id, parent_external)
    assert synced.id == stub.id
    assert await backend.access_rule(groups, stub.id) == [{"accessRule": AccessRule.OPEN.value}]
    assert await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, groups, stub.id, apps, connector_id)


async def test_a_full_sync_wipes_its_own_connectors_sync_edges_only(backend) -> None:
    """The sync edges of every node of the connector go, the App's
    included; another connector's stay."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    other_id, other_group, other_parent, _ = await _tree(backend, processor)

    deleted, ok = await backend.provider.delete_connector_sync_edges(connector_id)

    assert ok and deleted > 0
    groups, records, apps = CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value, CollectionNames.APPS.value
    relations = CollectionNames.NODE_RELATIONS.value
    assert not await backend.edge_exists(relations, apps, connector_id, groups, group.id)
    assert not await backend.edge_exists(relations, groups, group.id, records, parent.id)
    assert not await backend.edge_exists(relations, records, parent.id, records, child.id)
    assert not await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, records, child.id, records, parent.id)
    assert await backend.edge_exists(relations, apps, other_id, groups, other_group.id)
    assert await backend.edge_exists(relations, groups, other_group.id, records, other_parent.id)


SWEEP_GENERATION = 1_760_000_000_000


async def test_a_full_sync_that_fails_keeps_every_edge(backend) -> None:
    """FS-01: tagging is all a full sync does to the stored edges before it runs, so
    one that fails leaves the connector as readable as it was."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)

    marked, ok = await backend.provider.mark_connector_sync_edges(connector_id, SWEEP_GENERATION)

    assert ok and marked > 0
    groups, records, apps = CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value, CollectionNames.APPS.value
    relations = CollectionNames.NODE_RELATIONS.value
    assert await backend.edge_exists(relations, apps, connector_id, groups, group.id)
    assert await backend.edge_exists(relations, groups, group.id, records, parent.id)
    assert await backend.edge_exists(relations, records, parent.id, records, child.id)
    assert await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, records, child.id, records, parent.id)


async def test_a_successful_full_sync_sweeps_only_what_it_did_not_write_again(backend) -> None:
    """The child is gone at the source: the sync writes the group and the parent again,
    and the sweep takes the child's edges. Another connector's tagged edges stay."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    other_id, other_group, other_parent, _ = await _tree(backend, processor)
    await backend.provider.mark_connector_sync_edges(other_id, SWEEP_GENERATION)
    _, ok = await backend.provider.mark_connector_sync_edges(connector_id, SWEEP_GENERATION)
    assert ok

    await processor.on_new_record_groups([(_group(group.external_group_id, inherit=True, connector_id=connector_id), [])])
    await processor.on_new_records([(
        _record(parent.external_record_id, connector_id=connector_id, group_external=group.external_group_id), [],
    )])
    deleted, ok = await backend.provider.sweep_connector_sync_edges(connector_id, SWEEP_GENERATION)

    assert ok and deleted > 0
    groups, records, apps = CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value, CollectionNames.APPS.value
    relations = CollectionNames.NODE_RELATIONS.value
    assert await backend.edge_exists(relations, apps, connector_id, groups, group.id)
    assert await backend.edge_exists(relations, groups, group.id, records, parent.id)
    assert await backend.edge_exists(CollectionNames.BELONGS_TO.value, records, parent.id, groups, group.id)
    assert not await backend.edge_exists(relations, records, parent.id, records, child.id)
    assert not await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, records, child.id, records, parent.id)
    assert await backend.edge_exists(relations, apps, other_id, groups, other_group.id)
    assert await backend.edge_exists(relations, groups, other_group.id, records, other_parent.id)


async def test_a_full_sync_that_wrote_nothing_sweeps_nothing(backend) -> None:
    """A connector that answers an outage with an empty listing still reports success;
    with none of its edges written again, the sweep must not take them all."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    await backend.provider.mark_connector_sync_edges(connector_id, SWEEP_GENERATION)

    deleted, ok = await backend.provider.sweep_connector_sync_edges(connector_id, SWEEP_GENERATION)

    assert (deleted, ok) == (0, False)
    groups, records = CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value
    assert await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, groups, group.id, records, parent.id)
    assert await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, records, child.id, records, parent.id)


async def test_a_permission_only_update_in_a_full_sync_keeps_structure_and_grants(backend) -> None:
    """R1-01: Dropbox, Drive and others send an unchanged record only through
    on_updated_record_permissions. Its tagged BELONGS_TO read as present, the
    structural restore was skipped, and the sweep took group membership, the
    page -> sub-page edge and the group -> page edge."""
    from app.services.graph_db.common.sync_sweep import full_sync_running

    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    granted_email = f"u-{_suffix()}@example.com"
    granted = await _seed_user(backend, granted_email)
    other = await _seed_user(backend, f"v-{_suffix()}@example.com")
    grant = [Permission(email=granted_email, type=PermissionType.READ, entity_type=EntityType.USER)]

    group_external, parent_external = f"space-{_suffix()}", f"page-{_suffix()}"
    group = _group(group_external, inherit=True, connector_id=connector_id, rule=AccessRule.STRICT)
    await processor.on_new_record_groups([(group, [])])
    parent = _record(parent_external, connector_id=connector_id, group_external=group_external)
    await processor.on_new_records([(parent, [])])
    child = _record(f"page-{_suffix()}", connector_id=connector_id, parent_external=parent_external,
                    group_external=group_external, rule=AccessRule.RESTRICTED)
    await processor.on_new_records([(child, grant)])
    assert child.id in await backend.reachable_from_app(connector_id, [granted])

    _, ok = await backend.provider.mark_connector_sync_edges(connector_id, SWEEP_GENERATION)
    assert ok
    with full_sync_running(SWEEP_GENERATION):
        await processor.on_new_record_groups([(_group(group_external, inherit=True, connector_id=connector_id, rule=AccessRule.STRICT), [])])
        again = _record(parent_external, connector_id=connector_id, group_external=group_external)
        again.id = parent.id
        await processor.on_updated_record_permissions(again, [])
        again = _record(child.external_record_id, connector_id=connector_id, parent_external=parent_external,
                        group_external=group_external, rule=AccessRule.RESTRICTED)
        again.id = child.id
        await processor.on_updated_record_permissions(again, grant)
    deleted, ok = await backend.provider.sweep_connector_sync_edges(connector_id, SWEEP_GENERATION)

    assert ok
    groups, records = CollectionNames.RECORD_GROUPS.value, CollectionNames.RECORDS.value
    relations, belongs = CollectionNames.NODE_RELATIONS.value, CollectionNames.BELONGS_TO.value
    assert await backend.edge_exists(belongs, records, parent.id, groups, group.id)
    assert await backend.edge_exists(belongs, records, child.id, groups, group.id)
    assert await backend.edge_exists(relations, groups, group.id, records, parent.id)
    assert await backend.edge_exists(relations, records, parent.id, records, child.id)
    assert await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, records, child.id, records, parent.id)
    assert await backend.edge_exists(CollectionNames.PERMISSION.value, CollectionNames.USERS.value, granted, records, child.id)
    nodes = [f"{records}/{parent.id}", f"{records}/{child.id}"]
    for collection in (belongs, relations, CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.PERMISSION.value):
        assert await backend.sweep_tags(collection, nodes) == [], f"a rewritten {collection} edge carries no tag key"
    seen_by_granted = await backend.reachable_from_app(connector_id, [granted])
    assert {parent.id, child.id} <= seen_by_granted
    assert parent.id in await backend.reachable_from_app(connector_id, [other])


async def test_a_user_adopting_a_persons_fresh_grant_keeps_it_through_the_sweep(backend) -> None:
    """R1-12: the full sync wrote the grant to the Person again; the User who adopts
    it already held a tagged copy. The merged edge must lose the tag."""
    from app.models.entities import Person
    from app.services.graph_db.common.sync_sweep import full_sync_running

    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    email = f"p-{_suffix()}@example.com"
    user_id = await _seed_user(backend, email)
    person_id = await backend.provider.upsert_person_by_email(Person(email=email, org_id=ORG))
    records, users, people = CollectionNames.RECORDS.value, CollectionNames.USERS.value, CollectionNames.PEOPLE.value
    permission = CollectionNames.PERMISSION.value

    def grant(from_collection: str, from_id: str) -> dict:
        return {"from_id": from_id, "from_collection": from_collection, "to_id": parent.id,
                "to_collection": records, "role": "READER", "type": "USER",
                "createdAtTimestamp": TS, "updatedAtTimestamp": TS}

    await backend.provider.batch_create_edges([grant(users, user_id), grant(people, person_id)], permission)
    _, ok = await backend.provider.mark_connector_sync_edges(connector_id, SWEEP_GENERATION)
    assert ok
    with full_sync_running(SWEEP_GENERATION):
        await processor.on_new_records([(
            _record(parent.external_record_id, connector_id=connector_id, group_external=group.external_group_id), [],
        )])
        await backend.provider.batch_create_edges([grant(people, person_id)], permission)
        await backend.provider.migrate_person_to_user(email, user_id, ORG)
    _, ok = await backend.provider.sweep_connector_sync_edges(connector_id, SWEEP_GENERATION)

    assert ok
    assert await backend.edge_exists(permission, users, user_id, records, parent.id)
    assert await backend.sweep_tags(permission, [f"{users}/{user_id}"]) == []


async def test_a_sweep_that_keeps_the_gates_takes_everything_else(backend) -> None:
    """R1-10: Jira DC's user search may miss users, so their gates stay; the edges
    of what the source no longer has still go."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    user_id = await _seed_user(backend, f"g-{_suffix()}@example.com")
    users, apps, records = CollectionNames.USERS.value, CollectionNames.APPS.value, CollectionNames.RECORDS.value
    gates = CollectionNames.USER_APP_RELATION.value
    await backend.provider.batch_create_edges([{
        "from_id": user_id, "from_collection": users, "to_id": connector_id, "to_collection": apps,
        "syncState": "NOT_STARTED", "lastSyncUpdate": TS, "createdAtTimestamp": TS, "updatedAtTimestamp": TS,
    }], gates)
    await backend.provider.mark_connector_sync_edges(connector_id, SWEEP_GENERATION)
    await processor.on_new_records([(
        _record(parent.external_record_id, connector_id=connector_id, group_external=group.external_group_id), [],
    )])

    _, ok = await backend.provider.sweep_connector_sync_edges(
        connector_id, SWEEP_GENERATION, keep_collections=(gates,)
    )

    assert ok
    assert await backend.edge_exists(gates, users, user_id, apps, connector_id)
    assert not await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, records, parent.id, records, child.id)


async def test_a_later_full_sync_sweeps_only_its_own_tag_and_clears_older_ones(backend) -> None:
    """R1-24: tags an earlier sync left (it failed, or its mark stopped part way) are
    not this sync's evidence of what the source dropped, and do not outlive it."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    _, ok = await backend.provider.mark_connector_sync_edges(connector_id, SWEEP_GENERATION)
    assert ok
    later = SWEEP_GENERATION + 1

    await processor.on_new_records([(
        _record(parent.external_record_id, connector_id=connector_id, group_external=group.external_group_id), [],
    )])
    deleted, ok = await backend.provider.sweep_connector_sync_edges(connector_id, later)
    assert (deleted, ok) == (0, True)
    records = CollectionNames.RECORDS.value
    assert await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, records, parent.id, records, child.id)

    cleared, ok = await backend.provider.clear_connector_sync_edge_tags(connector_id, later)

    assert ok and cleared > 0
    node_ids = [f"{records}/{parent.id}", f"{records}/{child.id}", f"{CollectionNames.RECORD_GROUPS.value}/{group.id}"]
    for collection in (CollectionNames.NODE_RELATIONS.value, CollectionNames.INHERIT_PERMISSIONS.value,
                       CollectionNames.BELONGS_TO.value):
        assert await backend.sweep_tags(collection, node_ids) == []
    assert await backend.edge_exists(CollectionNames.INHERIT_PERMISSIONS.value, records, child.id, records, parent.id)


async def test_clearing_tags_leaves_a_newer_generation_alone(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    await backend.provider.mark_connector_sync_edges(connector_id, SWEEP_GENERATION)

    await backend.provider.clear_connector_sync_edge_tags(connector_id, SWEEP_GENERATION - 1)

    records = CollectionNames.RECORDS.value
    tags = await backend.sweep_tags(CollectionNames.NODE_RELATIONS.value, [f"{records}/{child.id}"])
    assert tags and set(tags) == {SWEEP_GENERATION}


async def test_a_groups_records_are_listed_for_reindex(backend) -> None:
    """The reindex listing (its callers filter by the batch check); on Neo4j a
    missing isDeleted must not hide a record."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)

    listed = await backend.provider.get_records_by_record_group(
        record_group_id=group.id, connector_id=connector_id, org_id=ORG, depth=-1,
    )
    below = await backend.provider.get_records_by_parent_record(
        parent_record_id=parent.id, connector_id=connector_id, org_id=ORG, depth=-1,
    )

    assert {r.id for r in listed} == {parent.id, child.id}
    assert {r.id for r in below} == {parent.id, child.id}, "a folder's reindex includes the folder"


async def _grandchild(processor, connector_id: str, group, child) -> WebpageRecord:
    grandchild = _record(f"page-{_suffix()}", connector_id=connector_id,
                         parent_external=child.external_record_id, group_external=group.external_group_id)
    await processor.on_new_records([(grandchild, [])])
    return grandchild


async def test_a_folders_descendants_are_found_below_it(backend) -> None:
    """The collection move guard: moving a folder into one of its own sub-folders
    is refused only if the sub-folder is found below it."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    grandchild = await _grandchild(processor, connector_id, group, child)

    assert await backend.provider.is_record_descendant_of(record_id=child.id, ancestor_id=parent.id)
    assert await backend.provider.is_record_descendant_of(record_id=grandchild.id, ancestor_id=parent.id)
    assert not await backend.provider.is_record_descendant_of(record_id=parent.id, ancestor_id=grandchild.id)
    assert not await backend.provider.is_record_descendant_of(record_id=parent.id, ancestor_id=parent.id)


async def test_a_link_left_in_the_hierarchy_does_not_hide_a_descendant(backend) -> None:
    """A link the migration has not moved out yet reaches the grandchild first.
    It is no path, and the grandchild is still reached through its parent, once."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id, group, parent, child = await _tree(backend, processor)
    grandchild = await _grandchild(processor, connector_id, group, child)
    await backend.add_node_relation(parent.id, grandchild.id, {"relationshipType": "BLOCKS", "createdAtTimestamp": TS})

    below = await backend.provider.get_records_by_parent_record(
        parent_record_id=parent.id, connector_id=connector_id, org_id=ORG, depth=-1,
    )

    assert sorted(r.id for r in below) == sorted([parent.id, child.id, grandchild.id])
    assert await backend.provider.is_record_descendant_of(record_id=grandchild.id, ancestor_id=parent.id)


async def test_a_hard_deleted_record_leaves_no_edge_behind(backend) -> None:
    """``DELETE /api/v1/delete/record/{id}``: sweeping a fixed list of edge
    collections would leave the inheritance and grant edges dangling."""
    processor, _ = build_processor(backend.provider, ORG)
    _connector_id, _group, parent, child = await _tree(backend, processor)
    records = CollectionNames.RECORDS.value
    inherits = (CollectionNames.INHERIT_PERMISSIONS.value, records, child.id, records, parent.id)
    assert await backend.edge_exists(*inherits)

    assert await backend.provider.delete_records_and_relations(child.id, hard_delete=True) is True

    assert not await backend.edge_exists(*inherits)
    assert not await backend.edge_exists(CollectionNames.NODE_RELATIONS.value, records, parent.id, records, child.id)


def _file(external_id: str, *, connector_id: str, group_external: str, folder: bool = False,
          mime: str = "text/plain", **extra) -> FileRecord:
    return FileRecord(
        org_id=ORG,
        record_name=f"File {external_id}",
        external_record_id=external_id,
        record_type=RecordType.FILE,
        external_record_group_id=group_external,
        record_group_type=RecordGroupType.CONFLUENCE_SPACES,
        version=1,
        origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.CONFLUENCE,
        connector_id=connector_id,
        mime_type=mime,
        is_file=not folder,
        extension=None if folder else "txt",
        **extra,
    )


async def test_connector_stats_count_each_file_the_app_reaches_once(backend) -> None:
    """Folders are told by the record's mimeType (every value a folder has been
    written with), a group cut off from its App takes its records out, and a
    record in two groups counts once."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    home, inbox, cut_off = (_group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)
                            for _ in range(3))
    await processor.on_new_record_groups([(home, []), (inbox, []), (cut_off, [])])
    home_id = home.external_group_id

    counted = [_file(f"f-{_suffix()}", connector_id=connector_id, group_external=home_id),
               _file(f"f-{_suffix()}", connector_id=connector_id, group_external=home_id, mime="")]
    shared = _file(f"f-{_suffix()}", connector_id=connector_id, group_external=home_id)
    folders = [_file(f"d-{_suffix()}", connector_id=connector_id, group_external=home_id, folder=True, mime=m)
               for m in ("text/directory", "application/vnd.folder", "application/vnd.google-apps.folder")]
    internal = _file(f"f-{_suffix()}", connector_id=connector_id, group_external=home_id, is_internal=True)
    stranded = _file(f"f-{_suffix()}", connector_id=connector_id, group_external=cut_off.external_group_id)
    await processor.on_new_records([(r, []) for r in (*counted, shared, *folders, internal, stranded)])

    both = _file(shared.external_record_id, connector_id=connector_id, group_external=home_id)
    both.id = shared.id
    both.shared_with_me_record_group_ids = [inbox.external_group_id]
    await processor.on_updated_record_permissions(both, [])
    groups, apps = CollectionNames.RECORD_GROUPS.value, CollectionNames.APPS.value
    assert await backend.edge_exists(CollectionNames.BELONGS_TO.value, CollectionNames.RECORDS.value,
                                     shared.id, groups, inbox.id), "the premise: one record in two groups"
    assert await backend.edge_exists(CollectionNames.BELONGS_TO.value, groups, cut_off.id, apps, connector_id)
    await backend.remove_edge(CollectionNames.BELONGS_TO.value, groups, cut_off.id, apps, connector_id)

    result = await backend.provider.get_connector_stats(ORG, connector_id)

    assert result["success"] is True
    stats = result["data"]
    assert stats["stats"]["total"] == 3
    assert [(t["recordType"], t["total"]) for t in stats["byRecordType"]] == [("FILE", 3)]


async def test_a_collection_root_is_found_although_the_app_is_its_hierarchy_parent(backend) -> None:
    """Root items carry an App -> item PARENT_CHILD edge. The root lookups (folder
    name conflicts, file name conflicts, the children listing) must not read "has a
    PARENT_CHILD parent" as "is not at the root": that allows a duplicate root
    folder and lists nothing at the root. Only a parent record counts."""
    processor, _ = build_processor(backend.provider, ORG)
    kb_id = f"kb-{_suffix()}"
    await _seed_org(backend)
    await backend.provider.batch_upsert_nodes(
        [{"_key": kb_id, "orgId": ORG, "name": "Collection", "type": Connectors.KNOWLEDGE_BASE.value,
          "appGroup": "Local Storage", "scope": "personal", "isActive": True, "createdAtTimestamp": TS}],
        collection=CollectionNames.APPS.value,
    )
    docs = _kb_item(f"folder-{_suffix()}", kb_id, folder=True)
    docs.record_name = "Docs"
    root_file = _kb_item(f"file-{_suffix()}", kb_id)
    root_file.record_name = "Plan.pdf"
    await processor.on_new_records([(docs, []), (root_file, [])])
    sub = _kb_item(f"folder-{_suffix()}", kb_id, folder=True, parent=docs.id)
    sub.record_name = "Sub"
    nested = _kb_item(f"file-{_suffix()}", kb_id, parent=docs.id)
    nested.record_name = "Nested.pdf"
    await processor.on_new_records([(sub, []), (nested, [])])
    provider = backend.provider

    found = await provider.find_folder_by_name_in_parent(kb_id, "docs")
    assert found and found["_key"] == docs.id
    assert await provider.find_folder_by_name_in_parent(kb_id, "Sub") is None
    in_docs = await provider.find_folder_by_name_in_parent(kb_id, "sub", parent_folder_id=docs.id)
    assert in_docs and in_docs["_key"] == sub.id

    names = {name for name, _mime in await provider._fetch_existing_file_names_in_parent(kb_id, None)}
    assert "plan.pdf" in names and "nested.pdf" not in names

    children = await provider.get_kb_children(kb_id, skip=0, limit=50)
    listed = str(children)
    assert docs.id in listed and root_file.id in listed
    assert sub.id not in listed and nested.id not in listed


async def test_a_folder_delete_takes_a_deep_subtree_and_stays_in_its_collection(backend) -> None:
    """The cascade stopped at 20 hops, stranding deeper items without a parent, and
    followed a hierarchy edge into another connector's record."""
    processor, _ = build_processor(backend.provider, ORG)
    await _seed_org(backend)
    kb_id, other_kb = f"kb-{_suffix()}", f"kb-{_suffix()}"
    await backend.provider.batch_upsert_nodes(
        [{"_key": k, "orgId": ORG, "name": k, "type": Connectors.KNOWLEDGE_BASE.value, "appGroup": "Local Storage",
          "scope": "personal", "isActive": True, "createdAtTimestamp": TS} for k in (kb_id, other_kb)],
        collection=CollectionNames.APPS.value,
    )
    chain, parent = [], None
    for _ in range(25):
        folder = _kb_item(f"folder-{_suffix()}", kb_id, folder=True, parent=parent)
        await processor.on_new_records([(folder, [])])
        chain.append(folder.id)
        parent = folder.id
    bottom = _kb_item(f"file-{_suffix()}", kb_id, parent=parent)
    foreign = _kb_item(f"file-{_suffix()}", other_kb)
    await processor.on_new_records([(bottom, []), (foreign, [])])
    await backend.add_node_relation(chain[3], foreign.id, {"relationshipType": "PARENT_CHILD", "createdAtTimestamp": TS})

    result = await backend.provider.delete_records_recursive([chain[0]], kb_id)

    assert result["success"] is True
    records = CollectionNames.RECORDS.value
    left = [k for k in [*chain, bottom.id] if await backend.provider.get_document(k, records)]
    assert left == []
    assert await backend.provider.get_document(foreign.id, records)


async def test_collection_stats_count_its_files_not_its_folders(backend) -> None:
    processor, _ = build_processor(backend.provider, ORG)
    kb_id = f"kb-{_suffix()}"
    await _seed_org(backend)
    await backend.provider.batch_upsert_nodes(
        [{"_key": kb_id, "orgId": ORG, "name": "Collection", "type": Connectors.KNOWLEDGE_BASE.value,
          "appGroup": "Local Storage", "scope": "personal", "isActive": True, "createdAtTimestamp": TS}],
        collection=CollectionNames.APPS.value,
    )
    folder = _kb_item(f"folder-{_suffix()}", kb_id, folder=True)
    legacy_folder = _kb_item(f"folder-{_suffix()}", kb_id, folder=True)
    legacy_folder.mime_type = "application/vnd.folder"
    files = [_kb_item(f"file-{_suffix()}", kb_id) for _ in range(2)]
    await processor.on_new_records([(r, []) for r in (folder, legacy_folder, *files)])

    result = await backend.provider.get_connector_stats(ORG, kb_id)

    assert result["success"] is True
    assert result["data"]["origin"] == "COLLECTION"
    assert result["data"]["stats"]["total"] == 2


async def test_every_folder_is_rewritten_to_text_directory_and_no_file_is_touched(backend) -> None:
    """The folder mimeType migration, over batches smaller than the work; a
    second run finds nothing left."""
    processor, _ = build_processor(backend.provider, ORG)
    connector_id = f"conn-{_suffix()}"
    await _seed_app(backend, connector_id)
    group = _group(f"space-{_suffix()}", inherit=True, connector_id=connector_id)
    await processor.on_new_record_groups([(group, [])])
    gid = group.external_group_id
    folders = [_file(f"d-{_suffix()}", connector_id=connector_id, group_external=gid, folder=True, mime=m)
               for m in ("application/vnd.folder", "application/vnd.google-apps.folder",
                         "application/octet-stream", "")]
    plain = _file(f"f-{_suffix()}", connector_id=connector_id, group_external=gid, mime="text/plain")
    await processor.on_new_records([(r, []) for r in (*folders, plain)])

    result = await backend.provider.normalize_folder_mime_types(batch_size=2)

    assert result["normalized"] >= len(folders)
    records = CollectionNames.RECORDS.value
    for folder in folders:
        assert (await backend.provider.get_document(folder.id, records))["mimeType"] == "text/directory"
    assert (await backend.provider.get_document(plain.id, records))["mimeType"] == "text/plain"
    assert (await backend.provider.normalize_folder_mime_types())["normalized"] == 0
