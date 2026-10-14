"""The CTO's soft-delete checklist, end to end, against a real Neo4j and a real ArangoDB.

Each test drives the production code over a real provider and checks what a
user would see, through the same reads search, chat, the record routes and the
listings use:

- Every connector's own delete call puts the record in the trash as a CONNECTOR
  delete, with its node, type doc and edges kept, and asks indexing to remove
  only its vectors.
- A user's delete through the record DELETE route hides the file from its
  owner and from a user the collection was shared with, in search, chat
  citations, opening the record, All Records, the collection listing and the
  counts. A restore brings all of it back under the same id and queues the
  file for indexing. A second delete and a purge after the retention remove it
  for good.
- A connector delete of a file shared with another user hides it from both,
  and the next sync that sees the file again brings it back for both.
- A purge ends exactly where the record DELETE route's hard delete ends: the
  same graph footprint and the same cleanup events.
- A whole folder of files goes to the trash as one batch and is purged page by
  page (``SOFT_DELETE_SCALE_RECORDS`` sets the size; the design's GitLab Linux
  kernel case is about 90,000).

The KV store, lease and broker are the in-memory fakes of
``test_trash_purge_e2e``, which answer as the real ones do.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  cd backend/python && pytest tests/integration/test_soft_delete_checklist_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import contextlib
import logging
import math
import os
import time
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    DeleteSource,
    EventTypes,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.connectors.api import router as router_module
from app.connectors.core.base.data_processor import (
    data_source_entities_processor as processor_module,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.core.registry.folder_scope import remove_records_not_listed
from app.connectors.services.trash_purge import Outcome, TrashPurger
from app.connectors.sources.localKB.handlers import kb_service as kb_service_module
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.models.entities import (
    CodeFileRecord,
    FileRecord,
    MailRecord,
    Record,
    RecordType,
    RelatedExternalRecord,
    SQLTableRecord,
    TicketRecord,
    WebpageRecord,
)
from app.services.artifact_registry.models import Actor
from app.services.graph_db.common.record_visibility import RecordVisibility
from app.services.graph_db.common.utils import TRASH_STATE_FIELDS
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.services.record_content.authorizer import TieredRecordAuthorizer
from app.services.record_content.models import RecordNotFoundError
from app.utils.chat_helpers import enrich_virtual_record_id_to_result_with_fk_children
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.test_record_visibility_e2e import _ids_anywhere
from tests.integration.test_soft_delete_e2e import _connect_arango, _connect_neo4j
from tests.integration.test_trash_purge_e2e import _KV, DAY_MS, _Broker, _Lease

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

logger = logging.getLogger("soft-delete-checklist-it")

RECORDS = CollectionNames.RECORDS.value
USERS = CollectionNames.USERS.value
APPS = CollectionNames.APPS.value
GROUPS = CollectionNames.RECORD_GROUPS.value
SCALE_RECORDS = int(os.environ.get("SOFT_DELETE_SCALE_RECORDS", "1000"))


class _Kafka:
    """The record DELETE route's broker: every event is accepted and kept."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    async def publish_event(self, topic: str, event: dict) -> bool:
        self.events.append(event)
        return True

    def of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e.get("eventType") == event_type]


@dataclass
class _User:
    user_id: str
    key: str


@dataclass
class _World:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    service: KnowledgeBaseService
    broker: _Broker
    kafka: _Kafka
    kv: _KV
    org_id: str
    owner: _User
    sharee: _User
    kb_id: str
    drive_id: str
    connectors: list[str] = field(default_factory=list)
    ids: dict[str, str] = field(default_factory=dict)
    docs: dict[str, str] = field(default_factory=dict)
    now: int = field(default_factory=get_epoch_timestamp_in_ms)

    def vrid(self, name: str) -> str:
        return f"vr-{self.ids[name]}"

    async def stored(self, name: str) -> dict | None:
        return await self.graph.get_document(self.ids[name], RECORDS)

    async def delete_through_the_route(self, name: str, *, soft: bool, monkeypatch: pytest.MonkeyPatch) -> dict:
        monkeypatch.setattr(router_module, "is_soft_delete_enabled", AsyncMock(return_value=soft))
        container = SimpleNamespace(logger=lambda: logger, config_service=lambda: MagicMock())
        request = SimpleNamespace(
            state=SimpleNamespace(user={"userId": self.owner.user_id, "orgId": self.org_id}),
            app=SimpleNamespace(container=container),
        )
        return await router_module.delete_record(
            self.ids[name], request, graph_provider=self.graph, kafka_service=self.kafka
        )

    async def purge(self, days_later: float = 15) -> str:
        clock = self.now + int(days_later * DAY_MS)
        purger = TrashPurger(logger, self.graph, self.kv, self.broker, _Lease(), clock=lambda: clock, sleep=AsyncMock())
        return await purger.tick()

    async def edges_of(self, name: str) -> int:
        node = self.ids[name]
        if isinstance(self.graph, Neo4jProvider):
            rows = await self.graph.client.execute_query(
                "MATCH (n {id: $id})-[r]-() RETURN count(r) AS n", parameters={"id": node}
            )
            return rows[0]["n"]
        total = 0
        for collection in (CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value,
                           CollectionNames.RECORD_RELATIONS.value, CollectionNames.IS_OF_TYPE.value,
                           CollectionNames.INHERIT_PERMISSIONS.value):
            rows = await self.graph.http_client.execute_aql(
                f"FOR e IN {collection} FILTER e._from == @id OR e._to == @id RETURN 1", {"id": f"{RECORDS}/{node}"}
            )
            total += len(rows or [])
        return total

    async def footprint(self, name: str) -> dict[str, Any]:
        """What the graph still holds of a record: its node, its type doc and its edges."""
        files = await self.graph.get_document(self.ids[name], CollectionNames.FILES.value)
        return {"record": await self.stored(name) is not None, "typeDoc": files is not None,
                "edges": await self.edges_of(name)}

    async def sees(self, user: _User, name: str) -> dict[str, bool]:
        """Whether each user-facing read lets ``user`` reach the record."""
        g, record_id = self.graph, self.ids[name]
        search = await g.get_accessible_virtual_record_ids(user.user_id, self.org_id, raise_on_error=True)
        permitted = await g.filter_accessible_record_ids([record_id], user.user_id, self.org_id)
        hydrated = await g.get_records_by_record_ids([record_id], self.org_id)
        listed, _, _ = await g.list_all_records(
            user.key, self.org_id, 0, 500, None, None, None, None, None, None, None, None,
            "recordName", "asc", "all",
        )
        return {
            # Search and chat retrieval: the virtual record ids a user may match.
            "search": search.get(self.vrid(name)) == record_id,
            "permission check": record_id in set(permitted or []),
            # GET /records/{id}, streaming, download, agent fetch_full_record.
            "open": await g.check_record_access_with_details(user.user_id, self.org_id, record_id) is not None,
            # The record-content resolver behind citations and agent reads.
            "citation": await self._citation_opens(user, record_id),
            "chat hydrate": record_id in {r.get("_key") or r.get("id") for r in hydrated or []},
            "All Records": record_id in {r.get("id") for r in listed},
        }

    async def _citation_opens(self, user: _User, record_id: str) -> bool:
        record = await self.graph.get_record_by_id(record_id)
        if record is None:
            return False
        try:
            await TieredRecordAuthorizer(self.graph).authorize(Actor(org_id=self.org_id, user_id=user.user_id), record)
        except RecordNotFoundError:
            return False
        return True

    async def in_collection(self, name: str) -> dict[str, bool]:
        g, record_id = self.graph, self.ids[name]
        kb_records, _, _ = await g.list_kb_records(
            self.kb_id, self.owner.key, self.org_id, 0, 500,
            None, None, None, None, None, None, None, "recordName", "asc",
        )
        folder = _ids_anywhere(await g.get_folder_children(self.kb_id, self.ids["docs"], 0, 500))
        return {"collection list": record_id in _ids_anywhere(kb_records), "folder browse": record_id in folder}

    async def collection_total(self) -> int:
        stats = await self.graph.get_connector_stats(self.org_id, self.kb_id)
        assert stats["success"] is True, stats
        return stats["data"]["stats"]["total"]


def _edge(from_id: str, from_col: str, to_id: str, to_col: str, **extra: object) -> dict:
    now = get_epoch_timestamp_in_ms()
    return {"from_id": from_id, "from_collection": from_col, "to_id": to_id, "to_collection": to_col,
            "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}


def _upload(w: _World, name: str, *, folder: bool = False, **extra: object) -> FileRecord:
    fields: dict = {
        "id": w.ids[name], "org_id": w.org_id, "record_name": name if folder else f"{name}.pdf",
        "record_type": RecordType.FILE, "external_record_id": w.docs.get(name) or f"ext-{w.ids[name]}",
        "version": 1, "origin": OriginTypes.UPLOAD, "connector_name": Connectors.KNOWLEDGE_BASE,
        "connector_id": w.kb_id, "mime_type": "application/vnd.folder" if folder else "application/pdf",
        "indexing_status": ProgressStatus.COMPLETED.value, "is_file": not folder,
        "extension": None if folder else "pdf", "size_in_bytes": 0 if folder else 2048,
    }
    return FileRecord(**{**fields, **extra})


def _drive_file(w: _World, name: str, **extra: object) -> FileRecord:
    fields: dict = {
        "id": w.ids[name], "org_id": w.org_id, "record_name": f"{name}.pdf", "record_type": RecordType.FILE,
        "external_record_id": f"ext-{w.ids[name]}", "external_revision_id": "rev-1", "version": 1,
        "origin": OriginTypes.CONNECTOR, "connector_name": Connectors.GOOGLE_DRIVE, "connector_id": w.drive_id,
        "mime_type": "application/pdf", "indexing_status": ProgressStatus.COMPLETED.value, "is_file": True,
        "extension": "pdf",
    }
    return FileRecord(**{**fields, **extra})


async def _add_uploads(w: _World, names: list[str], *, parent: str | None = "docs") -> None:
    for name in names:
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
        w.docs[name] = uuid.uuid4().hex[:24]
    for start in range(0, len(names), 500):
        chunk = names[start:start + 500]
        await w.graph.batch_upsert_records([_upload(w, n) for n in chunk])
        await w.graph.batch_update_nodes(
            [{"id": w.ids[n], "virtualRecordId": w.vrid(n)} for n in chunk], RECORDS
        )
        await w.graph.batch_create_edges(
            [_edge(w.ids[n], RECORDS, w.kb_id, APPS, entityType="KB") for n in chunk],
            collection=CollectionNames.BELONGS_TO.value,
        )
        if parent:
            await w.graph.batch_create_edges(
                [_edge(w.ids[parent], RECORDS, w.ids[n], RECORDS, relationshipType="PARENT_CHILD") for n in chunk],
                collection=CollectionNames.RECORD_RELATIONS.value,
            )


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    await g.batch_upsert_nodes(
        [{"id": u.key, "userId": u.user_id, "orgId": w.org_id, "email": f"{u.user_id}@example.com",
          "fullName": label, "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}
         for u, label in ((w.owner, "Owner"), (w.sharee, "Colleague"))],
        collection=USERS,
    )
    await g.batch_upsert_nodes(
        [{"id": w.kb_id, "name": "Policies", "type": "KB", "appGroup": "Local Storage", "scope": "team",
          "isActive": True, "orgId": w.org_id, "createdAtTimestamp": now, "updatedAtTimestamp": now},
         {"id": w.drive_id, "name": "Drive", "type": "Drive", "appGroup": "Google Workspace", "scope": "team",
          "isActive": True, "orgId": w.org_id, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=APPS,
    )
    w.ids["docs"] = f"docs-{uuid.uuid4().hex[:12]}"
    await g.batch_upsert_records([_upload(w, "docs", folder=True)])
    await g.batch_create_edges([_edge(w.ids["docs"], RECORDS, w.kb_id, APPS, entityType="KB")],
                               collection=CollectionNames.BELONGS_TO.value)
    await _add_uploads(w, ["policy", "handbook"])
    w.ids["shared_doc"] = f"shared-{uuid.uuid4().hex[:12]}"
    await g.batch_upsert_records([_drive_file(w, "shared_doc")])
    await g.update_node(w.ids["shared_doc"], RECORDS, {"virtualRecordId": w.vrid("shared_doc")})
    await g.batch_create_edges(
        [_edge(w.owner.key, USERS, w.kb_id, APPS, role="OWNER", type="USER"),
         # The collection is shared with a colleague, who may read but not delete.
         _edge(w.sharee.key, USERS, w.kb_id, APPS, role="READER", type="USER"),
         _edge(w.owner.key, USERS, w.ids["shared_doc"], RECORDS, role="OWNER", type="USER"),
         # The Drive file is shared with the colleague at the source.
         _edge(w.sharee.key, USERS, w.ids["shared_doc"], RECORDS, role="READER", type="USER")],
        collection=CollectionNames.PERMISSION.value,
    )
    # Both users have the Drive connector, which a connector record's access needs as well.
    await g.batch_create_edges(
        [_edge(u.key, USERS, w.drive_id, APPS, syncState="COMPLETED", lastSyncUpdate=now)
         for u in (w.owner, w.sharee)],
        collection=CollectionNames.USER_APP_RELATION.value,
    )


async def _remove(graph: IGraphDBProvider, w: _World) -> None:
    connectors = [w.kb_id, w.drive_id, *w.connectors]
    ids = [*w.ids.values(), w.owner.key, w.sharee.key, *connectors]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query(
            "MATCH (n) WHERE n.id IN $ids OR n.connectorId IN $connectors "
            "OPTIONAL MATCH (n)-[:IS_OF_TYPE]->(t) DETACH DELETE n, t",
            parameters={"ids": ids, "connectors": connectors},
        )
        return
    for collection in (RECORDS, GROUPS):
        ids += await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d.connectorId IN @c RETURN d._key", {"c": connectors}
        ) or []
    for collection in (RECORDS, CollectionNames.FILES.value, CollectionNames.MAILS.value,
                       CollectionNames.TICKETS.value, CollectionNames.WEBPAGES.value,
                       CollectionNames.SQL_TABLES.value, CollectionNames.CODE_FILES.value,
                       GROUPS, USERS, APPS):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value,
                  CollectionNames.IS_OF_TYPE.value, CollectionNames.RECORD_RELATIONS.value,
                  CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.USER_APP_RELATION.value):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    # The default, where each Neo4j statement commits on its own and a rollback undoes nothing.
    monkeypatch.delenv("NEO4J_EXPLICIT_TRANSACTIONS", raising=False)
    monkeypatch.setenv("SOFT_DELETE_PURGE_INTERVAL_SECONDS", "0")
    monkeypatch.delenv("SOFT_DELETE_PURGE_MIN_AGE_SECONDS", raising=False)
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (_connect_neo4j(monkeypatch) if request.param == "neo4j" else _connect_arango())
        except Exception as exc:
            env = "NEO4J_IT_URI" if request.param == "neo4j" else "ARANGO_IT_URL"
            if os.environ.get(env):
                pytest.fail(f"{request.param} is configured ({env}) but not reachable: {exc!r}")
            pytest.skip(f"{request.param} not configured ({env} unset) and not reachable locally: {exc!r}")
        disconnect = getattr(graph, "disconnect", None)
        if disconnect is not None:
            cleanup.push_async_callback(disconnect)
        if isinstance(graph, Neo4jProvider):
            # The indexes ensure_schema creates on a real install: restore reads batches
            # by one, and the purge waits until its walk index is online.
            for statement in graph._generate_performance_indexes():
                await graph.client.execute_query(statement)
            await graph.client.execute_query("CALL db.awaitIndexes(300)")
        flag = AsyncMock(return_value=True)
        for module in (processor_module, kb_service_module):
            monkeypatch.setattr(module, "is_soft_delete_enabled", flag)
        monkeypatch.setattr(processor_module, "notify_kb_records_changed", AsyncMock())
        monkeypatch.setattr(router_module, "notify_kb_records_changed", AsyncMock())
        suffix = uuid.uuid4().hex[:10]
        broker = _Broker()
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.messaging_producer = broker
        # No blob store here; renames still go through the production rename path.
        monkeypatch.setattr(processor, "_get_storage_cleanup", lambda: None)
        service = KnowledgeBaseService(
            logger, graph, AsyncMock(), processor_for_kb=AsyncMock(return_value=processor),
            config_service=MagicMock(),
        )
        w = _World(
            graph=graph, processor=processor, service=service, broker=broker, kafka=_Kafka(),
            kv=_KV({"featureFlags": {"ENABLE_SOFT_DELETE": True}, "softDeletePurge": {"pageSize": 500}}),
            org_id=f"org-chk-{suffix}",
            owner=_User(user_id=f"user-chk-{suffix}", key=f"ukey-chk-{suffix}"),
            sharee=_User(user_id=f"mate-chk-{suffix}", key=f"mkey-chk-{suffix}"),
            kb_id=f"kb-chk-{suffix}", drive_id=f"drive-chk-{suffix}",
        )
        processor.org_id = w.org_id
        monkeypatch.setattr(TrashPurger, "_org_ids", AsyncMock(return_value=[w.org_id]))
        cleanup.push_async_callback(_remove, graph, w)
        await _seed(w)
        yield w


def _everywhere(seen: dict[str, bool]) -> set[str]:
    return {gate for gate, ok in seen.items() if ok}


# ---------------------------------------------------------------------------
# "Record delete for each connector - must be marked for soft delete"
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ConnectorDelete:
    """How a connector removes a record the source no longer has, as its sync code calls it."""

    connector: Connectors
    entry: str
    record_class: type[Record] = FileRecord
    record_type: RecordType = RecordType.FILE


# Built from every delete call under app/connectors/sources;
# tests/unit/connectors/sources/test_connector_delete_paths.py fails when a
# connector gains a delete call that is not one of these entries.
CONNECTOR_DELETES = [
    *(_ConnectorDelete(c, "record") for c in (
        Connectors.GOOGLE_DRIVE, Connectors.GOOGLE_DRIVE_WORKSPACE, Connectors.ONEDRIVE,
        Connectors.SHAREPOINT_ONLINE, Connectors.BOX, Connectors.DROPBOX, Connectors.DROPBOX_PERSONAL,
        Connectors.NEXTCLOUD, Connectors.AZURE_FILES, Connectors.LOCAL_FS, Connectors.SMB,
    )),
    *(_ConnectorDelete(c, "record", MailRecord, RecordType.MAIL) for c in (
        Connectors.GOOGLE_MAIL, Connectors.GOOGLE_MAIL_WORKSPACE,
    )),
    *(_ConnectorDelete(c, "record", WebpageRecord, RecordType.WEBPAGE) for c in (
        Connectors.CONFLUENCE, Connectors.NOTION, Connectors.BOOKSTACK, Connectors.WEB, Connectors.SALESFORCE,
    )),
    *(_ConnectorDelete(c, "record", SQLTableRecord, RecordType.SQL_TABLE) for c in (
        Connectors.POSTGRESQL, Connectors.MARIADB, Connectors.SNOWFLAKE,
    )),
    _ConnectorDelete(Connectors.ZAMMAD, "record", TicketRecord, RecordType.TICKET),
    *(_ConnectorDelete(c, "record", CodeFileRecord, RecordType.CODE_FILE) for c in (
        Connectors.GITLAB, Connectors.GITHUB_TEAMS,
    )),
    *(_ConnectorDelete(c, "cascade") for c in (
        Connectors.GOOGLE_DRIVE, Connectors.BOX, Connectors.NEXTCLOUD, Connectors.GITHUB_TEAMS,
    )),
    _ConnectorDelete(Connectors.CONFLUENCE_DATA_CENTER, "cascade", WebpageRecord, RecordType.WEBPAGE),
    _ConnectorDelete(Connectors.JIRA, "issue cascade", TicketRecord, RecordType.TICKET),
    _ConnectorDelete(Connectors.OUTLOOK, "by external id", MailRecord, RecordType.MAIL),
    *(_ConnectorDelete(c, "own batch", TicketRecord, RecordType.TICKET) for c in (
        Connectors.LINEAR, Connectors.JIRA_DATA_CENTER,
    )),
    *(_ConnectorDelete(c, "listing scan") for c in (
        Connectors.S3, Connectors.MINIO, Connectors.GCS, Connectors.AZURE_BLOB,
    )),
]


def _connector_record(w: _World, case: _ConnectorDelete, connector_id: str, name: str, external_id: str) -> Record:
    fields: dict[str, Any] = {
        "id": w.ids[name], "org_id": w.org_id, "record_name": f"{name} item",
        "record_type": case.record_type, "external_record_id": external_id, "version": 1,
        "origin": OriginTypes.CONNECTOR, "connector_name": case.connector, "connector_id": connector_id,
        "mime_type": "text/plain", "indexing_status": ProgressStatus.COMPLETED.value,
    }
    if case.record_class is FileRecord:
        fields.update(is_file=True, extension="txt")
    if case.record_class is CodeFileRecord:
        fields.update(file_path=f"src/{name}.py", file_hash=uuid.uuid4().hex)
    return case.record_class(**fields)


async def _seed_connector(w: _World, case: _ConnectorDelete) -> tuple[str, str]:
    """A connector holding one item and one item attached to it, with permissions and a parent."""
    connector_id = f"{case.connector.name.lower()}-{uuid.uuid4().hex[:10]}"
    w.connectors.append(connector_id)
    bucket = f"bucket-{uuid.uuid4().hex[:8]}"
    for name in ("item", "attachment", "parent"):
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
    external = {
        name: f"{bucket}/reports/{name}.txt" if case.entry == "listing scan" else f"ext-{w.ids[name]}"
        for name in ("item", "attachment", "parent")
    }
    now = get_epoch_timestamp_in_ms()
    await w.graph.batch_upsert_nodes(
        [{"id": connector_id, "name": case.connector.value, "type": case.connector.value, "appGroup": "Test",
          "scope": "team", "isActive": True, "orgId": w.org_id, "createdAtTimestamp": now,
          "updatedAtTimestamp": now}],
        collection=APPS,
    )
    await w.graph.batch_upsert_records(
        [_connector_record(w, case, connector_id, n, external[n]) for n in ("item", "attachment", "parent")]
    )
    for name in ("item", "attachment", "parent"):
        await w.graph.update_node(w.ids[name], RECORDS, {"virtualRecordId": w.vrid(name)})
    await w.graph.batch_create_edges(
        [_edge(w.owner.key, USERS, w.ids[n], RECORDS, role="OWNER", type="USER") for n in ("item", "attachment")],
        collection=CollectionNames.PERMISSION.value,
    )
    await w.graph.batch_create_edges(
        [_edge(w.ids["parent"], RECORDS, w.ids["item"], RECORDS, relationshipType="PARENT_CHILD"),
         _edge(w.ids["item"], RECORDS, w.ids["attachment"], RECORDS, relationshipType="ATTACHMENT")],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )
    if case.entry == "listing scan":
        group_id = f"rg-{uuid.uuid4().hex[:12]}"
        w.ids["bucket"] = group_id
        await w.graph.batch_upsert_nodes(
            [{"id": group_id, "orgId": w.org_id, "groupName": bucket, "externalGroupId": bucket,
              "groupType": "BUCKET", "connectorName": case.connector.value, "connectorId": connector_id,
              "createdAtTimestamp": now, "updatedAtTimestamp": now}],
            collection=GROUPS,
        )
        for name in ("item", "attachment", "parent"):
            await w.graph.update_node(w.ids[name], RECORDS, {"recordGroupId": group_id})
    return connector_id, bucket


async def _delete_as_the_connector_does(
    w: _World, case: _ConnectorDelete, connector_id: str, bucket: str,
) -> set[str]:
    """Run the connector's own delete call; return the names it puts in the trash."""
    p = w.processor
    if case.entry == "record":
        assert await p.on_record_deleted(w.ids["item"]) is True
        return {"item"}
    if case.entry == "cascade":
        result = await p.on_records_deleted_cascade([w.ids["item"]], connector_id)
        assert result["success"] is True, result
        return {"item", "attachment"}
    if case.entry == "issue cascade":
        # Jira Cloud keeps PARENT_CHILD children (stories under an epic) and takes attachments.
        result = await p.on_records_deleted_cascade([w.ids["item"]], connector_id, cascade_children=False)
        assert result["success"] is True, result
        return {"item", "attachment"}
    if case.entry == "by external id":
        # A message goes with its direct attachments on both backends, as ArangoDB's hard delete takes them.
        await p.delete_record_by_external_id(connector_id, f"ext-{w.ids['item']}", w.owner.user_id)
        return {"item", "attachment"}
    if case.entry == "own batch":
        await p.on_records_soft_deleted(
            [w.ids["item"], w.ids["attachment"]], connector_id, delete_source=DeleteSource.CONNECTOR, follow=(),
        )
        return {"item", "attachment"}
    assert case.entry == "listing scan"
    # The full listing returned only the parent: the other two are gone at the source.
    result = await remove_records_not_listed(p, connector_id, bucket, [""], {f"{bucket}/reports/parent.txt"}, logger)
    assert (result.removed, result.failed) == (2, 0), result
    return {"item", "attachment"}


@pytest.mark.parametrize("case", CONNECTOR_DELETES, ids=lambda c: f"{c.connector.value}-{c.entry}")
async def test_each_connectors_delete_puts_the_record_in_the_trash(world: _World, case: _ConnectorDelete) -> None:
    await _assert_the_connector_delete_trashes(world, case)


OUTLOOK_PERSONAL = _ConnectorDelete(Connectors.OUTLOOK_INDIVIDUAL, "by external id", MailRecord, RecordType.MAIL)


async def test_an_outlook_personal_delete_puts_the_record_in_the_trash(world: _World) -> None:
    await _assert_the_connector_delete_trashes(world, OUTLOOK_PERSONAL)


async def _assert_the_connector_delete_trashes(world: _World, case: _ConnectorDelete) -> None:
    connector_id, bucket = await _seed_connector(world, case)
    started = get_epoch_timestamp_in_ms()
    edges_before = {n: await world.edges_of(n) for n in ("item", "attachment")}

    gone = await _delete_as_the_connector_does(world, case, connector_id, bucket)

    for name in ("item", "attachment", "parent"):
        doc = await world.stored(name)
        assert doc is not None, f"{name}: the node must stay for a restore"
        if name not in gone:
            assert doc.get("isDeleted") is not True, f"{name} was trashed but the hard delete keeps it"
            continue
        assert doc["isDeleted"] is True, name
        assert doc["deleteSource"] == DeleteSource.CONNECTOR.value, name
        assert doc.get("deletedByUserId") is None, name
        assert doc["deletedAtTimestamp"] >= started, name
        assert doc["deleteBatchId"], name
        assert await world.edges_of(name) == edges_before[name], f"{name}: permissions and structure stay"
        assert await world.graph.get_record_by_id(world.ids[name]) is not None, f"{name}: type doc stays"
    if case.entry != "listing scan":
        # A listing scan removes each missing object on its own, so each is its own batch.
        assert len({(await world.stored(n))["deleteBatchId"] for n in gone}) == 1, "one delete is one batch"

    assert world.broker.of_type(EventTypes.DELETE_RECORD.value) == [], "nothing but the vectors goes now"
    vectors = {v for e in world.broker.of_type(EventTypes.SOFT_DELETE_RECORDS.value)
               for v in e["payload"]["virtualRecordIds"]}
    assert vectors == {world.vrid(n) for n in gone}

    live = {r.get("_key") or r.get("id") for r in await world.graph.get_records_by_record_ids(
        [world.ids[n] for n in ("item", "attachment", "parent")], world.org_id)}
    assert live == {world.ids[n] for n in ("item", "attachment", "parent")} - {world.ids[n] for n in gone}
    assert await world.graph.check_record_access_with_details(
        world.owner.user_id, world.org_id, world.ids["item"]) is None, "GET /records/{id} answers 404"
    holder = await world.graph.get_record_by_external_id(
        connector_id, (await world.stored("item"))["externalRecordId"], visibility=RecordVisibility.ALL)
    assert holder is not None and holder.id == world.ids["item"], "the next sync still finds it"


# ---------------------------------------------------------------------------
# Hidden everywhere, for the owner and for a colleague it was shared with;
# restore brings it back; purge after the retention removes it.
# ---------------------------------------------------------------------------


async def test_a_users_delete_hides_the_file_from_everyone_until_it_is_restored_then_the_purge_removes_it(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_gates = await world.sees(world.owner, "policy")
    sharee_gates = await world.sees(world.sharee, "policy")
    assert _everywhere(owner_gates) == set(owner_gates), owner_gates
    assert _everywhere(sharee_gates) >= {"search", "permission check", "open", "citation", "chat hydrate"}, sharee_gates
    assert await world.in_collection("policy") == {"collection list": True, "folder browse": True}
    total_before = await world.collection_total()

    response = await world.delete_through_the_route("policy", soft=True, monkeypatch=monkeypatch)

    assert response["softDeleted"] is True, response
    (event,) = world.kafka.of_type(EventTypes.SOFT_DELETE_RECORDS.value)
    assert event["payload"]["virtualRecordIds"] == [world.vrid("policy")]
    assert world.kafka.of_type(EventTypes.DELETE_RECORD.value) == []
    assert world.kafka.of_type(EventTypes.DELETE_STORED_DOCUMENTS.value) == [], "the upload stays for a restore"
    assert _everywhere(await world.sees(world.owner, "policy")) == set()
    assert _everywhere(await world.sees(world.sharee, "policy")) == set()
    assert await world.in_collection("policy") == {"collection list": False, "folder browse": False}
    assert await world.collection_total() == total_before - 1
    assert _everywhere(await world.sees(world.owner, "handbook")) == set(owner_gates), "the file beside it stays"
    with pytest.raises(RecordNotFoundError):
        # Opening an old citation gives "This item was deleted".
        await TieredRecordAuthorizer(world.graph).authorize(
            Actor(org_id=world.org_id, user_id=world.sharee.user_id), await world.graph.get_record_by_id(world.ids["policy"])
        )

    restored = await world.service.restore_record(world.ids["policy"], world.owner.user_id, world.org_id)

    assert restored["success"] is True, restored
    assert [r["recordId"] for r in restored["restoredRecords"]] == [world.ids["policy"]], "same id"
    # Search matches it again once indexing has put its vectors back; the rest is immediate.
    assert await world.sees(world.owner, "policy") == {**owner_gates, "search": False}
    assert await world.sees(world.sharee, "policy") == {**sharee_gates, "search": False}
    await world.graph.update_node(world.ids["policy"], RECORDS, {"indexingStatus": ProgressStatus.COMPLETED.value})
    assert await world.sees(world.owner, "policy") == owner_gates
    assert await world.sees(world.sharee, "policy") == sharee_gates
    assert await world.in_collection("policy") == {"collection list": True, "folder browse": True}
    assert await world.collection_total() == total_before
    reindexed = [e["payload"]["recordId"] for e in world.broker.of_type(EventTypes.REINDEX_RECORD.value)]
    assert reindexed == [world.ids["policy"]], "its vectors went with the delete, so it is indexed again"
    assert (await world.stored("policy"))["virtualRecordId"] == world.vrid("policy"), "old citations resolve"

    await world.delete_through_the_route("policy", soft=True, monkeypatch=monkeypatch)
    assert await world.purge(13) == Outcome.FINISHED
    assert (await world.stored("policy"))["isDeleted"] is True, "nothing goes before the retention"
    assert await world.purge(15) == Outcome.FINISHED

    assert await world.footprint("policy") == {"record": False, "typeDoc": False, "edges": 0}
    assert _everywhere(await world.sees(world.owner, "policy")) == set()
    assert [e["payload"]["recordId"] for e in world.broker.of_type(EventTypes.DELETE_RECORD.value)] == [
        world.ids["policy"]]
    assert world.broker.stored_documents() == {world.kb_id: [world.docs["policy"]]}
    after = await world.service.restore_record(world.ids["policy"], world.owner.user_id, world.org_id)
    assert (after["success"], after["code"]) == (False, 404), "after the purge there is nothing to restore"


async def test_a_file_deleted_at_the_source_is_hidden_from_everyone_it_was_shared_with_until_it_comes_back(
    world: _World,
) -> None:
    owner_gates = await world.sees(world.owner, "shared_doc")
    sharee_gates = await world.sees(world.sharee, "shared_doc")
    assert {"search", "permission check", "open", "citation", "chat hydrate"} <= _everywhere(sharee_gates)

    assert await world.processor.on_record_deleted(world.ids["shared_doc"]) is True

    assert _everywhere(await world.sees(world.owner, "shared_doc")) == set()
    assert _everywhere(await world.sees(world.sharee, "shared_doc")) == set()

    # The file is back at the source (restored from the Drive trash); the next sync sees it.
    seen_again = _drive_file(world, "shared_doc")
    fresh_id = seen_again.id = str(uuid.uuid4())
    await world.processor.on_new_records([(seen_again, [])])

    doc = await world.stored("shared_doc")
    assert doc["isDeleted"] is False
    assert {k: doc.get(k) for k in TRASH_STATE_FIELDS} == dict.fromkeys(TRASH_STATE_FIELDS)
    assert await world.graph.get_document(fresh_id, RECORDS) is None, "restored in place, not created again"
    assert await world.sees(world.sharee, "shared_doc") == {**sharee_gates, "search": False}
    await world.graph.update_node(world.ids["shared_doc"], RECORDS, {"indexingStatus": ProgressStatus.COMPLETED.value})
    assert await world.sees(world.owner, "shared_doc") == owner_gates
    assert await world.sees(world.sharee, "shared_doc") == sharee_gates
    queued = [e["payload"].get("recordId") for e in world.broker.events
              if e.get("eventType") in (EventTypes.NEW_RECORD.value, EventTypes.REINDEX_RECORD.value)]
    assert queued == [world.ids["shared_doc"]], "indexed again even though its content did not change"


# ---------------------------------------------------------------------------
# "cleanup same as a single record"
# ---------------------------------------------------------------------------


def _without_ids(payload: dict, w: _World, name: str) -> dict:
    swap = {w.ids[name]: "<record>", w.vrid(name): "<vrid>", w.docs.get(name): "<upload>"}
    return {k: swap.get(v, v) for k, v in payload.items()}


async def test_a_purge_ends_where_the_routes_hard_delete_ends(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    """The same upload twice, side by side: one hard-deleted by the record DELETE
    route with the trash off, one sent to the trash by the same route and purged."""
    await _add_uploads(world, ["hard_copy", "soft_copy"])
    await world.graph.batch_create_edges(
        [_edge(world.owner.key, USERS, world.ids[n], RECORDS, role="OWNER", type="USER")
         for n in ("hard_copy", "soft_copy")],
        collection=CollectionNames.PERMISSION.value,
    )
    before = {n: await world.footprint(n) for n in ("hard_copy", "soft_copy")}
    assert before["hard_copy"] == before["soft_copy"] and before["hard_copy"]["edges"] >= 4, before

    hard = await world.delete_through_the_route("hard_copy", soft=False, monkeypatch=monkeypatch)
    assert hard["success"] is True and "softDeleted" not in hard, hard
    hard_events = list(world.kafka.events)
    world.kafka.events.clear()
    await world.delete_through_the_route("soft_copy", soft=True, monkeypatch=monkeypatch)
    assert await world.purge(15) == Outcome.FINISHED

    assert await world.footprint("hard_copy") == await world.footprint("soft_copy") == {
        "record": False, "typeDoc": False, "edges": 0,
    }
    assert await world.stored("docs") is not None and await world.stored("handbook") is not None

    [hard_delete] = [e["payload"] for e in hard_events if e["eventType"] == EventTypes.DELETE_RECORD.value]
    [purge_delete] = [e["payload"] for e in world.broker.of_type(EventTypes.DELETE_RECORD.value)]
    expected = _without_ids(hard_delete, world, "hard_copy")
    assert {"orgId", "recordId", "virtualRecordId", "connectorId"} <= expected.keys(), expected
    got = _without_ids(purge_delete, world, "soft_copy")
    assert {k: got.get(k) for k in expected} == expected, "the purge's deleteRecord carries everything the hard one does"
    [hard_files] = [e["payload"] for e in hard_events if e["eventType"] == EventTypes.DELETE_STORED_DOCUMENTS.value]
    assert hard_files["documentIds"] == [world.docs["hard_copy"]]
    assert world.broker.stored_documents() == {world.kb_id: [world.docs["soft_copy"]]}


# ---------------------------------------------------------------------------
# Scale: a whole folder in one batch, purged page by page.
# ---------------------------------------------------------------------------


async def test_a_large_folder_goes_to_the_trash_as_one_batch_and_is_purged_page_by_page(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    names = [f"bulk_{i}" for i in range(SCALE_RECORDS)]
    started = time.monotonic()
    await _add_uploads(world, names)
    logger.warning("scale: seeded %d records in %.1fs", SCALE_RECORDS, time.monotonic() - started)

    started = time.monotonic()
    result = await world.processor.on_records_deleted_cascade(
        [world.ids["docs"]], world.kb_id, delete_source=DeleteSource.USER, deleted_by_user_id=world.owner.key,
    )
    marked_in = time.monotonic() - started

    assert result["success"] is True, {k: v for k, v in result.items() if k != "deleted_records"}
    assert len(result["deleted_records"]) == SCALE_RECORDS + 3, "the folder, its two files and every bulk file"
    events = world.broker.of_type(EventTypes.SOFT_DELETE_RECORDS.value)
    assert len(events) == math.ceil((SCALE_RECORDS + 2) / 5000)
    assert sum(len(e["payload"]["virtualRecordIds"]) for e in events) == SCALE_RECORDS + 2
    sample = [await world.stored(n) for n in (names[0], names[-1], "docs")]
    assert len({d["deleteBatchId"] for d in sample}) == 1 and all(d["isDeleted"] for d in sample)

    listing = world.graph.get_purgeable_trashed_records
    pages: list[int] = []

    async def count_pages(*args: object, **kwargs: object) -> dict:
        page = await listing(*args, **kwargs)
        pages.append(len(page.get("records") or []))
        return page

    monkeypatch.setattr(world.graph, "get_purgeable_trashed_records", count_pages)
    started = time.monotonic()
    runs = 0
    # A folder may wait for a later run than its files; the trash is empty within a few.
    while await world.stored("docs") is not None and runs < 3:
        assert await world.purge(15) == Outcome.FINISHED
        runs += 1
    purged_in = time.monotonic() - started

    assert max(pages) <= 500, pages
    deleted = world.broker.deleted_record_ids()
    assert len(deleted) == len(set(deleted)) == SCALE_RECORDS + 3, "every record once"
    for name in (names[0], names[len(names) // 2], names[-1], "docs", "policy"):
        assert await world.stored(name) is None, name
    logger.warning(
        "scale: %d records marked in %.1fs, purged in %d run(s) of %d page(s) in %.1fs on %s",
        SCALE_RECORDS + 3, marked_in, runs, len(pages), purged_in, type(world.graph).__name__,
    )


# ---------------------------------------------------------------------------
# Chat: a table in the trash must not reach an answer through a live table's foreign key.
# ---------------------------------------------------------------------------


class _StoredContent:
    """The blob store's processed content by virtual record id. The trash keeps a
    record's content until the purge, so a trashed table's content is still here."""

    def __init__(self) -> None:
        self.content: dict[str, dict] = {}

    async def get_record_from_storage(self, virtual_record_id: str, org_id: str, lookup_result: dict | None = None) -> dict | None:
        found = self.content.get(virtual_record_id)
        return dict(found) if found else None


def _table(w: _World, name: str, connector_id: str, *, references: str | None = None) -> SQLTableRecord:
    fqn = f"public.{name}"
    table = SQLTableRecord(
        id=w.ids[name], org_id=w.org_id, record_name=name, record_type=RecordType.SQL_TABLE,
        external_record_id=fqn, external_revision_id="rev-1", version=1, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.POSTGRESQL, connector_id=connector_id, mime_type=MimeTypes.SQL_TABLE.value,
        indexing_status=ProgressStatus.COMPLETED.value, inherit_permissions=True,
    )
    if references:
        table.related_external_records.append(RelatedExternalRecord(
            external_record_id=f"public.{references}", record_type=RecordType.SQL_TABLE, record_name=references,
            relation_type=RecordRelations.FOREIGN_KEY, source_column=f"{references}_id", target_column="id",
            child_table_name=fqn, parent_table_name=f"public.{references}", constraint_name=f"fk_{references}",
        ))
    return table


async def test_chat_does_not_pull_in_a_trashed_table_through_a_foreign_key(world: _World) -> None:
    # The trash keeps the foreign-key edges and the stored content until the purge.
    connector_id = f"postgres-{uuid.uuid4().hex[:10]}"
    world.connectors.append(connector_id)
    for name in ("customers", "orders"):
        world.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
    await world.processor.on_new_records([(_table(world, "customers", connector_id), [])])
    await world.processor.on_new_records([(_table(world, "orders", connector_id, references="customers"), [])])
    blobs = _StoredContent()
    for name in ("customers", "orders"):
        await world.graph.update_node(world.ids[name], RECORDS, {"virtualRecordId": world.vrid(name)})
        blobs.content[world.vrid(name)] = {
            "record_name": name,
            "block_containers": {"block_groups": [{"type": "table", "data": {
                "table_summary": f"The {name} table", "ddl": f"CREATE TABLE {name} (id int)"}}], "blocks": []},
        }
    parents = await world.graph.get_parent_record_ids_by_relation_type(
        world.ids["orders"], RecordRelations.FOREIGN_KEY.value)
    assert [p["record_id"] for p in parents] == [world.ids["customers"]], "the sync wrote the foreign key"

    # The customers table was dropped; the Postgres sync deletes its record.
    assert await world.processor.on_record_deleted(world.ids["customers"]) is True

    results = {world.vrid("orders"): {"id": world.ids["orders"], "record_type": "SQL_TABLE", "record_name": "orders"}}
    flattened: list[dict] = []
    await enrich_virtual_record_id_to_result_with_fk_children(
        results, blobs, world.org_id, graph_provider=world.graph, flattened_results=flattened,
    )

    assert results.get(world.vrid("customers")) is None, "the trashed table's content reached the chat context"
    assert world.ids["customers"] not in {r.get("record_id") for r in flattened}
