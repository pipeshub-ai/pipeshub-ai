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
import hashlib
import logging
import os
import random
import re
import secrets
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field, replace
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
from app.exceptions.graph_db_exceptions import PermissionVerificationUnavailableError
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
from . import images as I
from .contract import PushRefusal
from .ledger import IMAGE, TEXT
from .retry import is_attempt_lost
from .worker import PushWorker, Validated, schema_id_hint

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
    from app.models.entities import FileRecord as FileRecordT
    from app.modules.record_metadata.schemas import MetadataSchemaRegistry
    from app.services.graph_db.interface.graph_db_provider import IGraphDBProvider
    from app.services.storage_documents import StorageDocumentInfo

    from .image_admission import ImageAdmission, ImageSlot
    from .ledger import PushLedger
    from .materialiser import Materialiser
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
#: The most a record may hold once its images are inlined (every push); the platform's file size limit can lower it.
MAX_INLINED_BYTES = 48 << 20
#: Storage ids a push may name; checked again before they reach a task payload.
_DOC_ID = I._STORAGE_REF
#: A storage id read back from a tag: it goes into a URL path, so it is checked before it is used.
_TAG_DOC_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
SETTLE_RETRY_S = 2.0
TOKEN_TTL_S = 180
#: ``kb_push_untrusted_total{reason}``: pushes whose image fingerprint could not be trusted, by cause.
UNTRUSTED: Counter[str] = Counter()
_UNTRUSTED_LOGGED: dict[str, float] = {}
#: ``kb_push_heal_total{outcome}``: the previous-document cleanup after a content change (cleaned | skipped | failed).
HEAL: Counter[str] = Counter()
_HEAL_LOGGED: dict[str, float] = {}
HEAL_TIMEOUT_S = 10.0


def _deny() -> PushRefusal:
    """The one answer for a reference that cannot be used, whatever the cause (absent, foreign, deleted, not an image...)."""
    return PushRefusal(422, "IMAGE_REF_INVALID", "An image reference in this record cannot be used.", extra={"code": "IMAGE_REF_INVALID"})


def _mib(n: int) -> str:
    return f"{n / (1 << 20):g}"


def resolved_too_large(cap: int, limit: int) -> PushRefusal:
    if limit < MAX_INLINED_BYTES:
        hint = f"The inlined size of this record cannot exceed {_mib(cap)} MiB. This install's file size limit is {_mib(limit)} MiB."
    else:
        hint = f"The inlined size of this record cannot exceed {_mib(cap)} MiB."
    return PushRefusal(413, "PAYLOAD_TOO_LARGE", "The record is too large once its images are inlined.",
                       extra={"limit": "resolved", "limitBytes": cap, "hint": hint})


@dataclass
class ImagePush:
    """Per-push state of a push with ``storage://`` images."""

    slot: ImageSlot
    work_dir: str
    infos: dict[str, StorageDocumentInfo] = field(default_factory=dict)
    image_fp: str = ""
    request_key: str = ""
    out_path: str | None = None


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
        materialiser: Materialiser | None = None,
        image_admission: ImageAdmission | None = None,
        ledger: PushLedger | None = None,
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
        # None: references are refused as before images existed (a deployment or test that has no materialiser).
        self.materialiser = materialiser
        self.image_admission = image_admission
        # None: no accounting (a test or a deployment that builds the service without a runtime).
        self.ledger = ledger

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

    def precheck(self, org_id: str, user_id: str) -> None:
        """Refuse a caller whose pushes are suspended or whose shared-worker budget is spent, before any body byte is read."""
        if self.ledger is not None:
            self.ledger.check_all((org_id, user_id))

    async def push(self, *, kb_id: str, user_id: str, org_id: str, body: bytes, folder_id: str | None) -> PushResult:
        user = (org_id, user_id)
        report = None
        if self.ledger is not None:
            self.precheck(org_id, user_id)
            body_key = "b:" + await asyncio.to_thread(lambda: hashlib.sha256(body).hexdigest())
            self.ledger.check_pair(user, body_key)
            report = self.ledger.accountant(user, body_key, TEXT)
        in_path = self.spool.write(body)
        out_path = self.spool.new_path("out")
        img: ImagePush | None = None
        try:
            schema_id, schema = await self._load_schema(org_id, kb_id, body)
            validated = await (
                self.worker.validate(in_path, out_path, schema_id=schema_id, schema=schema)
                if report is None else self.worker.validate(in_path, out_path, schema_id=schema_id, schema=schema, account=report)
            )
            if validated.refs:
                request_key = C.request_key(validated.blocks_sha, validated.properties, [r for r, _ in validated.refs])
                if self.ledger is not None:
                    self.ledger.check_images(user)
                    self.ledger.check_pair(user, "r:" + request_key)
                img = self._begin_images(user_id, org_id)
                img.request_key = request_key
                validated = await self._authorise_images(img, validated, user_id=user_id, org_id=org_id)
            return await self._write(kb_id=kb_id, user_id=user_id, org_id=org_id, v=validated, folder_id=folder_id, img=img)
        finally:
            self.spool.remove(in_path, out_path)
            if img is not None:
                if img.out_path:
                    self.spool.remove(img.out_path)
                self.materialiser.remove_dir(img.work_dir)  # type: ignore[union-attr]
                img.slot.release()

    # ---- images -----------------------------------------------------------------------------

    def _begin_images(self, user_id: str, org_id: str) -> ImagePush:
        if self.materialiser is None and self.image_admission is not None:
            # A deployment whose materialiser could not be made (a malformed origin list): an operator fault, not the caller's.
            raise _refuse(503, "IMAGES_UNAVAILABLE", "Images in pushes are unavailable on this deployment.")
        if self.materialiser is None or self.image_admission is None:
            raise PushRefusal(422, "IMAGE_REF_INVALID", "An image reference must be a data:image/(png|jpeg|gif|webp);base64 URI.",
                              extra={"code": "BLOCKS_INVALID"})
        slot = self.image_admission.reserve(user_id, org_id)
        try:
            return ImagePush(slot=slot, work_dir=self.materialiser.make_dir(self.spool.dir))
        except BaseException:
            slot.release()
            raise

    async def _read_refs(self, user_id: str, org_id: str, ids: list[str]) -> tuple[dict[str, StorageDocumentInfo | None], Any]:
        """The verified ``info`` of every ref and the graph's answer, always both, in parallel (no early exit on a denial)."""
        infos, access = await asyncio.gather(
            self.storage.info_many(org_id, ids, verify=True), self.graph.get_ref_access(user_id, org_id, ids),
            return_exceptions=True,
        )
        if isinstance(infos, asyncio.CancelledError) or isinstance(access, asyncio.CancelledError):
            raise asyncio.CancelledError
        if isinstance(infos, StorageUploadError):
            logger.warning("push image info failed: %s", infos)
            raise PushRefusal(422, "IMAGE_FETCH_FAILED", "An image could not be read from storage.", extra={"hint": "Send the push again."})
        if isinstance(access, PermissionVerificationUnavailableError):
            raise _refuse(503, "PERMISSION_CHECK_UNAVAILABLE", "Access to the images could not be checked; retry.", retryAfter=5)
        for failure in (infos, access):
            if isinstance(failure, BaseException):
                raise failure
        return infos, access

    def _decide_refs(self, ids: list[str], infos: dict[str, StorageDocumentInfo | None], access: object, user_id: str) -> dict[str, StorageDocumentInfo]:
        ok: dict[str, StorageDocumentInfo] = {}
        denied = False
        for ref_id in ids:
            info = infos.get(ref_id)
            usable = (
                info is not None and not info.is_deleted and not info.retired and not info.awaiting_direct_upload
                and not info.is_artifact_set and (info.mime_type or "").lower().startswith("image/")
                and not any(n is not None and n > I.MAX_IMAGE_BYTES for n in (info.object_size, info.declared_size))
            )
            if usable:
                allowed = ref_id in access.readable if ref_id in access.linked else bool(info.initiator_user_id) and info.initiator_user_id == user_id
                usable = allowed
            if usable:
                ok[ref_id] = info  # type: ignore[assignment]
            else:
                denied = True
        if denied:
            raise _deny()
        return ok

    async def _authorise_images(self, img: ImagePush, v: Validated, *, user_id: str, org_id: str) -> Validated:
        ids = [r for r, _ in v.refs]
        infos, access = await self._read_refs(user_id, org_id, ids)
        img.infos = self._decide_refs(ids, infos, access, user_id)
        # A writer window that is still open usually closes within moments: one bounded wait and read before the slow path.
        if any(i.untrusted_reason == "open_writer" for i in img.infos.values()):
            await self._sleep(SETTLE_RETRY_S)
            again, access = await self._read_refs(user_id, org_id, ids)
            img.infos = self._decide_refs(ids, again, access, user_id)
        nonce = secrets.token_hex(16)  # once per push, outside the retry loop
        untrusted = [i for i in img.infos.values() if not i.trusted]
        for info in untrusted:
            self._count_untrusted(info.untrusted_reason or "other")
        if untrusted:
            fp = hashlib.sha256(b"untrusted" + nonce.encode()).hexdigest()
        else:
            h = hashlib.sha256()
            for ref_id in ids:
                h.update(ref_id.encode() + b"\x1f" + (img.infos[ref_id].fingerprint or "").encode() + b"\x1e")
            fp = h.hexdigest()
        img.image_fp = fp
        # Arithmetic from what storage says, before any download: a refusal here costs no bytes.
        sizes = {r: (i.object_size if i.object_size is not None else i.declared_size or 0) for r, i in img.infos.items()}
        refs = I.ImageRefs(order=ids, counts=dict(v.refs))
        lower = I.inlined_size_lower_bound(os.path.getsize(v.stored_path), v.image_nonce or "", refs, sizes)
        cap, limit = await self._cap(authoritative=False)
        if lower > cap:
            raise resolved_too_large(cap, limit)
        return replace(v, digest=C.request_digest(v.blocks_sha, v.properties, fp))

    @staticmethod
    def _count_untrusted(reason: str) -> None:
        UNTRUSTED[reason] += 1
        now = time.monotonic()
        if now - _UNTRUSTED_LOGGED.get(reason, -60.0) >= 60.0:
            _UNTRUSTED_LOGGED[reason] = now
            logger.warning("push image fingerprint not trusted (%s); materialising every time (count so far %d)", reason, UNTRUSTED[reason])

    async def _cap(self, *, authoritative: bool) -> tuple[int, int]:
        """``(effective cap, platform limit)``. The authoritative read is never cached."""
        try:
            limit = await self.storage.upload_limit(use_cache=not authoritative)
        except StorageUploadError:
            raise _refuse(503, "STORAGE_UNAVAILABLE", "The upload limit could not be read; retry.", retryAfter=5) from None
        return min(MAX_INLINED_BYTES, limit), limit

    async def _enforce_cap(self, size: int, *, authoritative: bool) -> int:
        cap, limit = await self._cap(authoritative=authoritative)
        if size > cap:
            raise resolved_too_large(cap, limit)
        return cap

    async def _materialise(self, img: ImagePush, v: Validated, *, org_id: str, user_id: str | None = None) -> Validated:
        """Fetch the images and splice them into the stored file. The caller keeps the result for the next attempt."""
        cap, _limit = await self._cap(authoritative=True)
        refs = []
        for ref_id, _count in v.refs:
            if not _DOC_ID.fullmatch(f"storage://{ref_id}"):
                raise _deny()
            info = img.infos[ref_id]
            refs.append({"id": ref_id, "etag": info.object_etag, "vendor": info.storage_vendor, "size": info.object_size, "object_url": info.object_url})
        img.out_path = self.spool.new_path("img-out")
        try:
            token = await self.storage.mint_token(org_id, ttl_s=TOKEN_TTL_S)
            storage_url = await self.storage._storage_url()
        except Exception:
            raise _refuse(503, "STORAGE_UNAVAILABLE", "The images could not be fetched; retry.", retryAfter=5) from None
        task = {
            "sentinel_path": v.stored_path, "out_path": img.out_path, "work_dir": img.work_dir, "nonce": v.image_nonce,
            "refs": refs, "storage_url": storage_url, "token": token, "cap_bytes": cap,
        }
        async with img.slot.running():
            if self.ledger is not None and user_id is not None:
                account = self.ledger.accountant((org_id, user_id), "r:" + img.request_key, IMAGE)
                done = await self.materialiser.run(task, account=account)  # type: ignore[union-attr]
            else:
                done = await self.materialiser.run(task)  # type: ignore[union-attr]
        return replace(v, stored_path=img.out_path, size=done.size, content_rev=done.content_rev)

    async def _recheck_refs(self, img: ImagePush, v: Validated, *, user_id: str, org_id: str) -> None:
        """Just before the claim: the caller may have lost access, or a document may have been deleted, since the first read.

        Its result never changes the fingerprint in the digest (that is the first read's)."""
        ids = [r for r, _ in v.refs]
        infos, access = await self._read_refs(user_id, org_id, ids)
        self._decide_refs(ids, infos, access, user_id)

    def _decide(self, existing: dict[str, Any] | None, v: Validated, kb_id: str, org_id: str) -> tuple[str, dict[str, Any] | None]:
        """``(action, extra)``; action is create | content | metadata | unchanged | retag | maybe_changed, or raises the 409."""
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
        # With images, the bytes are unknown until they are fetched: the digest (which holds the images' fingerprint) can
        # still prove "same", anything else is "maybe_changed" and is decided again once the bytes exist.
        unknown = v.content_rev is None
        same_digest = tag == C.NORMALISER and digest == v.digest
        same_content = same_digest or (not unknown and rev == v.content_rev)
        if v.version == stored_version:
            if not same_content:
                if unknown:
                    return "maybe_changed", None
                raise _refuse(409, "VERSION_CONFLICT", "This version is stored with different content.", currentVersion=stored_version)
            return ("unchanged", None) if same_digest else ("retag", None)
        if same_content:
            return "metadata", None
        return ("maybe_changed", None) if unknown else ("content", None)

    async def _write(self, *, kb_id: str, user_id: str, org_id: str, v: Validated, folder_id: str | None, img: ImagePush | None = None) -> PushResult:
        key = push_record_key(org_id, kb_id, v.external_record_id)
        if not v.needs_materialising:
            await self._enforce_cap(v.size, authoritative=False)  # every push, text-only included
        new_doc: str | None = None
        new_doc_is_ours = False
        heal_prev: str | None = None  # a verified leaked document to clean together with the superseded one
        first_loss: float | None = None  # the retry budget runs from the first lost attempt, not from the push's start
        try:
            for attempt in range(MAX_ATTEMPTS):
                if first_loss is not None and time.monotonic() - first_loss > RETRY_BUDGET_S:
                    break
                existing = await self.graph.get_document(key, CollectionNames.RECORDS.value, raise_on_error=True)
                action, _ = self._decide(existing, v, kb_id, org_id)
                if action == "unchanged" and await self._document_is_gone(existing, org_id):
                    action = "content"  # heal: same version, same bytes, a new document
                if img is not None and v.content_rev is None and action in ("create", "content", "maybe_changed"):
                    v = await self._materialise(img, v, org_id=org_id, user_id=user_id)
                    if action == "maybe_changed":
                        action, _ = self._decide(existing, v, kb_id, org_id)
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
                revision = C.revision_id(v.digest, v.content_rev) if v.content_rev else None
                if action in ("create", "content") and new_doc is None:
                    await self._enforce_cap(v.size, authoritative=True)
                    previous = existing.get("externalRecordId") if action == "content" and existing else None
                    # Read before any cleanup is published: once the superseded document is purged its tags are gone.
                    heal_prev = await self._heal_prepare(org_id, kb_id, key, previous) if previous else None
                    new_doc = await self._store(org_id, kb_id, key, v, previous=previous)
                    new_doc_is_ours = True
                if img is not None and action in ("create", "content"):
                    # Every attempt: the caller's access or a document may have changed since the first read.
                    await self._recheck_refs(img, v, user_id=user_id, org_id=org_id)
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
                    await self._cleanup(org_id, kb_id, [existing["externalRecordId"], *([heal_prev] if heal_prev else [])])
                    if heal_prev:
                        HEAL["cleaned"] += 1
                status = "created" if action == "create" else "updated"
                indexing = "NOT_STARTED"
                if outcome.event:
                    indexing = "QUEUED" if outcome.published else "NOT_STARTED"
                elif existing is not None:
                    indexing = existing.get("indexingStatus")
                return PushResult(
                    status=status, http_status=201 if action == "create" else 200, record_id=key, kb_id=kb_id,
                    external_record_id=v.external_record_id, version=v.version,
                    content_changed=action in ("create", "content"),
                    # A metadata change with images never fetched them: the stored bytes' revision is the answer.
                    content_rev=v.content_rev or (stored_rev[2] if (stored_rev := C.parse_revision_id((existing or {}).get("externalRevisionId"))) else None),
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

    async def _store(self, org_id: str, kb_id: str, key: str, v: Validated, previous: str | None = None) -> str:
        tags = {"connectorId": kb_id, "pushRecordKey": key, "contentRev": v.content_rev}
        if previous:
            tags["previousDocumentId"] = previous  # a crash after the commit leaves this one to the next content change
        try:
            return await self.storage.create(
                org_id=org_id, name=key, document_path=f"{STORAGE_PATH}/{kb_id}", file_path=v.stored_path,
                extension="bin", content_type="application/octet-stream",
                tags=tags,
            )
        except StorageUploadError as e:
            logger.error("push storage upload failed: %s", e)
            if e.status == 413 and e.limit_bytes is not None:
                raise resolved_too_large(min(MAX_INLINED_BYTES, e.limit_bytes), e.limit_bytes) from None
            if e.status == 400:
                # Not an oversize file (create() maps that to 413): retrying the same bytes cannot succeed.
                raise _refuse(500, "STORAGE_REJECTED", "The content was rejected by storage.") from None
            raise _refuse(503, "STORAGE_UNAVAILABLE", "The content could not be stored; retry.", retryAfter=5) from None

    @staticmethod
    def _is_ours(info: StorageDocumentInfo | None, key: str, kb_id: str) -> bool:
        """A live document that carries this record's tags. Tags can be set by anyone who uploads, so this only
        rules out the documents that cannot be ours; the record's own pointer is what makes E trustworthy."""
        return info is not None and not info.is_deleted and info.tags.get("pushRecordKey") == key and info.tags.get("connectorId") == kb_id

    async def _heal_prepare(self, org_id: str, kb_id: str, key: str, previous: str) -> str | None:
        """The document an earlier crash may have leaked: the one the superseded document E was tagged to replace.

        Best effort and isolated: any failure (storage down, timeout, a document without our tags) means "nothing
        to clean", never an error for the push. Both documents are read here, before anything is published, and
        P is returned only if its own tags are this record's: the cleanup consumer deletes by id alone.
        """
        outcome = "skipped"
        try:
            async with asyncio.timeout(HEAL_TIMEOUT_S):
                superseded = await self.storage.info(org_id, previous)
                leaked = superseded.tags.get("previousDocumentId") if self._is_ours(superseded, key, kb_id) else None  # type: ignore[union-attr]
                if not leaked or leaked == previous or not _TAG_DOC_ID.fullmatch(leaked):
                    return None
                if not self._is_ours(await self.storage.info(org_id, leaked), key, kb_id):
                    return None
                outcome = "verified"
                return leaked
        except Exception as e:
            outcome = "failed"
            now = time.monotonic()
            if now - _HEAL_LOGGED.get("failed", -60.0) >= 60.0:
                _HEAL_LOGGED["failed"] = now
                logger.warning("push of %s: the previous-document check failed (%s); continuing", key, type(e).__name__)
            return None
        finally:
            if outcome != "verified":
                HEAL[outcome] += 1

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
                push_origin=True,
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
        record.push_origin = True
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
            # Same bytes: the document and its contentRev stay, the digest becomes this request's.
            stored_rev = C.parse_revision_id(existing["externalRevisionId"])
            record.external_revision_id = C.revision_id(v.digest, stored_rev[2]) if stored_rev else existing["externalRevisionId"]
            new = PushSet(existing["externalRecordId"], v.version)
        outcome = await processor.on_pushed_record(record, expected=expected, new=new, content_changed=action == "content")
        return outcome, record, expected, new

    async def _retag(self, existing: dict[str, Any], v: Validated, kb_id: str, org_id: str) -> bool:
        """Same version, same bytes, a different digest or an older tag: only the revision string changes.

        False when the stored revision is no longer the one read (nothing was changed)."""
        stored_rev = C.parse_revision_id(existing["externalRevisionId"])
        new_revision = C.revision_id(v.digest, stored_rev[2] if stored_rev else v.content_rev)
        try:
            async with (await self._processor(kb_id)).data_store_provider.transaction() as tx:
                return await tx.retag_push_record(
                    key=existing.get("id") or existing.get("_key"), kb_id=kb_id, org_id=org_id,
                    expected_revision=existing["externalRevisionId"], new_revision=new_revision,
                )
        except Exception as e:
            # A conflict or an aborted transaction changed nothing: the caller reads again.
            if is_attempt_lost(e):
                return False
            raise
