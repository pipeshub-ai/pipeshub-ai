"""
Issue synchronisation for the GitHub Teams connector.

GitHub's ``/issues`` endpoint returns pull requests too (each PR item carries a
``pull_request`` key) — one paginated fetch per repo covers both entity types,
so this module also splits out and delegates PR items to ``PullRequestsSync``
rather than paying for a second, separately-paginated PR listing call.

Responsibilities:
- ``fetch_issues_batched``: fetch + batch-process both issues and PRs for a repo.
- ``_process_issue_to_ticket``: map a single GitHub issue to a ``TicketRecord``.
- ``process_new_records``: persist a batch and advance the issues/PRs checkpoint.
- ``build_ticket_blocks``: stream/index ticket content as ``BlocksContainer``.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from github.Repository import Repository  # type: ignore

from app.config.constants.arangodb import Connectors, MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.base.sync_point.sync_point import generate_record_sync_point_key
from app.connectors.core.registry.filters import IndexingFilterKey
from app.models.blocks import BlockGroup, BlocksContainer, DataFormat, GroupSubType, GroupType
from app.models.entities import Record, RecordGroupType, RecordType, TicketRecord
from app.utils.time_conversion import get_epoch_timestamp_in_ms

from .models import GitHubLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


class IssuesSync:
    """Handles issue (ticket) synchronisation for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # Sync entry point
    # ------------------------------------------------------------------

    async def fetch_issues_batched(self, repo: Repository) -> None:
        """Fetch issues+PRs for a repo (one paginated call), split, and batch-process both."""
        c = self.c
        owner, repo_name = repo.owner.login, repo.name

        issues_checkpoint = await self._get_sync_checkpoint(f"{repo.id}-work-items")
        prs_checkpoint = await self._get_sync_checkpoint(f"{repo.id}-pull-requests")
        checkpoints = [ts for ts in (issues_checkpoint, prs_checkpoint) if ts is not None]
        last_sync_time = min(checkpoints) if checkpoints else None
        since_dt = (
            datetime.fromtimestamp(last_sync_time / 1000, tz=timezone.utc)
            if last_sync_time is not None else None
        )

        issues_res = await c.runtime.ds_call(
            c.data_source.list_issues, owner, repo_name,
            state="all", since=since_dt, sort="updated", direction="asc",
        )
        if not issues_res.success:
            self.logger.error("Error fetching issues for %s: %s", repo.full_name, issues_res.error)
            return
        all_issues = issues_res.data or []
        if not all_issues:
            self.logger.debug("No issues/PRs found for %s", repo.full_name)
            return

        self.logger.info("Fetched %s issue(s)/PR(s) for %s; processing in batches", len(all_issues), repo.full_name)
        for i in range(0, len(all_issues), c.batch_size):
            batch = all_issues[i : i + c.batch_size]
            record_updates = await self._build_issue_and_pr_records(repo, batch)
            await self.process_new_records(record_updates)

    # ------------------------------------------------------------------
    # Record building (split issues vs PRs)
    # ------------------------------------------------------------------

    async def _build_issue_and_pr_records(self, repo: Repository, issue_batch: list[Any]) -> list[RecordUpdate]:
        c = self.c
        record_updates: list[RecordUpdate] = []
        issues_enabled = self._issues_indexing_enabled()
        comments_enabled = self._comments_indexing_enabled()

        for issue in issue_batch:
            is_pull_request = getattr(issue, "pull_request", None) is not None
            if is_pull_request:
                record_update = await c.pull_requests.process_pull_request_stub(repo, issue)
            else:
                record_update = await self._process_issue_to_ticket(repo, issue)
                if record_update and not issues_enabled:
                    record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            if not record_update:
                continue
            record_updates.append(record_update)

            markdown_raw: str = getattr(issue, "body", "") or ""
            _, attachments = await c.comments.clean_github_content(markdown_raw)
            if attachments:
                file_updates = await c.comments.make_file_records_from_list(attachments, record_update.record)
                if file_updates:
                    if is_pull_request or (not issues_enabled):
                        for ru in file_updates:
                            ru.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value
                    record_updates.extend(file_updates)

            if not is_pull_request and comments_enabled:
                comment_attachments = await self._fetch_issue_comment_attachments(repo, issue, record_update.record)
                record_updates.extend(comment_attachments)

        return record_updates

    async def _fetch_issue_comment_attachments(self, repo: Repository, issue: Any, record: Record) -> list[RecordUpdate]:
        """Extract file records from an issue's comments (content itself is fetched at stream time)."""
        c = self.c
        owner, repo_name = repo.owner.login, repo.name
        comments_res = await c.runtime.ds_call(c.data_source.list_issue_comments, owner, repo_name, issue.number)
        if not comments_res.success:
            self.logger.warning("Failed to list comments for issue #%s: %s", issue.number, comments_res.error)
            return []
        updates: list[RecordUpdate] = []
        for comment in comments_res.data or []:
            body = getattr(comment, "body", "") or ""
            _, attachments = await c.comments.clean_github_content(body)
            if attachments:
                updates.extend(await c.comments.make_file_records_from_list(attachments, record))
        return updates

    # ------------------------------------------------------------------
    # Issue -> TicketRecord
    # ------------------------------------------------------------------

    async def _process_issue_to_ticket(self, repo: Repository, issue: Any) -> RecordUpdate | None:
        """Map a single GitHub issue to a TicketRecord RecordUpdate."""
        c = self.c
        external_id = f"{repo.id}/issues/{issue.number}"
        try:
            async with c.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=c.connector_id, external_id=external_id,
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

            label_names: list[str] = [getattr(label, "name", str(label)) for label in (issue.labels or [])]
            assignee_logins: list[str] = [
                getattr(a, "login", None) for a in (issue.assignees or []) if getattr(a, "login", None)
            ]

            ticket_record = TicketRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=issue.title,
                external_record_id=external_id,
                record_type=RecordType.TICKET.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=self._datetime_to_epoch_ms(issue.updated_at),
                source_created_at=self._datetime_to_epoch_ms(issue.created_at),
                version=0,
                external_record_group_id=f"{repo.id}-work-items",
                org_id=c.data_entities_processor.org_id,
                record_group_type=RecordGroupType.PROJECT.value,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=issue.html_url,
                status=issue.state,
                external_revision_id=str(self._datetime_to_epoch_ms(issue.updated_at)),
                preview_renderable=False,
                labels=label_names,
                assignee_source_id=assignee_logins,
                inherit_permissions=True,
            )
            return RecordUpdate(
                record=ticket_record,
                is_new=is_new, is_updated=is_updated, is_deleted=False,
                metadata_changed=metadata_changed, content_changed=content_changed, permissions_changed=False,
                old_permissions=[], new_permissions=[], external_record_id=external_id,
            )
        except Exception as e:
            self.logger.error("Error processing issue #%s to ticket: %s", getattr(issue, "number", "?"), e, exc_info=True)
            return None

    @staticmethod
    def _datetime_to_epoch_ms(dt: Any) -> int:
        if dt is None:
            return get_epoch_timestamp_in_ms()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round(dt.timestamp() * 1000)

    # ------------------------------------------------------------------
    # Record persistence + checkpoint advancement
    # ------------------------------------------------------------------

    async def process_new_records(self, batch_records: list[RecordUpdate]) -> None:
        """Persist a batch of records and advance the issues/PRs sync checkpoint."""
        c = self.c
        if not batch_records:
            return
        need_sync_update = True
        for i in range(0, len(batch_records), c.batch_size):
            batch = batch_records[i : i + c.batch_size]
            batch_sent = [(ru.record, ru.new_permissions) for ru in batch]
            try:
                await c.data_entities_processor.on_new_records(batch_sent)
                if not need_sync_update:
                    continue
                for record_update in batch:
                    if record_update.record.record_type in (RecordType.TICKET.value, RecordType.PULL_REQUEST.value):
                        last_sync_time = record_update.record.source_updated_at
                        group_id = record_update.record.external_record_group_id
                        if group_id and last_sync_time:
                            await self._update_sync_checkpoint(group_id, last_sync_time)
            except Exception as e:
                self.logger.error("Error processing batch of GitHub issue/PR records: %s", e, exc_info=True)
                need_sync_update = False

    # ------------------------------------------------------------------
    # Content streaming (block building)
    # ------------------------------------------------------------------

    async def build_ticket_blocks(self, record: Record) -> bytes:
        """Build BlocksContainer JSON bytes for a ticket record."""
        c = self.c
        external_group_id: str = getattr(record, "external_record_group_id", None) or ""
        if not external_group_id:
            raise Exception("Repository id not found on ticket record.")
        repo_id = int(external_group_id.split("-")[0])
        issue_number = int(str(record.external_record_id).rsplit("/", 1)[-1])

        repo_res = await c.runtime.ds_call(c.data_source.get_repo_by_id, repo_id)
        if not repo_res.success or not repo_res.data:
            raise Exception(f"Failed to resolve repo id={repo_id} for record {record.external_record_id}: {repo_res.error}")
        owner, repo_name = repo_res.data.owner.login, repo_res.data.name

        issue_res = await c.runtime.ds_call(c.data_source.get_issue, owner, repo_name, issue_number)
        if not issue_res.success or not issue_res.data:
            raise Exception(f"Failed to fetch issue for record {record.external_record_id}: {issue_res.error}")
        issue = issue_res.data

        markdown_raw: str = getattr(issue, "body", "") or ""
        body_with_images = await c.comments.embed_images_as_base64(markdown_raw)
        child_records, remaining = await c.comments.make_child_records_of_attachments(markdown_raw, record)

        bg_0 = BlockGroup(
            index=0,
            name=record.record_name,
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            sub_type=GroupSubType.CONTENT.value,
            source_group_id=record.weburl,
            data=f"{issue.title}\n\n{body_with_images}",
            source_modified_date=getattr(issue, "updated_at", None),
            requires_processing=True,
            children_records=child_records,
        )
        block_groups: list[BlockGroup] = [bg_0]

        if self._comments_indexing_enabled():
            comment_bgs, comment_remaining = await c.comments.build_issue_comment_blocks(
                owner, repo_name, issue_number, parent_index=0, record=record,
            )
            block_groups.extend(comment_bgs)
            remaining.extend(comment_remaining)

        await self.process_new_records(remaining)
        blocks_container = BlocksContainer(blocks=[], block_groups=block_groups)
        return blocks_container.model_dump_json(indent=2).encode(GitHubLiterals.UTF_8.value)

    # ------------------------------------------------------------------
    # Reindex helpers
    # ------------------------------------------------------------------

    def parse_repo_id_and_number_from_record(self, record: Record) -> tuple[int, int] | None:
        """Resolve ``(repo_id, issue_or_pr_number)`` from a synced record's external id."""
        external_group_id = getattr(record, "external_record_group_id", None) or ""
        if not external_group_id:
            return None
        try:
            repo_id = int(external_group_id.split("-")[0])
            number = int(str(record.external_record_id).rsplit("/", 1)[-1])
            return (repo_id, number)
        except (ValueError, IndexError):
            return None

    async def check_and_fetch_updated_ticket_for_reindex(self, record: Record) -> tuple[Record, list[Any]] | None:
        """Fetch a TICKET from GitHub; return updated data if source revision changed."""
        c = self.c
        parsed = self.parse_repo_id_and_number_from_record(record)
        if not parsed:
            self.logger.warning("Cannot reindex-check GitHub ticket %s: missing/malformed external ids", record.id)
            return None
        repo_id, number = parsed
        repo_res = await c.runtime.ds_call(c.data_source.get_repo_by_id, repo_id)
        if not repo_res.success or not repo_res.data:
            self.logger.error("Failed to resolve repo id=%s for reindex %s: %s", repo_id, record.id, repo_res.error)
            return None
        repo = repo_res.data
        issue_res = await c.runtime.ds_call(c.data_source.get_issue, repo.owner.login, repo.name, number)
        if not issue_res.success or not issue_res.data:
            self.logger.error("Failed to fetch GitHub issue for reindex %s: %s", record.id, issue_res.error)
            return None
        issue = issue_res.data
        new_rev = str(self._datetime_to_epoch_ms(issue.updated_at))
        if getattr(record, "external_revision_id", None) == new_rev:
            return None
        ru = await self._process_issue_to_ticket(repo, issue)
        if not ru:
            return None
        return (ru.record, ru.new_permissions)

    # ------------------------------------------------------------------
    # Checkpoints
    # ------------------------------------------------------------------

    async def _get_sync_checkpoint(self, external_record_group_id: str) -> int | None:
        try:
            key = generate_record_sync_point_key(Connectors.GITHUB_TEAMS.value, external_record_group_id, "")
            data = await self.c.record_sync_point.read_sync_point(key)
            return data.get(GitHubLiterals.LAST_SYNC_TIME.value) if data else None
        except Exception:
            return None

    async def _update_sync_checkpoint(self, external_record_group_id: str, last_sync_time: Any) -> None:
        key = generate_record_sync_point_key(Connectors.GITHUB_TEAMS.value, external_record_group_id, "")
        await self.c.record_sync_point.update_sync_point(key, {GitHubLiterals.LAST_SYNC_TIME.value: last_sync_time})

    # ------------------------------------------------------------------
    # Indexing flags
    # ------------------------------------------------------------------

    def _issues_indexing_enabled(self) -> bool:
        c = self.c
        if not c.indexing_filters:
            return True
        return c.indexing_filters.is_enabled(IndexingFilterKey.ISSUES)

    def _comments_indexing_enabled(self) -> bool:
        c = self.c
        if not c.indexing_filters:
            return True
        return c.indexing_filters.is_enabled(IndexingFilterKey.COMMENTS)
