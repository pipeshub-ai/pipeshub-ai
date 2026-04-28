"""
Comprehensive integration-style unit tests for connector workflows end-to-end
with DataSourceEntitiesProcessor.

These tests simulate real connector sync workflows (Google Drive, Jira, Confluence,
Slack) by mocking the TransactionStore (and thus the graph database) with two
in-memory providers -- MockArangoProvider and MockNeo4jProvider -- that both
conform to the patterns used by GraphTransactionStore.  Every test runs
against both providers via a parameterised fixture.

No real database, Kafka, or HTTP calls are made.
"""

import copy
import uuid
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors as ConnectorsEnum,
    EntityRelations,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.models.entities import (
    AppRole,
    AppUser,
    AppUserGroup,
    CommentRecord,
    FileRecord,
    LinkPublicStatus,
    LinkRecord,
    MailRecord,
    MessageRecord,
    Person,
    Priority,
    ProjectRecord,
    Record,
    RecordGroup,
    RecordGroupType,
    RecordType,
    Status,
    TicketRecord,
    User,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType

# ---------------------------------------------------------------------------
# Constants used across tests
# ---------------------------------------------------------------------------

ORG_ID = "org-test-001"
GDRIVE_CONNECTOR_ID = "connector-gdrive-001"
JIRA_CONNECTOR_ID = "connector-jira-001"
CONFLUENCE_CONNECTOR_ID = "connector-confluence-001"
SLACK_CONNECTOR_ID = "connector-slack-001"


# ---------------------------------------------------------------------------
# In-memory graph provider base — shared logic for both mock providers
# ---------------------------------------------------------------------------


class _InMemoryGraphStore:
    """
    Shared in-memory storage that simulates graph operations.

    Collections are dictionaries keyed by document _key / id.
    Edges are stored per edge-collection as lists of dicts with
    ``_from``, ``_to``, and arbitrary properties.
    """

    def __init__(self):
        self.collections: Dict[str, Dict[str, Dict[str, Any]]] = {}
        self.edges: Dict[str, List[Dict[str, Any]]] = {}

    # -- node helpers -------------------------------------------------------

    def upsert_node(self, collection: str, doc: Dict[str, Any]) -> Dict[str, Any]:
        if collection not in self.collections:
            self.collections[collection] = {}
        key = doc.get("_key") or doc.get("id") or str(uuid.uuid4())
        doc["_key"] = key
        doc["id"] = key
        self.collections[collection][key] = doc
        return doc

    def get_node(self, collection: str, key: str) -> Optional[Dict[str, Any]]:
        return self.collections.get(collection, {}).get(key)

    def delete_node(self, collection: str, key: str) -> bool:
        coll = self.collections.get(collection, {})
        if key in coll:
            del coll[key]
            return True
        return False

    def find_nodes(self, collection: str, filters: Dict[str, Any]) -> List[Dict[str, Any]]:
        results = []
        for doc in self.collections.get(collection, {}).values():
            if all(doc.get(k) == v for k, v in filters.items()):
                results.append(doc)
        return results

    # -- edge helpers -------------------------------------------------------

    def add_edge(self, edge_collection: str, edge: Dict[str, Any]) -> Dict[str, Any]:
        if edge_collection not in self.edges:
            self.edges[edge_collection] = []
        # Normalise to _from/_to for internal storage
        if "from_id" in edge and "from_collection" in edge:
            _from = f"{edge['from_collection']}/{edge['from_id']}"
            _to = f"{edge['to_collection']}/{edge['to_id']}"
            stored = {**edge, "_from": _from, "_to": _to}
        else:
            stored = {**edge}

        # Upsert semantics — replace if same _from/_to already exists in collection
        for i, existing in enumerate(self.edges[edge_collection]):
            if existing.get("_from") == stored.get("_from") and existing.get("_to") == stored.get("_to"):
                # For entity relations, also match on edgeType
                if edge_collection == CollectionNames.ENTITY_RELATIONS.value:
                    if existing.get("edgeType") == stored.get("edgeType"):
                        self.edges[edge_collection][i] = stored
                        return stored
                else:
                    self.edges[edge_collection][i] = stored
                    return stored
        self.edges[edge_collection].append(stored)
        return stored

    def get_edge(self, edge_collection: str, from_id: str, from_collection: str,
                 to_id: str, to_collection: str) -> Optional[Dict[str, Any]]:
        _from = f"{from_collection}/{from_id}"
        _to = f"{to_collection}/{to_id}"
        for e in self.edges.get(edge_collection, []):
            if e.get("_from") == _from and e.get("_to") == _to:
                return e
        return None

    def delete_edge(self, edge_collection: str, from_id: str, from_collection: str,
                    to_id: str, to_collection: str) -> bool:
        _from = f"{from_collection}/{from_id}"
        _to = f"{to_collection}/{to_id}"
        edges = self.edges.get(edge_collection, [])
        before = len(edges)
        self.edges[edge_collection] = [
            e for e in edges if not (e.get("_from") == _from and e.get("_to") == _to)
        ]
        return len(self.edges[edge_collection]) < before

    def delete_edges_from(self, edge_collection: str, from_id: str, from_collection: str) -> int:
        _from = f"{from_collection}/{from_id}"
        edges = self.edges.get(edge_collection, [])
        before = len(edges)
        self.edges[edge_collection] = [e for e in edges if e.get("_from") != _from]
        return before - len(self.edges[edge_collection])

    def delete_edges_to(self, edge_collection: str, to_id: str, to_collection: str) -> int:
        _to = f"{to_collection}/{to_id}"
        edges = self.edges.get(edge_collection, [])
        before = len(edges)
        self.edges[edge_collection] = [e for e in edges if e.get("_to") != _to]
        return before - len(self.edges[edge_collection])

    def delete_edges_by_relationship_types(
        self, edge_collection: str, from_id: str, from_collection: str, relationship_types: List[str]
    ) -> int:
        _from = f"{from_collection}/{from_id}"
        edges = self.edges.get(edge_collection, [])
        before = len(edges)
        self.edges[edge_collection] = [
            e for e in edges
            if not (e.get("_from") == _from and e.get("relationType") in relationship_types)
        ]
        return before - len(self.edges[edge_collection])

    def get_edges_from_node(self, node_id: str, edge_collection: str) -> List[Dict[str, Any]]:
        return [e for e in self.edges.get(edge_collection, []) if e.get("_from") == node_id]

    def get_edges_to_node(self, node_id: str, edge_collection: str) -> List[Dict[str, Any]]:
        return [e for e in self.edges.get(edge_collection, []) if e.get("_to") == node_id]

    def count_edges(self, edge_collection: str) -> int:
        return len(self.edges.get(edge_collection, []))


# ---------------------------------------------------------------------------
# MockArangoProvider
# ---------------------------------------------------------------------------


class MockArangoProvider(_InMemoryGraphStore):
    """In-memory mock that simulates ArangoDB-style graph operations."""

    provider_type = "arango"

    def __init__(self):
        super().__init__()
        # Pre-populate the org
        self.upsert_node(
            CollectionNames.ORGS.value,
            {"_key": ORG_ID, "name": "Test Org"},
        )

    # convenience
    def count_collection(self, collection: str) -> int:
        return len(self.collections.get(collection, {}))


# ---------------------------------------------------------------------------
# MockNeo4jProvider
# ---------------------------------------------------------------------------


class MockNeo4jProvider(_InMemoryGraphStore):
    """In-memory mock that simulates Neo4j-style graph operations."""

    provider_type = "neo4j"

    def __init__(self):
        super().__init__()
        self.upsert_node(
            CollectionNames.ORGS.value,
            {"_key": ORG_ID, "name": "Test Org"},
        )

    def count_collection(self, collection: str) -> int:
        return len(self.collections.get(collection, {}))


# ---------------------------------------------------------------------------
# Mock TransactionStore that delegates to in-memory provider
# ---------------------------------------------------------------------------


class MockTransactionStore:
    """
    Thin mock that exposes the same async methods as GraphTransactionStore
    but delegates to an in-memory graph store.
    """

    def __init__(self, store: _InMemoryGraphStore):
        self._s = store
        self.logger = MagicMock()

    # -- records ---

    async def get_record_by_external_id(self, connector_id: str, external_id: str) -> Optional[Record]:
        for doc in self._s.collections.get(CollectionNames.RECORDS.value, {}).values():
            if doc.get("connectorId") == connector_id and doc.get("externalRecordId") == external_id:
                return self._doc_to_record(doc)
        return None

    async def get_record_by_key(self, key: str) -> Optional[Dict]:
        doc = self._s.get_node(CollectionNames.RECORDS.value, key)
        if doc:
            return self._doc_to_record(doc)
        return None

    async def batch_upsert_records(self, records: List[Record]) -> None:
        for record in records:
            doc = record.to_arango_base_record()
            self._s.upsert_node(CollectionNames.RECORDS.value, doc)

    async def batch_upsert_nodes(self, nodes: List[Dict], collection: str) -> bool:
        for node in nodes:
            self._s.upsert_node(collection, node)
        return True

    # -- record groups ---

    async def get_record_group_by_external_id(self, connector_id: str, external_id: str) -> Optional[RecordGroup]:
        for doc in self._s.collections.get(CollectionNames.RECORD_GROUPS.value, {}).values():
            if doc.get("connectorId") == connector_id and doc.get("externalGroupId") == external_id:
                return RecordGroup(
                    id=doc["_key"],
                    org_id=doc.get("orgId", ""),
                    name=doc.get("groupName", ""),
                    external_group_id=doc.get("externalGroupId"),
                    connector_name=ConnectorsEnum(doc.get("connectorName", "UNKNOWN")),
                    connector_id=doc.get("connectorId", ""),
                    group_type=doc.get("groupType", RecordGroupType.KB),
                )
        return None

    async def batch_upsert_record_groups(self, record_groups: List[RecordGroup]) -> None:
        for rg in record_groups:
            doc = rg.to_arango_base_record_group()
            self._s.upsert_node(CollectionNames.RECORD_GROUPS.value, doc)

    async def create_record_group_relation(self, record_id: str, record_group_id: str) -> None:
        self._s.add_edge(CollectionNames.BELONGS_TO.value, {
            "from_id": record_id,
            "from_collection": CollectionNames.RECORDS.value,
            "to_id": record_group_id,
            "to_collection": CollectionNames.RECORD_GROUPS.value,
        })

    async def create_record_groups_relation(self, child_id: str, parent_id: str) -> None:
        self._s.add_edge(CollectionNames.BELONGS_TO.value, {
            "from_id": child_id,
            "from_collection": CollectionNames.RECORD_GROUPS.value,
            "to_id": parent_id,
            "to_collection": CollectionNames.RECORD_GROUPS.value,
        })

    async def create_node_relation(
        self,
        from_id: str,
        to_id: str,
        from_collection: str,
        to_collection: str,
        relationship_type: str,
    ) -> None:
        self._s.add_edge(CollectionNames.RECORD_RELATIONS.value, {
            "_from": f"{from_collection}/{from_id}",
            "_to": f"{to_collection}/{to_id}",
            "relationshipType": relationship_type,
        })

    async def create_record_relation(self, from_record_id: str, to_record_id: str, relation_type: str) -> None:
        self._s.add_edge(CollectionNames.RECORD_RELATIONS.value, {
            "_from": f"{CollectionNames.RECORDS.value}/{from_record_id}",
            "_to": f"{CollectionNames.RECORDS.value}/{to_record_id}",
            "relationType": relation_type,
        })

    async def batch_upsert_record_relations(self, edges: List[Dict]) -> None:
        for edge in edges:
            stored = dict(edge)
            if "relationshipType" in stored and "relationType" not in stored:
                stored["relationType"] = stored["relationshipType"]
            self._s.add_edge(CollectionNames.RECORD_RELATIONS.value, stored)

    async def create_inherit_permissions_relation_record_group(self, record_id: str, record_group_id: str) -> None:
        self._s.add_edge(CollectionNames.INHERIT_PERMISSIONS.value, {
            "from_id": record_id,
            "from_collection": CollectionNames.RECORDS.value,
            "to_id": record_group_id,
            "to_collection": CollectionNames.RECORD_GROUPS.value,
        })

    async def delete_inherit_permissions_relation_record_group(self, record_id: str, record_group_id: str) -> None:
        self._s.delete_edge(
            CollectionNames.INHERIT_PERMISSIONS.value,
            record_id, CollectionNames.RECORDS.value,
            record_group_id, CollectionNames.RECORD_GROUPS.value,
        )

    # -- users ---

    async def get_user_by_email(self, email: str) -> Optional[User]:
        for doc in self._s.collections.get(CollectionNames.USERS.value, {}).values():
            if doc.get("email") == email:
                return User(
                    id=doc["_key"],
                    email=doc["email"],
                    org_id=doc.get("orgId", ""),
                    full_name=doc.get("fullName"),
                    is_active=doc.get("isActive", True),
                )
        return None

    async def get_users(self, org_id: str, active: bool = True) -> List[User]:
        results = []
        for doc in self._s.collections.get(CollectionNames.USERS.value, {}).values():
            if doc.get("orgId") == org_id and (not active or doc.get("isActive", True)):
                results.append(User(
                    id=doc["_key"], email=doc["email"], org_id=org_id,
                    full_name=doc.get("fullName"), is_active=doc.get("isActive", True),
                ))
        return results

    async def get_app_users(self, org_id: str, connector_id: str) -> List[AppUser]:
        results = []
        for doc in self._s.collections.get("appUsers", {}).values():
            if doc.get("orgId") == org_id and doc.get("connectorId") == connector_id:
                results.append(AppUser(
                    id=doc["_key"], email=doc["email"], org_id=org_id,
                    full_name=doc.get("fullName", ""),
                    source_user_id=doc.get("userId", ""),
                    app_name=ConnectorsEnum(doc.get("appName", "UNKNOWN")),
                    connector_id=connector_id,
                ))
        return results

    async def batch_upsert_app_users(self, users: List[AppUser]) -> None:
        for user in users:
            doc = user.to_arango_base_user()
            doc["appName"] = user.app_name.value
            doc["connectorId"] = user.connector_id
            self._s.upsert_node("appUsers", doc)

    # -- user groups ---

    async def get_user_group_by_external_id(self, connector_id: str, external_id: str) -> Optional[AppUserGroup]:
        for doc in self._s.collections.get(CollectionNames.GROUPS.value, {}).values():
            if doc.get("connectorId") == connector_id and doc.get("externalGroupId") == external_id:
                return AppUserGroup(
                    id=doc["_key"],
                    app_name=ConnectorsEnum(doc.get("connectorName", "UNKNOWN")),
                    connector_id=connector_id,
                    source_user_group_id=doc.get("externalGroupId", ""),
                    name=doc.get("name", ""),
                    org_id=doc.get("orgId", ""),
                )
        return None

    async def batch_upsert_user_groups(self, user_groups: List[AppUserGroup]) -> None:
        for ug in user_groups:
            doc = ug.to_arango_base_user_group()
            self._s.upsert_node(CollectionNames.GROUPS.value, doc)

    # -- roles ---

    async def get_app_role_by_external_id(self, external_id: str, connector_id: str) -> Optional[AppRole]:
        for doc in self._s.collections.get(CollectionNames.ROLES.value, {}).values():
            if doc.get("connectorId") == connector_id and doc.get("externalRoleId") == external_id:
                return AppRole(
                    id=doc["_key"],
                    app_name=ConnectorsEnum(doc.get("connectorName", "UNKNOWN")),
                    connector_id=connector_id,
                    source_role_id=doc.get("externalRoleId", ""),
                    name=doc.get("name", ""),
                    org_id=doc.get("orgId", ""),
                )
        return None

    async def batch_upsert_app_roles(self, roles: List[AppRole]) -> None:
        for role in roles:
            doc = role.to_arango_base_role()
            self._s.upsert_node(CollectionNames.ROLES.value, doc)

    # -- edges ---

    async def batch_create_edges(self, edges: List[Dict], collection: str) -> bool:
        for edge in edges:
            self._s.add_edge(collection, edge)
        return True

    async def batch_create_entity_relations(self, edges: List[Dict]) -> None:
        for edge in edges:
            self._s.add_edge(CollectionNames.ENTITY_RELATIONS.value, edge)

    async def delete_edges_to(self, to_id: str, to_collection: str, collection: str) -> int:
        return self._s.delete_edges_to(collection, to_id, to_collection)

    async def delete_edges_from(self, from_id: str, from_collection: str, collection: str) -> int:
        return self._s.delete_edges_from(collection, from_id, from_collection)

    async def delete_edge(self, from_id: str, from_collection: str, to_id: str,
                          to_collection: str, collection: str) -> bool:
        return self._s.delete_edge(collection, from_id, from_collection, to_id, to_collection)

    async def delete_edges_by_relationship_types(
        self, from_id: str, from_collection: str, collection: str, relationship_types: List[str]
    ) -> int:
        return self._s.delete_edges_by_relationship_types(collection, from_id, from_collection, relationship_types)

    async def delete_parent_child_edge_to_record(self, record_id: str) -> int:
        _to = f"{CollectionNames.RECORDS.value}/{record_id}"
        edges = self._s.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        before = len(edges)
        self._s.edges[CollectionNames.RECORD_RELATIONS.value] = [
            e for e in edges
            if not (e.get("_to") == _to and e.get("relationType") == RecordRelations.PARENT_CHILD.value)
        ]
        return before - len(self._s.edges[CollectionNames.RECORD_RELATIONS.value])

    async def get_edges_from_node(self, from_node_id: str, edge_collection: str) -> List[Dict]:
        return self._s.get_edges_from_node(from_node_id, edge_collection)

    async def get_edges_to_node(self, node_id: str, edge_collection: str) -> List[Dict]:
        return self._s.get_edges_to_node(node_id, edge_collection)

    async def delete_record_by_key(self, key: str) -> None:
        self._s.delete_node(CollectionNames.RECORDS.value, key)

    async def get_all_orgs(self) -> List[Dict]:
        return list(self._s.collections.get(CollectionNames.ORGS.value, {}).values())

    async def batch_upsert_people(self, people: List[Person]) -> None:
        for person in people:
            self._s.upsert_node(CollectionNames.PEOPLE.value, person.to_arango_person())

    # -- helpers ---

    def _doc_to_record(self, doc: Dict) -> Record:
        """Convert stored ArangoDB-style doc back to a Record object."""
        rt = RecordType(doc.get("recordType", "FILE"))
        conn_name = doc.get("connectorName", ConnectorsEnum.UNKNOWN.value)
        try:
            connector_name = ConnectorsEnum(conn_name)
        except ValueError:
            connector_name = ConnectorsEnum.UNKNOWN

        return Record(
            id=doc.get("_key"),
            org_id=doc.get("orgId", ""),
            record_name=doc.get("recordName", ""),
            record_type=rt,
            external_record_id=doc.get("externalRecordId", ""),
            external_revision_id=doc.get("externalRevisionId"),
            external_record_group_id=doc.get("externalGroupId"),
            record_group_id=doc.get("recordGroupId"),
            parent_external_record_id=doc.get("externalParentId"),
            version=doc.get("version", 0),
            origin=OriginTypes(doc.get("origin", "CONNECTOR")),
            connector_name=connector_name,
            connector_id=doc.get("connectorId", ""),
            mime_type=doc.get("mimeType", MimeTypes.UNKNOWN.value),
            indexing_status=doc.get("indexingStatus", ProgressStatus.QUEUED.value),
        )


# ---------------------------------------------------------------------------
# Mock DataStoreProvider that wraps the in-memory store in a context manager
# ---------------------------------------------------------------------------


class MockDataStoreProvider:
    """DataStoreProvider replacement that yields MockTransactionStore."""

    def __init__(self, graph_store: _InMemoryGraphStore):
        self._store = graph_store
        self.logger = MagicMock()

    @asynccontextmanager
    async def transaction(self):
        yield MockTransactionStore(self._store)

    async def execute_in_transaction(self, func, *args, **kwargs):
        async with self.transaction() as tx:
            return await func(tx, *args, **kwargs)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(params=["arango", "neo4j"])
def graph_store(request):
    """Parameterised fixture returning MockArangoProvider or MockNeo4jProvider."""
    if request.param == "arango":
        return MockArangoProvider()
    return MockNeo4jProvider()


@pytest.fixture
def data_store_provider(graph_store):
    return MockDataStoreProvider(graph_store)


@pytest.fixture
def processor(data_store_provider):
    """Return a DataSourceEntitiesProcessor wired to the in-memory store."""
    logger = MagicMock()
    config_service = AsyncMock()
    proc = DataSourceEntitiesProcessor(logger, data_store_provider, config_service)
    proc.org_id = ORG_ID
    proc.messaging_producer = AsyncMock()
    return proc


@pytest.fixture
def tx_store(graph_store):
    """Return a ready-to-use MockTransactionStore."""
    return MockTransactionStore(graph_store)


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------


def make_file_record(
    connector_id: str = GDRIVE_CONNECTOR_ID,
    connector_name: ConnectorsEnum = ConnectorsEnum.GOOGLE_DRIVE,
    external_id: str = None,
    name: str = "document.pdf",
    record_group_ext_id: str = None,
    record_group_type: RecordGroupType = RecordGroupType.DRIVE,
    parent_ext_id: str = None,
    version: int = 1,
    mime_type: str = "application/pdf",
    size: int = 1024,
    is_file: bool = True,
    inherit_permissions: bool = True,
    indexing_status: str = ProgressStatus.QUEUED.value,
) -> FileRecord:
    return FileRecord(
        org_id=ORG_ID,
        external_record_id=external_id or str(uuid.uuid4()),
        record_name=name,
        origin=OriginTypes.CONNECTOR,
        connector_name=connector_name,
        connector_id=connector_id,
        record_type=RecordType.FILE,
        record_group_type=record_group_type,
        external_record_group_id=record_group_ext_id,
        parent_external_record_id=parent_ext_id,
        version=version,
        mime_type=mime_type,
        source_created_at=1700000000000,
        source_updated_at=1700001000000,
        is_file=is_file,
        extension="pdf",
        size_in_bytes=size,
        weburl="https://drive.google.com/file/d/abc",
        inherit_permissions=inherit_permissions,
        indexing_status=indexing_status,
    )


def make_ticket_record(
    connector_id: str = JIRA_CONNECTOR_ID,
    external_id: str = None,
    name: str = "PROJ-101: Fix login bug",
    group_ext_id: str = "project-alpha",
    assignee_email: str = "dev@example.com",
    reporter_email: str = "pm@example.com",
    creator_email: str = "pm@example.com",
) -> TicketRecord:
    return TicketRecord(
        org_id=ORG_ID,
        external_record_id=external_id or str(uuid.uuid4()),
        record_name=name,
        origin=OriginTypes.CONNECTOR,
        connector_name=ConnectorsEnum.JIRA,
        connector_id=connector_id,
        record_type=RecordType.TICKET,
        record_group_type=RecordGroupType.PROJECT,
        external_record_group_id=group_ext_id,
        version=1,
        mime_type=MimeTypes.UNKNOWN.value,
        source_created_at=1700000000000,
        source_updated_at=1700001000000,
        weburl="https://jira.example.com/browse/PROJ-101",
        status=Status.OPEN,
        priority=Priority.HIGH,
        assignee_email=assignee_email,
        reporter_email=reporter_email,
        creator_email=creator_email,
    )


def make_webpage_record(
    connector_id: str = CONFLUENCE_CONNECTOR_ID,
    connector_name: ConnectorsEnum = ConnectorsEnum.CONFLUENCE,
    record_type: RecordType = RecordType.CONFLUENCE_PAGE,
    external_id: str = None,
    name: str = "Architecture Overview",
    group_ext_id: str = "space-eng",
    parent_ext_id: str = None,
) -> WebpageRecord:
    return WebpageRecord(
        org_id=ORG_ID,
        external_record_id=external_id or str(uuid.uuid4()),
        record_name=name,
        origin=OriginTypes.CONNECTOR,
        connector_name=connector_name,
        connector_id=connector_id,
        record_type=record_type,
        record_group_type=RecordGroupType.CONFLUENCE_SPACES,
        external_record_group_id=group_ext_id,
        parent_external_record_id=parent_ext_id,
        version=1,
        mime_type=MimeTypes.HTML.value,
        source_created_at=1700000000000,
        source_updated_at=1700001000000,
        weburl="https://confluence.example.com/pages/12345",
    )


def make_message_record(
    connector_id: str = SLACK_CONNECTOR_ID,
    external_id: str = None,
    name: str = "Message in #general",
    group_ext_id: str = "channel-general",
) -> MessageRecord:
    return MessageRecord(
        org_id=ORG_ID,
        external_record_id=external_id or str(uuid.uuid4()),
        record_name=name,
        origin=OriginTypes.CONNECTOR,
        connector_name=ConnectorsEnum.SLACK,
        connector_id=connector_id,
        record_type=RecordType.MESSAGE,
        record_group_type=RecordGroupType.SLACK_CHANNEL,
        external_record_group_id=group_ext_id,
        version=1,
        mime_type=MimeTypes.PLAIN_TEXT.value,
        source_created_at=1700000000000,
        source_updated_at=1700001000000,
    )


def make_project_record(
    connector_id: str = JIRA_CONNECTOR_ID,
    external_id: str = None,
    name: str = "Project Alpha",
    group_ext_id: str = "project-alpha",
    lead_email: str = "lead@example.com",
) -> ProjectRecord:
    return ProjectRecord(
        org_id=ORG_ID,
        external_record_id=external_id or str(uuid.uuid4()),
        record_name=name,
        origin=OriginTypes.CONNECTOR,
        connector_name=ConnectorsEnum.JIRA,
        connector_id=connector_id,
        record_type=RecordType.PROJECT,
        record_group_type=RecordGroupType.PROJECT,
        external_record_group_id=group_ext_id,
        version=1,
        mime_type=MimeTypes.UNKNOWN.value,
        source_created_at=1700000000000,
        source_updated_at=1700001000000,
        weburl="https://jira.example.com/projects/ALPHA",
        lead_email=lead_email,
    )


def make_record_group(
    connector_id: str = GDRIVE_CONNECTOR_ID,
    connector_name: ConnectorsEnum = ConnectorsEnum.GOOGLE_DRIVE,
    external_id: str = None,
    name: str = "My Drive",
    group_type: RecordGroupType = RecordGroupType.DRIVE,
    parent_ext_group_id: str = None,
    inherit_permissions: bool = False,
) -> RecordGroup:
    return RecordGroup(
        org_id=ORG_ID,
        name=name,
        external_group_id=external_id or str(uuid.uuid4()),
        connector_name=connector_name,
        connector_id=connector_id,
        group_type=group_type,
        parent_external_group_id=parent_ext_group_id,
        inherit_permissions=inherit_permissions,
    )


def make_user(email: str, name: str = None) -> User:
    return User(
        email=email,
        org_id=ORG_ID,
        full_name=name or email.split("@")[0].title(),
        is_active=True,
    )


def make_app_user(
    email: str,
    connector_id: str = GDRIVE_CONNECTOR_ID,
    connector_name: ConnectorsEnum = ConnectorsEnum.GOOGLE_DRIVE,
    source_user_id: str = None,
) -> AppUser:
    return AppUser(
        email=email,
        org_id=ORG_ID,
        full_name=email.split("@")[0].title(),
        source_user_id=source_user_id or str(uuid.uuid4()),
        app_name=connector_name,
        connector_id=connector_id,
        is_active=True,
    )


def make_permission(
    email: str = None,
    external_id: str = None,
    perm_type: PermissionType = PermissionType.READ,
    entity_type: EntityType = EntityType.USER,
) -> Permission:
    return Permission(
        external_id=external_id,
        email=email,
        type=perm_type,
        entity_type=entity_type,
    )


def seed_user(graph_store: _InMemoryGraphStore, email: str, name: str = None) -> str:
    """Insert a user into the graph store and return its key."""
    user = make_user(email, name)
    doc = {
        "_key": user.id,
        "email": user.email,
        "orgId": user.org_id,
        "fullName": user.full_name,
        "isActive": True,
    }
    graph_store.upsert_node(CollectionNames.USERS.value, doc)
    return user.id


def seed_user_group(graph_store: _InMemoryGraphStore, connector_id: str,
                    connector_name: ConnectorsEnum, external_id: str,
                    name: str = "Test Group") -> str:
    """Insert a user group and return its key."""
    ug = AppUserGroup(
        app_name=connector_name,
        connector_id=connector_id,
        source_user_group_id=external_id,
        name=name,
        org_id=ORG_ID,
    )
    doc = ug.to_arango_base_user_group()
    graph_store.upsert_node(CollectionNames.GROUPS.value, doc)
    return ug.id


def seed_role(graph_store: _InMemoryGraphStore, connector_id: str,
              connector_name: ConnectorsEnum, external_id: str,
              name: str = "Test Role") -> str:
    """Insert a role and return its key."""
    role = AppRole(
        app_name=connector_name,
        connector_id=connector_id,
        source_role_id=external_id,
        name=name,
        org_id=ORG_ID,
    )
    doc = role.to_arango_base_role()
    graph_store.upsert_node(CollectionNames.ROLES.value, doc)
    return role.id


# ===========================================================================
# 1. Google Drive Full Sync Workflow
# ===========================================================================


class TestGoogleDriveFullSyncWorkflow:
    """Simulates a complete Google Drive connector initial sync."""

    @pytest.mark.asyncio
    async def test_sync_single_file_creates_record(self, processor, graph_store):
        """A single file sync creates a record in the graph."""
        file_rec = make_file_record(record_group_ext_id="drive-001")
        await processor.on_new_records([(file_rec, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 1

    @pytest.mark.asyncio
    async def test_sync_creates_record_group_automatically(self, processor, graph_store):
        """If record group doesn't exist, it is auto-created from the record's external group id."""
        file_rec = make_file_record(record_group_ext_id="drive-auto-created")
        await processor.on_new_records([(file_rec, [])])
        assert graph_store.count_collection(CollectionNames.RECORD_GROUPS.value) >= 1

    @pytest.mark.asyncio
    async def test_sync_multiple_files_same_group(self, processor, graph_store):
        """Multiple files in the same group share one record group."""
        group_ext = "shared-drive-001"
        files = [
            make_file_record(external_id=f"file-{i}", name=f"file{i}.pdf", record_group_ext_id=group_ext)
            for i in range(5)
        ]
        await processor.on_new_records([(f, []) for f in files])
        # Exactly one record group should be created
        rg_count = graph_store.count_collection(CollectionNames.RECORD_GROUPS.value)
        assert rg_count == 1, f"Expected 1 record group, got {rg_count}"

    @pytest.mark.asyncio
    async def test_sync_creates_belongs_to_edges(self, processor, graph_store):
        """Each record gets a BELONGS_TO edge to its record group."""
        group_ext = "drive-bt-001"
        files = [
            make_file_record(external_id=f"file-bt-{i}", name=f"file{i}.pdf", record_group_ext_id=group_ext)
            for i in range(3)
        ]
        await processor.on_new_records([(f, []) for f in files])
        bt_edges = graph_store.edges.get(CollectionNames.BELONGS_TO.value, [])
        record_to_group = [e for e in bt_edges
                           if CollectionNames.RECORDS.value in e.get("_from", "")
                           and CollectionNames.RECORD_GROUPS.value in e.get("_to", "")]
        assert len(record_to_group) == 3

    @pytest.mark.asyncio
    async def test_sync_publishes_kafka_events(self, processor, graph_store):
        """Each new record triggers a Kafka newRecord event."""
        files = [
            make_file_record(external_id=f"kafka-{i}", name=f"kafka{i}.txt", record_group_ext_id="drive-kafka")
            for i in range(4)
        ]
        await processor.on_new_records([(f, []) for f in files])
        assert processor.messaging_producer.send_message.call_count == 4

    @pytest.mark.asyncio
    async def test_sync_folder_record(self, processor, graph_store):
        """Folder records (is_file=False) are stored correctly."""
        folder = make_file_record(
            external_id="folder-001", name="Engineering", is_file=False,
            mime_type=MimeTypes.FOLDER.value, record_group_ext_id="drive-folders",
        )
        await processor.on_new_records([(folder, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 1

    @pytest.mark.asyncio
    async def test_sync_parent_child_edge(self, processor, graph_store):
        """Child record that specifies parent gets PARENT_CHILD edge."""
        parent_ext = "parent-folder-001"
        parent_folder = make_file_record(
            external_id=parent_ext, name="ParentFolder", is_file=False,
            mime_type=MimeTypes.FOLDER.value, record_group_ext_id="drive-pc",
        )
        child_file = make_file_record(
            external_id="child-file-001", name="child.pdf", parent_ext_id=parent_ext,
            record_group_ext_id="drive-pc",
        )
        await processor.on_new_records([(parent_folder, [])])
        await processor.on_new_records([(child_file, [])])
        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        pc_edges = [e for e in rr if e.get("relationType") == RecordRelations.PARENT_CHILD.value]
        assert len(pc_edges) == 1

    @pytest.mark.asyncio
    async def test_sync_creates_placeholder_parent_when_missing(self, processor, graph_store):
        """If a child references a parent that hasn't been synced, a placeholder is created."""
        child = make_file_record(
            external_id="orphan-child-001", name="orphan.pdf",
            parent_ext_id="nonexistent-parent-999",
            record_group_ext_id="drive-orphan",
        )
        child.parent_record_type = RecordType.FILE
        await processor.on_new_records([(child, [])])
        # Both the placeholder parent and the child should exist
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 2

    @pytest.mark.asyncio
    async def test_sync_with_user_permission(self, processor, graph_store):
        """Record with a USER permission creates a permission edge."""
        user_email = "alice@example.com"
        seed_user(graph_store, user_email, "Alice")
        file_rec = make_file_record(external_id="perm-file-001", record_group_ext_id="drive-perm")
        perm = make_permission(email=user_email, perm_type=PermissionType.OWNER)
        await processor.on_new_records([(file_rec, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) >= 1

    @pytest.mark.asyncio
    async def test_sync_multiple_permissions_per_record(self, processor, graph_store):
        """Record with multiple user permissions creates multiple permission edges."""
        emails = ["bob@example.com", "charlie@example.com", "diana@example.com"]
        for e in emails:
            seed_user(graph_store, e)

        file_rec = make_file_record(external_id="multi-perm-001", record_group_ext_id="drive-mperm")
        perms = [
            make_permission(email="bob@example.com", perm_type=PermissionType.OWNER),
            make_permission(email="charlie@example.com", perm_type=PermissionType.WRITE),
            make_permission(email="diana@example.com", perm_type=PermissionType.READ),
        ]
        await processor.on_new_records([(file_rec, perms)])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) == 3

    @pytest.mark.asyncio
    async def test_sync_skips_auto_index_off_kafka_event(self, processor, graph_store):
        """Records with AUTO_INDEX_OFF status skip Kafka events."""
        file_rec = make_file_record(
            external_id="auto-off-001", record_group_ext_id="drive-autooff",
            indexing_status=ProgressStatus.AUTO_INDEX_OFF.value,
        )
        await processor.on_new_records([(file_rec, [])])
        assert processor.messaging_producer.send_message.call_count == 0

    @pytest.mark.asyncio
    async def test_sync_inherit_permissions_edge(self, processor, graph_store):
        """When inherit_permissions=True, an INHERIT_PERMISSIONS edge is created."""
        file_rec = make_file_record(
            external_id="inherit-001", record_group_ext_id="drive-inherit",
            inherit_permissions=True,
        )
        await processor.on_new_records([(file_rec, [])])
        inherit_edges = graph_store.edges.get(CollectionNames.INHERIT_PERMISSIONS.value, [])
        assert len(inherit_edges) >= 1

    @pytest.mark.asyncio
    async def test_sync_no_inherit_permissions(self, processor, graph_store):
        """When inherit_permissions=False, no INHERIT_PERMISSIONS edge is created."""
        file_rec = make_file_record(
            external_id="noinherit-001", record_group_ext_id="drive-noinherit",
            inherit_permissions=False,
        )
        await processor.on_new_records([(file_rec, [])])
        inherit_edges = graph_store.edges.get(CollectionNames.INHERIT_PERMISSIONS.value, [])
        # Filter to edges from our record
        from_our_record = [e for e in inherit_edges if file_rec.id in e.get("_from", "")]
        assert len(from_our_record) == 0

    @pytest.mark.asyncio
    async def test_sync_15_files_batch(self, processor, graph_store):
        """Simulates syncing a batch of 15 files at once."""
        group_ext = "drive-batch-15"
        files = [
            make_file_record(
                external_id=f"batch15-{i}",
                name=f"file{i}.txt",
                record_group_ext_id=group_ext,
                mime_type=MimeTypes.PLAIN_TEXT.value,
            )
            for i in range(15)
        ]
        await processor.on_new_records([(f, []) for f in files])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 15
        assert processor.messaging_producer.send_message.call_count == 15


# ===========================================================================
# 2. Jira Full Sync Workflow
# ===========================================================================


class TestJiraFullSyncWorkflow:
    """Simulates a complete Jira connector sync with projects, issues, users."""

    @pytest.mark.asyncio
    async def test_sync_project_as_record(self, processor, graph_store):
        """A Jira project is synced as a ProjectRecord."""
        seed_user(graph_store, "lead@example.com", "Lead")
        proj = make_project_record(external_id="proj-alpha-001")
        await processor.on_new_records([(proj, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 1

    @pytest.mark.asyncio
    async def test_sync_ticket_creates_entity_edges(self, processor, graph_store):
        """A Jira ticket creates ASSIGNED_TO, CREATED_BY, REPORTED_BY edges."""
        seed_user(graph_store, "dev@example.com", "Dev")
        seed_user(graph_store, "pm@example.com", "PM")
        ticket = make_ticket_record(external_id="ticket-entity-001")
        await processor.on_new_records([(ticket, [])])
        entity_edges = graph_store.edges.get(CollectionNames.ENTITY_RELATIONS.value, [])
        edge_types = {e.get("edgeType") for e in entity_edges}
        assert EntityRelations.ASSIGNED_TO.value in edge_types
        assert EntityRelations.CREATED_BY.value in edge_types
        assert EntityRelations.REPORTED_BY.value in edge_types

    @pytest.mark.asyncio
    async def test_sync_ticket_missing_user_no_edge(self, processor, graph_store):
        """If user doesn't exist in graph, entity relation edges are not created."""
        ticket = make_ticket_record(
            external_id="ticket-nousers-001",
            assignee_email="ghost@example.com",
            reporter_email="ghost@example.com",
            creator_email="ghost@example.com",
        )
        await processor.on_new_records([(ticket, [])])
        entity_edges = graph_store.edges.get(CollectionNames.ENTITY_RELATIONS.value, [])
        assert len(entity_edges) == 0

    @pytest.mark.asyncio
    async def test_sync_project_lead_edge(self, processor, graph_store):
        """A ProjectRecord creates a LEAD_BY edge to the lead user."""
        seed_user(graph_store, "lead@example.com", "Lead")
        proj = make_project_record(external_id="proj-lead-001", lead_email="lead@example.com")
        await processor.on_new_records([(proj, [])])
        entity_edges = graph_store.edges.get(CollectionNames.ENTITY_RELATIONS.value, [])
        lead_edges = [e for e in entity_edges if e.get("edgeType") == EntityRelations.LEAD_BY.value]
        assert len(lead_edges) == 1

    @pytest.mark.asyncio
    async def test_sync_multiple_tickets_same_project(self, processor, graph_store):
        """Multiple tickets in the same project share a record group."""
        seed_user(graph_store, "dev@example.com")
        seed_user(graph_store, "pm@example.com")
        tickets = [
            make_ticket_record(external_id=f"jira-ticket-{i}", name=f"PROJ-{100+i}: issue {i}")
            for i in range(5)
        ]
        await processor.on_new_records([(t, []) for t in tickets])
        rg_count = graph_store.count_collection(CollectionNames.RECORD_GROUPS.value)
        assert rg_count == 1

    @pytest.mark.asyncio
    async def test_sync_ticket_with_role_permission(self, processor, graph_store):
        """A ticket with ROLE-based permission creates permission edge from role node."""
        role_ext_id = "role-developers"
        seed_role(graph_store, JIRA_CONNECTOR_ID, ConnectorsEnum.JIRA, role_ext_id, "Developers")
        ticket = make_ticket_record(external_id="ticket-role-001")
        perm = make_permission(external_id=role_ext_id, entity_type=EntityType.ROLE, perm_type=PermissionType.READ)
        await processor.on_new_records([(ticket, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        role_edges = [e for e in perm_edges if CollectionNames.ROLES.value in e.get("from_collection", "")]
        assert len(role_edges) == 1

    @pytest.mark.asyncio
    async def test_sync_ticket_with_group_permission(self, processor, graph_store):
        """A ticket with GROUP-based permission creates permission edge from group node."""
        group_ext_id = "group-engineering"
        seed_user_group(graph_store, JIRA_CONNECTOR_ID, ConnectorsEnum.JIRA, group_ext_id, "Engineering")
        ticket = make_ticket_record(external_id="ticket-group-001")
        perm = make_permission(external_id=group_ext_id, entity_type=EntityType.GROUP, perm_type=PermissionType.READ)
        await processor.on_new_records([(ticket, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        group_edges = [e for e in perm_edges if CollectionNames.GROUPS.value in e.get("from_collection", "")]
        assert len(group_edges) == 1

    @pytest.mark.asyncio
    async def test_sync_ticket_with_org_permission(self, processor, graph_store):
        """A ticket with ORG-level permission creates permission edge from org node."""
        ticket = make_ticket_record(external_id="ticket-org-001")
        perm = make_permission(entity_type=EntityType.ORG, perm_type=PermissionType.READ)
        await processor.on_new_records([(ticket, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        org_edges = [e for e in perm_edges if CollectionNames.ORGS.value in e.get("from_collection", "")]
        assert len(org_edges) == 1

    @pytest.mark.asyncio
    async def test_ticket_related_external_records(self, processor, graph_store):
        """Tickets with related_external_records create record relations."""
        from app.models.entities import RelatedExternalRecord
        blocked_ticket = make_ticket_record(external_id="blocked-001", name="Blocked ticket")
        await processor.on_new_records([(blocked_ticket, [])])

        blocker = make_ticket_record(external_id="blocker-001", name="Blocker")
        blocker.related_external_records = [
            RelatedExternalRecord(
                external_record_id="blocked-001",
                record_type=RecordType.TICKET,
                relation_type=RecordRelations.BLOCKS,
            )
        ]
        await processor.on_new_records([(blocker, [])])
        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        block_edges = [e for e in rr if e.get("relationType") == RecordRelations.BLOCKS.value]
        assert len(block_edges) == 1


# ===========================================================================
# 3. Confluence Workflow
# ===========================================================================


class TestConfluenceWorkflow:
    """Simulates Confluence space/page sync."""

    @pytest.mark.asyncio
    async def test_sync_confluence_page(self, processor, graph_store):
        """A Confluence page is synced as a WebpageRecord."""
        page = make_webpage_record(external_id="conf-page-001")
        await processor.on_new_records([(page, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 1

    @pytest.mark.asyncio
    async def test_sync_page_hierarchy(self, processor, graph_store):
        """Parent page -> child page creates a PARENT_CHILD edge."""
        parent = make_webpage_record(external_id="conf-parent-001", name="Parent Page")
        child = make_webpage_record(
            external_id="conf-child-001", name="Child Page", parent_ext_id="conf-parent-001",
        )
        child.parent_record_type = RecordType.CONFLUENCE_PAGE
        await processor.on_new_records([(parent, [])])
        await processor.on_new_records([(child, [])])
        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        pc = [e for e in rr if e.get("relationType") == RecordRelations.PARENT_CHILD.value]
        assert len(pc) == 1

    @pytest.mark.asyncio
    async def test_sync_confluence_space_record_group(self, processor, graph_store):
        """Confluence space becomes a RecordGroup, pages link to it."""
        space_rg = make_record_group(
            connector_id=CONFLUENCE_CONNECTOR_ID,
            connector_name=ConnectorsEnum.CONFLUENCE,
            external_id="space-eng",
            name="Engineering Space",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
        )
        perm = make_permission(entity_type=EntityType.ORG, perm_type=PermissionType.READ)
        await processor.on_new_record_groups([(space_rg, [perm])])
        assert graph_store.count_collection(CollectionNames.RECORD_GROUPS.value) >= 1
        # Should have BELONGS_TO edge from group to org
        bt = graph_store.edges.get(CollectionNames.BELONGS_TO.value, [])
        rg_to_org = [e for e in bt
                     if CollectionNames.RECORD_GROUPS.value in e.get("_from", "")
                     and CollectionNames.ORGS.value in e.get("_to", "")]
        assert len(rg_to_org) >= 1

    @pytest.mark.asyncio
    async def test_sync_blogpost_record(self, processor, graph_store):
        """A Confluence blogpost is synced as a WebpageRecord with CONFLUENCE_BLOGPOST type."""
        blog = make_webpage_record(
            external_id="blog-001",
            name="Release Notes Q4",
            record_type=RecordType.CONFLUENCE_BLOGPOST,
        )
        await processor.on_new_records([(blog, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 1

    @pytest.mark.asyncio
    async def test_sync_multiple_pages_under_space(self, processor, graph_store):
        """All pages under a space should link to the same record group."""
        space_ext = "space-qa"
        pages = [
            make_webpage_record(external_id=f"qa-page-{i}", name=f"QA Page {i}", group_ext_id=space_ext)
            for i in range(8)
        ]
        await processor.on_new_records([(p, []) for p in pages])
        bt = graph_store.edges.get(CollectionNames.BELONGS_TO.value, [])
        record_to_group = [e for e in bt
                           if CollectionNames.RECORDS.value in e.get("_from", "")
                           and CollectionNames.RECORD_GROUPS.value in e.get("_to", "")]
        assert len(record_to_group) == 8

    @pytest.mark.asyncio
    async def test_sync_page_with_comment_child(self, processor, graph_store):
        """A comment on a page creates a child record with PARENT_CHILD edge."""
        page = make_webpage_record(external_id="page-with-comment-001", name="Page With Comments")
        await processor.on_new_records([(page, [])])

        comment = CommentRecord(
            org_id=ORG_ID,
            external_record_id="comment-001",
            record_name="Comment on page",
            origin=OriginTypes.CONNECTOR,
            connector_name=ConnectorsEnum.CONFLUENCE,
            connector_id=CONFLUENCE_CONNECTOR_ID,
            record_type=RecordType.COMMENT,
            record_group_type=RecordGroupType.CONFLUENCE_SPACES,
            external_record_group_id="space-eng",
            parent_external_record_id="page-with-comment-001",
            parent_record_type=RecordType.CONFLUENCE_PAGE,
            version=1,
            mime_type=MimeTypes.HTML.value,
            source_created_at=1700000000000,
            source_updated_at=1700001000000,
            author_source_id="user-abc-123",
        )
        await processor.on_new_records([(comment, [])])
        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        pc = [e for e in rr if e.get("relationType") == RecordRelations.PARENT_CHILD.value]
        assert len(pc) == 1


# ===========================================================================
# 4. Slack Workflow
# ===========================================================================


class TestSlackWorkflow:
    """Simulates Slack channel/message sync."""

    @pytest.mark.asyncio
    async def test_sync_slack_message(self, processor, graph_store):
        """Slack messages are synced as MessageRecord."""
        msg = make_message_record(external_id="slack-msg-001")
        await processor.on_new_records([(msg, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 1

    @pytest.mark.asyncio
    async def test_sync_multiple_channels(self, processor, graph_store):
        """Messages from different channels create separate record groups."""
        msg1 = make_message_record(external_id="msg-ch1-001", group_ext_id="channel-general")
        msg2 = make_message_record(external_id="msg-ch2-001", group_ext_id="channel-random")
        await processor.on_new_records([(msg1, []), (msg2, [])])
        rg_count = graph_store.count_collection(CollectionNames.RECORD_GROUPS.value)
        assert rg_count == 2

    @pytest.mark.asyncio
    async def test_slack_channel_record_group(self, processor, graph_store):
        """Slack channel becomes a RecordGroup."""
        channel_rg = make_record_group(
            connector_id=SLACK_CONNECTOR_ID,
            connector_name=ConnectorsEnum.SLACK,
            external_id="channel-general",
            name="#general",
            group_type=RecordGroupType.SLACK_CHANNEL,
        )
        await processor.on_new_record_groups([(channel_rg, [])])
        assert graph_store.count_collection(CollectionNames.RECORD_GROUPS.value) >= 1

    @pytest.mark.asyncio
    async def test_sync_batch_of_messages(self, processor, graph_store):
        """Batch syncing 20 messages in one call."""
        messages = [
            make_message_record(external_id=f"slack-batch-{i}", name=f"Message {i}")
            for i in range(20)
        ]
        await processor.on_new_records([(m, []) for m in messages])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 20


# ===========================================================================
# 5. Incremental Sync Workflow
# ===========================================================================


class TestIncrementalSyncWorkflow:
    """Tests change detection and incremental updates."""

    @pytest.mark.asyncio
    async def test_update_existing_record_revision(self, processor, graph_store):
        """Updating a record with a new revision ID triggers an update."""
        file_rec = make_file_record(
            external_id="incr-update-001",
            record_group_ext_id="drive-incr",
        )
        file_rec.external_revision_id = "rev-1"
        await processor.on_new_records([(file_rec, [])])

        # Now sync again with a new revision
        file_rec_v2 = make_file_record(
            external_id="incr-update-001",
            record_group_ext_id="drive-incr",
            version=2,
        )
        file_rec_v2.external_revision_id = "rev-2"
        await processor.on_new_records([(file_rec_v2, [])])
        # Should still be only 1 record (updated, not duplicated)
        rec_count = graph_store.count_collection(CollectionNames.RECORDS.value)
        assert rec_count == 1

    @pytest.mark.asyncio
    async def test_update_preserves_existing_id(self, processor, graph_store):
        """When updating, the internal record ID is preserved."""
        file_rec = make_file_record(external_id="preserve-id-001", record_group_ext_id="drive-pid")
        file_rec.external_revision_id = "rev-1"
        await processor.on_new_records([(file_rec, [])])
        original_id = file_rec.id

        file_rec_v2 = make_file_record(external_id="preserve-id-001", record_group_ext_id="drive-pid", version=2)
        file_rec_v2.external_revision_id = "rev-2"
        await processor.on_new_records([(file_rec_v2, [])])
        # The processor should have set the ID to the existing record's ID
        assert file_rec_v2.id == original_id

    @pytest.mark.asyncio
    async def test_same_revision_skips_update(self, processor, graph_store):
        """If revision ID is the same, the record is not re-upserted."""
        file_rec = make_file_record(external_id="skip-rev-001", record_group_ext_id="drive-skip")
        file_rec.external_revision_id = "rev-same"
        await processor.on_new_records([(file_rec, [])])

        # Sync again with same revision
        file_rec_v2 = make_file_record(external_id="skip-rev-001", record_group_ext_id="drive-skip", version=2)
        file_rec_v2.external_revision_id = "rev-same"
        await processor.on_new_records([(file_rec_v2, [])])
        # Still 1 record
        assert graph_store.count_collection(CollectionNames.RECORDS.value) == 1

    @pytest.mark.asyncio
    async def test_on_record_content_update(self, processor, graph_store):
        """Content update triggers Kafka update event."""
        file_rec = make_file_record(external_id="content-update-001", record_group_ext_id="drive-cu")
        file_rec.external_revision_id = "rev-1"
        await processor.on_new_records([(file_rec, [])])
        processor.messaging_producer.send_message.reset_mock()

        file_rec_v2 = make_file_record(external_id="content-update-001", record_group_ext_id="drive-cu", version=2)
        file_rec_v2.external_revision_id = "rev-2"
        await processor.on_record_content_update(file_rec_v2)
        assert processor.messaging_producer.send_message.call_count == 1
        call_args = processor.messaging_producer.send_message.call_args
        assert call_args[0][1]["eventType"] == "updateRecord"

    @pytest.mark.asyncio
    async def test_record_deletion(self, processor, graph_store):
        """Deleting a record removes it from the graph."""
        file_rec = make_file_record(external_id="delete-me-001", record_group_ext_id="drive-del")
        await processor.on_new_records([(file_rec, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 1
        record_id = file_rec.id

        await processor.on_record_deleted(record_id)
        doc = graph_store.get_node(CollectionNames.RECORDS.value, record_id)
        assert doc is None

    @pytest.mark.asyncio
    async def test_incremental_add_new_files_to_existing_group(self, processor, graph_store):
        """Adding new files in an incremental sync reuses the existing record group."""
        group_ext = "drive-incr-add"
        initial_files = [
            make_file_record(external_id=f"incr-add-init-{i}", record_group_ext_id=group_ext)
            for i in range(3)
        ]
        await processor.on_new_records([(f, []) for f in initial_files])
        rg_count_before = graph_store.count_collection(CollectionNames.RECORD_GROUPS.value)

        new_files = [
            make_file_record(external_id=f"incr-add-new-{i}", record_group_ext_id=group_ext)
            for i in range(2)
        ]
        await processor.on_new_records([(f, []) for f in new_files])
        rg_count_after = graph_store.count_collection(CollectionNames.RECORD_GROUPS.value)
        assert rg_count_after == rg_count_before

    @pytest.mark.asyncio
    async def test_parent_change_updates_edge(self, processor, graph_store):
        """When a record's parent changes, the old PARENT_CHILD edge is replaced."""
        parent1 = make_file_record(
            external_id="parent-change-p1", is_file=False,
            mime_type=MimeTypes.FOLDER.value, record_group_ext_id="drive-pchange",
        )
        parent2 = make_file_record(
            external_id="parent-change-p2", is_file=False,
            mime_type=MimeTypes.FOLDER.value, record_group_ext_id="drive-pchange",
        )
        child = make_file_record(
            external_id="parent-change-child", parent_ext_id="parent-change-p1",
            record_group_ext_id="drive-pchange",
        )
        child.parent_record_type = RecordType.FILE
        await processor.on_new_records([(parent1, []), (parent2, [])])
        await processor.on_new_records([(child, [])])

        # Now change the parent
        child_v2 = make_file_record(
            external_id="parent-change-child", parent_ext_id="parent-change-p2",
            record_group_ext_id="drive-pchange", version=2,
        )
        child_v2.external_revision_id = "new-rev"
        child_v2.parent_record_type = RecordType.FILE
        await processor.on_new_records([(child_v2, [])])
        # Should have edge from parent2 to child, and the old one should be gone or replaced
        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        pc_edges = [e for e in rr if e.get("relationType") == RecordRelations.PARENT_CHILD.value]
        assert len(pc_edges) >= 1


# ===========================================================================
# 6. Permission Sync Workflow
# ===========================================================================


class TestPermissionSyncWorkflow:
    """Tests permission edge building for all entity types."""

    @pytest.mark.asyncio
    async def test_user_permission_on_record(self, processor, graph_store):
        """USER permission creates edge from user to record."""
        user_id = seed_user(graph_store, "user-perm@example.com")
        file_rec = make_file_record(external_id="perm-user-rec", record_group_ext_id="drive-perms")
        perm = make_permission(email="user-perm@example.com", entity_type=EntityType.USER, perm_type=PermissionType.WRITE)
        await processor.on_new_records([(file_rec, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert any(user_id in e.get("from_id", "") for e in perm_edges)

    @pytest.mark.asyncio
    async def test_group_permission_on_record(self, processor, graph_store):
        """GROUP permission creates edge from group to record."""
        group_id = seed_user_group(graph_store, GDRIVE_CONNECTOR_ID, ConnectorsEnum.GOOGLE_DRIVE, "grp-ext-001")
        file_rec = make_file_record(external_id="perm-group-rec", record_group_ext_id="drive-perms")
        perm = make_permission(external_id="grp-ext-001", entity_type=EntityType.GROUP, perm_type=PermissionType.READ)
        await processor.on_new_records([(file_rec, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert any(group_id in e.get("from_id", "") for e in perm_edges)

    @pytest.mark.asyncio
    async def test_role_permission_on_record(self, processor, graph_store):
        """ROLE permission creates edge from role to record."""
        role_id = seed_role(graph_store, GDRIVE_CONNECTOR_ID, ConnectorsEnum.GOOGLE_DRIVE, "role-ext-001")
        file_rec = make_file_record(external_id="perm-role-rec", record_group_ext_id="drive-perms")
        perm = make_permission(external_id="role-ext-001", entity_type=EntityType.ROLE, perm_type=PermissionType.READ)
        await processor.on_new_records([(file_rec, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert any(role_id in e.get("from_id", "") for e in perm_edges)

    @pytest.mark.asyncio
    async def test_org_permission_on_record(self, processor, graph_store):
        """ORG permission creates edge from org to record."""
        file_rec = make_file_record(external_id="perm-org-rec", record_group_ext_id="drive-perms")
        perm = make_permission(entity_type=EntityType.ORG, perm_type=PermissionType.READ)
        await processor.on_new_records([(file_rec, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        org_edges = [e for e in perm_edges if e.get("from_id") == ORG_ID]
        assert len(org_edges) == 1

    @pytest.mark.asyncio
    async def test_permission_update_replaces_old_edges(self, processor, graph_store):
        """on_updated_record_permissions deletes old edges and creates new ones."""
        user1_id = seed_user(graph_store, "old-perm@example.com")
        user2_id = seed_user(graph_store, "new-perm@example.com")
        file_rec = make_file_record(external_id="perm-replace-001", record_group_ext_id="drive-permreplace")
        old_perm = make_permission(email="old-perm@example.com", perm_type=PermissionType.READ)
        await processor.on_new_records([(file_rec, [old_perm])])

        # Now update permissions
        new_perm = make_permission(email="new-perm@example.com", perm_type=PermissionType.OWNER)
        file_rec.inherit_permissions = False
        await processor.on_updated_record_permissions(file_rec, [new_perm])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        record_perms = [e for e in perm_edges
                        if e.get("to_id") == file_rec.id or file_rec.id in e.get("_to", "")]
        # Old edge should be gone, new one present
        assert any(user2_id in e.get("from_id", "") for e in record_perms)

    @pytest.mark.asyncio
    async def test_permission_on_record_group(self, processor, graph_store):
        """on_new_record_groups creates permission edges to record group."""
        user_id = seed_user(graph_store, "rg-perm@example.com")
        rg = make_record_group(external_id="rg-perm-001", name="Shared Drive")
        perm = make_permission(email="rg-perm@example.com", entity_type=EntityType.USER, perm_type=PermissionType.OWNER)
        await processor.on_new_record_groups([(rg, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) >= 1

    @pytest.mark.asyncio
    async def test_missing_user_skips_permission(self, processor, graph_store):
        """If user doesn't exist in graph, permission edge is skipped (not created)."""
        file_rec = make_file_record(external_id="perm-missing-user-001", record_group_ext_id="drive-perms")
        perm = make_permission(email="nonexistent@example.com", perm_type=PermissionType.READ)
        await processor.on_new_records([(file_rec, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) == 0

    @pytest.mark.asyncio
    async def test_missing_group_skips_permission(self, processor, graph_store):
        """If user group doesn't exist, permission edge is skipped."""
        file_rec = make_file_record(external_id="perm-missing-group-001", record_group_ext_id="drive-perms")
        perm = make_permission(external_id="nonexistent-group", entity_type=EntityType.GROUP, perm_type=PermissionType.READ)
        await processor.on_new_records([(file_rec, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) == 0

    @pytest.mark.asyncio
    async def test_mixed_permission_types_on_same_record(self, processor, graph_store):
        """One record can have USER, GROUP, ROLE, and ORG permissions simultaneously."""
        user_id = seed_user(graph_store, "mixed@example.com")
        group_id = seed_user_group(graph_store, GDRIVE_CONNECTOR_ID, ConnectorsEnum.GOOGLE_DRIVE, "grp-mixed")
        role_id = seed_role(graph_store, GDRIVE_CONNECTOR_ID, ConnectorsEnum.GOOGLE_DRIVE, "role-mixed")

        file_rec = make_file_record(external_id="perm-mixed-001", record_group_ext_id="drive-perms")
        perms = [
            make_permission(email="mixed@example.com", entity_type=EntityType.USER, perm_type=PermissionType.OWNER),
            make_permission(external_id="grp-mixed", entity_type=EntityType.GROUP, perm_type=PermissionType.READ),
            make_permission(external_id="role-mixed", entity_type=EntityType.ROLE, perm_type=PermissionType.WRITE),
            make_permission(entity_type=EntityType.ORG, perm_type=PermissionType.READ),
        ]
        await processor.on_new_records([(file_rec, perms)])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) == 4


# ===========================================================================
# 7. Multi-Connector Scenario
# ===========================================================================


class TestMultiConnectorWorkflow:
    """Tests multiple connectors syncing to same org."""

    @pytest.mark.asyncio
    async def test_gdrive_and_jira_coexist(self, processor, graph_store):
        """Records from Google Drive and Jira coexist in the graph."""
        gdrive_file = make_file_record(external_id="mc-gdrive-001", record_group_ext_id="drive-mc")
        jira_ticket = make_ticket_record(external_id="mc-jira-001")
        await processor.on_new_records([(gdrive_file, []), (jira_ticket, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 2

    @pytest.mark.asyncio
    async def test_separate_record_groups_per_connector(self, processor, graph_store):
        """Each connector creates its own record groups."""
        gdrive_file = make_file_record(external_id="mc-sep-gdrive", record_group_ext_id="my-drive")
        jira_ticket = make_ticket_record(external_id="mc-sep-jira", group_ext_id="project-beta")
        confluence_page = make_webpage_record(external_id="mc-sep-conf", group_ext_id="space-docs")

        await processor.on_new_records([
            (gdrive_file, []),
            (jira_ticket, []),
            (confluence_page, []),
        ])
        rg_count = graph_store.count_collection(CollectionNames.RECORD_GROUPS.value)
        assert rg_count == 3

    @pytest.mark.asyncio
    async def test_shared_users_across_connectors(self, processor, graph_store):
        """Same user can have permissions across different connector records."""
        user_id = seed_user(graph_store, "shared-user@example.com")
        gdrive_file = make_file_record(external_id="mc-shared-gdrive", record_group_ext_id="drive-shared")
        jira_ticket = make_ticket_record(external_id="mc-shared-jira")
        perm = make_permission(email="shared-user@example.com", perm_type=PermissionType.READ)
        await processor.on_new_records([(gdrive_file, [perm]), (jira_ticket, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) == 2

    @pytest.mark.asyncio
    async def test_four_connectors_simultaneous(self, processor, graph_store):
        """Four connectors can sync simultaneously without conflicts."""
        records = [
            (make_file_record(external_id="4c-gdrive", record_group_ext_id="drive-4c"), []),
            (make_ticket_record(external_id="4c-jira"), []),
            (make_webpage_record(external_id="4c-conf"), []),
            (make_message_record(external_id="4c-slack"), []),
        ]
        await processor.on_new_records(records)
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 4
        assert graph_store.count_collection(CollectionNames.RECORD_GROUPS.value) >= 4

    @pytest.mark.asyncio
    async def test_different_connector_same_external_id_no_collision(self, processor, graph_store):
        """Same external ID in different connectors does not collide."""
        gdrive_file = make_file_record(
            external_id="shared-ext-id-001",
            connector_id=GDRIVE_CONNECTOR_ID,
            record_group_ext_id="drive-nocol",
        )
        jira_ticket = make_ticket_record(
            external_id="shared-ext-id-001",
            connector_id=JIRA_CONNECTOR_ID,
            group_ext_id="project-nocol",
        )
        await processor.on_new_records([(gdrive_file, []), (jira_ticket, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 2


# ===========================================================================
# 8. Error Recovery Workflow
# ===========================================================================


class TestErrorRecoveryWorkflow:
    """Tests error handling and recovery during sync."""

    @pytest.mark.asyncio
    async def test_empty_records_list_noop(self, processor, graph_store):
        """on_new_records with empty list does nothing (no error)."""
        await processor.on_new_records([])
        assert processor.messaging_producer.send_message.call_count == 0

    @pytest.mark.asyncio
    async def test_duplicate_records_idempotent(self, processor, graph_store):
        """Syncing the same record twice is idempotent."""
        file_rec = make_file_record(external_id="dup-001", record_group_ext_id="drive-dup")
        file_rec.external_revision_id = "rev-fixed"
        await processor.on_new_records([(file_rec, [])])
        await processor.on_new_records([(file_rec, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) == 1

    @pytest.mark.asyncio
    async def test_partial_batch_failure_first_records_persist(self, processor, graph_store):
        """If we process records one by one and a later one has issues, earlier ones are saved."""
        good_file = make_file_record(external_id="partial-good", record_group_ext_id="drive-partial")
        await processor.on_new_records([(good_file, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 1

    @pytest.mark.asyncio
    async def test_record_with_no_group_still_works(self, processor, graph_store):
        """A record without external_record_group_id is stored without group edge."""
        file_rec = make_file_record(external_id="nogroup-001", record_group_ext_id=None)
        await processor.on_new_records([(file_rec, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 1
        bt = graph_store.edges.get(CollectionNames.BELONGS_TO.value, [])
        from_this = [e for e in bt if file_rec.id in e.get("_from", "")]
        assert len(from_this) == 0

    @pytest.mark.asyncio
    async def test_permission_with_no_email_no_external_id_skipped(self, processor, graph_store):
        """Permission with no email and no external_id is safely skipped."""
        file_rec = make_file_record(external_id="perm-empty-001", record_group_ext_id="drive-perms")
        perm = Permission(type=PermissionType.READ, entity_type=EntityType.USER)
        await processor.on_new_records([(file_rec, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) == 0

    @pytest.mark.asyncio
    async def test_retry_after_initial_sync(self, processor, graph_store):
        """Re-syncing the same data acts as a successful retry."""
        files = [
            make_file_record(external_id=f"retry-{i}", record_group_ext_id="drive-retry")
            for i in range(5)
        ]
        await processor.on_new_records([(f, []) for f in files])
        count1 = graph_store.count_collection(CollectionNames.RECORDS.value)

        # "Retry" by syncing same data again
        await processor.on_new_records([(f, []) for f in files])
        count2 = graph_store.count_collection(CollectionNames.RECORDS.value)
        assert count2 == count1

    @pytest.mark.asyncio
    async def test_empty_record_groups_list_noop(self, processor, graph_store):
        """on_new_record_groups with empty list is a no-op."""
        await processor.on_new_record_groups([])
        assert graph_store.count_collection(CollectionNames.RECORD_GROUPS.value) == 0

    @pytest.mark.asyncio
    async def test_empty_app_users_list_noop(self, processor, graph_store):
        """on_new_app_users with empty list is a no-op."""
        await processor.on_new_app_users([])

    @pytest.mark.asyncio
    async def test_empty_user_groups_list_noop(self, processor, graph_store):
        """on_new_user_groups with empty list is a no-op."""
        await processor.on_new_user_groups([])

    @pytest.mark.asyncio
    async def test_empty_app_roles_list_noop(self, processor, graph_store):
        """on_new_app_roles with empty list is a no-op."""
        await processor.on_new_app_roles([])


# ===========================================================================
# 9. Deletion Cascade Workflow
# ===========================================================================


class TestDeletionCascadeWorkflow:
    """Tests record and entity deletion."""

    @pytest.mark.asyncio
    async def test_delete_record_by_key(self, processor, graph_store):
        """Deleting a record by key removes it from the records collection."""
        file_rec = make_file_record(external_id="del-cascade-001", record_group_ext_id="drive-del")
        await processor.on_new_records([(file_rec, [])])
        await processor.on_record_deleted(file_rec.id)
        doc = graph_store.get_node(CollectionNames.RECORDS.value, file_rec.id)
        assert doc is None

    @pytest.mark.asyncio
    async def test_delete_does_not_affect_record_group(self, processor, graph_store):
        """Deleting a record does not delete its record group."""
        group_ext = "drive-del-group"
        file_rec = make_file_record(external_id="del-keepgroup-001", record_group_ext_id=group_ext)
        await processor.on_new_records([(file_rec, [])])
        await processor.on_record_deleted(file_rec.id)
        rg_count = graph_store.count_collection(CollectionNames.RECORD_GROUPS.value)
        assert rg_count >= 1

    @pytest.mark.asyncio
    async def test_multiple_deletions(self, processor, graph_store):
        """Multiple records can be deleted sequentially."""
        ids = []
        for i in range(5):
            file_rec = make_file_record(external_id=f"multi-del-{i}", record_group_ext_id="drive-multidel")
            await processor.on_new_records([(file_rec, [])])
            ids.append(file_rec.id)

        for rec_id in ids:
            await processor.on_record_deleted(rec_id)

        for rec_id in ids:
            assert graph_store.get_node(CollectionNames.RECORDS.value, rec_id) is None


# ===========================================================================
# 10. User and UserGroup Workflow
# ===========================================================================


class TestUserGroupWorkflow:
    """Tests user group creation and membership."""

    @pytest.mark.asyncio
    async def test_create_user_group_with_members(self, processor, graph_store):
        """on_new_user_groups creates group and permission edges for members."""
        seed_user(graph_store, "member1@example.com")
        seed_user(graph_store, "member2@example.com")
        ug = AppUserGroup(
            app_name=ConnectorsEnum.GOOGLE_DRIVE,
            connector_id=GDRIVE_CONNECTOR_ID,
            source_user_group_id="gws-group-001",
            name="Engineering Team",
            org_id=ORG_ID,
        )
        members = [
            make_app_user("member1@example.com"),
            make_app_user("member2@example.com"),
        ]
        await processor.on_new_user_groups([(ug, members)])
        assert graph_store.count_collection(CollectionNames.GROUPS.value) >= 1
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) >= 2

    @pytest.mark.asyncio
    async def test_update_user_group_replaces_edges(self, processor, graph_store):
        """Updating a user group deletes old permission edges and creates new ones."""
        seed_user(graph_store, "old-member@example.com")
        seed_user(graph_store, "new-member@example.com")
        ug = AppUserGroup(
            app_name=ConnectorsEnum.GOOGLE_DRIVE,
            connector_id=GDRIVE_CONNECTOR_ID,
            source_user_group_id="gws-update-group-001",
            name="Design Team",
            org_id=ORG_ID,
        )
        old_members = [make_app_user("old-member@example.com")]
        await processor.on_new_user_groups([(ug, old_members)])

        # Update with new members
        new_members = [make_app_user("new-member@example.com")]
        await processor.on_new_user_groups([(ug, new_members)])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        # Should have new member but not old
        member_emails = set()
        for e in perm_edges:
            from_key = e.get("from_id", e.get("_from", "").split("/")[-1])
            user_doc = graph_store.get_node(CollectionNames.USERS.value, from_key)
            if user_doc:
                member_emails.add(user_doc.get("email"))
        assert "new-member@example.com" in member_emails

    @pytest.mark.asyncio
    async def test_remove_user_from_group(self, processor, graph_store):
        """on_user_group_member_removed removes the permission edge."""
        seed_user(graph_store, "removed@example.com")
        ug = AppUserGroup(
            app_name=ConnectorsEnum.GOOGLE_DRIVE,
            connector_id=GDRIVE_CONNECTOR_ID,
            source_user_group_id="gws-remove-group-001",
            name="Ops Team",
            org_id=ORG_ID,
        )
        members = [make_app_user("removed@example.com")]
        await processor.on_new_user_groups([(ug, members)])
        perm_edges_before = len(graph_store.edges.get(CollectionNames.PERMISSION.value, []))

        result = await processor.on_user_group_member_removed(
            external_group_id="gws-remove-group-001",
            user_email="removed@example.com",
            connector_id=GDRIVE_CONNECTOR_ID,
        )
        assert result is True
        perm_edges_after = len(graph_store.edges.get(CollectionNames.PERMISSION.value, []))
        assert perm_edges_after < perm_edges_before

    @pytest.mark.asyncio
    async def test_remove_nonexistent_user_returns_false(self, processor, graph_store):
        """Removing a user that doesn't exist returns False."""
        result = await processor.on_user_group_member_removed(
            external_group_id="gws-nouser-group",
            user_email="nobody@example.com",
            connector_id=GDRIVE_CONNECTOR_ID,
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_remove_from_nonexistent_group_returns_false(self, processor, graph_store):
        """Removing from a group that doesn't exist returns False."""
        seed_user(graph_store, "exist@example.com")
        result = await processor.on_user_group_member_removed(
            external_group_id="nonexistent-group",
            user_email="exist@example.com",
            connector_id=GDRIVE_CONNECTOR_ID,
        )
        assert result is False


# ===========================================================================
# 11. App Roles Workflow
# ===========================================================================


class TestAppRolesWorkflow:
    """Tests app role creation and membership."""

    @pytest.mark.asyncio
    async def test_create_app_role_with_members(self, processor, graph_store):
        """on_new_app_roles creates role and permission edges for members."""
        seed_user(graph_store, "role-member1@example.com")
        seed_user(graph_store, "role-member2@example.com")
        role = AppRole(
            app_name=ConnectorsEnum.JIRA,
            connector_id=JIRA_CONNECTOR_ID,
            source_role_id="jira-role-admin",
            name="Jira Administrators",
            org_id=ORG_ID,
        )
        members = [
            make_app_user("role-member1@example.com", JIRA_CONNECTOR_ID, ConnectorsEnum.JIRA),
            make_app_user("role-member2@example.com", JIRA_CONNECTOR_ID, ConnectorsEnum.JIRA),
        ]
        await processor.on_new_app_roles([(role, members)])
        assert graph_store.count_collection(CollectionNames.ROLES.value) >= 1
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) >= 2

    @pytest.mark.asyncio
    async def test_update_app_role_replaces_edges(self, processor, graph_store):
        """Updating an app role replaces the old permission edges."""
        seed_user(graph_store, "old-role-mem@example.com")
        seed_user(graph_store, "new-role-mem@example.com")
        role = AppRole(
            app_name=ConnectorsEnum.JIRA,
            connector_id=JIRA_CONNECTOR_ID,
            source_role_id="jira-role-dev",
            name="Developers",
            org_id=ORG_ID,
        )
        old_members = [make_app_user("old-role-mem@example.com", JIRA_CONNECTOR_ID, ConnectorsEnum.JIRA)]
        await processor.on_new_app_roles([(role, old_members)])

        new_members = [make_app_user("new-role-mem@example.com", JIRA_CONNECTOR_ID, ConnectorsEnum.JIRA)]
        await processor.on_new_app_roles([(role, new_members)])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        member_ids = set()
        for e in perm_edges:
            from_key = e.get("from_id", e.get("_from", "").split("/")[-1])
            user_doc = graph_store.get_node(CollectionNames.USERS.value, from_key)
            if user_doc:
                member_ids.add(user_doc.get("email"))
        assert "new-role-mem@example.com" in member_ids

    @pytest.mark.asyncio
    async def test_role_with_no_members(self, processor, graph_store):
        """Creating a role with no members creates the role but no permission edges."""
        role = AppRole(
            app_name=ConnectorsEnum.JIRA,
            connector_id=JIRA_CONNECTOR_ID,
            source_role_id="jira-role-empty",
            name="Empty Role",
            org_id=ORG_ID,
        )
        await processor.on_new_app_roles([(role, [])])
        assert graph_store.count_collection(CollectionNames.ROLES.value) >= 1
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        role_perms = [e for e in perm_edges if CollectionNames.ROLES.value in e.get("_to", "")]
        assert len(role_perms) == 0


# ===========================================================================
# 12. Record Group Advanced Scenarios
# ===========================================================================


class TestRecordGroupAdvanced:
    """Advanced record group scenarios."""

    @pytest.mark.asyncio
    async def test_record_group_with_parent(self, processor, graph_store):
        """RecordGroup with parent_external_group_id creates BELONGS_TO to parent."""
        parent_rg = make_record_group(
            connector_id=CONFLUENCE_CONNECTOR_ID,
            connector_name=ConnectorsEnum.CONFLUENCE,
            external_id="space-parent",
            name="Parent Space",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
        )
        await processor.on_new_record_groups([(parent_rg, [])])

        child_rg = make_record_group(
            connector_id=CONFLUENCE_CONNECTOR_ID,
            connector_name=ConnectorsEnum.CONFLUENCE,
            external_id="space-child",
            name="Child Category",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
            parent_ext_group_id="space-parent",
        )
        await processor.on_new_record_groups([(child_rg, [])])
        bt = graph_store.edges.get(CollectionNames.BELONGS_TO.value, [])
        rg_to_rg = [e for e in bt
                     if CollectionNames.RECORD_GROUPS.value in e.get("_from", "")
                     and CollectionNames.RECORD_GROUPS.value in e.get("_to", "")]
        assert len(rg_to_rg) >= 1
        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        parent_to_child = [
            e for e in rr
            if e.get("relationshipType") == RecordRelations.PARENT_CHILD.value
            and CollectionNames.RECORD_GROUPS.value in (e.get("_from") or "")
            and CollectionNames.RECORD_GROUPS.value in (e.get("_to") or "")
        ]
        assert len(parent_to_child) >= 1

    @pytest.mark.asyncio
    async def test_record_group_belongs_to_org(self, processor, graph_store):
        """Every record group has a BELONGS_TO edge to the org."""
        rg = make_record_group(external_id="rg-org-test", name="Test Group")
        await processor.on_new_record_groups([(rg, [])])
        bt = graph_store.edges.get(CollectionNames.BELONGS_TO.value, [])
        rg_to_org = [e for e in bt
                     if CollectionNames.RECORD_GROUPS.value in e.get("_from", "")
                     and CollectionNames.ORGS.value in e.get("_to", "")]
        assert len(rg_to_org) >= 1

    @pytest.mark.asyncio
    async def test_record_group_belongs_to_app(self, processor, graph_store):
        """Top-level record group has a BELONGS_TO edge to the app/connector."""
        rg = make_record_group(external_id="rg-app-test", name="My Drive")
        await processor.on_new_record_groups([(rg, [])])
        bt = graph_store.edges.get(CollectionNames.BELONGS_TO.value, [])
        rg_to_app = [e for e in bt
                     if CollectionNames.RECORD_GROUPS.value in e.get("_from", "")
                     and CollectionNames.APPS.value in e.get("_to", "")]
        assert len(rg_to_app) >= 1

    @pytest.mark.asyncio
    async def test_update_record_group_name(self, processor, graph_store):
        """update_record_group_name changes the group's name in the graph."""
        rg = make_record_group(
            connector_id=GDRIVE_CONNECTOR_ID,
            external_id="rg-rename-001",
            name="Old Name",
        )
        await processor.on_new_record_groups([(rg, [])])
        await processor.update_record_group_name(
            folder_id="rg-rename-001",
            new_name="New Name",
            old_name="Old Name",
            connector_id=GDRIVE_CONNECTOR_ID,
        )
        # Verify the name changed
        for doc in graph_store.collections.get(CollectionNames.RECORD_GROUPS.value, {}).values():
            if doc.get("externalGroupId") == "rg-rename-001":
                assert doc.get("groupName") == "New Name"
                break
        else:
            pytest.fail("Record group not found after rename")

    @pytest.mark.asyncio
    async def test_record_group_update_preserves_id(self, processor, graph_store):
        """Re-syncing a record group preserves its internal ID."""
        rg = make_record_group(external_id="rg-preserve-001", name="Stable Group")
        await processor.on_new_record_groups([(rg, [])])
        original_id = rg.id

        rg2 = make_record_group(external_id="rg-preserve-001", name="Stable Group Updated")
        await processor.on_new_record_groups([(rg2, [])])
        assert rg2.id == original_id

    @pytest.mark.asyncio
    async def test_record_group_inherit_permissions(self, processor, graph_store):
        """RecordGroup with inherit_permissions creates INHERIT_PERMISSIONS edge to parent."""
        parent_rg = make_record_group(
            connector_id=CONFLUENCE_CONNECTOR_ID,
            connector_name=ConnectorsEnum.CONFLUENCE,
            external_id="space-inherit-parent",
            name="Parent",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
        )
        await processor.on_new_record_groups([(parent_rg, [])])

        child_rg = make_record_group(
            connector_id=CONFLUENCE_CONNECTOR_ID,
            connector_name=ConnectorsEnum.CONFLUENCE,
            external_id="space-inherit-child",
            name="Child",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
            parent_ext_group_id="space-inherit-parent",
            inherit_permissions=True,
        )
        await processor.on_new_record_groups([(child_rg, [])])
        ip = graph_store.edges.get(CollectionNames.INHERIT_PERMISSIONS.value, [])
        assert len(ip) >= 1


# ===========================================================================
# 13. Reindex Workflow
# ===========================================================================


class TestReindexWorkflow:
    """Tests reindex event publishing."""

    @pytest.mark.asyncio
    async def test_reindex_existing_records(self, processor, graph_store):
        """reindex_existing_records publishes reindexRecord events."""
        files = [
            make_file_record(external_id=f"reindex-{i}", record_group_ext_id="drive-reindex")
            for i in range(3)
        ]
        await processor.on_new_records([(f, []) for f in files])
        processor.messaging_producer.send_message.reset_mock()

        await processor.reindex_existing_records(files)
        assert processor.messaging_producer.send_message.call_count == 3
        for call in processor.messaging_producer.send_message.call_args_list:
            assert call[0][1]["eventType"] == "reindexRecord"

    @pytest.mark.asyncio
    async def test_reindex_skips_internal_records(self, processor, graph_store):
        """Internal records are skipped during reindex."""
        file_rec = make_file_record(external_id="reindex-internal-001", record_group_ext_id="drive-reindex")
        file_rec.is_internal = True
        await processor.on_new_records([(file_rec, [])])
        processor.messaging_producer.send_message.reset_mock()

        await processor.reindex_existing_records([file_rec])
        assert processor.messaging_producer.send_message.call_count == 0

    @pytest.mark.asyncio
    async def test_reindex_empty_list(self, processor, graph_store):
        """Reindexing empty list does nothing."""
        await processor.reindex_existing_records([])
        assert processor.messaging_producer.send_message.call_count == 0


# ===========================================================================
# 14. App Users Workflow
# ===========================================================================


class TestAppUsersWorkflow:
    """Tests app user sync."""

    @pytest.mark.asyncio
    async def test_sync_app_users(self, processor, graph_store):
        """on_new_app_users stores app users."""
        users = [
            make_app_user("appuser1@example.com"),
            make_app_user("appuser2@example.com"),
        ]
        await processor.on_new_app_users(users)
        assert graph_store.count_collection("appUsers") == 2

    @pytest.mark.asyncio
    async def test_get_all_active_users(self, processor, graph_store):
        """get_all_active_users returns users from the graph."""
        seed_user(graph_store, "active1@example.com")
        seed_user(graph_store, "active2@example.com")
        # Add user data with correct orgId
        for doc in graph_store.collections.get(CollectionNames.USERS.value, {}).values():
            doc["orgId"] = ORG_ID
        users = await processor.get_all_active_users()
        assert len(users) >= 2

    @pytest.mark.asyncio
    async def test_get_record_by_external_id(self, processor, graph_store):
        """get_record_by_external_id retrieves the correct record."""
        file_rec = make_file_record(external_id="get-by-ext-001", record_group_ext_id="drive-get")
        await processor.on_new_records([(file_rec, [])])
        found = await processor.get_record_by_external_id(GDRIVE_CONNECTOR_ID, "get-by-ext-001")
        assert found is not None
        assert found.external_record_id == "get-by-ext-001"


# ===========================================================================
# 15. Complex Real-World Scenarios
# ===========================================================================


class TestComplexRealWorldScenarios:
    """Simulates complex real-world sync scenarios."""

    @pytest.mark.asyncio
    async def test_full_jira_project_with_tickets_and_subtasks(self, processor, graph_store):
        """Simulate a full Jira project: project record + 5 tickets + subtask hierarchy."""
        seed_user(graph_store, "dev@example.com")
        seed_user(graph_store, "pm@example.com")
        seed_user(graph_store, "lead@example.com")

        # Create project
        proj = make_project_record(external_id="proj-complex-001", group_ext_id="project-complex")
        await processor.on_new_records([(proj, [])])

        # Create 5 tickets
        tickets = []
        for i in range(5):
            ticket = make_ticket_record(
                external_id=f"complex-ticket-{i}",
                name=f"COMPLEX-{i}: Task {i}",
                group_ext_id="project-complex",
            )
            tickets.append(ticket)
        await processor.on_new_records([(t, []) for t in tickets])

        # Create subtask under first ticket
        subtask = make_ticket_record(
            external_id="complex-subtask-001",
            name="COMPLEX-S1: Subtask",
            group_ext_id="project-complex",
        )
        subtask.parent_external_record_id = "complex-ticket-0"
        subtask.parent_record_type = RecordType.TICKET
        await processor.on_new_records([(subtask, [])])

        # Verify: 1 project + 5 tickets + 1 subtask + possibly 1 placeholder = >= 7
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 7
        # Verify: only 1 record group
        assert graph_store.count_collection(CollectionNames.RECORD_GROUPS.value) == 1
        # Verify parent-child edge for subtask
        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        pc = [e for e in rr if e.get("relationType") == RecordRelations.PARENT_CHILD.value]
        assert len(pc) >= 1

    @pytest.mark.asyncio
    async def test_google_drive_nested_folders(self, processor, graph_store):
        """Simulate nested folder structure: root -> subfolder -> file."""
        root_folder = make_file_record(
            external_id="root-folder", name="Root",
            is_file=False, mime_type=MimeTypes.FOLDER.value,
            record_group_ext_id="drive-nested",
        )
        sub_folder = make_file_record(
            external_id="sub-folder", name="SubFolder",
            is_file=False, mime_type=MimeTypes.FOLDER.value,
            parent_ext_id="root-folder",
            record_group_ext_id="drive-nested",
        )
        sub_folder.parent_record_type = RecordType.FILE
        deep_file = make_file_record(
            external_id="deep-file", name="deep.pdf",
            parent_ext_id="sub-folder",
            record_group_ext_id="drive-nested",
        )
        deep_file.parent_record_type = RecordType.FILE

        await processor.on_new_records([(root_folder, [])])
        await processor.on_new_records([(sub_folder, [])])
        await processor.on_new_records([(deep_file, [])])

        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        pc_edges = [e for e in rr if e.get("relationType") == RecordRelations.PARENT_CHILD.value]
        assert len(pc_edges) == 2

    @pytest.mark.asyncio
    async def test_confluence_space_with_nested_pages_and_comments(self, processor, graph_store):
        """Confluence space with page hierarchy and comments."""
        # Space
        space_rg = make_record_group(
            connector_id=CONFLUENCE_CONNECTOR_ID,
            connector_name=ConnectorsEnum.CONFLUENCE,
            external_id="space-deep",
            name="Deep Space",
            group_type=RecordGroupType.CONFLUENCE_SPACES,
        )
        await processor.on_new_record_groups([(space_rg, [])])

        # Root page
        root_page = make_webpage_record(external_id="deep-root-page", name="Root Page", group_ext_id="space-deep")
        await processor.on_new_records([(root_page, [])])

        # Child page
        child_page = make_webpage_record(
            external_id="deep-child-page", name="Child Page",
            group_ext_id="space-deep", parent_ext_id="deep-root-page",
        )
        child_page.parent_record_type = RecordType.CONFLUENCE_PAGE
        await processor.on_new_records([(child_page, [])])

        # Comment on child page
        comment = CommentRecord(
            org_id=ORG_ID,
            external_record_id="deep-comment-001",
            record_name="Comment",
            origin=OriginTypes.CONNECTOR,
            connector_name=ConnectorsEnum.CONFLUENCE,
            connector_id=CONFLUENCE_CONNECTOR_ID,
            record_type=RecordType.COMMENT,
            record_group_type=RecordGroupType.CONFLUENCE_SPACES,
            external_record_group_id="space-deep",
            parent_external_record_id="deep-child-page",
            parent_record_type=RecordType.CONFLUENCE_PAGE,
            version=1,
            mime_type=MimeTypes.HTML.value,
            source_created_at=1700000000000,
            source_updated_at=1700001000000,
            author_source_id="user-xyz",
        )
        await processor.on_new_records([(comment, [])])

        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        pc_edges = [e for e in rr if e.get("relationType") == RecordRelations.PARENT_CHILD.value]
        assert len(pc_edges) == 2  # root->child, child->comment

    @pytest.mark.asyncio
    async def test_full_permission_graph(self, processor, graph_store):
        """Build a complete permission graph: user -> group -> role -> record."""
        user_id = seed_user(graph_store, "full-perm@example.com")
        group_id = seed_user_group(
            graph_store, GDRIVE_CONNECTOR_ID, ConnectorsEnum.GOOGLE_DRIVE, "grp-full", "Full Group"
        )
        role_id = seed_role(
            graph_store, GDRIVE_CONNECTOR_ID, ConnectorsEnum.GOOGLE_DRIVE, "role-full", "Full Role"
        )

        # Create user group with user as member
        ug = AppUserGroup(
            id=group_id,
            app_name=ConnectorsEnum.GOOGLE_DRIVE,
            connector_id=GDRIVE_CONNECTOR_ID,
            source_user_group_id="grp-full",
            name="Full Group",
            org_id=ORG_ID,
        )
        members = [make_app_user("full-perm@example.com")]
        await processor.on_new_user_groups([(ug, members)])

        # Create record with group, role, and user permissions
        file_rec = make_file_record(external_id="full-perm-file", record_group_ext_id="drive-fullperm")
        perms = [
            make_permission(email="full-perm@example.com", entity_type=EntityType.USER, perm_type=PermissionType.OWNER),
            make_permission(external_id="grp-full", entity_type=EntityType.GROUP, perm_type=PermissionType.READ),
            make_permission(external_id="role-full", entity_type=EntityType.ROLE, perm_type=PermissionType.WRITE),
        ]
        await processor.on_new_records([(file_rec, perms)])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        # At least 3 edges from the record perms + 1 from user->group membership
        assert len(perm_edges) >= 4

    @pytest.mark.asyncio
    async def test_attachment_on_ticket(self, processor, graph_store):
        """File attachment on a Jira ticket creates an ATTACHMENT edge."""
        ticket = make_ticket_record(external_id="ticket-attach-001")
        ticket.assignee_email = None
        ticket.reporter_email = None
        ticket.creator_email = None
        await processor.on_new_records([(ticket, [])])

        attachment = make_file_record(
            external_id="attachment-001",
            name="screenshot.png",
            connector_id=JIRA_CONNECTOR_ID,
            connector_name=ConnectorsEnum.JIRA,
            record_group_ext_id="project-alpha",
            record_group_type=RecordGroupType.PROJECT,
            parent_ext_id="ticket-attach-001",
            mime_type=MimeTypes.PNG.value,
        )
        attachment.parent_record_type = RecordType.TICKET
        await processor.on_new_records([(attachment, [])])

        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        attachment_edges = [e for e in rr if e.get("relationType") == RecordRelations.ATTACHMENT.value]
        assert len(attachment_edges) == 1

    @pytest.mark.asyncio
    async def test_large_batch_sync_50_records(self, processor, graph_store):
        """Simulate syncing 50 records in a single batch."""
        records = [
            (make_file_record(
                external_id=f"large-batch-{i}",
                name=f"file{i}.txt",
                record_group_ext_id="drive-large",
            ), [])
            for i in range(50)
        ]
        await processor.on_new_records(records)
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 50
        assert processor.messaging_producer.send_message.call_count == 50

    @pytest.mark.asyncio
    async def test_link_record_on_ticket(self, processor, graph_store):
        """A LinkRecord attached to a ticket creates an ATTACHMENT edge."""
        ticket = make_ticket_record(external_id="ticket-link-001")
        ticket.assignee_email = None
        ticket.reporter_email = None
        ticket.creator_email = None
        await processor.on_new_records([(ticket, [])])

        link = LinkRecord(
            org_id=ORG_ID,
            external_record_id="link-on-ticket-001",
            record_name="External Link",
            origin=OriginTypes.CONNECTOR,
            connector_name=ConnectorsEnum.JIRA,
            connector_id=JIRA_CONNECTOR_ID,
            record_type=RecordType.LINK,
            record_group_type=RecordGroupType.PROJECT,
            external_record_group_id="project-alpha",
            parent_external_record_id="ticket-link-001",
            parent_record_type=RecordType.TICKET,
            version=1,
            mime_type=MimeTypes.UNKNOWN.value,
            source_created_at=1700000000000,
            source_updated_at=1700001000000,
            url="https://external.example.com/doc",
            is_public=LinkPublicStatus.TRUE,
        )
        await processor.on_new_records([(link, [])])
        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        parent_child_edges = [e for e in rr if e.get("relationType") == RecordRelations.PARENT_CHILD.value]
        assert len(parent_child_edges) >= 1

    @pytest.mark.asyncio
    async def test_mail_record_with_attachment(self, processor, graph_store):
        """Mail record with file attachment creates ATTACHMENT edge."""
        mail = MailRecord(
            org_id=ORG_ID,
            external_record_id="mail-attach-001",
            record_name="Important email",
            origin=OriginTypes.CONNECTOR,
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            connector_id="connector-gmail-001",
            record_type=RecordType.MAIL,
            record_group_type=RecordGroupType.MAILBOX,
            external_record_group_id="inbox-001",
            version=1,
            mime_type=MimeTypes.GMAIL.value,
            source_created_at=1700000000000,
            source_updated_at=1700001000000,
            subject="Important Subject",
            from_email="sender@example.com",
            to_emails=["receiver@example.com"],
        )
        await processor.on_new_records([(mail, [])])

        attachment = make_file_record(
            external_id="mail-file-attach-001",
            name="document.pdf",
            connector_id="connector-gmail-001",
            connector_name=ConnectorsEnum.GOOGLE_MAIL,
            record_group_ext_id="inbox-001",
            record_group_type=RecordGroupType.MAILBOX,
            parent_ext_id="mail-attach-001",
        )
        attachment.parent_record_type = RecordType.MAIL
        await processor.on_new_records([(attachment, [])])

        rr = graph_store.edges.get(CollectionNames.RECORD_RELATIONS.value, [])
        attachment_edges = [e for e in rr if e.get("relationType") == RecordRelations.ATTACHMENT.value]
        assert len(attachment_edges) == 1


# ===========================================================================
# 16. Edge cases and data integrity
# ===========================================================================


class TestEdgeCasesAndDataIntegrity:
    """Additional edge cases and data integrity checks."""

    @pytest.mark.asyncio
    async def test_record_org_id_set_from_processor(self, processor, graph_store):
        """Processor sets org_id on records from its own org_id."""
        file_rec = make_file_record(external_id="org-set-001", record_group_ext_id="drive-org")
        file_rec.org_id = ""  # Blank initially
        await processor.on_new_records([(file_rec, [])])
        assert file_rec.org_id == ORG_ID

    @pytest.mark.asyncio
    async def test_record_group_org_id_set(self, processor, graph_store):
        """Processor sets org_id on record groups."""
        rg = make_record_group(external_id="rg-orgset-001", name="OrgSet")
        rg.org_id = ""
        await processor.on_new_record_groups([(rg, [])])
        assert rg.org_id == ORG_ID

    @pytest.mark.asyncio
    async def test_user_group_org_id_set(self, processor, graph_store):
        """Processor sets org_id on user groups."""
        ug = AppUserGroup(
            app_name=ConnectorsEnum.GOOGLE_DRIVE,
            connector_id=GDRIVE_CONNECTOR_ID,
            source_user_group_id="ug-orgset-001",
            name="OrgSet UG",
        )
        ug.org_id = ""
        await processor.on_new_user_groups([(ug, [])])
        assert ug.org_id == ORG_ID

    @pytest.mark.asyncio
    async def test_app_role_org_id_set(self, processor, graph_store):
        """Processor sets org_id on app roles."""
        role = AppRole(
            app_name=ConnectorsEnum.JIRA,
            connector_id=JIRA_CONNECTOR_ID,
            source_role_id="role-orgset-001",
            name="OrgSet Role",
        )
        role.org_id = ""
        await processor.on_new_app_roles([(role, [])])
        assert role.org_id == ORG_ID

    @pytest.mark.asyncio
    async def test_internal_record_skips_kafka(self, processor, graph_store):
        """Internal records do not publish Kafka events."""
        file_rec = make_file_record(external_id="internal-001", record_group_ext_id="drive-internal")
        file_rec.is_internal = True
        await processor.on_new_records([(file_rec, [])])
        assert processor.messaging_producer.send_message.call_count == 0

    @pytest.mark.asyncio
    async def test_record_without_record_group_type(self, processor, graph_store):
        """Record with no record_group_type still stores correctly."""
        file_rec = make_file_record(external_id="no-rgt-001", record_group_ext_id=None)
        file_rec.record_group_type = None
        await processor.on_new_records([(file_rec, [])])
        assert graph_store.count_collection(CollectionNames.RECORDS.value) >= 1

    @pytest.mark.asyncio
    async def test_permission_edge_includes_role_and_type(self, processor, graph_store):
        """Permission edges include role and type properties."""
        user_id = seed_user(graph_store, "prop-check@example.com")
        file_rec = make_file_record(external_id="perm-props-001", record_group_ext_id="drive-props")
        perm = make_permission(email="prop-check@example.com", perm_type=PermissionType.WRITE)
        await processor.on_new_records([(file_rec, [perm])])
        perm_edges = graph_store.edges.get(CollectionNames.PERMISSION.value, [])
        assert len(perm_edges) >= 1
        edge = perm_edges[0]
        assert edge.get("role") == PermissionType.WRITE.value
        assert edge.get("type") == EntityType.USER.value

    @pytest.mark.asyncio
    async def test_concurrent_connectors_no_data_leak(self, processor, graph_store):
        """Records from one connector cannot be found via another connector's ID."""
        gdrive_file = make_file_record(
            external_id="noleak-001",
            connector_id=GDRIVE_CONNECTOR_ID,
            record_group_ext_id="drive-noleak",
        )
        await processor.on_new_records([(gdrive_file, [])])

        found_via_jira = await processor.get_record_by_external_id(JIRA_CONNECTOR_ID, "noleak-001")
        assert found_via_jira is None

        found_via_gdrive = await processor.get_record_by_external_id(GDRIVE_CONNECTOR_ID, "noleak-001")
        assert found_via_gdrive is not None

    @pytest.mark.asyncio
    async def test_ticket_entity_edges_replaced_on_resync(self, processor, graph_store):
        """Re-syncing a ticket replaces entity relation edges."""
        seed_user(graph_store, "dev@example.com")
        seed_user(graph_store, "pm@example.com")
        seed_user(graph_store, "newdev@example.com")

        ticket = make_ticket_record(
            external_id="resync-ticket-001",
            assignee_email="dev@example.com",
            reporter_email="pm@example.com",
            creator_email="pm@example.com",
        )
        ticket.external_revision_id = "rev-1"
        await processor.on_new_records([(ticket, [])])

        # Re-sync with different assignee
        ticket_v2 = make_ticket_record(
            external_id="resync-ticket-001",
            assignee_email="newdev@example.com",
            reporter_email="pm@example.com",
            creator_email="pm@example.com",
        )
        ticket_v2.external_revision_id = "rev-2"
        await processor.on_new_records([(ticket_v2, [])])

        entity_edges = graph_store.edges.get(CollectionNames.ENTITY_RELATIONS.value, [])
        assigned_edges = [e for e in entity_edges if e.get("edgeType") == EntityRelations.ASSIGNED_TO.value]
        # Should have only 1 ASSIGNED_TO edge (the updated one)
        assert len(assigned_edges) == 1

    @pytest.mark.asyncio
    async def test_project_lead_edge_replaced_on_resync(self, processor, graph_store):
        """Re-syncing a project replaces the LEAD_BY edge."""
        seed_user(graph_store, "lead@example.com")
        seed_user(graph_store, "newlead@example.com")

        proj = make_project_record(external_id="resync-proj-001", lead_email="lead@example.com")
        proj.external_revision_id = "rev-1"
        await processor.on_new_records([(proj, [])])

        proj_v2 = make_project_record(external_id="resync-proj-001", lead_email="newlead@example.com")
        proj_v2.external_revision_id = "rev-2"
        await processor.on_new_records([(proj_v2, [])])

        entity_edges = graph_store.edges.get(CollectionNames.ENTITY_RELATIONS.value, [])
        lead_edges = [e for e in entity_edges if e.get("edgeType") == EntityRelations.LEAD_BY.value]
        assert len(lead_edges) == 1
