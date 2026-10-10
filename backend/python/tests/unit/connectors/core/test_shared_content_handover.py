"""Deleting a connector hands its copies of shared content over to a survivor.

Records with identical content share one virtualRecordId, whose stored documents
are filed once, under whichever connector indexed it first. Deleting that
connector used to delete them and re-index one survivor; a lost re-index left
the survivors reading a document that was gone. Now the copy moves under a
surviving holder before the connector's storage is deleted, and the delete is
skipped (and retried later) unless every copy moved.

Node is a small in-memory double served over HTTP, so the Python side runs its
real requests, auth and paging against it.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import jwt
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from app.config.constants.arangodb import CollectionNames
from app.connectors.services.event_service import EventService
from tests.unit.connectors.services.coordinator_stub import installed_stub

ORG = "org1"
PREFIX = "/api/v1/document/internal"


class FakeNode:
    """The storage routes the connector delete uses, over in-memory documents."""

    def __init__(self, *, handover_routes: bool = True) -> None:
        self.docs: dict[str, dict] = {}
        self.handover_routes = handover_routes
        self.fail_relocate: set[str] = set()
        self.page_size = 2
        self._next_id = 0

    def add(self, name: str, path: str, connector_id: str | None, org: str = ORG) -> str:
        self._next_id += 1
        doc_id = f"{self._next_id:024x}"
        tags = [{"key": "connectorId", "value": connector_id}] if connector_id else []
        self.docs[doc_id] = {
            "_id": doc_id, "orgId": org, "documentName": name,
            "documentPath": f"{org}/PipesHub/{path}", "customMetadata": tags,
        }
        return doc_id

    @staticmethod
    def tag(doc: dict) -> str | None:
        return next((m["value"] for m in doc["customMetadata"] if m["key"] == "connectorId"), None)

    def path(self, doc_id: str) -> str:
        return self.docs[doc_id]["documentPath"].split("/PipesHub/", 1)[1]

    @staticmethod
    def _org(request: web.Request) -> str:
        token = request.headers["Authorization"].removeprefix("Bearer ")
        return jwt.decode(token, options={"verify_signature": False})["orgId"]

    def _in_scope(self, doc: dict, org: str, connector_id: str) -> bool:
        if doc["orgId"] != org:
            return False
        prefix = f"{org}/PipesHub/records/{connector_id}"
        path = doc["documentPath"]
        return self.tag(doc) == connector_id or path == prefix or path.startswith(prefix + "/")

    async def get_document(self, request: web.Request) -> web.Response:
        doc = self.docs.get(request.match_info["doc_id"])
        if not doc or doc["orgId"] != self._org(request):
            return web.json_response({"error": "not found"}, status=404)
        return web.json_response(doc)

    async def delete_connector(self, request: web.Request) -> web.Response:
        org, cid = self._org(request), request.match_info["cid"]
        gone = [i for i, d in self.docs.items() if self._in_scope(d, org, cid)]
        for doc_id in gone:
            del self.docs[doc_id]
        return web.json_response({"deleted": len(gone)})

    async def list_vrids(self, request: web.Request) -> web.Response:
        if not self.handover_routes:
            return web.json_response({"error": "Not found"}, status=404)
        org, cid = self._org(request), request.match_info["cid"]
        after = request.query.get("after") or ""
        rows = sorted(
            (d for d in self.docs.values() if self._in_scope(d, org, cid) and d["_id"] > after),
            key=lambda d: d["_id"],
        )[: self.page_size]
        vrids = list(dict.fromkeys(d["documentName"].split("_", 1)[1] for d in rows))
        nxt = rows[-1]["_id"] if len(rows) == self.page_size else None
        return web.json_response({"virtualRecordIds": vrids, "next": nxt})

    async def relocate(self, request: web.Request) -> web.Response:
        if not self.handover_routes:
            return web.json_response({"error": "Not found"}, status=404)
        org, body = self._org(request), await request.json()
        moved, failed, missing = [], [], []
        for move in body["moves"]:
            vrid = move["virtualRecordId"]
            if vrid in self.fail_relocate:
                failed.append(vrid)
                continue
            docs = [
                d for d in self.docs.values()
                if d["documentName"] in (f"record_{vrid}", f"metadata_{vrid}")
                and self._in_scope(d, org, body["fromConnectorId"])
            ]
            for d in docs:
                d["documentPath"] = f"{org}/PipesHub/{move['newPath']}"
                d["customMetadata"] = [{"key": "connectorId", "value": move["connectorId"]}]
            (moved if docs else missing).append(vrid)
        return web.json_response({"moved": moved, "failed": failed, "missing": missing})

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_get(f"{PREFIX}/connector/{{cid}}/virtual-records", self.list_vrids)
        app.router.add_delete(f"{PREFIX}/connector/{{cid}}", self.delete_connector)
        app.router.add_post(f"{PREFIX}/records/relocate", self.relocate)
        app.router.add_get(f"{PREFIX}/{{doc_id}}", self.get_document)
        return app


class FakeGraph:
    """Records, record groups and the delete cascade, enough for a connector delete."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.groups: dict[str, list[str]] = {}
        self.apps: dict[str, dict] = {}
        self.mappings: dict[str, dict] = {}

    def add(self, key: str, connector_id: str, vrid: str, *, group: str | None = None,
            name: str | None = None, deleted: bool = False) -> None:
        self.records[key] = {
            "_key": key, "id": key, "orgId": ORG, "connectorId": connector_id,
            "connectorName": "DRIVE", "recordGroupId": group, "recordName": name or key,
            "virtualRecordId": vrid, "isDeleted": deleted, "recordType": "FILE",
        }
        self.apps.setdefault(connector_id, {"_key": connector_id, "orgId": ORG})

    # -- the new handover lookup ---------------------------------------------
    async def get_virtual_record_holders(self, virtual_record_ids, org_id, transaction=None):
        wanted = set(virtual_record_ids)
        out: dict[str, list[dict]] = {}
        for r in self.records.values():
            if r["virtualRecordId"] in wanted and r["orgId"] == org_id:
                out.setdefault(r["virtualRecordId"], []).append(dict(r))
        return out

    # -- what the code on main asks -------------------------------------------
    async def get_virtual_record_ids_shared_outside_connector(self, connector_id, transaction=None):
        mine = {r["virtualRecordId"] for r in self.records.values() if r["connectorId"] == connector_id}
        return sorted({
            r["virtualRecordId"] for r in self.records.values()
            if r["virtualRecordId"] in mine and r["connectorId"] != connector_id and not r["isDeleted"]
        })

    async def get_records_by_virtual_record_id(self, virtual_record_id, *_a, **_kw):
        return [k for k, r in self.records.items() if r["virtualRecordId"] == virtual_record_id and not r["isDeleted"]]

    async def _create_reindex_event_payload(self, record, file_record):
        return {"recordId": record["_key"]}

    # -- shared --------------------------------------------------------------
    async def get_document(self, key, collection, **_kw):
        if collection == CollectionNames.APPS.value:
            return self.apps.get(key)
        if collection == CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value:
            return self.mappings.get(key)
        return self.records.get(key)

    async def get_record_group_path(self, record_group_id, **_kw):
        return self.groups.get(record_group_id, [])

    async def get_record_group_by_id(self, record_group_id, **_kw):
        return None

    async def get_record_path_segments(self, record_id, **_kw):
        return [self.records[record_id]["recordName"]]

    async def delete_connector_instance(self, connector_id, org_id):
        gone = [k for k, r in self.records.items() if r["connectorId"] == connector_id]
        vrids = sorted({self.records[k]["virtualRecordId"] for k in gone})
        for k in gone:
            del self.records[k]
        self.apps.pop(connector_id, None)
        return {"success": True, "virtual_record_ids": vrids, "record_group_ids": [], "deleted_records_count": len(gone)}

    async def update_node(self, *_a, **_kw):
        return True

    async def batch_upsert_nodes(self, *_a, **_kw):
        return True

    async def delete_nodes(self, keys, collection):
        for key in keys:
            self.mappings.pop(key, None)


class FakeConfig:
    """Secrets and endpoints for the storage calls, and a KV for intents."""

    def __init__(self, endpoint: str) -> None:
        self.kv: dict[str, object] = {}
        self.endpoint = endpoint

    async def get_config(self, key, default=None, use_cache=True, **_kw):
        if key == "/services/secretKeys":
            return {"scopedJwtSecret": "s3cret-for-tests-only-0123456789abcdef"}
        if key == "/services/endpoints":
            return {"cm": {"endpoint": self.endpoint}}
        return self.kv.get(key, default)

    async def set_config(self, key, value):
        self.kv[key] = value
        return True

    async def delete_config(self, key):
        self.kv.pop(key, None)
        return True

    async def list_keys_in_directory(self, directory):
        return [k for k in self.kv if k.startswith(directory)]


@pytest.fixture
async def node():
    fake = FakeNode()
    server = TestServer(fake.app())
    await server.start_server()
    fake.endpoint = str(server.make_url("")).rstrip("/")
    yield fake
    await server.close()


@pytest.fixture(autouse=True)
def coordinator():
    with installed_stub() as stub:
        yield stub


def _event_service(graph: FakeGraph, config: FakeConfig) -> tuple[EventService, AsyncMock]:
    container = MagicMock()
    container.config_service.return_value = config
    container.messaging_producer = AsyncMock()
    container.messaging_producer.send_message = AsyncMock(return_value=True)
    return EventService(MagicMock(), container, graph), container.messaging_producer.send_message


async def _delete_connector(service: EventService, connector_id: str) -> bool:
    with patch("app.connectors.services.event_service.reindex_task_manager") as rtm, \
         patch("app.connectors.services.event_service.free_lane_of_deleted_connector", new=AsyncMock()):
        rtm.cancel_by_prefix = AsyncMock()
        result = await service._handle_delete("drive", {"orgId": ORG, "connectorId": connector_id})
    from app.connectors.services import event_service
    while event_service._storage_release_tasks:
        await asyncio.gather(*list(event_service._storage_release_tasks))
    return result


def _reindexed(send_message: AsyncMock) -> list[dict]:
    return [
        c.kwargs.get("message") or c.args[1]
        for c in send_message.await_args_list
        if (c.kwargs.get("message") or c.args[1]).get("eventType") == "newRecord"
    ]


def _world(node: FakeNode) -> tuple[FakeGraph, dict[str, str]]:
    """Connector A indexed v-shared first; B's record has the same content."""
    graph = FakeGraph()
    graph.groups["rg-b"] = ["Drive"]
    graph.add("a1", "conn-a", "v-shared", name="Report")
    graph.add("a2", "conn-a", "v-own", name="Notes")
    graph.add("b1", "conn-b", "v-shared", group="rg-b", name="Report")
    docs = {
        "shared_record": node.add("record_v-shared", "records/conn-a/Report", "conn-a"),
        "shared_meta": node.add("metadata_v-shared", "records/conn-a/Report", "conn-a"),
        "own": node.add("record_v-own", "records/conn-a/Notes", "conn-a"),
        "b_unrelated": node.add("record_v-b", "records/conn-b/Drive/Other", "conn-b"),
    }
    return graph, docs


class TestConnectorDelete:
    async def test_the_survivor_keeps_its_content_and_nothing_is_reindexed(self, node):
        graph, docs = _world(node)
        config = FakeConfig(node.endpoint)
        service, send = _event_service(graph, config)

        assert await _delete_connector(service, "conn-a") is True

        for key in ("shared_record", "shared_meta"):
            doc = node.docs.get(docs[key])
            assert doc is not None, f"{key} was deleted with conn-a"
            assert node.path(docs[key]) == "records/conn-b/Drive/Report"
            assert node.tag(doc) == "conn-b"
        assert docs["own"] not in node.docs
        assert docs["b_unrelated"] in node.docs
        assert _reindexed(send) == []
        assert not [k for k in config.kv if "storageRelease" in k]

    async def test_a_failed_handover_skips_the_delete_and_a_retry_completes_it(self, node):
        from app.connectors.services.storage_release import (
            StorageReleaseReconciler,
            list_pending_storage_releases,
        )

        graph, docs = _world(node)
        config = FakeConfig(node.endpoint)
        service, send = _event_service(graph, config)
        node.fail_relocate.add("v-shared")

        assert await _delete_connector(service, "conn-a") is True

        assert docs["own"] in node.docs, "storage was deleted although a handover failed"
        assert node.tag(node.docs[docs["shared_record"]]) == "conn-a"
        (intent,) = await list_pending_storage_releases(config)
        assert intent["connectorId"] == "conn-a" and intent["attempts"] == 1

        node.fail_relocate.clear()
        lock = SimpleNamespace(try_acquire=AsyncMock(return_value=True))
        reconciler = StorageReleaseReconciler(
            logger=MagicMock(), graph_provider=graph, config_service=config, lock=lock,
            now_ms=lambda: int(intent["nextAttemptAt"]) + 1,
        )
        assert await reconciler.tick() == "released"

        assert node.path(docs["shared_record"]) == "records/conn-b/Drive/Report"
        assert docs["own"] not in node.docs
        assert await list_pending_storage_releases(config) == []
        assert _reindexed(send) == []

    async def test_an_older_node_without_the_handover_routes_keeps_everything(self, node):
        graph, docs = _world(node)
        node.handover_routes = False
        config = FakeConfig(node.endpoint)
        service, _ = _event_service(graph, config)

        assert await _delete_connector(service, "conn-a") is True

        assert set(docs.values()) <= set(node.docs)
        assert [k for k in config.kv if "storageRelease" in k]

    async def test_a_connector_whose_graph_delete_failed_leaves_no_release_pending(self, node):
        graph, docs = _world(node)
        graph.delete_connector_instance = AsyncMock(return_value={"success": False, "error": "boom"})
        config = FakeConfig(node.endpoint)
        service, _ = _event_service(graph, config)

        assert await _delete_connector(service, "conn-a") is False

        assert set(docs.values()) <= set(node.docs)
        assert not [k for k in config.kv if "storageRelease" in k]


class TestOwnerChoice:
    async def _release(self, node, graph):
        from app.connectors.core.base.data_processor.storage_cleanup import (
            StorageCleanupHelper,
        )

        helper = StorageCleanupHelper(MagicMock(), graph, FakeConfig(node.endpoint))
        try:
            return await helper.release_connector_storage(ORG, "conn-a")
        finally:
            await helper.close()

    async def test_a_trashed_holder_gets_the_copy_when_no_live_one_is_left(self, node):
        graph = FakeGraph()
        graph.add("b1", "conn-b", "v1", name="Kept", deleted=True)
        doc = node.add("record_v1", "records/conn-a/x", "conn-a")

        result = await self._release(node, graph)

        assert result.completed and result.handed_over == 1
        assert node.path(doc) == "records/conn-b/Kept"
        assert node.tag(node.docs[doc]) == "conn-b"

    async def test_a_live_holder_is_preferred_then_the_smallest_key(self, node):
        graph = FakeGraph()
        graph.add("a-trashed", "conn-t", "v1", name="T", deleted=True)
        graph.add("z-live", "conn-z", "v1", name="Z")
        graph.add("c-live", "conn-c", "v1", name="C")
        doc = node.add("record_v1", "records/conn-a/x", "conn-a")

        await self._release(node, graph)

        assert node.path(doc) == "records/conn-c/C"
        assert node.tag(node.docs[doc]) == "conn-c"

    async def test_unshared_content_is_deleted_without_any_move(self, node):
        graph = FakeGraph()
        own = node.add("record_v1", "records/conn-a/x", "conn-a")

        result = await self._release(node, graph)

        assert result.completed and result.handed_over == 0
        assert own not in node.docs

    async def test_a_release_run_twice_is_a_no_op_the_second_time(self, node):
        graph = FakeGraph()
        graph.add("b1", "conn-b", "v1", name="B")
        doc = node.add("record_v1", "records/conn-a/x", "conn-a")

        first = await self._release(node, graph)
        second = await self._release(node, graph)

        assert first.completed and second.completed
        assert second.handed_over == 0
        assert node.path(doc) == "records/conn-b/B"

    async def test_a_handed_over_copy_is_purged_once_its_last_holder_goes(self, node):
        from app.modules.indexing.stored_content_cleanup import StoredContentCleanup

        graph = FakeGraph()
        graph.add("b1", "conn-b", "v1", name="B")
        doc = node.add("record_v1", "records/conn-a/x", "conn-a")
        await self._release(node, graph)
        graph.mappings["v1"] = {"_key": "v1", "orgId": ORG, "documentId": doc}

        del graph.records["b1"]

        async def purge(org_id, vrid):
            # Node purges by name wherever the document is filed.
            for doc_id in [i for i, d in node.docs.items() if d["documentName"] in (f"record_{vrid}", f"metadata_{vrid}")]:
                del node.docs[doc_id]

        blob = SimpleNamespace(purge_virtual_record_documents=AsyncMock(side_effect=purge))
        graph.get_records_by_virtual_record_id = AsyncMock(return_value=[])
        failed = await StoredContentCleanup(MagicMock(), graph, blob).release_virtual_records(["v1"], org_id=ORG)

        assert failed == []
        assert doc not in node.docs
        assert "v1" not in graph.mappings


class TestKnowledgeBaseDelete:
    async def test_kb_delete_hands_shared_content_over_the_same_way(self, node):
        from app.connectors.sources.localKB.handlers import kb_service as kb_module
        from app.connectors.sources.localKB.handlers.kb_service import (
            KnowledgeBaseService,
        )

        graph, docs = _world(node)
        graph.get_user_by_user_id = AsyncMock(return_value={"id": "u", "_key": "u"})
        graph.get_user_kb_permission = AsyncMock(return_value="OWNER")
        graph.get_uploaded_document_ids = AsyncMock(return_value=[])
        config = FakeConfig(node.endpoint)
        kafka = AsyncMock()
        kafka.publish_event = AsyncMock(return_value=True)
        service = KnowledgeBaseService(
            MagicMock(), graph, kafka, processor_for_kb=AsyncMock(), config_service=config,
        )

        with patch.object(kb_module, "free_lane_of_deleted_connector", new=AsyncMock()):
            result = await service.delete_knowledge_base("conn-a", "user", ORG)
            await asyncio.gather(*list(kb_module._BACKGROUND_TASKS))

        assert result["success"] is True
        assert node.path(docs["shared_record"]) == "records/conn-b/Drive/Report"
        assert node.tag(node.docs[docs["shared_record"]]) == "conn-b"
        assert docs["own"] not in node.docs
        reindexed = [
            c.args[1] for c in kafka.publish_event.await_args_list
            if c.args[1].get("eventType") == "newRecord"
        ]
        assert reindexed == []
        assert not [k for k in config.kv if "storageRelease" in k]


class TestReleaseFailsClosed:
    async def _release(self, node, graph):
        from app.connectors.core.base.data_processor.storage_cleanup import (
            StorageCleanupHelper,
        )

        helper = StorageCleanupHelper(MagicMock(), graph, FakeConfig(node.endpoint))
        try:
            return await helper.release_connector_storage(ORG, "conn-a")
        finally:
            await helper.close()

    def _shared(self, node):
        graph = FakeGraph()
        graph.add("b1", "conn-b", "v1", name="B")
        return graph, node.add("record_v1", "records/conn-a/x", "conn-a"), node.add("record_v2", "records/conn-a/y", "conn-a")

    async def test_a_graph_that_cannot_answer_deletes_nothing(self, node):
        graph, shared, own = self._shared(node)
        graph.get_virtual_record_holders = AsyncMock(side_effect=RuntimeError("graph down"))

        result = await self._release(node, graph)

        assert not result.completed and "graph down" in result.reason
        assert {shared, own} <= set(node.docs)

    async def test_an_owner_whose_path_cannot_be_read_deletes_nothing(self, node):
        graph, shared, own = self._shared(node)
        graph.get_record_path_segments = AsyncMock(side_effect=RuntimeError("timeout"))

        result = await self._release(node, graph)

        assert not result.completed and result.failed == 1
        assert node.path(shared) == "records/conn-a/x"
        assert own in node.docs

    async def test_a_refused_relocate_deletes_nothing(self, node):
        graph, shared, own = self._shared(node)

        async def refuse(_request):
            return web.json_response({"error": "boom"}, status=500)

        node.relocate = refuse
        server = TestServer(node.app())
        await server.start_server()
        try:
            node.endpoint = str(server.make_url("")).rstrip("/")
            result = await self._release(node, graph)
        finally:
            await server.close()

        assert not result.completed and result.failed == 1
        assert {shared, own} <= set(node.docs)

    async def test_a_failed_storage_delete_is_not_completed(self, node):
        graph, shared, own = self._shared(node)

        async def refuse(_request):
            return web.json_response({"error": "boom"}, status=503)

        node.delete_connector = refuse
        server = TestServer(node.app())
        await server.start_server()
        try:
            node.endpoint = str(server.make_url("")).rstrip("/")
            result = await self._release(node, graph)
        finally:
            await server.close()

        assert not result.completed and result.handed_over == 1
        assert "storage delete failed" in result.reason


class TestReconciler:
    NOW = 10 * 24 * 60 * 60 * 1000

    def _reconciler(self, graph, config, *, leader=True):
        from app.connectors.services.storage_release import StorageReleaseReconciler

        lock = SimpleNamespace(try_acquire=AsyncMock(return_value=leader))
        return StorageReleaseReconciler(
            logger=MagicMock(), graph_provider=graph, config_service=config, lock=lock,
            now_ms=lambda: self.NOW,
        )

    async def _intent(self, config, age_ms, **fields):
        from app.connectors.services.storage_release import (
            record_pending_storage_release,
        )

        await record_pending_storage_release(config, org_id=ORG, connector_id="conn-a", now_ms=self.NOW - age_ms)
        key = "/services/storageRelease/pending/conn-a"
        config.kv[key] = {**config.kv[key], **fields}
        return key

    async def test_only_the_leader_runs(self, node):
        config = FakeConfig(node.endpoint)
        await self._intent(config, 60 * 60 * 1000)

        assert await self._reconciler(FakeGraph(), config, leader=False).tick() == "not_leader"

    async def test_a_fresh_intent_is_left_to_the_delete_that_recorded_it(self, node):
        config = FakeConfig(node.endpoint)
        await self._intent(config, 60 * 1000)

        assert await self._reconciler(FakeGraph(), config).tick() == "idle"

    async def test_an_intent_waits_for_its_backoff(self, node):
        config = FakeConfig(node.endpoint)
        await self._intent(config, 60 * 60 * 1000, attempts=2, nextAttemptAt=self.NOW + 1)

        assert await self._reconciler(FakeGraph(), config).tick() == "idle"

    async def test_an_intent_whose_connector_still_exists_is_dropped_once_stale(self, node):
        graph = FakeGraph()
        graph.apps["conn-a"] = {"_key": "conn-a"}
        config = FakeConfig(node.endpoint)
        key = await self._intent(config, 2 * 24 * 60 * 60 * 1000)
        own = node.add("record_v1", "records/conn-a/x", "conn-a")

        assert await self._reconciler(graph, config).tick() == "dropped"
        assert key not in config.kv
        assert own in node.docs

    async def test_an_intent_whose_connector_is_still_deleting_is_kept(self, node):
        graph = FakeGraph()
        graph.apps["conn-a"] = {"_key": "conn-a", "status": "DELETING"}
        config = FakeConfig(node.endpoint)
        key = await self._intent(config, 2 * 24 * 60 * 60 * 1000)

        assert await self._reconciler(graph, config).tick() == "idle"
        assert key in config.kv

    async def test_a_retry_that_fails_again_backs_off_further(self, node):
        from app.connectors.services.storage_release import retry_delay_ms

        node.handover_routes = False
        config = FakeConfig(node.endpoint)
        key = await self._intent(config, 60 * 60 * 1000, attempts=1, nextAttemptAt=self.NOW - 1)

        assert await self._reconciler(FakeGraph(), config).tick() == "retrying"
        assert config.kv[key]["attempts"] == 2
        assert config.kv[key]["nextAttemptAt"] == self.NOW + retry_delay_ms(1)
