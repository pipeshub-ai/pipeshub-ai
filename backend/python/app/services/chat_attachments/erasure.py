"""Erasing a deleted conversation's chat attachments (QA15-F4).

An upload is a RECORDS node, a FILES node of the same key, an IS_OF_TYPE edge,
PERMISSION edges, the virtual record's mapping node, the uploaded file's
storage document and the parsed blocks' storage documents (no vectors: the
upload's pipeline is sink-only). Node names the attachments its messages
carry, each with the message's sender; the record ids come from a client's
message, so one is erased only if it is the org's, an upload, and that
sender's: the uploader its record names (QA-15G), or for an upload written
before uploaders were recorded, the holder of its OWNER edge. The bytes go
first and the RECORDS node last, so a crash part-way leaves the record to find
again: every call is idempotent and resumable.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from app.config.constants.arangodb import CollectionNames, Connectors
from app.core.org_context import org_of
from app.services.artifact_registry.erasure import BlobPurger
from app.services.chat_attachments.uploads import (
    AWAITING_SEND_FIELD,
    UPLOAD_CONVERSATION_FIELD,
    recorded_uploader,
)

logger = logging.getLogger(__name__)

ERASE_PAGE = 50

__all__ = [
    "ERASE_PAGE",
    "AttachmentBlobs",
    "AttachmentEraser",
    "AttachmentErasureReport",
    "AttachmentRef",
    "OwnedBy",
    "UnsentPage",
    "list_unsent",
    "UnsentUpload",
    "UploadSweepReport",
]

OwnedBy = Callable[[str, list[str]], Awaitable[set[str]]]
"""The record ids of a list that a user (by auth user id) holds an OWNER edge to."""


class AttachmentBlobs(BlobPurger, Protocol):
    async def purge_virtual_record_documents(self, org_id: str, virtual_record_id: str) -> int:
        """How many of the virtual record's documents this call removed."""
        ...


@dataclass(frozen=True)
class AttachmentRef:
    record_id: str
    owner_id: str


@dataclass(frozen=True)
class AttachmentErasureReport:
    attachments: int
    blobs: int
    refused: int
    unrecorded: int
    """Of the refused, uploads that name no uploader and whose OWNER edge the sender does not hold."""
    removed: tuple[str, ...]
    """The named record ids no record holds any more: erased by this call or before it."""


@dataclass(frozen=True)
class UnsentUpload:
    record_id: str
    owner_id: str
    """The uploader's auth user id, as the message-based erase checks it (``AttachmentRef.owner_id``)."""
    conversation_id: str | None


@dataclass(frozen=True)
class UnsentPage:
    uploads: tuple[UnsentUpload, ...]
    remaining: bool
    next: str | None
    """The key to continue after while ``remaining``."""


@dataclass(frozen=True)
class UploadSweepReport:
    attachments: int
    blobs: int
    remaining: bool


def _record_key(record: dict[str, Any]) -> str | None:
    key = record.get("_key") or record.get("id")
    return str(key) if key else None


class AttachmentEraser:
    def __init__(
        self, graph_provider: Any, blobs: AttachmentBlobs, owned_by: OwnedBy, *, page: int = ERASE_PAGE,  # noqa: ANN401 - IGraphDBProvider
    ) -> None:
        self._graph = graph_provider
        self._blobs = blobs
        self._owned_by = owned_by
        self._page = page

    async def erase(self, org_id: str, refs: Sequence[AttachmentRef]) -> AttachmentErasureReport:
        attachments = blobs = refused = unrecorded = 0
        removed: list[str] = []
        for ref in refs:
            record: dict[str, Any] | None = await self._graph.get_document(ref.record_id, CollectionNames.RECORDS.value)
            if record is None:
                removed.append(ref.record_id)
                continue
            reason = await self._refusal(org_id, ref, record)
            if reason is not None:
                logger.warning("attachments.erase_refused reason=%s attachment=%s", reason, ref.record_id)
                refused += 1
                unrecorded += reason == "uploader_unrecorded"
                continue
            blobs += await self.erase_record(org_id, ref.record_id, record)
            attachments += 1
            removed.append(ref.record_id)
        return AttachmentErasureReport(attachments, blobs, refused, unrecorded, tuple(dict.fromkeys(removed)))

    async def erase_record(self, org_id: str, record_id: str, record: dict[str, Any]) -> int:
        """One upload whose erasure the caller authorized: bytes first, the RECORDS node last. Returns blobs purged."""
        blobs = await self._erase_blobs(org_id, record_id, record)
        await self._graph.delete_nodes_and_edges([record_id], CollectionNames.FILES.value)
        await self._graph.delete_nodes_and_edges([record_id], CollectionNames.RECORDS.value)
        return blobs

    async def erase_conversation_uploads(self, org_id: str, conversation_id: str) -> UploadSweepReport:
        """Up to one page of the uploads made into a deleted conversation that no message carried (CLEANUP-G5).

        A sent upload is left to the message-based erase, which also marks it removed in conversations forked
        from this one; taking it here would leave their messages pointing at nothing.
        """
        records = await self._graph.get_documents_paginated(
            CollectionNames.RECORDS.value,
            skip=0,
            limit=self._page + 1,
            filters={
                "orgId": org_id,
                "connectorName": Connectors.ATTACHMENTS.value,
                UPLOAD_CONVERSATION_FIELD: conversation_id,
                AWAITING_SEND_FIELD: True,
            },
            sort_field="_key",
            raise_on_error=True,
        )
        attachments = blobs = 0
        for record in records[: self._page]:
            key = _record_key(record)
            if key is None:
                continue
            blobs += await self.erase_record(org_id, key, record)
            attachments += 1
        return UploadSweepReport(attachments, blobs, remaining=len(records) > self._page)

    async def erase_org_uploads(self, org_id: str) -> UploadSweepReport:
        """Up to one page of every chat attachment the org holds, sent or not (CLEANUP-G1: the org is erased)."""
        records = await self._graph.get_documents_paginated(
            CollectionNames.RECORDS.value,
            skip=0,
            limit=self._page + 1,
            filters={"orgId": org_id, "connectorName": Connectors.ATTACHMENTS.value},
            sort_field="_key",
            raise_on_error=True,
        )
        attachments = blobs = 0
        for record in records[: self._page]:
            key = _record_key(record)
            if key is None:
                continue
            blobs += await self.erase_record(org_id, key, record)
            attachments += 1
        return UploadSweepReport(attachments, blobs, remaining=len(records) > self._page)

    async def _refusal(self, org_id: str, ref: AttachmentRef, record: dict[str, Any]) -> str | None:
        if org_of(record) != org_id:
            return "foreign_org"
        if record.get("connectorName") != Connectors.ATTACHMENTS.value:
            return "not_attachment"
        # The recorded uploader is authoritative; it needs no user node, so it also
        # covers a service account's person and an uploader since removed from the graph.
        uploader = recorded_uploader(record)
        if uploader is not None:
            return None if uploader == ref.owner_id else "not_uploader"
        if ref.record_id in await self._owned_by(ref.owner_id, [ref.record_id]):
            return None
        return "uploader_unrecorded"

    async def _erase_blobs(self, org_id: str, record_id: str, record: dict[str, Any]) -> int:
        purged = 0
        document_id = str(record.get("externalRecordId") or "")
        if document_id and document_id != record_id:
            purged += await self._blobs.purge_document(org_id, document_id)
        virtual_record_id = str(record.get("virtualRecordId") or "")
        if not virtual_record_id:
            return purged
        # Content is deduplicated: another live record may still read these documents.
        sharing: list[str] = await self._graph.get_records_by_virtual_record_id(virtual_record_id, raise_on_error=True) or []
        if any(key != record_id for key in sharing):
            return purged
        purged += await self._blobs.purge_virtual_record_documents(org_id, virtual_record_id)
        await self._graph.delete_nodes_and_edges(
            [virtual_record_id], CollectionNames.VIRTUAL_RECORD_TO_DOC_ID_MAPPING.value
        )
        return purged


async def list_unsent(
    graph: Any,  # noqa: ANN401 - IGraphDBProvider
    org_id: str,
    before_ms: int,
    limit: int,
    *,
    after: str | None = None,
    page: int = ERASE_PAGE,
    pages: int = 20,
) -> UnsentPage:
    """The org's uploads still awaiting a message and uploaded before ``before_ms``, by key after ``after``.

    Node decides which of them no message names and erases those through the message-based erase. Reads at
    most ``pages`` pages of the org's awaiting uploads; ``next`` is the last key read, to continue from, while
    ``remaining``. An upload written before the marker existed carries none and is never listed.
    """
    found: list[UnsentUpload] = []
    cursor = after
    for _ in range(pages):
        records = await graph.get_documents_paginated(
            CollectionNames.RECORDS.value,
            skip=0,
            limit=page,
            filters={"orgId": org_id, "connectorName": Connectors.ATTACHMENTS.value, AWAITING_SEND_FIELD: True},
            sort_field="_key",
            raise_on_error=True,
            after_key=cursor,
        )
        for index, record in enumerate(records):
            key = _record_key(record)
            if key is None:
                continue
            cursor = key
            owner = recorded_uploader(record)
            if owner is None or int(record.get("createdAtTimestamp") or 0) >= before_ms:
                continue
            conversation = record.get(UPLOAD_CONVERSATION_FIELD)
            found.append(UnsentUpload(key, owner, conversation if isinstance(conversation, str) else None))
            if len(found) == limit:
                more = index < len(records) - 1 or len(records) == page
                return UnsentPage(tuple(found), remaining=more, next=key if more else None)
        if len(records) < page:
            return UnsentPage(tuple(found), remaining=False, next=None)
    return UnsentPage(tuple(found), remaining=True, next=cursor)
