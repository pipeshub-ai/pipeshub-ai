"""Pushing application/blocks records into a knowledge base, against real Neo4j and ArangoDB.

The real service, processor, graph provider and KB service run; storage and the broker are
recording fakes. Environment: NEO4J_IT_URI, NEO4J_IT_PASSWORD, ARANGO_IT_URL, ARANGO_IT_PASSWORD.
PUSH_IT_RUNS scales the race tests (default 6).
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import json
import logging
import os
import random
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Never
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import CollectionNames
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.core.base.data_store.graph_data_store import GraphDataStore
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push import worker as W
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.service import KbPushService, push_record_key
from app.connectors.sources.localKB.push.spool import Spool
from app.services.graph_db.push_claim import PushClaim
from app.services.storage_documents import StorageUploadError
from tests.integration.real_graph import (
    backend_unavailable,
    connect_arango,
    connect_neo4j,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

pytestmark = [pytest.mark.integration, pytest.mark.timeout(600)]

ARANGO_DB = "kb_push_it"
RUNS = int(os.environ.get("PUSH_IT_RUNS", "6"))
logger = logging.getLogger("kb-push-it")


class FakeStorage:
    """Records creates, keeps the bytes, and applies cleanup events."""

    def __init__(self) -> None:
        self.docs: dict[str, bytes] = {}
        self.tags: dict[str, dict] = {}
        self.gone: set[str] = set()
        self.creates = 0
        self.fail = False
        self._ids = itertools.count(1)

    async def create(self, *, org_id, name, document_path, file_path, extension, content_type, tags) -> str:
        if self.fail:
            raise StorageUploadError("down")
        self.creates += 1
        doc_id = f"{next(self._ids):024x}"
        with open(file_path, "rb") as f:
            self.docs[doc_id] = f.read()
        self.tags[doc_id] = dict(tags)
        assert (extension, content_type) == ("bin", "application/octet-stream")
        return doc_id

    async def exists(self, org_id: str, document_id: str) -> bool:
        return document_id in self.docs and document_id not in self.gone

    async def _storage_url(self) -> str:
        return "http://storage.test"


class InlineWorker:
    """Runs task 1 in-process (the process worker has its own tests)."""

    def __init__(self) -> None:
        self.calls = 0

    async def validate(self, in_path, out_path, *, schema_id, schema) -> W.Validated:
        self.calls += 1
        try:
            return W.validate_body(open(in_path, "rb").read(), schema_id=schema_id, schema=schema, out_path=out_path)
        except PushRefusal:
            raise


class FakeConfig:
    def __init__(self) -> None:
        self.data: dict = {}
        self.logger = logger

    async def get_config(self, key, default=None, use_cache=True, raise_on_error=False) -> object:
        return self.data.get(key, default)

    async def create_config_if_absent(self, key, value) -> bool:
        return self.data.setdefault(key, value) is value

    async def set_config(self, key, value) -> bool:
        self.data[key] = value
        return True


@dataclass
class Env:
    graph: object
    org_id: str
    user_id: str
    user_key: str
    kb_id: str
    kb_service: KnowledgeBaseService
    service: KbPushService
    storage: FakeStorage
    events: list = field(default_factory=list)
    cleanups: list = field(default_factory=list)
    processor: DataSourceEntitiesProcessor = None
    seeded: dict = field(default_factory=dict)
    extra_services: list = field(default_factory=list)

    def body(self, ext="rec-1", version=1, text="hello", name="Doc", **over) -> bytes:
        obj = {"recordName": name, "mimeType": "application/blocks", "externalRecordId": ext, "version": version,
               "blocksFormat": 1, "blocks": {"blocks": [{"index": 0, "type": "text", "format": "txt", "data": text}]}}
        obj.update(over)
        return json.dumps(obj).encode()

    async def push(self, body: bytes, *, folder_id=None, service=None) -> object:
        return await (service or self.service).push(kb_id=self.kb_id, user_id=self.user_id, org_id=self.org_id, body=body, folder_id=folder_id)

    def key(self, ext="rec-1") -> str:
        return push_record_key(self.org_id, self.kb_id, ext)

    async def record(self, ext="rec-1") -> dict | None:
        return await self.graph.get_document(self.key(ext), CollectionNames.RECORDS.value)

    def live_docs(self) -> set[str]:
        return set(self.storage.docs) - {d for ev in self.cleanups for d in ev["payload"]["documentIds"]}

    def cleaned(self) -> set[str]:
        return {d for ev in self.cleanups for d in ev["payload"]["documentIds"]}


def build_service(graph, org_id, kb_service, processor, storage, env_events, env_cleanups, schemas) -> KbPushService:
    async def processor_for_kb(_kb) -> DataSourceEntitiesProcessor:
        return processor

    async def publish(event) -> bool:
        env_cleanups.append(event)
        return True

    return KbPushService(
        graph_provider=graph, kb_service=kb_service, processor_for_kb=processor_for_kb, schema_registry=schemas,
        storage=storage, worker=InlineWorker(), spool=Spool(), publish_event=publish, sleep=lambda s: asyncio.sleep(0),
    )


@pytest.fixture(params=["neo4j", "arango"])
async def env(request, monkeypatch, tmp_path) -> AsyncIterator[Env]:
    monkeypatch.setenv("PUSH_SPOOL_DIR", str(tmp_path))
    async with contextlib.AsyncExitStack() as cleanup:
        try:
            graph = await (connect_neo4j(logger, monkeypatch) if request.param == "neo4j" else connect_arango(logger, ARANGO_DB))
        except Exception as exc:
            backend_unavailable(request.param, exc)
        cleanup.push_async_callback(graph.disconnect)
        run = uuid.uuid4().hex[:10]
        org_id, user_id, user_key = f"org-push-{run}", f"user-push-{run}", f"ukey-push-{run}"
        seeded = {CollectionNames.ORGS.value: [org_id], CollectionNames.USERS.value: [user_key],
                  CollectionNames.APPS.value: [], CollectionNames.RECORDS.value: [], CollectionNames.FILES.value: []}

        async def remove() -> None:
            for collection, keys in seeded.items():
                if keys:
                    with contextlib.suppress(Exception):
                        await graph.delete_nodes_and_edges(keys, collection)

        cleanup.push_async_callback(remove)
        assert await graph.batch_upsert_nodes([{"_key": org_id, "accountType": "enterprise", "isActive": True, "name": "push"}], CollectionNames.ORGS.value)
        assert await graph.batch_upsert_nodes(
            [{"_key": user_key, "userId": user_id, "orgId": org_id, "email": f"{user_id}@example.com", "isActive": True}],
            CollectionNames.USERS.value)
        processor = DataSourceEntitiesProcessor(logger, GraphDataStore(logger, graph), MagicMock())
        processor.org_id = org_id
        processor.messaging_producer = MagicMock()
        events: list = []

        async def send_message(topic, message, key=None) -> None:
            events.append(message)

        processor.messaging_producer.send_message = send_message
        processor.messaging_producer.send_messages = AsyncMock(return_value=[True])

        async def pfk(_kb) -> DataSourceEntitiesProcessor:
            return processor

        kb_service = KnowledgeBaseService(logger, graph, MagicMock(), processor_for_kb=pfk)
        created = await kb_service.create_knowledge_base(user_id=user_id, org_id=org_id, name=f"push {run}")
        assert created and created.get("success") is not False, created
        kb_id = created["id"]
        seeded[CollectionNames.APPS.value].append(kb_id)
        from app.modules.record_metadata.schemas import MetadataSchemaRegistry

        storage, cleanups = FakeStorage(), []
        e = Env(graph, org_id, user_id, user_key, kb_id, kb_service,
                build_service(graph, org_id, kb_service, processor, storage, events, cleanups, MetadataSchemaRegistry(FakeConfig())),
                storage, events, cleanups, processor, seeded)
        yield e


def record_keys(env: Env, *exts: str) -> None:
    for ext in exts:
        env.seeded[CollectionNames.RECORDS.value].append(env.key(ext))
        env.seeded[CollectionNames.FILES.value].append(env.key(ext))


def digest_of(env: Env, body: bytes) -> str:
    out = env.service.spool.new_path("t")
    return W.validate_body(body, schema_id=None, schema=None, out_path=out).content_rev



async def test_create_publishes_one_new_record(env) -> None:
    record_keys(env, "rec-1")
    r = await env.push(env.body())
    assert (r.http_status, r.status, r.event, r.content_changed) == (201, "created", "newRecord", True)
    assert r.record_id == push_record_key(env.org_id, env.kb_id, "rec-1")
    assert env.storage.creates == 1
    (doc_id, stored), = env.storage.docs.items()
    assert env.storage.tags[doc_id] == {"connectorId": env.kb_id, "pushRecordKey": r.record_id, "contentRev": r.content_rev}
    assert C.content_rev(stored) == r.content_rev
    rec = await env.record()
    assert rec["externalRecordId"] == doc_id and rec["version"] == 1 and rec["mimeType"] == "application/blocks"
    tag, digest, rev = C.parse_revision_id(rec["externalRevisionId"])
    assert (tag, rev) == ("pb1", r.content_rev)
    assert [e["eventType"] for e in env.events] == ["newRecord"]
    assert rec["indexingStatus"] == "QUEUED" and rec.get("queuedAtTimestamp")
    assert rec["connectorId"] == env.kb_id and rec["origin"] == "UPLOAD"


async def test_identical_push_writes_nothing(env) -> None:
    record_keys(env, "rec-1")
    first = await env.push(env.body())
    before = await env.record()
    spy = AsyncMock(wraps=env.processor.on_pushed_record)
    env.processor.on_pushed_record = spy
    again = await env.push(env.body())
    assert (again.http_status, again.status, again.event, again.content_changed) == (200, "unchanged", None, False)
    assert again.content_rev == first.content_rev
    assert env.storage.creates == 1 and len(env.events) == 1 and spy.await_count == 0
    assert await env.record() == before


async def test_content_rev_before_indexing(env) -> None:
    record_keys(env, "rec-1")
    a = await env.push(env.body())
    b = await env.push(env.body())
    assert a.content_rev and a.content_rev == b.content_rev


async def test_hundred_identical_pushes(env) -> None:
    record_keys(env, "rec-1")
    results = [await env.push(env.body()) for _ in range(100)]
    assert env.storage.creates == 1 and len([e for e in env.events if e["eventType"] == "newRecord"]) == 1
    assert not [e for e in env.events if e["eventType"] == "updateRecord"]
    assert {r.content_rev for r in results} == {results[0].content_rev}



async def test_changed_content_new_document_then_update(env) -> None:
    record_keys(env, "rec-1")
    await env.push(env.body())
    old = await env.record()
    r = await env.push(env.body(version=2, text="changed"))
    assert (r.http_status, r.status, r.content_changed, r.event) == (200, "updated", True, "updateRecord")
    rec = await env.record()
    assert rec["externalRecordId"] != old["externalRecordId"] and rec["version"] == 2
    assert rec["externalRecordId"] in env.storage.docs and env.storage.creates == 2
    assert env.cleaned() == {old["externalRecordId"]}
    assert [e["eventType"] for e in env.events] == ["newRecord", "updateRecord"]
    assert not rec.get("md5Checksum") and rec["indexingStatus"] == "QUEUED"
    assert C.content_rev(env.storage.docs[rec["externalRecordId"]]) == C.parse_revision_id(rec["externalRevisionId"])[2]


async def test_rename_only_push_does_not_reindex(env) -> None:
    record_keys(env, "rec-1")
    await env.push(env.body())
    old = await env.record()
    r = await env.push(env.body(version=2, name="Renamed"))
    assert (r.status, r.content_changed, r.event) == ("updated", False, None)
    rec = await env.record()
    assert rec["recordName"] == "Renamed" and rec["version"] == 2
    assert rec["externalRecordId"] == old["externalRecordId"] and rec["externalRevisionId"] == old["externalRevisionId"]
    assert env.storage.creates == 1 and len(env.events) == 1 and not env.cleanups


async def test_stale_version_refused(env) -> None:
    record_keys(env, "rec-1")
    await env.push(env.body(version=3))
    with pytest.raises(PushRefusal) as e:
        await env.push(env.body(version=2, text="other"))
    assert (e.value.status, e.value.reason, e.value.extra["currentVersion"]) == (409, "STALE_VERSION", 3)
    assert env.storage.creates == 1 and len(env.events) == 1


async def test_equal_version_other_content_conflicts(env) -> None:
    record_keys(env, "rec-1")
    await env.push(env.body(version=2))
    with pytest.raises(PushRefusal) as e:
        await env.push(env.body(version=2, text="different"))
    assert (e.value.status, e.value.reason) == (409, "VERSION_CONFLICT")
    assert env.storage.creates == 1


async def test_ui_rename_survives_identical_push(env) -> None:
    record_keys(env, "rec-1")
    r = await env.push(env.body())
    renamed = await env.kb_service.update_record(env.user_id, r.record_id, {"recordName": "UI name"})
    assert renamed["success"] is True, renamed
    again = await env.push(env.body())
    assert again.status == "unchanged"
    assert (await env.record())["recordName"] == "UI name"


async def test_tag_upgrade_is_metadata_only(env) -> None:
    record_keys(env, "rec-1")
    await env.push(env.body())
    rec = await env.record()
    tag, digest, rev = C.parse_revision_id(rec["externalRevisionId"])
    old_tag = f"pb0:{digest}:{rev}"
    await env.graph.batch_upsert_nodes([{**rec, "_key": rec.get("_key") or rec["id"], "externalRevisionId": old_tag}], CollectionNames.RECORDS.value)
    again = await env.push(env.body())
    assert again.status == "unchanged"
    after = await env.record()
    assert after["externalRevisionId"] == f"pb1:{digest}:{rev}"
    assert after["indexingStatus"] == rec["indexingStatus"] and after.get("queuedAtTimestamp") == rec.get("queuedAtTimestamp")
    assert len(env.events) == 1 and env.storage.creates == 1



async def test_folder_on_create_only(env) -> None:
    folder = await env.kb_service.create_folder_in_kb(env.kb_id, "docs", env.user_id, env.org_id)
    assert folder["success"] is not False, folder
    env.seeded[CollectionNames.RECORDS.value].append(folder["id"])
    record_keys(env, "rec-1", "rec-2")
    with pytest.raises(PushRefusal) as e:
        await env.push(env.body(), folder_id="nope")
    assert (e.value.status, e.value.reason) == (404, "FOLDER_NOT_FOUND")
    assert env.storage.creates == 0
    await env.push(env.body(), folder_id=folder["id"])
    info = await env.graph.get_record_parent_info(env.key())
    assert info and info.get("id") == folder["id"]
    await env.push(env.body(version=2, text="x"), folder_id=None)
    info = await env.graph.get_record_parent_info(env.key())
    assert info and info.get("id") == folder["id"]


async def test_trashed_record_then_purged(env) -> None:
    record_keys(env, "rec-1")
    await env.push(env.body())
    rec = await env.record()
    await env.graph.batch_upsert_nodes([{**rec, "_key": rec.get("_key") or rec["id"], "isDeleted": True}], CollectionNames.RECORDS.value)
    with pytest.raises(PushRefusal) as e:
        await env.push(env.body(version=2, text="x"))
    assert (e.value.status, e.value.reason) == (409, "RECORD_IN_TRASH")
    creates = env.storage.creates
    await env.graph.delete_nodes_and_edges([env.key()], CollectionNames.RECORDS.value)
    await env.graph.delete_nodes_and_edges([env.key()], CollectionNames.FILES.value)
    r = await env.push(env.body(version=2, text="x"))
    assert (r.http_status, r.record_id) == (201, env.key())
    assert env.storage.creates == creates + 1


async def test_duplicate_name_refused(env) -> None:
    record_keys(env, "rec-1", "rec-2")
    await env.push(env.body(ext="rec-1", name="Same"))
    with pytest.raises(PushRefusal) as e:
        await env.push(env.body(ext="rec-2", name="Same", text="other"))
    assert (e.value.status, e.value.reason) == (409, "DUPLICATE_NAME")
    assert await env.record("rec-2") is None



async def test_reserved_property_projects_nothing(env) -> None:
    record_keys(env, "rec-1")
    schema = await env.service.schemas.register(env.org_id, env.kb_id, [])
    body = env.body(metadata={"schemaId": schema.schema_id, "values": {"orgId": "x"}})
    with pytest.raises(PushRefusal) as e:
        await env.push(body)
    assert (e.value.status, e.value.reason, e.value.location) == (422, "PROPERTY_RESERVED", "orgId")
    assert env.storage.creates == 0 and not env.events and await env.record() is None


async def test_properties_validated_against_the_named_version_and_stored(env) -> None:
    from app.models.record_metadata import PropertyDefinition, PropertyType

    record_keys(env, "rec-1")
    v1 = await env.service.schemas.register(env.org_id, env.kb_id, [PropertyDefinition(name="team", type=PropertyType.STRING, display_label="Team")])
    v2 = await env.service.schemas.register(env.org_id, env.kb_id, [PropertyDefinition(name="team", type=PropertyType.STRING, display_label="Team"),
                                                                     PropertyDefinition(name="n", type=PropertyType.INT, display_label="N")])
    with pytest.raises(PushRefusal) as e:
        await env.push(env.body(metadata={"schemaId": v1.schema_id, "values": {"n": 1}}))
    assert e.value.reason == "PROPERTY_UNKNOWN"
    r = await env.push(env.body(metadata={"schemaId": v2.schema_id, "values": {"n": 1, "team": "core"}}))
    stored = json.loads(next(iter(env.storage.docs.values())))
    assert stored["properties"] == {"schemaId": v2.schema_id, "values": {"n": 1, "team": "core"}} and r.schema_id == v2.schema_id


async def test_schema_of_another_kb_or_org_is_a_mismatch(env) -> None:
    for sid in (f"{env.org_id}:other-kb:1", f"other-org:{env.kb_id}:1"):
        with pytest.raises(PushRefusal) as e:
            await env.push(env.body(metadata={"schemaId": sid, "values": {}}))
        assert (e.value.status, e.value.reason) == (422, "SCHEMA_MISMATCH")
    with pytest.raises(PushRefusal) as e:
        await env.push(env.body(metadata={"schemaId": f"{env.org_id}:{env.kb_id}:9", "values": {}}))
    assert e.value.reason == "SCHEMA_NOT_FOUND"


async def test_roles_and_other_org(env) -> None:
    other = f"stranger-{uuid.uuid4().hex[:8]}"
    ukey = f"ukey-{other}"
    env.seeded[CollectionNames.USERS.value].append(ukey)
    assert await env.graph.batch_upsert_nodes(
        [{"_key": ukey, "userId": other, "orgId": f"org-b-{other}", "email": f"{other}@example.com", "isActive": True}], CollectionNames.USERS.value)
    with pytest.raises(PushRefusal) as e:
        await env.service.authorize(env.kb_id, other)
    assert (e.value.status, e.value.reason) == (404, "KB_NOT_FOUND")
    assert await env.service.authorize(env.kb_id, env.user_id) == env.user_key
    reader = f"reader-{uuid.uuid4().hex[:8]}"
    rkey = f"ukey-{reader}"
    env.seeded[CollectionNames.USERS.value].append(rkey)
    assert await env.graph.batch_upsert_nodes(
        [{"_key": rkey, "userId": reader, "orgId": env.org_id, "email": f"{reader}@example.com", "isActive": True}], CollectionNames.USERS.value)
    granted = await env.kb_service.create_kb_permissions(env.kb_id, env.user_id, user_ids=[reader], team_ids=[], role="READER")
    assert granted.get("success") is not False, granted
    with pytest.raises(PushRefusal) as e:
        await env.service.authorize(env.kb_id, reader)
    assert (e.value.status, e.value.reason) == (403, "KB_ROLE_INSUFFICIENT")



async def test_graph_failure_cleans_new_document(env) -> None:
    record_keys(env, "rec-1")
    real = env.processor.on_pushed_record
    env.processor.on_pushed_record = AsyncMock(side_effect=RuntimeError("graph down"))
    with pytest.raises(RuntimeError):
        await env.push(env.body())
    assert env.cleaned() == set(env.storage.docs) and not env.events and await env.record() is None
    env.processor.on_pushed_record = real
    r = await env.push(env.body())
    assert r.http_status == 201 and len(env.live_docs()) == 1


async def test_crash_before_commit_converges(env) -> None:
    record_keys(env, "rec-1")
    real = env.processor.on_pushed_record
    env.processor.on_pushed_record = AsyncMock(side_effect=asyncio.CancelledError())
    with pytest.raises(asyncio.CancelledError):
        await env.push(env.body())
    env.processor.on_pushed_record = real
    r = await env.push(env.body())
    assert r.http_status == 201
    rec = await env.record()
    assert env.storage.docs[rec["externalRecordId"]] and C.content_rev(env.storage.docs[rec["externalRecordId"]]) == r.content_rev
    assert all(t["pushRecordKey"] == env.key() and t["connectorId"] == env.kb_id for t in env.storage.tags.values())


async def test_failed_update_then_revert(env) -> None:
    record_keys(env, "rec-1")
    await env.push(env.body(version=1, text="A"))
    real = env.processor.on_pushed_record
    env.processor.on_pushed_record = AsyncMock(side_effect=RuntimeError("claim failed"))
    with pytest.raises(RuntimeError):
        await env.push(env.body(version=2, text="B"))
    env.processor.on_pushed_record = real
    r = await env.push(env.body(version=3, text="A"))
    rec = await env.record()
    assert r.content_changed is False
    assert b'"data":"A"' in env.storage.docs[rec["externalRecordId"]]
    assert C.content_rev(env.storage.docs[rec["externalRecordId"]]) == C.parse_revision_id(rec["externalRevisionId"])[2]
    assert rec["version"] == 3


async def test_publish_failure_leaves_record_for_sweep(env) -> None:
    record_keys(env, "rec-1")

    async def boom(topic, message, key=None) -> Never:
        raise ConnectionError("broker down")

    env.processor.messaging_producer.send_message = boom
    r = await env.push(env.body())
    assert (r.http_status, r.event, r.indexing_status) == (201, "newRecord", "NOT_STARTED")
    rec = await env.record()
    assert rec["indexingStatus"] == "NOT_STARTED" and rec.get("queuedAtTimestamp")
    again = await env.push(env.body())
    assert again.status == "unchanged"


async def test_update_publish_failure_leaves_record_not_started(env) -> None:
    record_keys(env, "rec-1")
    await env.push(env.body())
    rec = await env.record()
    await env.graph.batch_upsert_nodes([{**rec, "_key": rec.get("_key") or rec["id"], "indexingStatus": "COMPLETED", "md5Checksum": "abc", "virtualRecordId": "vr-1"}], CollectionNames.RECORDS.value)

    async def boom(topic, message, key=None) -> Never:
        raise ConnectionError("broker down")

    env.processor.messaging_producer.send_message = boom
    r = await env.push(env.body(version=2, text="new"))
    assert r.status == "updated"
    after = await env.record()
    assert after["indexingStatus"] == "NOT_STARTED" and not after.get("md5Checksum") and after.get("queuedAtTimestamp")
    assert after["virtualRecordId"] == "vr-1"


async def test_storage_down_is_503_and_writes_nothing(env) -> None:
    record_keys(env, "rec-1")
    env.storage.fail = True
    with pytest.raises(PushRefusal) as e:
        await env.push(env.body())
    assert e.value.status == 503 and await env.record() is None and not env.events


# ---- claim semantics ----------------------------------------------------------------------------------

async def test_claim_results_on_both_stores(env) -> None:
    record_keys(env, "rec-1")
    await env.push(env.body())
    rec = await env.record()
    provider = env.graph
    from app.services.graph_db.push_claim import PushExpect, PushSet

    kw = {"kb_id": env.kb_id, "org_id": env.org_id, "key": env.key()}
    assert await provider.claim_push_record(expected=None, new=PushSet("d", 1), **kw) is PushClaim.KEY_TAKEN
    assert await provider.claim_push_record(expected=PushExpect("wrong", rec["externalRevisionId"], 1), new=PushSet("d", 2), **kw) is PushClaim.LOST
    assert await provider.claim_push_record(expected=None, new=PushSet("d", 1), kb_id="nope", org_id=env.org_id, key="k") is PushClaim.KB_MISSING
    assert await provider.claim_push_record(expected=None, new=PushSet("d", 1), kb_id=env.kb_id, org_id="other", key="k") is PushClaim.KB_MISSING
    # An app of the same org that is not a knowledge base (a connector) is no place to push to.
    connector_app = f"{env.kb_id}-connector"
    env.seeded[CollectionNames.APPS.value].append(connector_app)
    await provider.batch_upsert_nodes(
        [{"_key": connector_app, "name": "connector", "type": "JIRA", "appGroup": "Atlassian", "scope": "team",
          "isActive": True, "createdAtTimestamp": 1, "orgId": env.org_id}], CollectionNames.APPS.value,
    )
    for expected in (None, PushExpect("d", rec["externalRevisionId"], 1)):
        assert await provider.claim_push_record(expected=expected, new=PushSet("d", 2), kb_id=connector_app, org_id=env.org_id, key=env.key()) is PushClaim.KB_MISSING
    kb = await provider.get_document(env.kb_id, CollectionNames.APPS.value)
    await provider.batch_upsert_nodes([{**kb, "_key": kb.get("_key") or kb["id"], "status": "DELETING"}], CollectionNames.APPS.value)
    try:
        assert await provider.claim_push_record(expected=None, new=PushSet("d", 1), kb_id=env.kb_id, org_id=env.org_id, key="k") is PushClaim.KB_DELETING
        with pytest.raises(PushRefusal) as e:
            await env.push(env.body(ext="rec-9", name="Other"))
        assert (e.value.status, e.value.reason) == (409, "KB_BEING_DELETED")
        assert env.cleaned() == {d for d in env.storage.docs if d not in {rec["externalRecordId"]}}
    finally:
        await provider.batch_upsert_nodes([{**kb, "_key": kb.get("_key") or kb["id"]}], CollectionNames.APPS.value)


async def test_the_claim_refuses_a_trashed_record_on_both_stores(env: Env) -> None:
    """The claim's own isDeleted condition, with the decide-time check and the upsert skip out of play."""
    from app.services.graph_db.push_claim import PushExpect, PushSet

    record_keys(env, "rec-1")
    await env.push(env.body())
    rec = await env.record()
    expected = PushExpect(rec["externalRecordId"], rec["externalRevisionId"], rec["version"])
    kw = {"kb_id": env.kb_id, "org_id": env.org_id, "key": env.key()}
    assert await env.graph.claim_push_record(expected=expected, new=PushSet("d2", 2), **kw) is PushClaim.CLAIMED
    await env.graph.batch_upsert_nodes([{**rec, "_key": rec.get("_key") or rec["id"], "isDeleted": True}], CollectionNames.RECORDS.value)
    assert await env.graph.claim_push_record(expected=expected, new=PushSet("d3", 3), **kw) is PushClaim.LOST
    assert (await env.record())["isDeleted"] is True


# ---- the race ------------------------------------------------------------------------------------

async def race(env: Env, seed: int) -> list:
    rng = random.Random(seed)
    versions = list(range(1, 17))
    rng.shuffle(versions)
    results = await asyncio.gather(*(env.push(env.body(version=v, text=f"content {v}")) for v in versions), return_exceptions=True)
    return list(zip(versions, results))


@pytest.mark.parametrize("seed", range(RUNS))
async def test_same_key_race(env, seed) -> None:
    record_keys(env, "rec-1")
    outcomes = await race(env, seed)
    for v, r in outcomes:
        assert not isinstance(r, Exception) or (isinstance(r, PushRefusal) and r.status == 409), (v, r)
    ok = [(v, r) for v, r in outcomes if not isinstance(r, Exception)]
    assert ok, outcomes
    rec = await env.record()
    assert rec is not None
    top = max(v for v, _ in ok)
    assert rec["version"] == top
    doc = rec["externalRecordId"]
    assert C.content_rev(env.storage.docs[doc]) == C.parse_revision_id(rec["externalRevisionId"])[2]
    assert env.live_docs() == {doc}, (env.live_docs(), doc)
    assert len([e for e in env.events if e["eventType"] in ("newRecord", "updateRecord")]) == len([1 for _, r in ok if r.event])


async def test_same_key_race_with_claim_stubbed_is_caught(env) -> None:
    """Negative control: with an always-true claim, concurrent pushes break the invariant in some run."""
    record_keys(env, "rec-1")

    async def always(**kw) -> PushClaim:
        return PushClaim.CLAIMED

    env.graph.claim_push_record = always
    broken = False
    for seed in range(RUNS * 2):
        outcomes = await race(env, seed + 100)
        rec = await env.record()
        ok_versions = [v for v, r in outcomes if not isinstance(r, Exception)]
        live = env.live_docs()
        if rec["version"] != max(ok_versions) or live != {rec["externalRecordId"]}:
            broken = True
            break
        await env.graph.delete_nodes_and_edges([env.key()], CollectionNames.RECORDS.value)
        await env.graph.delete_nodes_and_edges([env.key()], CollectionNames.FILES.value)
    assert broken, "a stubbed claim should let a stale push win or leave two live documents"


# ---- the store ends the push's transaction mid-claim: decide again, never 500 --------------------------------------

def _inject_once(env: Env, nth: int) -> dict:
    """Fail the ``nth`` statement the push sends inside a transaction, once, the way each store would."""
    from neo4j.exceptions import TransientError

    from app.services.graph_db.arango.arango_http_client import ArangoQueryError

    state = {"seen": 0, "fired": False}
    graph = env.graph
    if hasattr(graph, "http_client"):
        target, name = graph.http_client, "execute_aql"
        positional = 2

        def in_txn(args: tuple, kwargs: dict) -> bool:
            return (args[positional] if len(args) > positional else kwargs.get("txn_id")) is not None

        def error() -> Exception:
            return ArangoQueryError("Query failed", 410, '{"code":410,"error":true,"errorNum":1654,"errorMessage":"transaction has already been aborted"}')
    else:
        target, name = graph.client, "execute_query"

        def in_txn(args: tuple, kwargs: dict) -> bool:
            return kwargs.get("txn_id") is not None

        def error() -> Exception:
            return TransientError("Neo.TransientError.Transaction.Terminated: injected")

    real = getattr(target, name)

    async def wrapper(*args: object, **kwargs: object) -> object:
        if in_txn(args, kwargs):
            state["seen"] += 1
            if state["seen"] == nth and not state["fired"]:
                state["fired"] = True
                raise error()
        return await real(*args, **kwargs)

    setattr(target, name, wrapper)
    state["restore"] = lambda: setattr(target, name, real)
    return state


@pytest.mark.parametrize("nth", range(1, 13))
@pytest.mark.parametrize("action", ["create", "content", "metadata"])
async def test_a_transaction_ended_by_the_store_is_decided_again_not_a_500(env: Env, nth: int, action: str) -> None:
    record_keys(env, "rec-1")
    if action != "create":
        await env.push(env.body())
    before = await env.record()
    state = _inject_once(env, nth)
    try:
        if action == "create":
            r = await env.push(env.body())
        elif action == "content":
            r = await env.push(env.body(version=2, text="changed"))
        else:
            r = await env.push(env.body(version=2, name="Renamed"))
    except PushRefusal as e:
        assert e.status == 409, (nth, action, e.status, e.reason)  # a clean refusal, never 500
        r = None
    finally:
        state["restore"]()
    rec = await env.record()
    if r is not None:
        assert r.http_status in (200, 201)
        assert rec["version"] == (1 if action == "create" else 2)
    doc = rec["externalRecordId"] if rec else None
    if rec is not None:
        assert C.content_rev(env.storage.docs[doc]) == C.parse_revision_id(rec["externalRevisionId"])[2]
        assert env.live_docs() == {doc}, (nth, action, env.live_docs(), doc)
    events = [e["eventType"] for e in env.events]
    expected = {"create": ["newRecord"], "content": ["newRecord", "updateRecord"], "metadata": ["newRecord"]}[action]
    if r is not None:
        assert events == expected, (nth, action, events)
    if before is not None and r is None:
        assert rec["version"] == before["version"]
