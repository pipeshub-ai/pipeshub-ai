"""The trash purge against a real Neo4j and a real ArangoDB.

Drives ``TrashPurger.tick`` over a real provider. Records go to the trash
through the production write path (``DataSourceEntitiesProcessor`` on a real
``GraphDataStore`` with ``ENABLE_SOFT_DELETE`` on); the purge's clock is moved
forward instead of waiting. The KV store, the leader lease and the broker are
in-memory fakes that answer as the real ones do.

- Past the retention a record goes with its type doc and every edge (permission,
  collection, folder, taxonomy), and the hard delete's events go out:
  ``deleteRecord`` and ``deleteStoredDocuments`` for an upload, a Local FS
  stored copy and a Web page's stored copy. Before the retention nothing goes.
- A restored record is never purged: restored before the run, between the
  listing and the delete, or while the purge waits on the restore's lock.
- A record group kept for the trash goes once its last record is purged; one
  the source still has, one with a live record, and one listed again stay.
- A page whose events cannot be published stays owed and the next tick
  publishes it; a page saved but never deleted owes nothing; a page the graph
  refuses is retried one by one, the bad record is counted and left out after
  the last attempt; a run that loses its lease resumes from its cursor.
- With ``ENABLE_SOFT_DELETE`` off nothing happens. A deleting connector's trash,
  a record marked deleted without a timestamp, and a trashed folder that still
  holds a live record are left alone.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  cd backend/python && pytest tests/integration/test_trash_purge_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import contextlib
import copy
import json
import logging
import os
import uuid
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    EventTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor import (
    data_source_entities_processor as processor_module,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.services import trash_purge as purge_module
from app.connectors.services.trash_purge import (
    OUTBOX_DIRECTORY,
    STATE_KEY,
    Outcome,
    TrashPurger,
)
from app.connectors.sources.dropbox.connector import DropboxConnector
from app.models.entities import FileRecord, RecordGroup, RecordGroupType, RecordType
from app.services.featureflag.platform_settings import PLATFORM_SETTINGS_KEY
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from tests.integration.test_soft_delete_e2e import _connect_arango, _connect_neo4j

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

logger = logging.getLogger("trash-purge-it")

DAY_MS = 24 * 60 * 60 * 1000
RECORDS = CollectionNames.RECORDS.value
FILES = CollectionNames.FILES.value
GROUPS = CollectionNames.RECORD_GROUPS.value
APPS = CollectionNames.APPS.value


class _KV:
    """The KV store through ``ConfigurationService``: values round-trip through JSON,
    and deleting a missing key reports failure, as Redis does."""

    def __init__(self, platform_settings: dict) -> None:
        self.values: dict[str, Any] = {PLATFORM_SETTINGS_KEY: platform_settings}

    async def get_config(
        self, key: str, default: object = None, use_cache: bool = False, *, raise_on_error: bool = False,
    ) -> object:
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: object) -> bool:
        self.values[key] = json.loads(json.dumps(value))
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return sorted(k for k in self.values if k.startswith(directory))

    def outbox(self) -> dict[str, Any]:
        return {k: v for k, v in self.values.items() if k.startswith(OUTBOX_DIRECTORY)}


class _Lease:
    def __init__(self) -> None:
        self.refreshes_left: int | None = None

    async def try_acquire(self) -> bool:
        return True

    async def refresh(self) -> bool:
        if self.refreshes_left is None:
            return True
        self.refreshes_left -= 1
        return self.refreshes_left >= 0

    async def release(self) -> None:
        return None

    async def close(self) -> None:
        return None


class _Broker:
    """Accepts or refuses every message; refusing reports per message, as ``send_messages`` does."""

    def __init__(self) -> None:
        self.events: list[dict] = []
        self.accept = True

    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        if self.accept:
            self.events.append(message)
        return self.accept

    async def send_messages(self, topic: str, messages: list) -> list[bool]:
        if self.accept:
            self.events.extend(m for _key, m in messages)
        return [self.accept] * len(messages)

    def of_type(self, event_type: str) -> list[dict]:
        return [e for e in self.events if e.get("eventType") == event_type]

    def deleted_record_ids(self) -> list[str]:
        return [e["payload"]["recordId"] for e in self.of_type(EventTypes.DELETE_RECORD.value)]

    def stored_documents(self) -> dict[str, list[str]]:
        out: dict[str, list[str]] = {}
        for e in self.of_type(EventTypes.DELETE_STORED_DOCUMENTS.value):
            out.setdefault(e["payload"]["connectorId"], []).extend(e["payload"]["documentIds"])
        return out


@dataclass
class _World:
    graph: IGraphDBProvider
    processor: DataSourceEntitiesProcessor
    kv: _KV
    lease: _Lease
    broker: _Broker
    org_id: str
    user_key: str
    kb_id: str
    drive_id: str
    local_fs_id: str
    web_id: str
    dropbox_id: str
    topic_id: str
    now: int = field(default_factory=get_epoch_timestamp_in_ms)
    ids: dict[str, str] = field(default_factory=dict)
    docs: dict[str, str] = field(default_factory=dict)

    def purger(self, days_later: float) -> TrashPurger:
        clock = self.now + int(days_later * DAY_MS)
        return TrashPurger(
            logger, self.graph, self.kv, self.broker, self.lease,
            clock=lambda: clock, sleep=AsyncMock(),
        )

    async def tick(self, days_later: float = 15) -> str:
        return await self.purger(days_later).tick()

    async def stored(self, name: str) -> dict | None:
        return await self.graph.get_document(self.ids[name], RECORDS)

    async def trash(self, *names: str) -> None:
        for name in names:
            assert await self.processor.on_record_deleted(self.ids[name]) is True, name

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
                           CollectionNames.BELONGS_TO_TOPIC.value, CollectionNames.INHERIT_PERMISSIONS.value):
            rows = await self.graph.http_client.execute_aql(
                f"FOR e IN {collection} FILTER e._from == @id OR e._to == @id RETURN 1", {"id": f"{RECORDS}/{node}"}
            )
            total += len(rows or [])
        return total

    def state(self) -> dict:
        return self.kv.values.get(STATE_KEY) or {}


def _edge(from_id: str, from_col: str, to_id: str, to_col: str, **extra: object) -> dict:
    now = get_epoch_timestamp_in_ms()
    return {"from_id": from_id, "from_collection": from_col, "to_id": to_id, "to_collection": to_col,
            "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}


def _file(w: _World, name: str, connector_id: str, connector: Connectors, *, folder: bool = False,
          origin: OriginTypes = OriginTypes.CONNECTOR, **extra: object) -> FileRecord:
    fields: dict = {
        "id": w.ids[name],
        "org_id": w.org_id,
        "record_name": name if folder else f"{name}.pdf",
        "record_type": RecordType.FILE,
        "external_record_id": w.docs.get(name) or f"ext-{w.ids[name]}",
        "version": 1,
        "origin": origin,
        "connector_name": connector,
        "connector_id": connector_id,
        "mime_type": "application/vnd.folder" if folder else "application/pdf",
        "indexing_status": ProgressStatus.COMPLETED.value,
        "is_file": not folder,
        "extension": None if folder else "pdf",
    }
    return FileRecord(**{**fields, **extra})


KB_FILES = ("upload", "upload_twin", "folder", "child", "restored")
DRIVE_FILES = ("drive_file",)


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    for name in (*KB_FILES, *DRIVE_FILES, "local_copy", "web_page", "unmarked"):
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"
    for name in ("upload", "upload_twin", "child", "restored", "local_copy", "web_page"):
        w.docs[name] = uuid.uuid4().hex[:24]

    await g.batch_upsert_nodes(
        [{"id": w.user_key, "userId": f"user-{w.user_key}", "orgId": w.org_id, "email": f"{w.user_key}@example.com",
          "fullName": "Purge Tester", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    apps = [
        (w.kb_id, "Collection", "KB", "Local Storage"),
        (w.drive_id, "Drive", "Drive", "Google Workspace"),
        (w.local_fs_id, "Local FS", "Local FS", "Local Storage"),
        (w.web_id, "Web", "Web", "Web"),
        (w.dropbox_id, "Dropbox", "Dropbox", "Dropbox"),
    ]
    await g.batch_upsert_nodes(
        [{"id": key, "name": name, "type": kind, "appGroup": group, "scope": "team", "isActive": True,
          "orgId": w.org_id, "createdAtTimestamp": now, "updatedAtTimestamp": now}
         for key, name, kind, group in apps],
        collection=APPS,
    )
    await g.batch_upsert_nodes(
        [{"id": w.topic_id, "name": f"Finance {w.topic_id}", "orgId": w.org_id, "createdAtTimestamp": now}],
        collection=CollectionNames.TOPICS.value,
    )
    kb = Connectors.KNOWLEDGE_BASE
    await g.batch_upsert_records([
        _file(w, "upload", w.kb_id, kb, origin=OriginTypes.UPLOAD),
        _file(w, "upload_twin", w.kb_id, kb, origin=OriginTypes.UPLOAD),
        _file(w, "folder", w.kb_id, kb, folder=True, origin=OriginTypes.UPLOAD),
        _file(w, "child", w.kb_id, kb, origin=OriginTypes.UPLOAD),
        _file(w, "restored", w.kb_id, kb, origin=OriginTypes.UPLOAD),
        _file(w, "drive_file", w.drive_id, Connectors.GOOGLE_DRIVE),
        _file(w, "local_copy", w.local_fs_id, Connectors.LOCAL_FS, path=f"storage://{w.docs['local_copy']}"),
        _file(w, "web_page", w.web_id, Connectors.WEB, storage_document_id=w.docs["web_page"]),
        _file(w, "unmarked", w.drive_id, Connectors.GOOGLE_DRIVE),
    ])
    for name in ("upload", "upload_twin", "child", "restored", "drive_file", "local_copy", "web_page"):
        await g.update_node(w.ids[name], RECORDS, {"virtualRecordId": f"vr-{w.ids[name]}"})
    users = CollectionNames.USERS.value
    await g.batch_create_edges(
        [_edge(w.user_key, users, w.ids[n], RECORDS, role="OWNER", type="USER") for n in ("upload", "drive_file")],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_create_edges(
        [_edge(w.ids[n], RECORDS, w.kb_id, APPS, entityType="KB") for n in KB_FILES],
        collection=CollectionNames.BELONGS_TO.value,
    )
    await g.batch_create_edges(
        [_edge(w.ids["folder"], RECORDS, w.ids[c], RECORDS, relationshipType="PARENT_CHILD")
         for c in ("upload", "child")],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )
    await g.batch_create_edges(
        [{"from_id": w.ids["upload"], "from_collection": RECORDS, "to_id": w.topic_id,
          "to_collection": CollectionNames.TOPICS.value, "createdAtTimestamp": now}],
        collection=CollectionNames.BELONGS_TO_TOPIC.value,
    )


async def _remove(graph: IGraphDBProvider, w: _World) -> None:
    connectors = [w.kb_id, w.drive_id, w.local_fs_id, w.web_id, w.dropbox_id]
    ids = [*w.ids.values(), w.user_key, w.topic_id, *connectors]
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
    for collection in (RECORDS, FILES, GROUPS, CollectionNames.USERS.value, APPS, CollectionNames.TOPICS.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.BELONGS_TO.value,
                  CollectionNames.IS_OF_TYPE.value, CollectionNames.RECORD_RELATIONS.value,
                  CollectionNames.INHERIT_PERMISSIONS.value, CollectionNames.BELONGS_TO_TOPIC.value):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    # The default, where each Neo4j statement commits on its own and a rollback undoes nothing.
    monkeypatch.delenv("NEO4J_EXPLICIT_TRANSACTIONS", raising=False)
    # Every tick may start a run; the retention (14 days) still comes from the settings.
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
            # The purge walks this index; ensure_schema creates it on a real install.
            await graph.client.execute_query(
                "CREATE INDEX record_deleted_at IF NOT EXISTS FOR (n:Record) ON (n.deletedAtTimestamp)"
            )
        flag = AsyncMock(return_value=True)
        monkeypatch.setattr(processor_module, "is_soft_delete_enabled", flag)
        monkeypatch.setattr(purge_module, "is_soft_delete_enabled", flag)
        monkeypatch.setattr(processor_module, "notify_kb_records_changed", AsyncMock())
        suffix = uuid.uuid4().hex[:10]
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.messaging_producer = _Broker()
        w = _World(
            graph=graph, processor=processor,
            kv=_KV({"featureFlags": {"ENABLE_SOFT_DELETE": True}, "softDeletePurge": {"pageSize": 500}}),
            lease=_Lease(), broker=_Broker(),
            org_id=f"org-prg-{suffix}", user_key=f"ukey-prg-{suffix}", kb_id=f"kb-prg-{suffix}",
            drive_id=f"drive-prg-{suffix}", local_fs_id=f"lfs-prg-{suffix}", web_id=f"web-prg-{suffix}",
            dropbox_id=f"dbx-prg-{suffix}", topic_id=f"topic-prg-{suffix}",
        )
        processor.org_id = w.org_id
        # Only this test's org, whatever else the shared database holds.
        monkeypatch.setattr(TrashPurger, "_org_ids", AsyncMock(return_value=[w.org_id]))
        cleanup.push_async_callback(_remove, graph, w)
        await _seed(w)
        yield w


# ---------------------------------------------------------------------------


async def test_past_the_retention_a_record_goes_with_everything_it_had(world: _World) -> None:
    assert await world.edges_of("upload") >= 4
    await world.trash("upload")
    file_doc_before = await world.graph.get_document(world.ids["upload"], FILES)
    assert file_doc_before is not None

    assert await world.tick(15) == Outcome.FINISHED

    assert await world.stored("upload") is None
    assert await world.graph.get_document(world.ids["upload"], FILES) is None
    assert await world.edges_of("upload") == 0, "permission, collection, folder and taxonomy edges all go"
    assert await world.graph.get_document(world.topic_id, CollectionNames.TOPICS.value) is not None
    assert await world.stored("folder") is not None, "the parent stays"

    [event] = world.broker.of_type(EventTypes.DELETE_RECORD.value)
    payload = event["payload"]
    assert {k: payload.get(k) for k in ("orgId", "recordId", "version", "extension", "virtualRecordId",
                                        "connectorId", "connectorName", "origin")} == {
        "orgId": world.org_id, "recordId": world.ids["upload"], "version": 1, "extension": "pdf",
        "virtualRecordId": f"vr-{world.ids['upload']}", "connectorId": world.kb_id,
        "connectorName": Connectors.KNOWLEDGE_BASE.value, "origin": OriginTypes.UPLOAD.value,
    }
    assert world.broker.stored_documents() == {world.kb_id: [world.docs["upload"]]}
    assert world.kv.outbox() == {}
    assert world.state()["status"] == "idle"
    assert world.state()["lastCounts"]["purged"] == 1


async def test_nothing_goes_before_the_retention(world: _World) -> None:
    await world.trash("upload")

    assert await world.tick(13) == Outcome.FINISHED
    assert (await world.stored("upload"))["isDeleted"] is True
    assert world.broker.events == []

    assert await world.tick(15) == Outcome.FINISHED
    assert await world.stored("upload") is None


async def test_a_restored_record_is_never_purged(world: _World) -> None:
    await world.trash("restored")
    batch = (await world.stored("restored"))["deleteBatchId"]
    assert await world.graph.restore_records([{"id": world.ids["restored"]}], batch) == [world.ids["restored"]]

    assert await world.tick(15) == Outcome.FINISHED

    assert (await world.stored("restored"))["isDeleted"] is False
    assert world.broker.events == []


async def test_a_record_restored_between_the_listing_and_the_delete_stays(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    await world.trash("restored", "upload_twin")
    batch = (await world.stored("restored"))["deleteBatchId"]
    real_purge = world.graph.purge_trashed_records

    async def restore_first(record_ids: list[str], *args: object, **kwargs: object) -> dict:
        await world.graph.restore_records([{"id": world.ids["restored"]}], batch)
        return await real_purge(record_ids, *args, **kwargs)

    monkeypatch.setattr(world.graph, "purge_trashed_records", restore_first)

    assert await world.tick(15) == Outcome.FINISHED

    assert (await world.stored("restored"))["isDeleted"] is False
    assert await world.stored("upload_twin") is None
    assert world.broker.deleted_record_ids() == [world.ids["upload_twin"]]
    assert world.state()["lastCounts"] == {"purged": 1, "kept": 1, "failed": 0, "groups": 0}


async def test_a_restore_still_running_when_the_purge_deletes_wins(world: _World) -> None:
    """The restore holds the record's lock; the purge waits for it, or gives up, and never deletes it."""
    await world.trash("restored")
    record_id = world.ids["restored"]
    cutoff = get_epoch_timestamp_in_ms() + DAY_MS

    if isinstance(world.graph, Neo4jProvider):
        async with world.graph.client.driver.session(database="neo4j") as session:
            tx = await session.begin_transaction()
            await tx.run("MATCH (r:Record {id: $id}) SET r.isDeleted = false, r.deletedAtTimestamp = null",
                         id=record_id)
            purge = asyncio.create_task(world.graph.purge_trashed_records([record_id], world.org_id, cutoff))
            await asyncio.sleep(1.0)
            assert not purge.done(), "the purge waits on the restore's write lock"
            await tx.commit()
            result = await purge
        assert result == {"purged": [], "kept": [record_id]}
        assert "purgeLock" not in (await world.stored("restored"))
    else:
        http = world.graph.http_client
        txn = await http.begin_transaction([RECORDS], [RECORDS])
        await http.execute_aql(
            "UPDATE @key WITH { isDeleted: false, deletedAtTimestamp: null } IN records",
            {"key": record_id}, txn_id=txn,
        )
        # The restore's uncommitted write conflicts with the purge's REMOVE, which rolls it all back.
        with pytest.raises(Exception, match="1200|conflict|Removed 0 of 1"):
            await world.graph.purge_trashed_records([record_id], world.org_id, cutoff)
        await http.commit_transaction(txn)

    doc = await world.stored("restored")
    assert doc is not None and doc["isDeleted"] is False
    assert await world.graph.get_document(record_id, FILES) is not None


async def test_a_group_kept_for_the_trash_goes_with_its_last_record(world: _World) -> None:
    team_folder = f"ns-{uuid.uuid4().hex[:8]}"
    file = FileRecord(
        id=str(uuid.uuid4()), org_id=world.org_id, record_name="budget.xlsx", record_type=RecordType.FILE,
        record_group_type=RecordGroupType.DRIVE.value, external_record_group_id=team_folder,
        external_record_id=f"id:{uuid.uuid4().hex[:12]}", external_revision_id="rev-1", version=1,
        origin=OriginTypes.CONNECTOR.value, connector_name=Connectors.DROPBOX, connector_id=world.dropbox_id,
        mime_type="application/vnd.ms-excel", indexing_status=ProgressStatus.COMPLETED.value,
        is_file=True, extension="xlsx", inherit_permissions=True,
    )
    world.ids["dropbox_file"] = file.id
    await world.processor.on_new_records([(file, [])])
    group = await world.processor.get_record_group_by_external_id(world.dropbox_id, team_folder)
    connector = SimpleNamespace(data_entities_processor=world.processor, connector_id=world.dropbox_id, logger=logger)
    connector._extract_folder_info_from_event = lambda event: DropboxConnector._extract_folder_info_from_event(
        connector, event
    )
    folder = SimpleNamespace(display_name="Finance", path=SimpleNamespace(namespace_relative=SimpleNamespace(ns_id=team_folder)))
    event = SimpleNamespace(assets=[SimpleNamespace(is_folder=lambda: True, get_folder=lambda: folder)])
    await DropboxConnector._handle_record_group_deleted_event(connector, event)

    kept = await world.graph.get_document(group.id, GROUPS)
    assert kept is not None and kept["isDeletedAtSource"] is True, "the trash keeps the group, marked"
    assert (await world.stored("dropbox_file"))["isDeleted"] is True

    # Before the retention the record stays, so the group does too.
    assert await world.tick(13) == Outcome.FINISHED
    assert await world.graph.get_document(group.id, GROUPS) is not None

    assert await world.tick(15) == Outcome.FINISHED
    assert await world.stored("dropbox_file") is None
    assert await world.graph.get_document(group.id, GROUPS) is None
    assert world.state()["lastCounts"]["groups"] == 1


@pytest.mark.parametrize("why", ["source-still-has-it", "live-record", "listed-again"])
async def test_a_group_that_is_not_only_kept_for_the_trash_stays(world: _World, why: str) -> None:
    group_id = f"rg-{uuid.uuid4().hex[:12]}"
    now = get_epoch_timestamp_in_ms()
    group = {"id": group_id, "orgId": world.org_id, "groupName": "Team", "externalGroupId": f"ext-{group_id}",
             "groupType": RecordGroupType.DRIVE.value, "connectorName": Connectors.GOOGLE_DRIVE.value,
             "connectorId": world.drive_id, "createdAtTimestamp": now, "updatedAtTimestamp": now}
    if why != "source-still-has-it":
        group.update(isDeletedAtSource=True, deletedAtSourceTimestamp=now)
    await world.graph.batch_upsert_nodes([group], collection=GROUPS)
    world.ids["group"] = group_id
    members = ["drive_file"] + (["unmarked"] if why == "live-record" else [])
    await world.graph.batch_create_edges(
        [_edge(world.ids[m], RECORDS, group_id, GROUPS) for m in members], collection=CollectionNames.BELONGS_TO.value
    )
    await world.trash("drive_file")
    if why == "listed-again":
        stored = await world.graph.get_document(group_id, GROUPS)
        await world.processor.on_new_record_groups([(RecordGroup.from_arango_base_record_group(stored), [])])
        assert (await world.graph.get_document(group_id, GROUPS))["isDeletedAtSource"] is False

    assert await world.tick(15) == Outcome.FINISHED

    assert await world.stored("drive_file") is None
    assert await world.graph.get_document(group_id, GROUPS) is not None


async def test_a_kept_parent_group_goes_in_the_same_run_as_its_kept_child(world: _World) -> None:
    now = get_epoch_timestamp_in_ms()
    ids = {name: f"rg-{name}-{uuid.uuid4().hex[:8]}" for name in ("parent", "child")}
    world.ids.update({f"group_{k}": v for k, v in ids.items()})
    await world.graph.batch_upsert_nodes(
        [{"id": key, "groupName": name, "externalGroupId": f"ext-{key}", "groupType": RecordGroupType.DRIVE.value,
          "connectorName": Connectors.GOOGLE_DRIVE.value, "connectorId": world.drive_id, "createdAtTimestamp": now,
          "updatedAtTimestamp": now, "isDeletedAtSource": True, "deletedAtSourceTimestamp": now}
         for name, key in ids.items()],
        collection=GROUPS,
    )
    await world.graph.batch_create_edges(
        [_edge(ids["child"], GROUPS, ids["parent"], GROUPS)], collection=CollectionNames.BELONGS_TO.value
    )

    assert await world.tick(15) == Outcome.FINISHED

    for key in ids.values():
        assert await world.graph.get_document(key, GROUPS) is None, key
    assert world.state()["lastCounts"]["groups"] == 2


async def test_stored_copies_of_uploads_local_fs_and_web_are_scheduled_for_removal(world: _World) -> None:
    await world.trash("upload", "local_copy", "web_page", "drive_file")

    assert await world.tick(15) == Outcome.FINISHED

    assert world.broker.stored_documents() == {
        world.kb_id: [world.docs["upload"]],
        world.local_fs_id: [world.docs["local_copy"]],
        world.web_id: [world.docs["web_page"]],
    }
    assert sorted(world.broker.deleted_record_ids()) == sorted(
        world.ids[n] for n in ("upload", "local_copy", "web_page", "drive_file")
    )


async def test_events_the_broker_refused_are_published_by_the_next_tick(world: _World) -> None:
    await world.trash("upload")
    world.broker.accept = False

    assert await world.tick(15) == Outcome.PAUSED

    assert await world.stored("upload") is None, "the graph delete went through"
    [owed] = world.kv.outbox().values()
    assert list(owed["items"]) == [world.ids["upload"]]
    assert world.state()["status"] == "running"

    world.broker.accept = True
    assert await world.tick(15) == Outcome.FINISHED
    assert world.broker.deleted_record_ids() == [world.ids["upload"]]
    assert world.broker.stored_documents() == {world.kb_id: [world.docs["upload"]]}
    assert world.kv.outbox() == {}


async def test_a_page_saved_but_never_deleted_owes_nothing(world: _World) -> None:
    """A crash between saving the outbox and the graph delete."""
    await world.trash("upload")
    purger = world.purger(15)
    page = await world.graph.get_purgeable_trashed_records(world.org_id, get_epoch_timestamp_in_ms() + 1000)
    assert [r["id"] for r in page["records"]] == [world.ids["upload"]]
    await purger._save_outbox(f"{OUTBOX_DIRECTORY}crashed-1", world.org_id,
                              {r["id"]: purge_module.cleanup_owed(r) for r in page["records"]})

    assert await purger.tick() == Outcome.FINISHED

    assert await world.stored("upload") is None
    assert world.broker.deleted_record_ids() == [world.ids["upload"]], "published once, by the run itself"
    assert world.kv.outbox() == {}


async def test_a_page_the_graph_refuses_is_retried_one_by_one(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    await world.trash("upload", "upload_twin")
    await world.graph.update_node(world.ids["upload_twin"], RECORDS, {"purgeAttempts": 4})
    real_purge = world.graph.purge_trashed_records

    async def refuse_the_twin(record_ids: list[str], *args: object, **kwargs: object) -> dict:
        if world.ids["upload_twin"] in record_ids:
            raise RuntimeError("the graph refused the write")
        return await real_purge(record_ids, *args, **kwargs)

    monkeypatch.setattr(world.graph, "purge_trashed_records", refuse_the_twin)

    assert await world.tick(15) == Outcome.FINISHED

    assert await world.stored("upload") is None
    twin = await world.stored("upload_twin")
    assert twin["isDeleted"] is True and twin["purgeAttempts"] == 5
    assert twin["purgeLastError"] == "RuntimeError: the graph refused the write"
    assert world.broker.deleted_record_ids() == [world.ids["upload"]]
    assert world.state()["lastCounts"]["failed"] == 1

    # The fifth failure was the last: it is left out from now on, and counted as stuck.
    monkeypatch.setattr(world.graph, "purge_trashed_records", real_purge)
    assert await world.tick(15) == Outcome.FINISHED
    assert (await world.stored("upload_twin"))["isDeleted"] is True
    stats = await world.graph.get_trash_purge_stats(world.org_id)
    assert stats["stuck"] == 1


async def test_a_run_that_loses_its_lease_resumes_from_its_cursor(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    world.kv.values[PLATFORM_SETTINGS_KEY]["softDeletePurge"]["pageSize"] = 1
    for name in ("upload", "upload_twin", "drive_file"):
        await world.trash(name)
    world.lease.refreshes_left = 1

    assert await world.tick(15) == Outcome.LOST_LEASE
    run = world.state()["run"]
    assert run["after"] is not None and run["purged"] == 1
    gone_first = set(world.broker.deleted_record_ids())

    world.lease.refreshes_left = None
    listing = world.graph.get_purgeable_trashed_records
    asked_after: list = []

    async def spy(*args: object, **kwargs: object) -> dict:
        asked_after.append(kwargs.get("after"))
        return await listing(*args, **kwargs)

    monkeypatch.setattr(world.graph, "get_purgeable_trashed_records", spy)
    assert await world.tick(15) == Outcome.FINISHED
    assert asked_after[0] == tuple(run["after"]), "the same run carries on where it stopped"
    assert world.state()["lastStartedAt"] == run["started_at"]
    ids = world.broker.deleted_record_ids()
    assert len(ids) == len(set(ids)) == 3, "every record once"
    assert gone_first < set(ids)
    for name in ("upload", "upload_twin", "drive_file"):
        assert await world.stored(name) is None, name


async def test_with_the_trash_off_nothing_happens(world: _World, monkeypatch: pytest.MonkeyPatch) -> None:
    await world.trash("upload")
    monkeypatch.setattr(purge_module, "is_soft_delete_enabled", AsyncMock(return_value=False))

    assert await world.tick(30) == Outcome.DISABLED

    assert (await world.stored("upload"))["isDeleted"] is True
    assert world.broker.events == []
    assert STATE_KEY not in world.kv.values


async def test_what_the_purge_leaves_alone(world: _World) -> None:
    await world.trash("folder", "drive_file", "unmarked")
    await world.graph.update_node(world.drive_id, APPS, {"status": "DELETING"})
    # The artifact registry marks a race loser deleted with no timestamp; that is not the trash.
    loser = world.ids["upload_twin"]
    await world.graph.update_node(loser, RECORDS, {"isDeleted": True})

    assert await world.tick(15) == Outcome.FINISHED

    assert (await world.stored("folder"))["isDeleted"] is True, "a live file is still in it"
    assert (await world.stored("child"))["isDeleted"] is not True
    for name in ("drive_file", "unmarked"):
        assert (await world.stored(name))["isDeleted"] is True, "its connector is being deleted"
    assert await world.graph.get_document(loser, RECORDS) is not None
    assert world.broker.events == []

    await world.trash("child", "upload")
    await world.graph.update_node(world.drive_id, APPS, {"status": None})
    assert await world.tick(15) == Outcome.FINISHED
    for name in ("folder", "child", "upload", "drive_file", "unmarked"):
        assert await world.stored(name) is None, name


async def test_both_stores_list_the_trash_in_the_same_shape(world: _World) -> None:
    """The rows the cleanup is built from, scoped to the org and the cutoff."""
    await world.trash("local_copy")
    cutoff = get_epoch_timestamp_in_ms() + 1000
    page = await world.graph.get_purgeable_trashed_records(world.org_id, cutoff)
    [row] = page["records"]
    assert page["next"] is None
    assert row["id"] == world.ids["local_copy"]
    assert row["filePath"] == f"storage://{world.docs['local_copy']}"
    assert purge_module.stored_document_ids(row) == [world.docs["local_copy"]]
    assert row["deleteRecordPayload"]["recordId"] == world.ids["local_copy"]

    assert (await world.graph.get_purgeable_trashed_records(world.org_id, world.now - DAY_MS))["records"] == []
    assert (await world.graph.get_purgeable_trashed_records(f"other-{world.org_id}", cutoff))["records"] == []
