"""Push one ``application/blocks`` record into a knowledge base.

The flow, in order, with nothing written before step 5:

1. access (role on the KB) before any body byte is read;
2. the body is validated in the worker (``worker.py``): shape, DTO, images, structure,
   property values, canonical stored bytes, digest and ``contentRev``;
3. the record at the key is read without a lock and the decision table is applied;
4. an unchanged push answers now; a refused one answers its 409;
5. otherwise (content change) the stored bytes become a new, never-overwritten storage
   document, then the conditional claim and the record upsert run in one transaction.

A lost claim means someone else moved the record: the push re-reads and re-decides.
The graph write names a storage document only in the same transaction that names its
digest, so a document a record references always holds the bytes its ``contentRev`` hashes.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
    PushClaimRefused,
    PushOutcome,
    PushRecordSkipped,
)
from app.connectors.services.vector_cleanup_events import (
    build_stored_document_cleanup_events,
)
from app.models.entities import FileRecord, RecordType
from app.models.source_dates import upload_origin
from app.services.graph_db.push_claim import (
    KB_DELETING_STATUS,
    PushClaim,
    PushExpect,
    PushSet,
)
from app.services.storage_documents import StorageDocuments, StorageUploadError
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from . import contract as C
from .contract import PushRefusal
from .retry import is_attempt_lost
from .worker import PushWorker, Validated, schema_id_hint

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
    from app.models.entities import FileRecord as FileRecordT
    from app.modules.record_metadata.schemas import MetadataSchemaRegistry
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider

    from .spool import Spool

logger = logging.getLogger(__name__)

#: Fixed namespace of the record key: one record per (org, KB, externalRecordId).
PUSH_NS = uuid.UUID("5f3c1a52-8e0e-5b64-9d0a-1c5b7f2f6a10")
MAX_ATTEMPTS = 5
#: Total time the graph retries may take, so a caller's push slot is freed in seconds.
RETRY_BUDGET_S = 5.0
PUSH_MIME = "application/blocks"
STORAGE_PATH = "PipesHub/KnowledgeBase/push"
WRITE_ROLES = ["OWNER", "WRITER"]


def push_record_key(org_id: str, kb_id: str, external_record_id: str) -> str:
    return str(uuid.uuid5(PUSH_NS, f"{org_id}\x1f{kb_id}\x1f{external_record_id}"))


@dataclass
class PushResult:
    status: str  # created | updated | unchanged
    http_status: int
    record_id: str
    kb_id: str
    external_record_id: str
    version: int
    content_changed: bool
    content_rev: str
    schema_id: str | None
    event: str | None
    indexing_status: str | None

    def to_json(self) -> dict[str, Any]:
        return {
            "recordId": self.record_id, "kbId": self.kb_id, "externalRecordId": self.external_record_id,
            "version": self.version, "status": self.status, "contentChanged": self.content_changed,
            "contentRev": self.content_rev, "schemaId": self.schema_id, "event": self.event,
            "indexingStatus": self.indexing_status,
        }


def _refuse(status: int, reason: str, message: str, **extra: object) -> PushRefusal:
    return PushRefusal(status, reason, message, extra=extra)


class KbPushService:
    def __init__(
        self,
        *,
        graph_provider: IGraphDBProvider,
        kb_service: KnowledgeBaseService,
        processor_for_kb: Callable[[str], Awaitable[DataSourceEntitiesProcessor]],
        schema_registry: MetadataSchemaRegistry,
        storage: StorageDocuments,
        worker: PushWorker,
        spool: Spool,
        publish_event: Callable[[dict[str, Any]], Awaitable[bool]],
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.graph = graph_provider
        self.kb_service = kb_service
        self.processor_for_kb = processor_for_kb
        self.schemas = schema_registry
        self.storage = storage
        self.worker = worker
        self.spool = spool
        self.publish_event = publish_event
        self._sleep = sleep

    # ---- access -----------------------------------------------------------------------------

    async def authorize(self, kb_id: str, user_id: str, roles: list[str] | None = WRITE_ROLES) -> str:
        """The caller's graph key, or a refusal: no role is a 404, a lower role a 403."""
        user_key, _role, error = await self.kb_service._resolve_user_and_kb_access(
            kb_id, user_id, required_roles=roles, for_write=roles is not None,
        )
        if error:
            code = error.get("code")
            if code == 403:
                raise _refuse(403, "KB_ROLE_INSUFFICIENT", "You do not have permission to write to this knowledge base.")
            if code == 404:
                raise _refuse(404, "KB_NOT_FOUND", "Knowledge base not found.")
            if code == 409:
                raise _refuse(409, "KB_BEING_DELETED", "This knowledge base is being deleted.")
            raise _refuse(500, "INTERNAL_ERROR", "The push could not be started.")
        return user_key

    # ---- schema -----------------------------------------------------------------------------

    async def _load_schema(self, org_id: str, kb_id: str, body: bytes) -> tuple[str | None, dict[str, Any] | None]:
        hint = schema_id_hint(body)
        if hint is None:
            return None, None
        parts = hint.rsplit(":", 2)
        if len(parts) != 3 or parts[0] != org_id or parts[1] != kb_id or not (parts[2].isascii() and parts[2].isdigit()) or len(parts[2]) > 6:
            raise PushRefusal(422, "SCHEMA_MISMATCH", "metadata.schemaId does not name a schema of this knowledge base.",
                              "metadata.schemaId", extra={"code": "ENVELOPE_INVALID"})
        schema = await self.schemas.get(org_id, kb_id, int(parts[2]))
        if schema is None:
            raise PushRefusal(422, "SCHEMA_NOT_FOUND", "No such schema version.", "metadata.schemaId", extra={"code": "ENVELOPE_INVALID"})
        return hint, schema.model_dump(mode="json")

    # ---- the push ---------------------------------------------------------------------------

    async def push(self, *, kb_id: str, user_id: str, org_id: str, body: bytes, folder_id: str | None) -> PushResult:
        in_path = self.spool.write(body)
        out_path = self.spool.new_path("out")
        try:
            schema_id, schema = await self._load_schema(org_id, kb_id, body)
            validated = await self.worker.validate(in_path, out_path, schema_id=schema_id, schema=schema)
            return await self._write(kb_id=kb_id, user_id=user_id, org_id=org_id, v=validated, folder_id=folder_id)
        finally:
            self.spool.remove(in_path, out_path)

    def _decide(self, existing: dict[str, Any] | None, v: Validated, kb_id: str, org_id: str) -> tuple[str, dict[str, Any] | None]:
        """``(action, extra)``; action is create | content | metadata | unchanged | retag, or raises the 409."""
        if existing is None:
            return "create", None
        if existing.get("isDeleted") is True:
            raise _refuse(409, "RECORD_IN_TRASH", "This record is in the trash. Purge or restore it first.")
        parsed = C.parse_revision_id(existing.get("externalRevisionId"))
        if existing.get("orgId") != org_id or existing.get("connectorId") != kb_id or parsed is None:
            raise _refuse(409, "RECORD_KEY_CONFLICT", "This key belongs to a record that was not pushed.")
        tag, digest, rev = parsed
        stored_version = existing.get("version")
        if v.version < stored_version:
            raise _refuse(409, "STALE_VERSION", "A newer version of this record is stored.", currentVersion=stored_version)
        same_content = (tag == C.NORMALISER and digest == v.digest) or rev == v.content_rev
        if v.version == stored_version:
            if not same_content:
                raise _refuse(409, "VERSION_CONFLICT", "This version is stored with different content.", currentVersion=stored_version)
            return ("retag", None) if tag != C.NORMALISER else ("unchanged", None)
        return ("metadata", None) if same_content else ("content", None)

    async def _write(self, *, kb_id: str, user_id: str, org_id: str, v: Validated, folder_id: str | None) -> PushResult:
        key = push_record_key(org_id, kb_id, v.external_record_id)
        new_doc: str | None = None
        new_doc_is_ours = False
        first_loss: float | None = None  # the retry budget runs from the first lost attempt, not from the push's start
        try:
            for attempt in range(MAX_ATTEMPTS):
                if first_loss is not None and time.monotonic() - first_loss > RETRY_BUDGET_S:
                    break
                existing = await self.graph.get_document(key, CollectionNames.RECORDS.value, raise_on_error=True)
                action, _ = self._decide(existing, v, kb_id, org_id)
                if action == "unchanged" and await self._document_is_gone(existing, org_id):
                    action = "content"  # heal: same version, same bytes, a new document
                if action == "unchanged":
                    return self._result(existing, v, key, kb_id, "unchanged", 200, changed=False, event=None, indexing=existing.get("indexingStatus"))
                if action == "retag":
                    if not await self._retag(existing, v, kb_id, org_id):
                        # A concurrent push moved the revision between the read and the retag.
                        first_loss = first_loss if first_loss is not None else time.monotonic()
                        await self._backoff(attempt)
                        continue
                    return self._result(existing, v, key, kb_id, "unchanged", 200, changed=False, event=None, indexing=existing.get("indexingStatus"))
                parent_folder_id = await self._placement(existing, key, kb_id, folder_id)
                await self._check_name(existing, v, kb_id, parent_folder_id, key)
                processor = await self._processor(kb_id)
                revision = C.revision_id(v.digest, v.content_rev)
                if action in ("create", "content") and new_doc is None:
                    new_doc = await self._store(org_id, kb_id, key, v)
                    new_doc_is_ours = True
                try:
                    outcome, record, expected, new = await self._claim_and_write(processor, existing, action, v, key, kb_id, org_id, user_id, parent_folder_id, new_doc, revision)
                except PushClaimRefused as refused:
                    if refused.claim is PushClaim.KB_MISSING:
                        raise _refuse(404, "KB_NOT_FOUND", "Knowledge base not found.") from None
                    if refused.claim is PushClaim.KB_DELETING:
                        raise _refuse(409, "KB_BEING_DELETED", "This knowledge base is being deleted.") from None
                    first_loss = first_loss if first_loss is not None else time.monotonic()
                    await self._backoff(attempt)
                    continue
                except PushRecordSkipped:
                    first_loss = first_loss if first_loss is not None else time.monotonic()
                    await self._backoff(attempt)
                    continue
                except Exception as e:
                    if not is_attempt_lost(e):
                        if getattr(e, "commit_outcome_unknown", False):
                            # The store may have applied the commit after the client gave up: the record
                            # may now name this document, and the re-read at cleanup cannot know yet.
                            # Leave it to the orphan sweep rather than risk deleting a referenced one.
                            new_doc_is_ours = False
                        raise
                    logger.warning("push attempt %d lost its graph transaction, deciding again: %s", attempt + 1, str(e)[:200])
                    first_loss = first_loss if first_loss is not None else time.monotonic()
                    await self._backoff(attempt)
                    continue
                # Only a create or a content change names the new document; after a lost attempt the
                # decision may have become a metadata change, which keeps the stored one.
                new_doc_is_ours = new_doc_is_ours and action not in ("create", "content")
                if action == "create":
                    await self._undo_create_in_deleted_kb(key, kb_id, org_id, new_doc)
                if action == "content" and existing.get("externalRecordId"):
                    await self._cleanup(org_id, kb_id, [existing["externalRecordId"]])
                status = "created" if action == "create" else "updated"
                indexing = "NOT_STARTED"
                if outcome.event:
                    indexing = "QUEUED" if outcome.published else "NOT_STARTED"
                elif existing is not None:
                    indexing = existing.get("indexingStatus")
                return PushResult(
                    status=status, http_status=201 if action == "create" else 200, record_id=key, kb_id=kb_id,
                    external_record_id=v.external_record_id, version=v.version,
                    content_changed=action in ("create", "content"), content_rev=v.content_rev,
                    schema_id=v.schema_id, event=outcome.event, indexing_status=indexing,
                )
            raise _refuse(409, "PUSH_CONFLICT", "Another push of this record is in progress; retry.", retryAfter=1)
        finally:
            if new_doc_is_ours and new_doc:
                with contextlib.suppress(Exception):
                    await self._cleanup_unless_referenced(key, org_id, kb_id, new_doc)

    async def _undo_create_in_deleted_kb(self, key: str, kb_id: str, org_id: str, document_id: str | None) -> None:
        """A create that committed after the pre-commit check but before the cascade collected: if the
        KB is now gone or being deleted, the cascade may have missed this record, so remove it.

        Creates only. An update's record existed before the marker, so the cascade (which collects after
        the marker) already has it, and compensating there would delete a record the owner meant to keep
        when a delete failed and the status was put back.
        """
        kb = await self.graph.get_document(kb_id, CollectionNames.APPS.value, raise_on_error=True)
        if kb is not None and kb.get("status") != KB_DELETING_STATUS:
            return
        try:
            await self.graph.delete_nodes_and_edges([key], CollectionNames.RECORDS.value)
        except Exception:
            logger.exception("push of %s: could not remove the record it created in a KB being deleted", key)
            raise
        if document_id:
            await self._cleanup(org_id, kb_id, [document_id])
        if kb is None:
            raise _refuse(404, "KB_NOT_FOUND", "Knowledge base not found.")
        raise _refuse(409, "KB_BEING_DELETED", "This knowledge base is being deleted.")

    async def _cleanup_unless_referenced(self, key: str, org_id: str, kb_id: str, document_id: str) -> None:
        """Remove a document this push made and the graph did not take, unless the record names it.

        A commit whose outcome the client missed (the store committed, the client saw an error)
        leaves a record pointing at this document; deleting it would break the record, and an
        identical re-push would then answer ``unchanged``. If the record cannot be read the
        outcome is unknown, so the document stays for the orphan sweep.
        """
        record = await self.graph.get_document(key, CollectionNames.RECORDS.value, raise_on_error=True)
        if record is not None and record.get("externalRecordId") == document_id:
            logger.warning("push of %s: the record references document %s; keeping it", key, document_id)
            return
        await self._cleanup(org_id, kb_id, [document_id])

    async def _document_is_gone(self, existing: dict[str, Any], org_id: str) -> bool:
        """An identical re-push of a record that indexing could not read: is its document missing?

        Only a FAILED record is checked, so an ordinary identical re-push still makes no storage call.
        """
        if existing.get("indexingStatus") != ProgressStatus.FAILED.value or not existing.get("externalRecordId"):
            return False
        try:
            return not await self.storage.exists(org_id, existing["externalRecordId"])
        except StorageUploadError:
            return False

    def _result(self, existing: dict[str, Any], v: Validated, key: str, kb_id: str, status: str, http: int,
                *, changed: bool, event: str | None, indexing: str | None) -> PushResult:
        parsed = C.parse_revision_id(existing.get("externalRevisionId"))
        return PushResult(
            status=status, http_status=http, record_id=key, kb_id=kb_id, external_record_id=v.external_record_id,
            version=existing.get("version", v.version), content_changed=changed,
            content_rev=parsed[2] if parsed else v.content_rev, schema_id=v.schema_id, event=event, indexing_status=indexing,
        )

    async def _backoff(self, attempt: int) -> None:
        await self._sleep(0.025 * (2 ** attempt) * (0.5 + random.random()))

    async def _processor(self, kb_id: str) -> DataSourceEntitiesProcessor:
        try:
            return await self.processor_for_kb(kb_id)
        except (PermissionError, LookupError):
            raise _refuse(404, "KB_NOT_FOUND", "Knowledge base not found.") from None

    async def _placement(self, existing: dict[str, Any] | None, key: str, kb_id: str, folder_id: str | None) -> str | None:
        """The folder a record lives in. A create may name one; a re-push never moves a record."""
        if existing is not None:
            info = await self.graph.get_record_parent_info(key)
            return info.get("id") if info and info.get("type") == "record" else None
        if not folder_id:
            return None
        if await self.graph.validate_folder_in_kb(kb_id, folder_id):
            return folder_id
        refusal = await self.kb_service._destination_folder_refusal(kb_id, folder_id, "push into it", "Folder not found")
        if refusal.get("code") == 409:
            raise _refuse(409, "FOLDER_IN_TRASH", refusal["reason"])
        raise _refuse(404, "FOLDER_NOT_FOUND", "Folder not found in this knowledge base.")

    async def _check_name(self, existing: dict[str, Any] | None, v: Validated, kb_id: str, parent: str | None, key: str) -> None:
        if existing is not None and existing.get("recordName") == v.record_name:
            return
        clash = await self.graph.find_file_by_name_in_parent(
            kb_id=kb_id, file_name=v.record_name, mime_type=PUSH_MIME, parent_folder_id=parent, exclude_record_id=key,
        )
        if clash:
            raise _refuse(409, "DUPLICATE_NAME", f"A file named '{v.record_name}' already exists in this location.")

    # ---- storage ----------------------------------------------------------------------------

    async def _store(self, org_id: str, kb_id: str, key: str, v: Validated) -> str:
        try:
            return await self.storage.create(
                org_id=org_id, name=key, document_path=f"{STORAGE_PATH}/{kb_id}", file_path=v.stored_path,
                extension="bin", content_type="application/octet-stream",
                tags={"connectorId": kb_id, "pushRecordKey": key, "contentRev": v.content_rev},
            )
        except StorageUploadError as e:
            logger.error("push storage upload failed: %s", e)
            raise _refuse(503, "STORAGE_UNAVAILABLE", "The content could not be stored; retry.", retryAfter=5) from None

    async def _cleanup(self, org_id: str, kb_id: str, document_ids: list[str]) -> None:
        for event in build_stored_document_cleanup_events(org_id=org_id, document_ids=document_ids, connector_id=kb_id):
            try:
                await self.publish_event(event)
            except Exception as e:
                logger.warning("could not publish the cleanup of documents %s: %s", document_ids, e)

    # ---- the graph write ----------------------------------------------------------------------

    async def _claim_and_write(
        self,
        processor: DataSourceEntitiesProcessor,
        existing: dict[str, Any] | None,
        action: str,
        v: Validated,
        key: str,
        kb_id: str,
        org_id: str,
        user_id: str,
        parent: str | None,
        new_doc: str | None,
        revision: str,
    ) -> tuple[PushOutcome, FileRecordT, PushExpect | None, PushSet]:
        now = get_epoch_timestamp_in_ms()
        source_ts = v.source_last_modified
        if action == "create":
            storage_url = await self.storage._storage_url()
            record = FileRecord(
                id=key, org_id=org_id, record_name=v.record_name, record_type=RecordType.FILE,
                external_record_id=new_doc, external_revision_id=revision, external_record_group_id=kb_id,
                parent_external_record_id=parent, version=v.version, origin=OriginTypes.UPLOAD,
                connector_name=Connectors.KNOWLEDGE_BASE, connector_id=kb_id, mime_type=PUSH_MIME,
                indexing_status=ProgressStatus.NOT_STARTED.value, size_in_bytes=v.size,
                source_created_at=source_ts or now, source_updated_at=source_ts or now,
                uploaded_by=user_id, is_file=True, extension=None, created_at=now, updated_at=now,
                fetch_signed_url=f"{storage_url}/api/v1/document/internal/{new_doc}/download",
                source_timestamp_origin=upload_origin(source_ts, now).value,
            )
            expected, new = None, PushSet(new_doc, v.version)
            outcome = await processor.on_pushed_record(record, expected=expected, new=new, content_changed=True)
            return outcome, record, expected, new
        record = await self.graph.get_file_record_by_id(key)
        if record is None:
            raise PushRecordSkipped(key)
        expected = PushExpect(existing["externalRecordId"], existing["externalRevisionId"], existing["version"])
        record.record_name = v.record_name
        record.version = v.version
        record.updated_at = now
        if source_ts:
            record.source_updated_at = source_ts
        if action == "content":
            storage_url = await self.storage._storage_url()
            record.external_record_id = new_doc
            record.external_revision_id = revision
            record.md5_hash = None
            record.indexing_status = ProgressStatus.NOT_STARTED.value
            record.size_in_bytes = v.size
            record.fetch_signed_url = f"{storage_url}/api/v1/document/internal/{new_doc}/download"
            new = PushSet(new_doc, v.version)
        else:
            record.external_record_id = existing["externalRecordId"]
            record.external_revision_id = existing["externalRevisionId"]
            new = PushSet(existing["externalRecordId"], v.version)
        outcome = await processor.on_pushed_record(record, expected=expected, new=new, content_changed=action == "content")
        return outcome, record, expected, new

    async def _retag(self, existing: dict[str, Any], v: Validated, kb_id: str, org_id: str) -> bool:
        """Same version, same bytes, older normaliser tag: only the revision's tag changes.

        False when the stored revision is no longer the one read (nothing was changed)."""
        new_revision = C.revision_id(v.digest, v.content_rev)
        async with (await self._processor(kb_id)).data_store_provider.transaction() as tx:
            return await tx.retag_push_record(
                key=existing.get("id") or existing.get("_key"), kb_id=kb_id, org_id=org_id,
                expected_revision=existing["externalRevisionId"], new_revision=new_revision,
            )
