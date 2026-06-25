"""
Issue synchronisation for the GitLab connector.

Responsibilities:
- ``_fetch_issues_batched``: fetch and batch-process all issues for a project.
- ``_build_issue_records``: build ``TicketRecord`` + attachment records from a batch.
- ``_process_issue_incident_task_to_ticket``: map a single GitLab issue to a ``TicketRecord``.
- ``_process_new_records``: persist a batch and advance the issues sync checkpoint.
- ``_build_ticket_blocks``: stream/index ticket content as ``BlocksContainer``.
- Sync checkpoint helpers for issues.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import (
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import Record, RecordGroupType, RecordType, TicketRecord, ItemType
from app.models.blocks import (
    BlockGroup,
    BlocksContainer,
    DataFormat,
    GroupSubType,
    GroupType,
)
from app.utils.time_conversion import parse_timestamp, string_to_datetime

from .common.utils import parse_item_id_from_url
from .models import GitlabLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.gitlab.connector import GitLabConnector


class IssuesSync:
    """Handles issue (work-item) synchronisation for ``GitLabConnector``."""

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Sync entry point
    # ------------------------------------------------------------------

    async def fetch_issues_batched(self, project_id: int) -> None:
        """Fetch and batch-process all issues for a project using sync checkpoint."""
        c = self.c
        last_sync_time: int | None = await self._get_issues_sync_checkpoint(project_id)
        since_dt = (
            datetime.fromtimestamp(last_sync_time / 1000, tz=timezone.utc)
            if last_sync_time is not None
            else None
        )
        filter_after, filter_before = c.datetime_range_from_sync_filter("modified")
        if filter_after is not None:
            since_dt = filter_after if since_dt is None else max(since_dt, filter_after)
        updated_before = filter_before
        created_after, created_before = c.datetime_range_from_sync_filter("created")

        issues_res = await c.runtime.ds_call(
            c.data_source.list_issues,
            project_id=project_id,
            updated_after=since_dt,
            updated_before=updated_before,
            created_after=created_after,
            created_before=created_before,
            order_by=GitlabLiterals.UPDATED_AT.value,
            sort="asc",
            get_all=True,
        )
        if not issues_res.success:
            self.logger.error("Error fetching issues for project %s: %s", project_id, issues_res.error)
            return
        if not issues_res.data:
            self.logger.debug("No issues found for project %s", project_id)
            return

        all_issues = issues_res.data
        self.logger.info("Fetched %s issues for project %s; processing in batches", len(all_issues), project_id)

        for i in range(0, len(all_issues), c.batch_size):
            batch_records = await self._build_issue_records(all_issues[i : i + c.batch_size])
            await self.process_new_records(batch_records)

    # ------------------------------------------------------------------
    # Record building
    # ------------------------------------------------------------------

    async def _build_issue_records(self, issue_batch: list[Any]) -> list[RecordUpdate]:
        """Build TicketRecord + attachment records from a batch of GitLab issues."""
        c = self.c
        record_updates_batch: list[RecordUpdate] = []
        attachment_records_cnt = 0
        issues_enabled = self._issues_indexing_enabled()
        comments_enabled = self._comments_indexing_enabled()

        for issue in issue_batch:
            record_update = await self._process_issue_incident_task_to_ticket(issue)
            if not record_update:
                continue
            if not issues_enabled:
                record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
            record_updates_batch.append(record_update)

            # Description attachments
            markdown_content_raw: str = getattr(issue, "description", "") or ""
            attachments, _ = await c.attachments.parse_gitlab_uploads(markdown_content_raw)
            if attachments:
                file_record_updates = await c.attachments.make_file_records_from_list(
                    attachments=attachments, record=record_update.record
                )
                if file_record_updates:
                    if not issues_enabled:
                        for ru in file_record_updates:
                            ru.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    record_updates_batch.extend(file_record_updates)
                    attachment_records_cnt += len(file_record_updates)

            # Note attachments
            attachment_records = await c.attachments.make_files_records_from_notes(
                issue, record_update.record
            )
            if attachment_records:
                if not issues_enabled or not comments_enabled:
                    for ru in attachment_records:
                        ru.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                record_updates_batch.extend(attachment_records)
                attachment_records_cnt += len(attachment_records)

        if attachment_records_cnt:
            self.logger.debug("Added %s attachments for issues batch", attachment_records_cnt)
        return record_updates_batch

    async def _process_issue_incident_task_to_ticket(self, issue: Any) -> RecordUpdate | None:
        """Map a single GitLab work-item to a TicketRecord RecordUpdate."""
        c = self.c
        try:
            async with c.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=c.connector_id, external_id=str(issue.id)
                )
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            if existing_record:
                if existing_record.record_name != issue.title:
                    metadata_changed = True
                    is_updated = True
                content_changed = True
                is_updated = True

            issue_type = ItemType.ISSUE.value
            if issue.issue_type == ItemType.INCIDENT.value.lower():
                issue_type = ItemType.INCIDENT.value
            elif issue.issue_type == ItemType.TASK.value.lower():
                issue_type = ItemType.TASK.value

            external_group_id = f"{issue.project_id}-work-items"
            ticket_record = TicketRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=issue.title,
                external_record_id=str(issue.id),
                record_type=RecordType.TICKET.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=parse_timestamp(issue.updated_at),
                source_created_at=parse_timestamp(issue.created_at),
                version=0,
                external_record_group_id=external_group_id,
                org_id=c.data_entities_processor.org_id,
                record_group_type=RecordGroupType.PROJECT.value,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=issue.web_url,
                status=issue.state,
                external_revision_id=str(parse_timestamp(issue.updated_at)),
                preview_renderable=False,
                type=issue_type,
                labels=list(issue.labels),
                inherit_permissions=True,
            )
            return RecordUpdate(
                record=ticket_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=False,
                old_permissions=[],
                new_permissions=[],
                external_record_id=str(issue.id),
            )
        except Exception as e:
            self.logger.error("Error processing issue/task/incident to ticket: %s", e, exc_info=True)
            return None

    # ------------------------------------------------------------------
    # Record persistence + checkpoint advancement
    # ------------------------------------------------------------------

    async def process_new_records(self, batch_records: list[RecordUpdate]) -> None:
        """Persist a batch of records and advance the appropriate sync checkpoint."""
        c = self.c
        need_sync_update: bool = True
        for i in range(0, len(batch_records), c.batch_size):
            batch = batch_records[i : i + c.batch_size]
            batch_sent = [(ru.record, ru.new_permissions) for ru in batch]
            try:
                await c.data_entities_processor.on_new_records(batch_sent)
                if not need_sync_update:
                    continue
                last_sync_time = None
                project_id: str | None = None
                for record_update in batch:
                    if record_update.record.record_type in (RecordType.TICKET, RecordType.PULL_REQUEST):
                        last_sync_time = record_update.record.source_updated_at
                        project_id = record_update.record.external_record_group_id
                if project_id and last_sync_time:
                    await self._update_sync_checkpoint(project_id, last_sync_time)
            except Exception as e:
                self.logger.error("Error processing batch of records: %s", e)
                need_sync_update = False

        self.logger.info("Processed %s records", len(batch_records))

    # ------------------------------------------------------------------
    # Content streaming (block building)
    # ------------------------------------------------------------------

    async def build_ticket_blocks(self, record: Record) -> bytes:
        """Build BlocksContainer JSON bytes for a ticket record."""
        c = self.c
        raw_url = getattr(record, "weburl", "") or ""
        if not raw_url:
            raise ValueError("Web URL is required for indexing ticket")
        issue_number = parse_item_id_from_url(raw_url)
        external_group_id: str = getattr(record, "external_record_group_id")
        if not external_group_id:
            raise Exception("Project id not found.")
        project_id = external_group_id.split("-")[0]

        issue_res = await c.runtime.ds_call(c.data_source.get_issue, project_id=project_id, issue_iid=issue_number)
        if not issue_res.success:
            raise Exception(f"Failed to fetch issue details for record {record.external_record_id}: {issue_res.error}")
        if not issue_res.data:
            raise Exception(f"No issue data found for record {record.external_record_id}")

        base_project_url = f"{c._gitlab_base_url}/api/v4/projects/{project_id}"
        block_group_number = 0
        block_groups: list[BlockGroup] = []
        issue = issue_res.data

        markdown_content_raw: str = getattr(issue, "description", "") or ""
        markdown_content_with_images_base64 = await c.attachments.embed_images_as_base64(markdown_content_raw, base_project_url)
        markdown_content_with_images_base64 = f"# {issue.title}\n\n{markdown_content_with_images_base64}"

        list_remaining_records: list[RecordUpdate] = []
        child_records, remaining_records = await c.attachments.make_child_records_of_attachments(
            markdown_raw=markdown_content_raw, record=record
        )
        list_remaining_records.extend(remaining_records)

        bg_0 = BlockGroup(
            index=block_group_number,
            name=record.record_name,
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            sub_type=GroupSubType.CONTENT.value,
            source_group_id=record.weburl,
            data=markdown_content_with_images_base64,
            source_modified_date=string_to_datetime(issue.updated_at),
            requires_processing=True,
            children_records=child_records,
        )
        block_groups.append(bg_0)

        if self._comments_indexing_enabled():
            comments_bg, remaining_records = await c.comments.build_comment_blocks(
                issue_url=record.weburl, parent_index=block_group_number, record=record
            )
            block_groups.extend(comments_bg)
            block_group_number += len(comments_bg)
            list_remaining_records.extend(remaining_records)

        blocks_container = BlocksContainer(blocks=[], block_groups=block_groups)
        await self.process_new_records(list_remaining_records)
        return blocks_container.model_dump_json(indent=2).encode(GitlabLiterals.UTF_8.value)

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    async def _get_issues_sync_checkpoint(self, project_id: int) -> int | None:
        """Return epoch-ms of last issues sync for a project."""
        from app.config.constants.arangodb import Connectors
        from app.connectors.core.base.sync_point.sync_point import generate_record_sync_point_key
        try:
            key = generate_record_sync_point_key(
                Connectors.GITLAB.value, f"{project_id}-work-items", ""
            )
            data = await self.c.record_sync_point.read_sync_point(key)
            return data.get(GitlabLiterals.LAST_SYNC_TIME.value) if data else None
        except Exception:
            return None

    async def _update_sync_checkpoint(self, external_record_group_id: str, last_sync_time: Any) -> None:
        """Update the sync checkpoint for a project entity group (issues or MRs).

        ``external_record_group_id`` is the value stored on the record, e.g.
        ``"123-work-items"`` or ``"123-merge-requests"``, which naturally
        produces distinct DB documents for each entity type.
        """
        from app.config.constants.arangodb import Connectors
        from app.connectors.core.base.sync_point.sync_point import generate_record_sync_point_key
        key = generate_record_sync_point_key(Connectors.GITLAB.value, external_record_group_id, "")
        await self.c.record_sync_point.update_sync_point(key, {GitlabLiterals.LAST_SYNC_TIME.value: last_sync_time})

    # ------------------------------------------------------------------
    # Indexing flags
    # ------------------------------------------------------------------

    def _issues_indexing_enabled(self) -> bool:
        c = self.c
        if not c.indexing_filters:
            return True
        from app.connectors.core.registry.filters import IndexingFilterKey
        return c.indexing_filters.is_enabled(IndexingFilterKey.ISSUES)

    def _comments_indexing_enabled(self) -> bool:
        c = self.c
        if not c.indexing_filters:
            return True
        from app.connectors.core.registry.filters import IndexingFilterKey
        return c.indexing_filters.is_enabled(IndexingFilterKey.COMMENTS)
