# pyright: ignore-file

"""Build expected Linear graph entities for integration tests (mirrors ``LinearConnector`` mapping)."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.config.constants.arangodb import (
    AppGroups,
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.connectors.utils.value_mapper import ValueMapper
from app.models.entities import (
    AppMetadata,
    AppUserGroup,
    FileRecord,
    ItemType,
    LinkPublicStatus,
    LinkRecord,
    ProjectRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    Status,
    TicketRecord,
    WebpageRecord,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from connectors.linear.linear_test_utils import parse_linear_timestamp


class LinearExpected:
    """Expected graph entities for Linear connector integration tests.

    Each ``@staticmethod`` builds the entity the connector *should* have
    produced, using the same field derivation rules as the connector transform.
    """

    @staticmethod
    def app_metadata_for_full_sync_baseline(
        linear_connector: Dict[str, Any],
    ) -> AppMetadata:
        return AppMetadata(
            connector_id=linear_connector["connector_id"],
            name=linear_connector["connector_name"],
            type="Linear",
            app_group=AppGroups.LINEAR.value,
            scope="team",
            created_at_timestamp=0,
            updated_at_timestamp=0,
        )

    @staticmethod
    async def ticket_record(
        issue_id: str,
        *,
        connector_id: str,
        datasource: Any,
        value_mapper: ValueMapper | None = None,
    ) -> TicketRecord:
        """Build a ``TicketRecord`` from live Linear issue JSON."""
        vm = value_mapper or ValueMapper()

        resp = await datasource.issue(id=issue_id)
        assert resp.success, f"issue({issue_id!r}) failed: {resp.message}"
        issue: Dict[str, Any] = (resp.data or {}).get("issue", {})

        identifier = issue.get("identifier", "")
        title = issue.get("title", "")
        if identifier and title:
            record_name = f"[{identifier}] {title}"
        elif identifier:
            record_name = identifier
        else:
            record_name = issue_id

        priority_num = issue.get("priority")
        if priority_num is None:
            priority_str = None
        elif priority_num == 0:
            priority_str = "none"
        else:
            priority_map = {1: "Urgent", 2: "High", 3: "Medium", 4: "Low"}
            priority_str = priority_map.get(priority_num)
        priority = vm.map_priority(priority_str)

        state = issue.get("state", {})
        state_name = state.get("name") if state else None
        state_type = state.get("type") if state else None
        status = vm.map_status(state_name)
        if status and not isinstance(status, Status):
            status = vm.map_status(state_type)

        labels = issue.get("labels", {})
        label_nodes = labels.get("nodes", []) if labels else []
        type_value = None
        for label in label_nodes:
            label_name = label.get("name", "") if label else ""
            if label_name:
                mapped = vm.map_type(label_name)
                if mapped and isinstance(mapped, ItemType):
                    type_value = mapped
                    break
        if not type_value:
            parent = issue.get("parent")
            if parent and parent.get("id"):
                type_value = ItemType.SUB_ISSUE
            else:
                type_value = ItemType.ISSUE

        assignee = issue.get("assignee", {})
        assignee_email = assignee.get("email") if assignee else None
        assignee_name = (assignee.get("displayName") or assignee.get("name")) if assignee else None
        creator = issue.get("creator", {})
        creator_email = creator.get("email") if creator else None
        creator_name = (creator.get("displayName") or creator.get("name")) if creator else None

        parent_obj = issue.get("parent")
        parent_external_record_id = parent_obj.get("id") if parent_obj and parent_obj.get("id") else None

        team = issue.get("team", {})
        team_id = team.get("id") if team else None

        created_at = parse_linear_timestamp(issue.get("createdAt"))
        updated_at = parse_linear_timestamp(issue.get("updatedAt"))
        weburl = issue.get("url")
        external_revision_id = str(updated_at) if updated_at else None

        return TicketRecord(
            id="",
            org_id="",
            record_name=record_name,
            record_type=RecordType.TICKET,
            external_record_id=issue.get("id", issue_id),
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=parent_external_record_id,
            parent_record_type=RecordType.TICKET if parent_external_record_id else None,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id=connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=weburl,
            source_created_at=created_at,
            source_updated_at=updated_at,
            created_at=created_at,
            updated_at=updated_at,
            inherit_permissions=True,
            preview_renderable=False,
            is_dependent_node=False,
            parent_node_id=None,
            status=status,
            priority=priority,
            type=type_value,
            assignee=assignee_name,
            creator_name=creator_name,
            creator_email=creator_email,
            assignee_email=assignee_email,
        )

    @staticmethod
    async def project_record(
        project_id: str,
        *,
        connector_id: str,
        datasource: Any,
        team_id: str,
    ) -> ProjectRecord:
        """Build a ``ProjectRecord`` from live Linear project JSON."""
        resp = await datasource.project(id=project_id)
        assert resp.success, f"project({project_id!r}) failed: {resp.message}"
        proj: Dict[str, Any] = (resp.data or {}).get("project", {})

        name = proj.get("name", "")
        slug_id = proj.get("slugId", "")
        record_name = name or slug_id or project_id

        status_obj = proj.get("status", {})
        status_name = status_obj.get("name") if status_obj else None
        priority_label = proj.get("priorityLabel", "")

        lead = proj.get("lead", {})
        lead_id = lead.get("id") if lead else None
        lead_name = (lead.get("displayName") or lead.get("name")) if lead else None
        lead_email = lead.get("email") if lead else None

        created_at = parse_linear_timestamp(proj.get("createdAt"))
        updated_at = parse_linear_timestamp(proj.get("updatedAt"))
        weburl = proj.get("url")
        external_revision_id = str(updated_at) if updated_at else None

        return ProjectRecord(
            id="",
            org_id="",
            record_name=record_name,
            record_type=RecordType.PROJECT,
            external_record_id=proj.get("id", project_id),
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=None,
            parent_record_type=None,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id=connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=weburl,
            source_created_at=created_at,
            source_updated_at=updated_at,
            created_at=created_at,
            updated_at=updated_at,
            status=status_name,
            priority=priority_label,
            lead_id=lead_id,
            lead_name=lead_name,
            lead_email=lead_email,
            preview_renderable=False,
            inherit_permissions=True,
            is_dependent_node=False,
            parent_node_id=None,
        )

    @staticmethod
    def user_group(
        *,
        name: str,
        source_user_group_id: str,
        connector_id: str,
    ) -> AppUserGroup:
        """Build an ``AppUserGroup`` for a Linear team."""
        return AppUserGroup(
            id="",
            org_id="",
            app_name=Connectors.LINEAR,
            connector_id=connector_id,
            source_user_group_id=str(source_user_group_id),
            name=name,
            description=None,
            created_at=0,
            updated_at=0,
            source_created_at=None,
            source_updated_at=None,
        )

    @staticmethod
    def record_group(
        team_data: Dict[str, Any],
        *,
        connector_id: str,
        organization_url_key: Optional[str] = None,
    ) -> RecordGroup:
        """Build a team ``RecordGroup`` from Linear team JSON."""
        team_id = team_data.get("id")
        team_name = team_data.get("name", "")
        team_key = team_data.get("key")

        web_url: Optional[str] = None
        if organization_url_key and team_key:
            web_url = f"https://linear.app/{organization_url_key}/team/{team_key}"

        parent_team = team_data.get("parent")
        parent_external_group_id = parent_team.get("id") if parent_team else None

        return RecordGroup(
            id="",
            org_id="",
            external_group_id=str(team_id) if team_id else None,
            connector_id=connector_id,
            connector_name=Connectors.LINEAR,
            name=team_name,
            short_name=team_key or str(team_id or ""),
            group_type=RecordGroupType.PROJECT,
            web_url=web_url,
            parent_external_group_id=parent_external_group_id,
        )

    @staticmethod
    def _mime_type_from_url(url: str, filename: str = "") -> str:
        ext = ""
        if filename and "." in filename:
            ext = filename.split(".")[-1].lower()
        elif "." in url:
            ext = url.split("?")[0].split(".")[-1].lower()
        extension_to_mime = {
            "pdf": MimeTypes.PDF.value,
            "png": MimeTypes.PNG.value,
            "jpg": MimeTypes.JPEG.value,
            "jpeg": MimeTypes.JPEG.value,
            "gif": MimeTypes.GIF.value,
            "webp": MimeTypes.WEBP.value,
            "svg": MimeTypes.SVG.value,
            "doc": MimeTypes.DOC.value,
            "docx": MimeTypes.DOCX.value,
            "xls": MimeTypes.XLS.value,
            "xlsx": MimeTypes.XLSX.value,
            "ppt": MimeTypes.PPT.value,
            "pptx": MimeTypes.PPTX.value,
            "csv": MimeTypes.CSV.value,
            "zip": MimeTypes.ZIP.value,
            "json": MimeTypes.JSON.value,
            "xml": MimeTypes.XML.value,
            "txt": MimeTypes.PLAIN_TEXT.value,
            "md": MimeTypes.MARKDOWN.value,
            "html": MimeTypes.HTML.value,
        }
        return extension_to_mime.get(ext, MimeTypes.UNKNOWN.value)

    @staticmethod
    async def link_record(
        attachment_id: str,
        *,
        connector_id: str,
        datasource: Any,
        team_id: str,
        parent_node_id: Optional[str] = None,
    ) -> LinkRecord:
        """Build a ``LinkRecord`` from live Linear attachment JSON."""
        resp = await datasource.attachment(id=attachment_id)
        assert resp.success, f"attachment({attachment_id!r}) failed: {resp.message}"
        attachment: Dict[str, Any] = (resp.data or {}).get("attachment", {})

        url = attachment.get("url", "")
        assert url, f"Attachment {attachment_id} missing url"
        title = (
            attachment.get("title")
            or attachment.get("subtitle")
            or attachment.get("label")
        )
        record_name = title if title else url.split("/")[-1] or f"Attachment {attachment_id[:8]}"

        issue = attachment.get("issue") or {}
        parent_external_id = issue.get("id")
        parent_record_type = RecordType.TICKET

        created_at = parse_linear_timestamp(attachment.get("createdAt"))
        updated_at = parse_linear_timestamp(attachment.get("updatedAt"))
        external_revision_id = str(updated_at) if updated_at else None

        return LinkRecord(
            id="",
            org_id="",
            record_name=record_name,
            record_type=RecordType.LINK,
            external_record_id=attachment.get("id", attachment_id),
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=parent_external_id,
            parent_record_type=parent_record_type,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id=connector_id,
            mime_type=MimeTypes.MARKDOWN.value,
            weburl=url,
            url=url,
            title=title,
            is_public=LinkPublicStatus.UNKNOWN,
            source_created_at=created_at,
            source_updated_at=updated_at,
            created_at=created_at,
            updated_at=updated_at,
            preview_renderable=False,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
        )

    @staticmethod
    async def webpage_record(
        document_id: str,
        *,
        connector_id: str,
        datasource: Any,
        team_id: str,
        parent_external_id: Optional[str] = None,
        parent_record_type: RecordType = RecordType.TICKET,
        parent_node_id: Optional[str] = None,
    ) -> WebpageRecord:
        """Build a ``WebpageRecord`` from live Linear document JSON."""
        resp = await datasource.document(id=document_id)
        assert resp.success, f"document({document_id!r}) failed: {resp.message}"
        document: Dict[str, Any] = (resp.data or {}).get("document", {})

        url = document.get("url", "")
        assert url, f"Document {document_id} missing url"
        title = document.get("title", "")
        record_name = title if title else f"Document {document_id[:8]}"

        if parent_external_id is None:
            issue = document.get("issue")
            project = document.get("project")
            if issue and issue.get("id"):
                parent_external_id = issue.get("id")
                parent_record_type = RecordType.TICKET
            elif project and project.get("id"):
                parent_external_id = project.get("id")
                parent_record_type = RecordType.PROJECT

        created_at = parse_linear_timestamp(document.get("createdAt"))
        updated_at = parse_linear_timestamp(document.get("updatedAt"))
        external_revision_id = str(updated_at) if updated_at else None

        return WebpageRecord(
            id="",
            org_id="",
            record_name=record_name,
            record_type=RecordType.WEBPAGE,
            external_record_id=document.get("id", document_id),
            external_revision_id=external_revision_id,
            external_record_group_id=team_id,
            parent_external_record_id=parent_external_id,
            parent_record_type=parent_record_type,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id=connector_id,
            mime_type=MimeTypes.MARKDOWN.value,
            weburl=url,
            source_created_at=created_at,
            source_updated_at=updated_at,
            created_at=created_at,
            updated_at=updated_at,
            preview_renderable=False,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
        )

    @staticmethod
    def file_record(
        file_url: str,
        filename: str,
        *,
        parent_external_id: str,
        parent_record_type: RecordType,
        team_id: str,
        connector_id: str,
        parent_weburl: Optional[str] = None,
        parent_created_at: int = 0,
        parent_updated_at: int = 0,
        parent_node_id: Optional[str] = None,
        size_in_bytes: int = 0,
    ) -> FileRecord:
        """Build a ``FileRecord`` from extracted markdown file metadata."""
        extension = None
        if "." in filename:
            extension = filename.split(".")[-1].lower()
        mime_type = LinearExpected._mime_type_from_url(file_url, filename)

        return FileRecord(
            id="",
            org_id="",
            record_name=filename,
            record_type=RecordType.FILE,
            external_record_id=file_url,
            external_revision_id=None,
            external_record_group_id=team_id,
            parent_external_record_id=parent_external_id,
            parent_record_type=parent_record_type,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.LINEAR,
            connector_id=connector_id,
            mime_type=mime_type,
            weburl=parent_weburl or file_url,
            source_created_at=parent_created_at,
            source_updated_at=parent_updated_at,
            created_at=parent_created_at,
            updated_at=parent_updated_at,
            preview_renderable=True,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
            inherit_permissions=True,
            is_file=True,
            extension=extension,
            size_in_bytes=size_in_bytes,
        )
