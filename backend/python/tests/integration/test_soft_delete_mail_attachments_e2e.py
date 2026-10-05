"""A mail in the trash takes its attachments with it, against a real Neo4j and a real ArangoDB.

A Gmail mail with a direct attachment, and a second mail with its own
attachment that nobody deletes. A UI/API delete of the first mail, with the
trash on, must leave its attachment out of everything the mail is left out
of: the search permission map and checks, the record hydration search uses,
the All Records list, Knowledge Hub search, the mail's child listing and the
access check. Left live, the attachment of a trashed mail stays searchable.

A restore brings the attachment back with its mail: restoring the delete batch
returns both, and a sync that sees a connector-deleted message and its
attachment again restores both, every delete field cleared and every surface
finding them again.

Each check is first made on the live records, so a query that finds nothing
cannot pass, and the untouched mail and attachment must still be found after.

Needs Docker services. A backend whose env var is set but cannot be reached
fails, naming it; one that is not configured skips:

  docker compose -f deployment/docker-compose/docker-compose.integration.graph-db.yml \
    up -d --wait neo4j-graph-it arango-graph-it
  cd backend/python && pytest tests/integration/test_soft_delete_mail_attachments_e2e.py -m integration

Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    DeleteSource,
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
from app.models.entities import FileRecord, MailRecord, RecordType
from app.services.graph_db.arango.arango_http_provider import ArangoHTTPProvider
from app.services.graph_db.common.utils import TRASH_STATE_FIELDS
from app.services.graph_db.neo4j.neo4j_provider import Neo4jProvider
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

pytestmark = [pytest.mark.integration, pytest.mark.timeout(300)]

NEO4J_URI = os.environ.get("NEO4J_IT_URI", "bolt://localhost:17687")
NEO4J_PASSWORD = os.environ.get("NEO4J_IT_PASSWORD", "ensure-it-pass")
ARANGO_URL = os.environ.get("ARANGO_IT_URL", "http://localhost:18529")
ARANGO_PASSWORD = os.environ.get("ARANGO_IT_PASSWORD", "ensure-it-pass")
ARANGO_DB = "soft_delete_mail_attachments_it"

logger = logging.getLogger("soft-delete-mail-attachments-it")

MAILS = ("mail", "other_mail")
ATTACHMENTS = {"attachment": "mail", "other_attachment": "other_mail"}
NAMES = (*MAILS, *ATTACHMENTS)


@dataclass
class _World:
    graph: IGraphDBProvider
    org_id: str
    user_id: str
    user_key: str
    connector_id: str
    ids: dict[str, str] = field(default_factory=dict)

    def ext(self, name: str) -> str:
        return f"ext-{self.ids[name]}"

    def vrid(self, name: str) -> str:
        return f"vr-{self.ids[name]}"

    async def stored(self, name: str) -> dict:
        doc = await self.graph.get_document(self.ids[name], CollectionNames.RECORDS.value)
        assert doc is not None, f"{name} is gone from the graph"
        return doc


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
    await provider.ensure_schema()
    return provider


async def _remove(graph: IGraphDBProvider, w: _World) -> None:
    ids = [*w.ids.values(), w.user_key, w.connector_id]
    if isinstance(graph, Neo4jProvider):
        await graph.client.execute_query("MATCH (n) WHERE n.id IN $ids DETACH DELETE n", parameters={"ids": ids})
        return
    for collection in (CollectionNames.RECORDS.value, CollectionNames.FILES.value, CollectionNames.MAILS.value,
                       CollectionNames.USERS.value, CollectionNames.APPS.value):
        await graph.http_client.execute_aql(
            f"FOR d IN {collection} FILTER d._key IN @ids REMOVE d IN {collection}", {"ids": ids}
        )
    for edges in (CollectionNames.PERMISSION.value, CollectionNames.IS_OF_TYPE.value,
                  CollectionNames.USER_APP_RELATION.value, CollectionNames.RECORD_RELATIONS.value):
        await graph.http_client.execute_aql(
            f"FOR e IN {edges} FILTER PARSE_IDENTIFIER(e._from).key IN @ids "
            f"OR PARSE_IDENTIFIER(e._to).key IN @ids REMOVE e IN {edges}",
            {"ids": ids},
        )


@pytest.fixture(params=["neo4j", "arango"])
async def world(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> AsyncIterator[_World]:
    # The default, where each Neo4j statement commits on its own.
    monkeypatch.delenv("NEO4J_EXPLICIT_TRANSACTIONS", raising=False)
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
            # The index restore reads batches by; ensure_schema creates it on a real install.
            await graph.client.execute_query(
                "CREATE INDEX record_delete_batch IF NOT EXISTS FOR (n:Record) ON (n.deleteBatchId)"
            )
        suffix = uuid.uuid4().hex[:10]
        w = _World(
            graph=graph, org_id=f"org-mailatt-{suffix}", user_id=f"user-mailatt-{suffix}",
            user_key=f"ukey-mailatt-{suffix}", connector_id=f"gmail-mailatt-{suffix}",
        )
        cleanup.push_async_callback(_remove, graph, w)
        await _seed(w)
        yield w


def _mail(w: _World, name: str) -> MailRecord:
    return MailRecord(
        id=w.ids[name], org_id=w.org_id, record_name=f"{name} subject", record_type=RecordType.MAIL,
        external_record_id=w.ext(name), version=1, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_MAIL, connector_id=w.connector_id, mime_type="text/html",
        indexing_status=ProgressStatus.COMPLETED.value, subject=f"{name} subject",
        from_email=f"{w.user_id}@example.com", to_emails=[f"{w.user_id}@example.com"],
    )


def _attachment(w: _World, name: str) -> FileRecord:
    return FileRecord(
        id=w.ids[name], org_id=w.org_id, record_name=f"{name}.pdf", record_type=RecordType.FILE,
        external_record_id=w.ext(name), version=1, origin=OriginTypes.CONNECTOR,
        connector_name=Connectors.GOOGLE_MAIL, connector_id=w.connector_id, mime_type="application/pdf",
        indexing_status=ProgressStatus.COMPLETED.value, is_file=True, extension="pdf",
        parent_external_record_id=w.ext(ATTACHMENTS[name]), parent_record_type=RecordType.MAIL,
    )


async def _seed(w: _World) -> None:
    g = w.graph
    now = get_epoch_timestamp_in_ms()
    for name in NAMES:
        w.ids[name] = f"{name}-{uuid.uuid4().hex[:12]}"

    await g.batch_upsert_nodes(
        [{"id": w.user_key, "userId": w.user_id, "orgId": w.org_id, "email": f"{w.user_id}@example.com",
          "fullName": "Mail Tester", "isActive": True, "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.USERS.value,
    )
    await g.batch_upsert_nodes(
        [{"id": w.connector_id, "name": "Gmail", "type": "Gmail", "appGroup": "Google Workspace",
          "scope": "personal", "isActive": True, "orgId": w.org_id,
          "createdAtTimestamp": now, "updatedAtTimestamp": now}],
        collection=CollectionNames.APPS.value,
    )
    await g.batch_upsert_records([*(_mail(w, n) for n in MAILS), *(_attachment(w, n) for n in ATTACHMENTS)])
    for name in NAMES:
        await g.update_node(w.ids[name], CollectionNames.RECORDS.value, {"virtualRecordId": w.vrid(name)})

    def edge(from_id: str, from_col: str, to_id: str, to_col: str, **extra: object) -> dict:
        return {"from_id": from_id, "from_collection": from_col, "to_id": to_id, "to_collection": to_col,
                "createdAtTimestamp": now, "updatedAtTimestamp": now, **extra}

    records, users, apps = CollectionNames.RECORDS.value, CollectionNames.USERS.value, CollectionNames.APPS.value
    await g.batch_create_edges(
        [edge(w.user_key, users, w.ids[n], records, role="OWNER", type="USER") for n in NAMES],
        collection=CollectionNames.PERMISSION.value,
    )
    await g.batch_create_edges(
        [edge(w.user_key, users, w.connector_id, apps, syncState="COMPLETED", lastSyncUpdate=now)],
        collection=CollectionNames.USER_APP_RELATION.value,
    )
    await g.batch_create_edges(
        [edge(w.ids[mail], records, w.ids[attachment], records, relationshipType="ATTACHMENT")
         for attachment, mail in ATTACHMENTS.items()],
        collection=CollectionNames.RECORD_RELATIONS.value,
    )


def _ids_anywhere(payload: object) -> set[str]:
    """Every "id"/"_key"/"recordId" value in a nested response, whatever its shape."""
    found: set[str] = set()
    stack = [payload]
    while stack:
        item = stack.pop()
        if isinstance(item, dict):
            for key in ("id", "_key", "recordId"):
                if isinstance(item.get(key), str):
                    found.add(item[key])
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set)):
            stack.extend(item)
        elif hasattr(item, "id") and isinstance(item.id, str):
            found.add(item.id)
    return found


async def _found_by(w: _World, name: str) -> set[str]:
    """The surfaces that find *name* for its owner; a record in the trash must be found by none."""
    g = w.graph
    record_id, vrid = w.ids[name], w.vrid(name)
    found: set[str] = set()
    if await g.check_record_access_with_details(w.user_id, w.org_id, record_id) is not None:
        found.add("access check")
    if (await g.get_accessible_virtual_record_ids(w.user_id, w.org_id, raise_on_error=True)).get(vrid) == record_id:
        found.add("search permission map")
    if vrid in await g.filter_accessible_virtual_record_ids([vrid], w.user_id, w.org_id):
        found.add("search vrid check")
    if record_id in await g.filter_accessible_record_ids([record_id], w.user_id, w.org_id):
        found.add("search record check")
    if record_id in _ids_anywhere(await g.get_records_by_record_ids([record_id], w.org_id)):
        found.add("search hydration")
    listed, _, _ = await g.list_all_records(
        w.user_key, w.org_id, 0, 200, None, None, None, None, None, None, None, None, "recordName", "asc", "all",
    )
    if record_id in _ids_anywhere(listed):
        found.add("All Records list")
    hub = await g.get_knowledge_hub_search(w.org_id, w.user_key, 0, 100, "name", "asc")
    if record_id in _ids_anywhere(hub.get("nodes", [])):
        found.add("Knowledge Hub search")
    if name in ATTACHMENTS:
        children = await g.get_records_by_parent(w.connector_id, w.ext(ATTACHMENTS[name]))
        if record_id in _ids_anywhere(children):
            found.add("mail's attachment listing")
    return found


SURFACES = {
    "access check", "search permission map", "search vrid check", "search record check",
    "search hydration", "All Records list", "Knowledge Hub search",
}


async def _assert_all_found(w: _World, names: tuple[str, ...]) -> None:
    for name in names:
        expected = SURFACES | ({"mail's attachment listing"} if name in ATTACHMENTS else set())
        assert await _found_by(w, name) == expected, name


async def _trash_mail(w: _World) -> dict:
    result = await w.graph.delete_record(w.ids["mail"], w.user_id, w.org_id, soft_delete=True)
    assert result["success"] is True and result["softDeleted"] is True, result
    return result


# ---------------------------------------------------------------------------


async def test_a_trashed_mail_takes_its_attachment_into_its_batch(world: _World) -> None:
    started = get_epoch_timestamp_in_ms()
    result = await _trash_mail(world)

    mail, attachment = await world.stored("mail"), await world.stored("attachment")
    assert attachment.get("isDeleted") is True, "the attachment of a trashed mail was left live"
    fields = ("isDeleted", "deleteBatchId", "deleteSource", "deletedByUserId", "deletedAtTimestamp")
    assert {f: attachment.get(f) for f in fields} == {f: mail.get(f) for f in fields}
    assert (mail["deleteSource"], mail["deletedByUserId"], mail["deleteBatchId"]) == (
        "USER", world.user_key, result["batchId"],
    )
    assert mail["deletedAtTimestamp"] >= started
    assert set(result["virtualRecordIds"]) == {world.vrid("mail"), world.vrid("attachment")}
    # Nothing is removed: the batch can be restored as it was.
    edges = await world.graph.get_edges_from_node(
        f"{CollectionNames.RECORDS.value}/{world.ids['mail']}", CollectionNames.RECORD_RELATIONS.value
    )
    assert world.ids["attachment"] in {(e.get("_to") or e.get("to_id") or "").split("/")[-1] for e in edges}
    for name in ("other_mail", "other_attachment"):
        assert (await world.stored(name)).get("isDeleted") is not True, name


async def test_a_trashed_mails_attachment_is_found_by_nothing(world: _World) -> None:
    await _assert_all_found(world, NAMES)

    await _trash_mail(world)

    assert await _found_by(world, "mail") == set()
    assert await _found_by(world, "attachment") == set(), "the attachment of a trashed mail is still visible"
    await _assert_all_found(world, ("other_mail", "other_attachment"))


class _Producer:
    async def send_message(self, topic: str, message: dict, key: str | None = None) -> bool:
        return True

    async def send_messages(self, topic: str, messages: list) -> list[bool]:
        return [True] * len(messages)


def _processor(w: _World, monkeypatch: pytest.MonkeyPatch) -> DataSourceEntitiesProcessor:
    processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, w.graph), MagicMock())
    processor.messaging_producer = _Producer()
    processor.org_id = w.org_id
    monkeypatch.setattr(processor_module, "is_soft_delete_enabled", AsyncMock(return_value=True))
    monkeypatch.setattr(processor_module, "notify_kb_records_changed", AsyncMock())
    return processor


async def _assert_out_of_the_trash(w: _World, names: tuple[str, ...]) -> None:
    for name in names:
        doc = await w.stored(name)
        assert doc.get("isDeleted") is False, f"{name} is still in the trash"
        assert {f: doc.get(f) for f in TRASH_STATE_FIELDS} == dict.fromkeys(TRASH_STATE_FIELDS), name


async def test_restoring_the_mails_batch_brings_its_attachment_back(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    batch_id = (await _trash_mail(world))["batchId"]

    members = await world.graph.get_records_in_delete_batch(batch_id, world.org_id)
    by_id = {item["record"]["_key"]: item for item in members}
    assert set(by_id) == {world.ids["mail"], world.ids["attachment"]}, "the attachment is not in the mail's batch"
    attachment = by_id[world.ids["attachment"]]
    assert (attachment["parentId"], attachment["parentRelation"]) == (world.ids["mail"], "ATTACHMENT")

    restored = await _processor(world, monkeypatch).restore_trashed_records(
        world.connector_id, batch_id,
        [{"id": key, "name": item["record"].get("recordName")} for key, item in by_id.items()],
    )

    assert set(restored) == set(by_id)
    await _assert_out_of_the_trash(world, ("mail", "attachment"))
    await _assert_all_found(world, NAMES)


async def test_a_sync_that_sees_the_message_again_brings_its_attachment_back(
    world: _World, monkeypatch: pytest.MonkeyPatch,
) -> None:
    processor = _processor(world, monkeypatch)
    await processor.delete_record_by_external_id(world.connector_id, world.ext("mail"), world.user_id)
    for name in ("mail", "attachment"):
        doc = await world.stored(name)
        assert (doc.get("isDeleted"), doc.get("deleteSource")) == (True, DeleteSource.CONNECTOR.value), name

    seen_again = [_mail(world, "mail"), _attachment(world, "attachment")]
    minted = []
    for record in seen_again:
        record.id = str(uuid.uuid4())
        minted.append(record.id)
    await processor.on_new_records([(record, []) for record in seen_again])

    for record_id in minted:
        assert await world.graph.get_document(record_id, CollectionNames.RECORDS.value) is None
    await _assert_out_of_the_trash(world, ("mail", "attachment"))
    # Queued to be indexed again, the search permission map takes them back once indexing completes.
    for name in ("mail", "attachment"):
        assert (await world.stored(name))["indexingStatus"] == ProgressStatus.QUEUED.value, name
        await world.graph.update_node(
            world.ids[name], CollectionNames.RECORDS.value, {"indexingStatus": ProgressStatus.COMPLETED.value}
        )
    await _assert_all_found(world, NAMES)
