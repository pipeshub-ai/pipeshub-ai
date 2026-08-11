"""Build expected Drive personal graph entities for integration tests.

Mirrors mapping in
``backend/python/app/connectors/sources/google/drive/individual/connector.py``.
Kept separate from ``DriveExpected`` (Workspace) because the two connectors are
independent implementations: different ``connector_name``, a single unnamed-
description record group, and no Shared-with-Me group.
"""

from __future__ import annotations

from typing import Any

from app.config.constants.arangodb import (  # type: ignore[import-not-found]
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.models.entities import (  # type: ignore[import-not-found]
    AppMetadata,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
)
from app.utils.time_conversion import (  # type: ignore[import-not-found]
    get_epoch_timestamp_in_ms,
    parse_timestamp,
)


class DriveIndividualExpected:
    """Expected graph entities for the Drive personal (OAuth) connector."""

    @staticmethod
    def app_metadata_for_full_sync_baseline(
        drive_connector: dict[str, Any],
    ) -> AppMetadata:
        """Expected apps document for TC-DRIVE-IND-SYNC-001."""
        return AppMetadata(
            connector_id=drive_connector["connector_id"],
            name=drive_connector["connector_name"],
            type="Drive",
            app_group="Google Workspace",
            scope="personal",
            created_at_timestamp=0,
            updated_at_timestamp=0,
        )

    @staticmethod
    def record_group_personal(
        *,
        email: str,
        root_id: str,
        connector_id: str,
    ) -> RecordGroup:
        """The single My Drive record group ``_create_personal_record_group`` builds.

        ``description`` and ``is_internal`` are never set on the personal path, so
        they stay at model defaults rather than the Workspace ``"My Drive"`` values.
        """
        return RecordGroup(
            id="",
            org_id="",
            name=f"Google Drive - {email}",
            external_group_id=root_id,
            connector_name=Connectors.GOOGLE_DRIVE,
            connector_id=connector_id,
            group_type=RecordGroupType.DRIVE,
        )

    @staticmethod
    def file_record_from_drive_meta(
        meta: dict[str, Any],
        *,
        connector_id: str,
        my_drive_root_id: str,
    ) -> FileRecord:
        """Build a ``FileRecord`` from live Drive ``files.get`` JSON (personal mapping)."""
        file_id = str(meta.get("id") or "")
        file_name = str(meta.get("name") or "Untitled")
        mime_type = meta.get("mimeType") or MimeTypes.UNKNOWN.value
        is_file = mime_type != MimeTypes.GOOGLE_DRIVE_FOLDER.value

        file_extension = meta.get("fileExtension")
        if not file_extension and "." in file_name:
            file_extension = file_name.rsplit(".", 1)[-1].lower()

        parent_external_record_id = (meta.get("parents") or [None])[0]
        if parent_external_record_id == my_drive_root_id:
            parent_external_record_id = None

        created_time = meta.get("createdTime")
        modified_time = meta.get("modifiedTime")
        timestamp_ms = get_epoch_timestamp_in_ms()
        source_created_at = int(parse_timestamp(created_time)) if created_time else timestamp_ms
        source_updated_at = int(parse_timestamp(modified_time)) if modified_time else timestamp_ms

        revision = meta.get("headRevisionId") or meta.get("version")
        external_revision_id = str(revision) if revision is not None else None

        return FileRecord(
            id="",
            org_id="",
            record_name=file_name,
            record_type=RecordType.FILE,
            record_group_type=RecordGroupType.DRIVE.value,
            external_record_group_id=my_drive_root_id,
            external_record_id=file_id,
            external_revision_id=external_revision_id,
            parent_external_record_id=parent_external_record_id,
            parent_record_type=RecordType.FILE if parent_external_record_id else None,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GOOGLE_DRIVE,
            connector_id=connector_id,
            created_at=timestamp_ms,
            updated_at=timestamp_ms,
            source_created_at=source_created_at,
            source_updated_at=source_updated_at,
            weburl=meta.get("webViewLink"),
            mime_type=mime_type,
            is_file=is_file,
            size_in_bytes=int(meta.get("size", 0) or 0),
            extension=file_extension,
            sha1_hash=meta.get("sha1Checksum"),
            sha256_hash=meta.get("sha256Checksum"),
            md5_hash=meta.get("md5Checksum"),
            is_shared=bool(meta.get("shared", False)),
            inherit_permissions=True,
        )
