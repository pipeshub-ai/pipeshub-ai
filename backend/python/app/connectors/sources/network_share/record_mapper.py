"""Pure mapping from a directory entry to a graph record decision."""

from __future__ import annotations

import mimetypes
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import TYPE_CHECKING

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.connectors.sources.network_share.pathing import (
    file_extension,
    is_ads_name,
    is_dot_entry,
    is_lock_file,
    normalize_rel_path,
    parent_of,
)
from app.models.entities import FileRecord, RecordGroupType, RecordType
from app.utils.time_conversion import get_epoch_timestamp_in_ms

if TYPE_CHECKING:
    from collections.abc import Collection

    from app.connectors.sources.network_share.entry import DirectoryEntry


def usable_file_id(file_id: int | None) -> bool:
    return file_id is not None and file_id != 0


def _iso(value: datetime | None) -> str:
    if value is None:
        return "0"
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _epoch_ms(value: datetime | None, fallback: int) -> int:
    if value is None:
        return fallback
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return int(value.timestamp() * 1000)


def revision_id(share: str, entry: DirectoryEntry, nfc_path: str) -> str:
    write_iso = _iso(entry.last_write_time)
    if usable_file_id(entry.file_id):
        if entry.is_directory:
            return f"{share}:{entry.file_id}"
        return f"{share}:{entry.file_id}:{entry.size}:{write_iso}"
    return f"{share}:path:{nfc_path}:{entry.size}:{write_iso}"


def external_record_id(share: str, nfc_path: str) -> str:
    return f"{share}/{nfc_path}"


def mime_for(rel_path: str, *, is_directory: bool) -> str:
    if is_directory:
        return MimeTypes.FOLDER.value
    guessed, _ = mimetypes.guess_type(rel_path)
    if not guessed:
        return MimeTypes.BIN.value
    try:
        return MimeTypes(guessed).value
    except ValueError:
        return MimeTypes.BIN.value


@dataclass(frozen=True)
class SkipDecision:
    reason: str


@dataclass(frozen=True)
class UpsertDecision:
    record: FileRecord


@dataclass(frozen=True)
class MoveDecision:
    old_external_id: str
    record: FileRecord


RecordDecision = SkipDecision | UpsertDecision | MoveDecision


class RecordMapper:
    """No I/O. Callers pass any records already loaded from the graph."""

    def skip_reason(self, entry: DirectoryEntry) -> str | None:
        if is_dot_entry(entry.name):
            return "dot-entry"
        if is_lock_file(entry.name):
            return "office-lock"
        if is_ads_name(entry.name):
            return "alternate-data-stream"
        if entry.is_symlink:
            return "symlink"
        return None

    def classify(
        self,
        *,
        entry: DirectoryEntry,
        share: str,
        parent_dir: str,
        connector_name: Connectors,
        connector_id: str,
        existing_by_id: FileRecord | None,
        existing_by_revision: FileRecord | None,
        seen_file_ids: Collection[int],
        indexing_manual: bool,
    ) -> RecordDecision:
        reason = self.skip_reason(entry)
        if reason:
            return SkipDecision(reason)

        nfc_path = normalize_rel_path(parent_dir, entry.name)
        if nfc_path is None or not nfc_path:
            return SkipDecision("invalid-path")

        ext_id = external_record_id(share, nfc_path)
        rev = revision_id(share, entry, nfc_path)
        hard_link = (
            usable_file_id(entry.file_id) and entry.file_id in seen_file_ids
        )
        is_move = (
            existing_by_id is None
            and existing_by_revision is not None
            and existing_by_revision.external_record_id != ext_id
            and usable_file_id(entry.file_id)
            and not hard_link
        )
        existing = existing_by_id or (existing_by_revision if is_move else None)
        record = self.build_record(
            entry=entry,
            share=share,
            nfc_path=nfc_path,
            connector_name=connector_name,
            connector_id=connector_id,
            existing=existing,
            indexing_manual=indexing_manual,
            revision=rev,
            ext_id=ext_id,
        )
        if is_move and existing_by_revision is not None:
            return MoveDecision(
                old_external_id=existing_by_revision.external_record_id,
                record=record,
            )
        return UpsertDecision(record)

    def build_record(
        self,
        *,
        entry: DirectoryEntry,
        share: str,
        nfc_path: str,
        connector_name: Connectors,
        connector_id: str,
        existing: FileRecord | None,
        indexing_manual: bool,
        revision: str,
        ext_id: str,
    ) -> FileRecord:
        now_ms = get_epoch_timestamp_in_ms()
        updated_ms = _epoch_ms(entry.last_write_time, now_ms)
        created_ms = _epoch_ms(entry.created_time, updated_ms)
        parent_path = parent_of(nfc_path)
        parent_ext = (
            None if parent_path is None else external_record_id(share, parent_path)
        )
        is_file = not entry.is_directory
        indexing_status = ProgressStatus.QUEUED.value
        if indexing_manual and is_file:
            indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

        return FileRecord(
            id=existing.id if existing else str(uuid.uuid4()),
            record_name=nfc_path.rsplit("/", 1)[-1],
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.FILE_SHARE.value,
            external_record_group_id=share,
            external_record_id=ext_id,
            external_revision_id=revision,
            version=0 if existing is None else existing.version + 1,
            origin=OriginTypes.CONNECTOR.value,
            connector_name=connector_name,
            connector_id=connector_id,
            source_created_at=(
                existing.source_created_at if existing and existing.source_created_at else created_ms
            ),
            source_updated_at=updated_ms,
            weburl=None,
            hide_weburl=True if not is_file else False,
            is_internal=not is_file,
            inherit_permissions=True,
            parent_external_record_id=parent_ext,
            parent_record_type=RecordType.FILE if parent_ext else None,
            size_in_bytes=entry.size if is_file else 0,
            is_file=is_file,
            extension=file_extension(nfc_path) if is_file else None,
            path=nfc_path,
            mime_type=mime_for(nfc_path, is_directory=entry.is_directory),
            indexing_status=indexing_status,
        )
