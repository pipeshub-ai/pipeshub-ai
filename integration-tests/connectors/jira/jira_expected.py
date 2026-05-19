"""Build expected Jira graph entities for integration tests (mirrors ``jira_cloud`` connector mapping)."""

from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
)
from app.connectors.utils.value_mapper import ValueMapper
from app.models.entities import (
    AppMetadata,
    AppRole,
    AppUserGroup,
    FileRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.utils.time_conversion import get_epoch_timestamp_in_ms


class JiraExpected:
    """Expected graph entities for Jira connector integration tests.

    Add new entity builders here as ``@staticmethod`` methods (sync or async).
    """

    @staticmethod
    def app_metadata_for_full_sync_baseline(
        jira_connector: dict[str, Any],
        actual: AppMetadata,
    ) -> AppMetadata:
        """Expected apps document for TC-SYNC-001: fixture connector id + instance name; rest from graph."""
        return actual.model_copy(
            update={
                "connector_id": jira_connector["connector_id"],
                "name": jira_connector["connector_name"],
            },
        )

    @staticmethod
    def _parse_jira_timestamp(timestamp_str: str | None) -> int:
        if not timestamp_str:
            return 0
        normalized = timestamp_str.replace("Z", "+00:00")
        normalized = re.sub(r"([+-])(\d{2})(\d{2})$", r"\1\2:\3", normalized)
        try:
            dt = datetime.fromisoformat(normalized)
            return int(dt.timestamp() * 1000)
        except (ValueError, AttributeError):
            normalized_strptime = re.sub(r"([+-])(\d{2}):(\d{2})$", r"\1\2\3", normalized)
            for fmt in ("%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
                try:
                    dt = datetime.strptime(normalized_strptime, fmt)
                    return int(dt.timestamp() * 1000)
                except ValueError:
                    continue
        return 0

    @staticmethod
    async def ticket_record(
        issue_key: str,
        *,
        connector_id: str,
        datasource: Any,
        site_base_url: str | None = None,
        value_mapper: ValueMapper | None = None,
        connector_enum: Connectors | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> TicketRecord:
        """Build a ``TicketRecord`` from live Jira issue JSON (mirrors connector issue mapping)."""
        vm = value_mapper or ValueMapper()
        ov = overrides or {}

        resp = await datasource.get_issue(issueIdOrKey=issue_key)
        assert resp.status == 200, f"get_issue({issue_key!r}) failed: HTTP {resp.status}"
        issue: dict[str, Any] = resp.json()
        fields = issue.get("fields", {}) or {}

        issue_id = str(issue.get("id", "") or "")
        key = issue.get("key", issue_key)
        issue_summary = fields.get("summary") or f"Issue {key}"
        issue_name = f"[{key}] {issue_summary}" if key else issue_summary

        issue_type_obj = fields.get("issuetype", {}) or {}
        raw_issue_type = issue_type_obj.get("name") if issue_type_obj else None
        issue_type = vm.map_type(raw_issue_type)
        hierarchy_level = issue_type_obj.get("hierarchyLevel") if issue_type_obj else None
        is_epic = hierarchy_level == 1
        is_subtask = hierarchy_level == -1

        parent_obj = fields.get("parent")
        parent_external_id = str(parent_obj.get("id")) if parent_obj and parent_obj.get("id") else None
        parent_record_type: RecordType | None = None
        if parent_external_id and (is_subtask or not is_epic):
            parent_record_type = RecordType.TICKET

        status_obj = fields.get("status", {}) or {}
        status = vm.map_status(status_obj.get("name") if status_obj else None)

        priority_obj = fields.get("priority", {}) or {}
        priority = vm.map_priority(priority_obj.get("name") if priority_obj else None)

        creator = fields.get("creator") or {}
        creator_name = creator.get("displayName")
        creator_email = creator.get("emailAddress")
        reporter = fields.get("reporter") or {}
        reporter_name = reporter.get("displayName")
        reporter_email = reporter.get("emailAddress")
        assignee = fields.get("assignee") or {}
        assignee_name = assignee.get("displayName") if assignee else None
        assignee_email = assignee.get("emailAddress") if assignee else None

        created_at = JiraExpected._parse_jira_timestamp(fields.get("created"))
        updated_at = JiraExpected._parse_jira_timestamp(fields.get("updated"))

        base_url = (
            site_base_url
            or os.getenv("JIRA_TEST_BASE_URL")
            or ""
        ).rstrip("/")
        weburl = f"{base_url}/browse/{key}" if base_url and key else None

        external_group = ov.get("external_record_group_id")
        if external_group is None:
            project = fields.get("project") or {}
            pid = project.get("id")
            external_group = str(pid) if pid is not None else None

        return TicketRecord(
            id="",
            org_id="",
            record_name=issue_name,
            record_type=RecordType.TICKET,
            external_record_id=issue_id,
            external_revision_id=str(updated_at) if updated_at else None,
            external_record_group_id=str(external_group) if external_group is not None else None,
            parent_external_record_id=parent_external_id,
            parent_record_type=parent_record_type,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=connector_enum or Connectors.JIRA,
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
            type=issue_type,
            assignee=assignee_name,
            reporter_name=reporter_name,
            creator_name=creator_name,
            reporter_email=reporter_email,
            assignee_email=assignee_email,
            creator_email=creator_email,
        )

    @staticmethod
    def user_group(
        *,
        name: str,
        source_user_group_id: str,
        connector_id: str,
    ) -> AppUserGroup:
        """Build an ``AppUserGroup`` for a Jira site group (mirrors ``_sync_user_groups``)."""
        return AppUserGroup(
            id="",
            org_id="",
            app_name=Connectors.JIRA,
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
    def project_role(
        *,
        project_key: str,
        jira_role_numeric_id: str | int,
        role_display_name: str,
        connector_id: str,
    ) -> AppRole:
        """Build an ``AppRole`` for a Jira project role (mirrors ``_sync_project_roles``)."""
        rid = str(jira_role_numeric_id).strip()
        return AppRole(
            id="",
            org_id="",
            app_name=Connectors.JIRA,
            connector_id=connector_id,
            source_role_id=f"{project_key}_{rid}",
            name=f"{project_key} - {role_display_name}",
            created_at=0,
            updated_at=0,
            source_created_at=None,
            source_updated_at=None,
            parent_role_id=None,
        )

    @staticmethod
    def record_group(
        proj_data: dict[str, Any],
        *,
        connector_id: str,
        project_key: str,
    ) -> RecordGroup:
        """Build a project ``RecordGroup`` from Jira ``get_project`` JSON."""
        project_id = proj_data.get("id")
        project_name = proj_data.get("name") or ""
        return RecordGroup(
            id="",
            org_id="",
            external_group_id=str(project_id) if project_id is not None else None,
            connector_id=connector_id,
            connector_name=Connectors.JIRA,
            name=project_name,
            short_name=project_key,
            group_type=RecordGroupType.PROJECT,
            web_url=proj_data.get("url") or proj_data.get("self"),
        )

    @staticmethod
    def file_record(
        *,
        attachment_id: str,
        filename: str,
        mime_type: str,
        file_size: int,
        created_at: int,
        issue_id: str,
        issue_key: str,
        project_id: str,
        connector_id: str,
        site_base_url: str | None = None,
        version: int = 0,
        parent_node_id: str | None = None,
    ) -> FileRecord:
        """Build a ``FileRecord`` for an issue attachment (mirrors ``_create_attachment_file_record``).

        ``parent_node_id`` is the graph ``Record.id`` of the parent TICKET (after sync).
        """
        extension = None
        if "." in filename:
            extension = filename.split(".")[-1].lower()

        base = (site_base_url or os.getenv("JIRA_TEST_BASE_URL") or "").rstrip("/")
        weburl = f"{base}/browse/{issue_key}" if base and issue_key else None

        ts = created_at or get_epoch_timestamp_in_ms()

        return FileRecord(
            id="",
            org_id="",
            record_name=filename,
            record_type=RecordType.FILE,
            external_record_id=f"attachment_{attachment_id}",
            external_revision_id=str(created_at) if created_at else None,
            parent_external_record_id=issue_id,
            connector_name=Connectors.JIRA,
            connector_id=connector_id,
            origin=OriginTypes.CONNECTOR,
            version=version,
            mime_type=mime_type,
            extension=extension,
            size_in_bytes=file_size,
            external_record_group_id=project_id,
            created_at=ts,
            updated_at=ts,
            source_created_at=created_at,
            source_updated_at=created_at,
            weburl=weburl,
            inherit_permissions=True,
            is_file=True,
            is_dependent_node=True,
            parent_node_id=parent_node_id,
        )

    @staticmethod
    def attachment_metadata(
        issue_fields: dict[str, Any], attachment_id: str
    ) -> tuple[str, str, int, int] | None:
        """Return ``(filename, mime_type, file_size, created_ms)`` for ``attachment_id`` in issue ``fields``."""
        for att in issue_fields.get("attachment") or []:
            if str(att.get("id", "")) != str(attachment_id):
                continue
            filename = att.get("filename", "unknown")
            file_size = int(att.get("size", 0) or 0)
            mime_type = att.get("mimeType", MimeTypes.UNKNOWN.value)
            created_str = att.get("created")
            created_ms = JiraExpected._parse_jira_timestamp(created_str) if created_str else 0
            return (filename, mime_type, file_size, created_ms)
        return None
