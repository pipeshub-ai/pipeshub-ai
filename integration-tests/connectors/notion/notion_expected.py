# pyright: ignore-file

"""Build expected Notion graph entities for integration tests.

Each builder mirrors one transform in ``app/connectors/sources/notion/connector.py`` and is fed
from LIVE Notion API responses, so a test compares "what Notion says" against "what the graph
holds" rather than comparing the graph with itself.
"""

from __future__ import annotations

import mimetypes
from typing import Any, Optional

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, PermissionModel
from app.models.entities import (
    AppMetadata,
    AppUser,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    WebpageRecord,
)
from app.utils.time_conversion import parse_timestamp

# Fields the graph owns or that shift between sync runs — never compared.
# ``record_group_type`` joins ``parent_record_type`` here: both are write-time hints the
# processor uses to build edges, and neither is persisted on the record document.
RECORD_SKIP_COMPARE = frozenset({
    "id", "org_id", "indexing_status", "record_group_id", "virtual_record_id",
    "parent_record_type", "record_group_type", "created_at", "updated_at", "version",
})

APP_METADATA_SKIP_COMPARE = frozenset({
    "created_at_timestamp", "updated_at_timestamp", "auth_type", "is_active",
    "is_agent_active", "is_configured", "is_authenticated", "created_by",
    "updated_by", "last_synced_by", "status", "is_locked",
})


def parse_notion_timestamp(value: Optional[str]) -> Optional[int]:
    """ISO 8601 → epoch ms, mirroring the connector's ``_parse_iso_timestamp``."""
    if not value:
        return None
    try:
        return parse_timestamp(value)
    except Exception:
        return None


def extract_page_title(page_data: dict[str, Any]) -> str:
    """Mirror of ``NotionConnector._extract_page_title`` (same lookup order)."""
    properties = page_data.get("properties") or {}
    for name in ("title", "Title", "Name", "name"):
        prop = properties.get(name) or {}
        if prop.get("type") == "title":
            joined = "".join(t.get("plain_text", "") for t in prop.get("title") or [])
            return joined or "Untitled"
    for prop in properties.values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            joined = "".join(t.get("plain_text", "") for t in prop.get("title") or [])
            return joined or "Untitled"
    return "Untitled"


def normalize_filename_for_id(filename: str) -> str:
    """Mirror of ``NotionConnector._normalize_filename_for_id`` for comment attachment ids."""
    from urllib.parse import unquote

    if not filename:
        return "attachment"
    normalized = unquote(filename)
    for char in ('/', '\\', ':', '*', '?', '"', '<', '>', '|'):
        normalized = normalized.replace(char, "_")
    return normalized.strip() or "attachment"


class NotionExpected:
    """Expected graph entities for the Notion connector integration tests."""

    # ------------------------------------------------------------------ records

    @staticmethod
    def webpage_record(
        page_data: dict[str, Any],
        *,
        connector_id: str,
        workspace_id: str,
        parent_external_id: Optional[str] = None,
        overrides: Optional[dict[str, Any]] = None,
    ) -> WebpageRecord:
        """Expected ``WebpageRecord`` for a Notion page (``_transform_to_webpage_record``).

        ``parent_external_id`` defaults to whatever the page's own ``parent`` says; pass it
        explicitly only for the block-parent cases the connector resolves recursively.
        """
        ov = overrides or {}
        parent = page_data.get("parent") or {}
        if parent_external_id is None:
            parent_type = parent.get("type")
            if parent_type == "page_id":
                parent_external_id = parent.get("page_id")
            elif parent_type == "database_id":
                parent_external_id = parent.get("database_id")
            elif parent_type == "data_source_id":
                parent_external_id = parent.get("data_source_id")

        last_edited = page_data.get("last_edited_time")
        return WebpageRecord(
            id="",
            org_id="",
            record_name=ov.get("record_name", extract_page_title(page_data)),
            record_type=RecordType.WEBPAGE,
            external_record_id=str(page_data["id"]),
            record_group_type=RecordGroupType.NOTION_WORKSPACE,
            external_record_group_id=workspace_id,
            external_revision_id=last_edited,
            parent_external_record_id=parent_external_id,
            parent_record_type=None,
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id=connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            # Notion records carry blocks, not a previewable file, so the connector
            # sets this False; WebpageRecord's own default is True.
            preview_renderable=False,
            weburl=page_data.get("url"),
            source_created_at=parse_notion_timestamp(page_data.get("created_time")),
            source_updated_at=parse_notion_timestamp(last_edited),
        )

    @staticmethod
    def data_source_record(
        data_source_data: dict[str, Any],
        *,
        connector_id: str,
        workspace_id: str,
        parent_external_id: Optional[str],
    ) -> WebpageRecord:
        """Expected ``WebpageRecord`` for a data source.

        ``parent_external_id`` is the parent of the *database* that owns this data source
        (``_get_database_parent_page_id``), and is ``None`` when that database sits at the
        workspace root — the data source then hangs off the record group directly.
        """
        title_parts = data_source_data.get("title") or []
        title = "".join(t.get("plain_text", "") for t in title_parts) or "Untitled Data Source"
        last_edited = data_source_data.get("last_edited_time")
        return WebpageRecord(
            id="",
            org_id="",
            record_name=title,
            record_type=RecordType.DATASOURCE,
            external_record_id=str(data_source_data["id"]),
            record_group_type=RecordGroupType.NOTION_WORKSPACE,
            external_record_group_id=workspace_id,
            external_revision_id=last_edited,
            parent_external_record_id=parent_external_id,
            parent_record_type=None,
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id=connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            # Notion records carry blocks, not a previewable file, so the connector
            # sets this False; WebpageRecord's own default is True.
            preview_renderable=False,
            weburl=data_source_data.get("url"),
            source_created_at=parse_notion_timestamp(data_source_data.get("created_time")),
            source_updated_at=parse_notion_timestamp(last_edited),
        )

    @staticmethod
    def block_file_record(
        block_data: dict[str, Any],
        *,
        connector_id: str,
        workspace_id: str,
        page_id: str,
        page_url: str = "",
    ) -> FileRecord:
        """Expected ``FileRecord`` for a file/pdf/audio/video block (``_transform_to_file_record``)."""
        block_type = str(block_data.get("type"))
        type_data = block_data.get(block_type) or {}

        file_url = ""
        if type_data.get("type") == "external":
            file_url = (type_data.get("external") or {}).get("url", "")
        else:
            file_url = (type_data.get("file") or {}).get("url", "")

        file_name = type_data.get("name") or ""
        if not file_name:
            if block_type == "pdf":
                file_name = "document.pdf"
            else:
                file_name = file_url.split("/")[-1].split("?")[0]
                if not file_name:
                    file_name = f"attachment_{str(block_data['id'])[:8]}"

        extension = file_name.split(".")[-1].lower() if "." in file_name else None
        mime_type, _ = mimetypes.guess_type(file_name)
        if not mime_type:
            mime_type = {
                "image": MimeTypes.PNG.value,
                "pdf": MimeTypes.PDF.value,
                "video": "video/mp4",
                "audio": "audio/mpeg",
            }.get(block_type, MimeTypes.UNKNOWN.value)

        return FileRecord(
            id="",
            org_id="",
            record_name=file_name,
            record_type=RecordType.FILE,
            external_record_id=str(block_data["id"]),
            parent_record_type=None,
            parent_external_record_id=page_id,
            record_group_type=RecordGroupType.NOTION_WORKSPACE,
            external_record_group_id=workspace_id,
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id=connector_id,
            mime_type=mime_type,
            weburl=page_url or "",
            is_file=True,
            extension=extension,
            size_in_bytes=0,
            source_created_at=parse_notion_timestamp(block_data.get("created_time")),
            source_updated_at=parse_notion_timestamp(block_data.get("last_edited_time")),
        )

    @staticmethod
    def comment_file_record(
        attachment: dict[str, Any],
        *,
        comment_id: str,
        connector_id: str,
        workspace_id: str,
        page_id: str,
        page_url: str = "",
    ) -> FileRecord:
        """Expected ``FileRecord`` for a comment attachment (``_transform_to_comment_file_record``).

        The filename comes from the signed URL's path, not the upload name, and the external id
        is ``ca_{comment_id}_{normalized_filename}``.
        """
        from urllib.parse import unquote, urlparse

        file_url = ((attachment.get("file") or {}).get("url")) or ""
        path = unquote(urlparse(file_url).path)
        url_filename = path.split("/")[-1] if "/" in path else ""
        file_name = url_filename or (attachment.get("name") or "attachment")

        category = attachment.get("category") or ""
        if category:
            mime_type = {
                "productivity": MimeTypes.BIN.value,
                "image": MimeTypes.PNG.value,
                "video": "video/mp4",
                "audio": "audio/mpeg",
            }.get(category, MimeTypes.BIN.value)
        else:
            guessed, _ = mimetypes.guess_type(file_url)
            mime_type = guessed or MimeTypes.BIN.value

        extension = file_name.split(".")[-1] if "." in file_name else ""

        return FileRecord(
            id="",
            org_id="",
            record_name=file_name,
            record_type=RecordType.FILE,
            external_record_id=f"ca_{comment_id}_{normalize_filename_for_id(file_name)}",
            parent_record_type=None,
            parent_external_record_id=page_id,
            record_group_type=RecordGroupType.NOTION_WORKSPACE,
            external_record_group_id=workspace_id,
            version=1,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.NOTION,
            connector_id=connector_id,
            mime_type=mime_type,
            weburl=page_url or "",
            is_file=True,
            extension=extension,
            size_in_bytes=0,
        )

    # ------------------------------------------------------------------ groups / users / app

    @staticmethod
    def workspace_record_group(
        *, workspace_id: str, workspace_name: Optional[str], connector_id: str
    ) -> RecordGroup:
        """Expected workspace ``RecordGroup`` (``_create_workspace_record_group``)."""
        return RecordGroup(
            id="",
            org_id="",
            external_group_id=workspace_id,
            connector_id=connector_id,
            connector_name=Connectors.NOTION,
            name=workspace_name,
            group_type=RecordGroupType.NOTION_WORKSPACE,
        )

    @staticmethod
    def app_user(user_data: dict[str, Any], *, connector_id: str) -> AppUser:
        """Expected ``AppUser`` from a ``retrieve_user`` payload (``_transform_to_app_user``)."""
        return AppUser(
            id="",
            app_name=Connectors.NOTION,
            connector_id=connector_id,
            source_user_id=str(user_data["id"]),
            org_id="",
            email=str((user_data.get("person") or {}).get("email", "")).strip(),
            full_name=user_data.get("name"),
        )

    @staticmethod
    def app_metadata(*, connector_id: str, connector_name: str) -> AppMetadata:
        """Expected ``apps`` document for the connector instance."""
        return AppMetadata(
            connector_id=connector_id,
            name=connector_name,
            type="Notion",
            app_group="Notion",
            scope="team",
            created_at_timestamp=0,
            updated_at_timestamp=0,
            permission_model=PermissionModel.RECORD_LEVEL.value,
            vector_membership_backfilled=True,
        )
