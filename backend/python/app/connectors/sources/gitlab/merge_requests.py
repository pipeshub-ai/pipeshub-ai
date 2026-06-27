"""
Merge-request synchronisation for the GitLab connector.

Responsibilities:
- ``fetch_prs_batched``: fetch and batch-process all MRs for a project.
- ``_build_pr_records``: build ``PullRequestRecord`` + attachment records from a batch.
- ``_process_mr_to_pull_request``: map a single GitLab MR to a ``PullRequestRecord``.
- ``build_pull_request_blocks``: stream/index MR content as ``BlocksContainer``.
- Sync checkpoint helpers for merge requests.
- Reindex helpers for tickets and pull requests.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from app.config.constants.arangodb import (
    MimeTypes,
    OriginTypes,
    ProgressStatus,
)
from app.models.entities import Record, RecordGroupType, RecordType, PullRequestRecord
from app.models.blocks import (
    Block,
    BlockGroup,
    BlockGroupChildren,
    BlocksContainer,
    BlockSubType,
    BlockType,
    DataFormat,
    GroupSubType,
    GroupType,
    IndexRange,
)
from app.utils.time_conversion import parse_timestamp, string_to_datetime

from .common.utils import parse_item_id_from_url
from .models import GitlabLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.gitlab.connector import GitLabConnector


class MergeRequestsSync:
    """Handles merge-request synchronisation for ``GitLabConnector``."""

    def __init__(self, connector: "GitLabConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Sync entry point
    # ------------------------------------------------------------------

    async def fetch_prs_batched(self, project_id: int) -> None:
        """Fetch and batch-process all MRs for a project using sync checkpoint."""
        c = self.c
        last_sync_time: int | None = await self._get_mr_sync_checkpoint(project_id)
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

        prs_res = await c.runtime.ds_call(
            c.data_source.list_merge_requests,
            project_id=project_id,
            updated_after=since_dt,
            updated_before=updated_before,
            created_after=created_after,
            created_before=created_before,
            order_by=GitlabLiterals.UPDATED_AT.value,
            sort="asc",
            get_all=True,
        )
        if not prs_res.success:
            self.logger.error("Error fetching merge requests for project %s: %s", project_id, prs_res.error)
            return
        if not prs_res.data:
            self.logger.debug("No merge requests found for project %s", project_id)
            return

        all_prs = prs_res.data
        self.logger.info("Fetched %s merge requests for project %s; processing in batches", len(all_prs), project_id)

        for i in range(0, len(all_prs), c.batch_size):
            batch_records = await self._build_pr_records(all_prs[i : i + c.batch_size])
            await c.issues.process_new_records(batch_records)

    # ------------------------------------------------------------------
    # Record building
    # ------------------------------------------------------------------

    async def _build_pr_records(self, prs_batch: list[Any]) -> list[RecordUpdate]:
        """Build PullRequestRecord + attachment records from a batch of GitLab MRs."""
        c = self.c
        record_updates_batch: list[RecordUpdate] = []
        attachments_count = 0
        mrs_enabled = self._merge_requests_indexing_enabled()
        comments_enabled = self._comments_indexing_enabled()

        for pr in prs_batch:
            record_update = await self._process_mr_to_pull_request(pr)
            if not record_update:
                continue
            if not mrs_enabled:
                record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
            record_updates_batch.append(record_update)

            # Description attachments
            markdown_content_raw: str = getattr(pr, "description", "") or ""
            attachments, _ = await c.attachments.parse_gitlab_uploads(markdown_content_raw)
            if attachments:
                file_record_updates = await c.attachments.make_file_records_from_list(
                    attachments=attachments, record=record_update.record
                )
                if file_record_updates:
                    if not mrs_enabled:
                        for ru in file_record_updates:
                            ru.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    record_updates_batch.extend(file_record_updates)
                    attachments_count += len(file_record_updates)

            # Note attachments
            attachment_records = await c.attachments.make_files_records_from_notes_mr(
                pr, record_update.record
            )
            if attachment_records:
                if not mrs_enabled or not comments_enabled:
                    for ru in attachment_records:
                        ru.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                record_updates_batch.extend(attachment_records)
                attachments_count += len(attachment_records)

        if attachments_count:
            self.logger.debug("Added %s attachments for merge requests batch", attachments_count)
        return record_updates_batch

    async def _process_mr_to_pull_request(self, pr: Any) -> RecordUpdate | None:
        """Map a single GitLab MR to a PullRequestRecord RecordUpdate."""
        c = self.c
        try:
            async with c.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=c.connector_id, external_id=str(pr.id)
                )
            is_new = existing_record is None
            is_updated = False
            metadata_changed = False
            content_changed = False
            if existing_record:
                if existing_record.record_name != pr.title:
                    metadata_changed = True
                    is_updated = True
                content_changed = True
                is_updated = True

            label_names: list[str] = list(pr.labels)
            assignee_list: list[str] = [a.get("username") for a in pr.assignees]
            reviewer_names: list[str] = [r.get("username") for r in pr.reviewers]
            merged_by: str | None = pr.merged_by.get("username") if pr.merged_by else None
            external_group_id = f"{pr.project_id}-merge-requests"

            merge_request_record = PullRequestRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=pr.title,
                external_record_id=str(pr.id),
                record_type=RecordType.PULL_REQUEST.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=parse_timestamp(pr.updated_at),
                source_created_at=parse_timestamp(pr.created_at),
                version=0,
                external_record_group_id=external_group_id,
                org_id=c.data_entities_processor.org_id,
                record_group_type=RecordGroupType.PROJECT.value,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=pr.web_url,
                status=pr.state,
                external_revision_id=str(parse_timestamp(pr.updated_at)),
                preview_renderable=False,
                mergeable=pr.merge_status,
                labels=label_names,
                inherit_permissions=True,
                assignee=assignee_list,
                merged_by=merged_by,
                review_name=reviewer_names,
            )
            return RecordUpdate(
                record=merge_request_record,
                is_new=is_new,
                is_updated=is_updated,
                is_deleted=False,
                metadata_changed=metadata_changed,
                content_changed=content_changed,
                permissions_changed=False,
                old_permissions=[],
                new_permissions=[],
                external_record_id=str(pr.id),
            )
        except Exception as e:
            self.logger.error("Error processing merge request to pull request: %s", e, exc_info=True)
            raise

    # ------------------------------------------------------------------
    # Content streaming (block building)
    # ------------------------------------------------------------------

    async def build_pull_request_blocks(self, record: Record) -> bytes:
        """Build BlocksContainer JSON bytes for a merge-request record."""
        c = self.c
        raw_url = getattr(record, "weburl", "") or ""
        if not raw_url:
            raise ValueError("Web URL is required for indexing merge request")
        mr_number = parse_item_id_from_url(raw_url)
        external_group_id = getattr(record, "external_record_group_id")
        if not external_group_id:
            raise Exception("Project id not found.")
        project_id = external_group_id.split("-")[0]

        mr_res = await c.runtime.ds_call(c.data_source.get_merge_request, project_id=project_id, mr_iid=mr_number)
        if not mr_res.success:
            raise Exception(f"Failed to fetch merge request details for record {record.external_record_id}: {mr_res.error}")
        if not mr_res.data:
            raise Exception(f"No merge request data found for record {record.external_record_id}")

        base_project_url = f"{c._gitlab_base_url}/api/v4/projects/{project_id}"
        block_group_number = 0
        block_number = 0
        blocks: list[Block] = []
        block_groups: list[BlockGroup] = []
        list_remaining_attachments: list[RecordUpdate] = []
        mr = mr_res.data

        markdown_content_raw: str = getattr(mr, "description", "") or ""
        markdown_with_images_base64 = await c.attachments.embed_images_as_base64(markdown_content_raw, base_project_url)
        markdown_content_with_title = f"{mr.title}\n\n{markdown_with_images_base64}"
        list_child_records, remaining_attachments = await c.attachments.make_child_records_of_attachments(
            markdown_content_raw, record
        )
        list_remaining_attachments.extend(remaining_attachments)

        bg_0 = BlockGroup(
            index=block_group_number,
            name=record.record_name,
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            sub_type=GroupSubType.CONTENT.value,
            source_group_id=record.weburl,
            data=markdown_content_with_title,
            source_modified_date=string_to_datetime(mr.updated_at),
            requires_processing=True,
            children_records=list_child_records,
        )
        block_groups.append(bg_0)
        block_group_number += 1

        if self._comments_indexing_enabled():
            comments_bg, remaining_attachments = await c.comments.build_merge_request_comment_blocks(
                mr_url=record.weburl, parent_index=0, record=record
            )
            block_groups.extend(comments_bg)
            block_group_number += len(comments_bg)
            list_remaining_attachments.extend(remaining_attachments)

        mr_commits_res = await c.runtime.ds_call(
            c.data_source.list_merge_requests_commits, project_id=project_id, mr_iid=mr_number, get_all=True,
        )
        if not mr_commits_res.success:
            raise Exception(f"Failed to fetch commits for merge request {mr_number}: {mr_commits_res.error}")

        mr_commits = mr_commits_res.data or []
        commits_block_start = block_number
        for commit in mr_commits:
            commit_message = getattr(commit, "message", "")
            commit_title = getattr(commit, "title", "")
            commit_web_url = getattr(commit, "web_url", "")
            commit_id = getattr(commit, "id", "")
            commit_committed_date = getattr(commit, "committed_date", "")
            block = Block(
                index=block_number,
                parent_index=block_group_number,
                type=BlockType.TEXT.value,
                sub_type=BlockSubType.COMMIT.value,
                weburl=commit_web_url,
                format=DataFormat.MARKDOWN,
                data=commit_message,
                source_id=commit_id,
                name=commit_title,
                source_creation_date=string_to_datetime(commit_committed_date),
            )
            block_number += 1
            blocks.append(block)

        bg_new = BlockGroup(
            index=block_group_number,
            name="block group for commits",
            type=GroupType.COMMITS,
            description=f"List of commits for merge request : {mr_number}",
            children=BlockGroupChildren(
                block_ranges=[IndexRange(start=commits_block_start, end=block_number - 1)]
            ) if blocks else None,
        )
        block_groups.append(bg_new)
        blocks_container = BlocksContainer(blocks=blocks, block_groups=block_groups)
        await c.issues.process_new_records(list_remaining_attachments)
        return blocks_container.model_dump_json(indent=2).encode(GitlabLiterals.UTF_8.value)

    # ------------------------------------------------------------------
    # Reindex helpers
    # ------------------------------------------------------------------

    def gitlab_project_id_and_iid_from_record(self, record: Record) -> tuple[str, int] | None:
        """Resolve GitLab project id and issue/MR IID from synced record fields."""
        external_group_id = getattr(record, "external_record_group_id", None) or ""
        if not external_group_id:
            return None
        project_part = external_group_id.split("-")[0]
        if not project_part:
            return None
        raw_url = getattr(record, "weburl", "") or ""
        if not raw_url:
            return None
        try:
            path = urlparse(raw_url).path
        except (TypeError, ValueError):
            return None
        segments = [s for s in path.split("/") if s]
        try:
            dash_idx = segments.index("-")
        except ValueError:
            return None
        if dash_idx + 2 >= len(segments):
            return None
        resource = segments[dash_idx + 1]
        if resource not in ("issues", "work_items", "merge_requests"):
            return None
        try:
            iid = int(segments[dash_idx + 2])
        except ValueError:
            return None
        return (project_part, iid)

    async def check_and_fetch_updated_record_for_reindex(
        self, record: Record
    ) -> tuple[Record, list[Any]] | None:
        """Fetch TICKET or PULL_REQUEST from GitLab; return updated data if source revision changed."""
        c = self.c
        parsed = self.gitlab_project_id_and_iid_from_record(record)
        if not parsed:
            self.logger.warning("Cannot reindex-check GitLab record %s: missing weburl or external_record_group_id", record.id)
            return None
        project_id, iid = parsed

        if record.record_type == RecordType.TICKET:
            issue_res = await c.runtime.ds_call(c.data_source.get_issue, project_id=project_id, issue_iid=iid)
            if not issue_res.success or not issue_res.data:
                self.logger.error("Failed to fetch GitLab issue for reindex %s: %s", record.id, issue_res.error)
                return None
            issue = issue_res.data
            new_rev = str(parse_timestamp(issue.updated_at))
            prev_rev = getattr(record, "external_revision_id", None)
            if prev_rev and prev_rev == new_rev:
                return None
            ru = await c.issues._process_issue_incident_task_to_ticket(issue)
            if not ru:
                return None
            return (ru.record, ru.new_permissions)

        if record.record_type == RecordType.PULL_REQUEST:
            mr_res = await c.runtime.ds_call(c.data_source.get_merge_request, project_id=project_id, mr_iid=iid)
            if not mr_res.success or not mr_res.data:
                self.logger.error("Failed to fetch GitLab merge request for reindex %s: %s", record.id, mr_res.error)
                return None
            mr = mr_res.data
            new_rev = str(parse_timestamp(mr.updated_at))
            prev_rev = getattr(record, "external_revision_id", None)
            if prev_rev and prev_rev == new_rev:
                return None
            ru = await self._process_mr_to_pull_request(mr)
            if not ru:
                return None
            return (ru.record, ru.new_permissions)

        return None

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    async def _get_mr_sync_checkpoint(self, project_id: int) -> int | None:
        """Return epoch-ms of last MR sync for a project."""
        from app.config.constants.arangodb import Connectors
        from app.connectors.core.base.sync_point.sync_point import generate_record_sync_point_key
        try:
            key = generate_record_sync_point_key(
                Connectors.GITLAB.value, f"{project_id}-merge-requests", ""
            )
            data = await self.c.record_sync_point.read_sync_point(key)
            return data.get(GitlabLiterals.LAST_SYNC_TIME.value) if data else None
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Indexing flags
    # ------------------------------------------------------------------

    def _merge_requests_indexing_enabled(self) -> bool:
        c = self.c
        if not c.indexing_filters:
            return True
        from app.connectors.core.registry.filters import IndexingFilterKey
        return c.indexing_filters.is_enabled(IndexingFilterKey.MERGE_REQUESTS)

    def _comments_indexing_enabled(self) -> bool:
        c = self.c
        if not c.indexing_filters:
            return True
        from app.connectors.core.registry.filters import IndexingFilterKey
        return c.indexing_filters.is_enabled(IndexingFilterKey.COMMENTS)
