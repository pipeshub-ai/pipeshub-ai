"""A push service wired to fakes that behave like the real ones, for the image tests.

* ``FakeDocs`` stands in for ``StorageDocuments``: ``info_many`` answers what Node's ``GET /internal/:id?verify=1``
  answers (``storage.controller.ts:515-555``, ``storage.fingerprint.ts:95-135``) through the real
  ``parse_document_info``; ``create`` keeps the uploaded bytes; the limit is a settable platform setting.
* The materialiser is the real child code (``materialiser_child.run_task``) run in process against ``FakeStorage``.
* The graph keeps one record and applies a claim the way ``on_pushed_record`` would.
"""

from __future__ import annotations

import base64
import hashlib
import json
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

from app.connectors.sources.localKB.push import contract as C
from app.connectors.sources.localKB.push import materialiser_child as child
from app.connectors.sources.localKB.push import worker as W
from app.connectors.sources.localKB.push.image_admission import ImageAdmission
from app.connectors.sources.localKB.push.materialiser import Materialised
from app.connectors.sources.localKB.push.service import KbPushService
from app.connectors.sources.localKB.push.spool import Spool
from app.services.graph_db.interface.graph_db_provider import RefAccess
from app.services.storage_documents import StorageUploadError, parse_document_info

from .bodies import envelope, raw
from .fake_storage import FakeStorage, etag_of

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.connectors.sources.localKB.push.ledger import PushLedger

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==")
ORG, KB, USER = "org1", "kb1", "user1"


def did(n: int) -> str:
    return f"{n:024x}"


def ref(n: int) -> str:
    return f"storage://{did(n)}"


def image_body(uris: list[str], *, version: int = 1, text: str = "hello", key: str = "k1") -> bytes:
    blocks = [{"index": 0, "type": "text", "format": "txt", "data": text}]
    blocks += [{"index": i + 1, "type": "image", "format": "base64", "data": {"uri": u}} for i, u in enumerate(uris)]
    return raw(envelope({"blocks": blocks}, version=version, externalRecordId=key))


@dataclass
class Doc:
    data: bytes
    initiator: str | None = USER
    ext: str = ".png"  # Node stores the extension with its dot and never sets mimeType (storage.controller.ts:483)
    vendor: str = "local"
    trusted: bool = True
    settled: bool = True
    fp_version: int | None = 1
    counter: int = 0
    deleted: bool = False
    retired: bool = False
    awaiting: bool = False
    artifact_set: bool = False
    object_url: str | None = None
    tags: dict[str, str] = field(default_factory=dict)

    def body(self, doc_id: str) -> dict[str, Any]:
        document = {"_id": doc_id, "isDeleted": self.deleted, "sizeInBytes": len(self.data), "storageVendor": self.vendor,
                    "initiatorUserId": self.initiator, "extension": self.ext, "awaitingDirectUpload": self.awaiting}
        if self.artifact_set:
            document["artifactSet"] = {"id": "set-1"}
        if self.retired:
            document["retiredAt"] = "2026-01-01T00:00:00Z"
        if self.object_url:
            document["s3" if self.vendor == "s3" else "azureBlob"] = {"url": self.object_url}
        if self.tags:
            document["customMetadata"] = [{"key": k, "value": v} for k, v in self.tags.items()]
        out: dict[str, Any] = dict(document)  # the route answers the row itself plus storageFingerprint (storage.controller.ts:549-552)
        if self.fp_version is not None:
            value = hashlib.sha256(f"{doc_id}:{self.counter}:{etag_of(self.data)}".encode()).hexdigest()[:32]
            out["storageFingerprint"] = {
                "version": self.fp_version, "value": value, "settled": self.settled, "verified": True,
                "object": {"size": len(self.data), "etag": etag_of(self.data)}, "objectUnverifiable": False,
                "trusted": self.trusted and self.settled,
            }
        return out


class FakeDocs:
    def __init__(self, store: FakeStorage) -> None:
        self.store = store
        self.docs: dict[str, Doc] = {}
        self.limit = 30 << 20
        self.limit_error: Exception | None = None
        self.info_error: Exception | None = None
        self.info_calls: list[list[str]] = []
        self.uploads: list[bytes] = []
        self.limit_reads: list[bool] = []
        self.create_error: StorageUploadError | None = None
        self.created: dict[str, dict[str, str]] = {}  # documents made by ``create``: id -> tags
        self.purged: set[str] = set()
        self.single_infos: list[str] = []
        self.single_info_error: Exception | None = None

    def put(self, n: int, data: bytes = PNG, **kw: object) -> Doc:
        doc = Doc(data, **kw)
        self.docs[did(n)] = doc
        self.store.add(did(n), data, vendor=doc.vendor)
        return doc

    async def info_many(self, org_id, ids, *, verify=False, concurrency=8) -> object:
        self.info_calls.append(list(ids))
        if self.info_error:
            raise self.info_error
        return {i: (parse_document_info(self.docs[i].body(i), verify=verify) if i in self.docs else None) for i in dict.fromkeys(ids)}

    async def upload_limit(self, *, use_cache=False) -> object:
        self.limit_reads.append(use_cache)
        if self.limit_error:
            raise self.limit_error
        return self.limit

    async def create(self, *, org_id, name, document_path, file_path, extension, content_type, tags) -> object:
        if self.create_error:
            raise self.create_error
        self.uploads.append(open(file_path, "rb").read())
        doc_id = f"doc{len(self.uploads):021x}"
        self.created[doc_id] = dict(tags)
        return doc_id

    async def info(self, org_id, doc_id, *, verify=False) -> object:
        """``StorageDocuments.info``: one document through the real parser; None once it is purged or unknown."""
        self.single_infos.append(doc_id)
        if self.single_info_error:
            raise self.single_info_error
        if doc_id in self.purged:
            return None
        if doc_id in self.created:
            body = {"_id": doc_id, "isDeleted": False, "customMetadata": [{"key": k, "value": v} for k, v in self.created[doc_id].items()]}
            return parse_document_info(body, verify=verify)
        if doc_id in self.docs:
            return parse_document_info(self.docs[doc_id].body(doc_id), verify=verify)
        return None

    async def mint_token(self, org_id, *, ttl_s) -> object:
        return "token"

    async def _storage_url(self) -> object:
        return self.store.url

    async def exists(self, org_id, doc_id) -> object:
        return True


class InProcMaterialiser:
    """The real child code, in process; counts runs and can be told to fail."""

    def __init__(self) -> None:
        self.runs = 0
        self.tasks: list[dict[str, Any]] = []
        self.accounts: list[Any] = []
        self.script: list[tuple[list[str], Exception | None]] = []

    def make_dir(self, spool_dir: str) -> str:
        import os
        path = os.path.join(spool_dir, "imgwork")
        os.makedirs(path, exist_ok=True)
        return path

    def remove_dir(self, path: str | None) -> None:
        import shutil
        shutil.rmtree(path or "", ignore_errors=True)

    async def run(self, task: dict[str, Any], account: Callable[[str, float], None] | None = None) -> Materialised:
        from app.connectors.sources.localKB.push.contract import PushRefusal
        self.runs += 1
        self.tasks.append(task)
        if account is not None:
            self.accounts.append(account)
        if self.script:  # a scripted outcome: events to report, then a refusal
            events, refusal = self.script.pop(0)
            for event in events:
                account and account(event, 0.0)
            if refusal is not None:
                raise refusal
        try:
            out = await child.run_task({**task, "allow_loopback": True, "listed_origins": [], "request_timeout_s": task.get("request_timeout_s", 3)})
        except child.Refused as r:
            raise PushRefusal(r.status, r.reason, r.message, extra=r.extra) from None
        return Materialised(out["size"], out["content_rev"])


class FakeGraph:
    def __init__(self) -> None:
        self.record: dict[str, Any] | None = None
        self.access = RefAccess()
        self.access_error: Exception | None = None
        self.access_calls: list[list[str]] = []
        self.document_reads = 0
        self.events: list[str] = []

    async def get_document(self, key, collection, *a, **kw) -> object:
        self.document_reads += 1
        return dict(self.record) if self.record else None

    async def get_record_parent_info(self, key) -> object:
        return None

    async def validate_folder_in_kb(self, *a) -> object:
        return True

    async def find_file_by_name_in_parent(self, **kw) -> object:
        return None

    async def get_ref_access(self, user_id, org_id, ids) -> object:
        self.events.append("graph")
        self.access_calls.append(list(ids))
        if self.access_error:
            raise self.access_error
        return self.access


@dataclass
class Harness:
    svc: KbPushService
    docs: FakeDocs
    graph: FakeGraph
    store: FakeStorage
    mat: InProcMaterialiser
    admission: ImageAdmission
    claims: list[tuple[str, str]] = field(default_factory=list)
    cleanups: list[Any] = field(default_factory=list)

    async def push(self, body: bytes, **kw: object) -> object:
        return await self.svc.push(kb_id=KB, user_id=kw.pop("user_id", USER), org_id=ORG, body=body, folder_id=None)

    def stored(self) -> dict[str, Any]:
        return json.loads(self.docs.uploads[-1])


async def make_harness(tmp_path, *, materialiser: bool = True, ledger: PushLedger | None = None) -> Harness:
    store = await FakeStorage().start()
    docs, graph, mat = FakeDocs(store), FakeGraph(), InProcMaterialiser()
    spool = Spool(str(tmp_path))
    worker = SimpleNamespace(validate=AsyncMock(side_effect=lambda i, o, schema_id, schema, account=None: W.validate_body(open(i, "rb").read(), schema_id=schema_id, schema=schema, out_path=o)))
    admission = ImageAdmission()
    svc = KbPushService(
        graph_provider=graph, kb_service=MagicMock(), processor_for_kb=AsyncMock(), schema_registry=MagicMock(),
        storage=docs, worker=worker, spool=spool, publish_event=AsyncMock(return_value=True), sleep=AsyncMock(),
        materialiser=mat if materialiser else None, image_admission=admission if materialiser else None, ledger=ledger,
    )
    h = Harness(svc, docs, graph, store, mat, admission)

    async def claim(processor, existing, action, v, key, kb_id, org_id, user_id, parent, new_doc, revision) -> object:
        h.claims.append((action, key))
        rec = dict(existing or {"id": key, "orgId": org_id, "connectorId": kb_id, "indexingStatus": "COMPLETED"})
        rec["version"] = v.version
        if action in ("create", "content"):
            rec.update(externalRecordId=new_doc, externalRevisionId=revision)
        else:
            stored = C.parse_revision_id(rec["externalRevisionId"])
            rec["externalRevisionId"] = C.revision_id(v.digest, stored[2])
        graph.record = rec
        return MagicMock(event="newRecord" if action == "create" else "updateRecord", published=True), None, None, None

    async def retag(existing, v, kb_id, org_id) -> object:
        stored = C.parse_revision_id(existing["externalRevisionId"])
        graph.record = {**graph.record, "externalRevisionId": C.revision_id(v.digest, stored[2])}
        h.claims.append(("retag", ""))
        return True

    svc._claim_and_write = claim
    svc._retag = retag
    svc._processor = AsyncMock()
    svc._cleanup = AsyncMock(side_effect=lambda *a: h.cleanups.append(a))
    svc._cleanup_unless_referenced = AsyncMock(side_effect=lambda *a: h.cleanups.append(a))
    return h
