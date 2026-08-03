"""
Pull-request synchronisation for the GitHub Teams connector.

PR *listing* piggybacks on ``IssuesSync.fetch_issues_batched`` (GitHub's
``/issues`` endpoint returns PR stubs too) — this module only owns:
- ``process_pull_request_stub``: map an issue-shaped PR stub to a ``PullRequestRecord``.
- ``build_pull_request_blocks``: stream/index full PR content (description,
  commits, file diffs, review comments) as a ``BlocksContainer``.
- Reindex-check helper mirroring ``IssuesSync``'s.
"""

from __future__ import annotations

import uuid
from datetime import timezone
from typing import TYPE_CHECKING, Any

from github.Repository import Repository  # type: ignore

from app.config.constants.arangodb import MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import IndexingFilterKey
from app.models.blocks import BlockGroup, BlocksContainer, DataFormat, GroupSubType, GroupType
from app.models.entities import PullRequestRecord, Record, RecordGroupType, RecordType

from .models import GitHubLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


class PullRequestsSync:
    """Handles pull-request synchronisation for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # PR stub -> PullRequestRecord (invoked from IssuesSync's shared batch)
    # ------------------------------------------------------------------

    async def process_pull_request_stub(self, repo: Repository, issue: Any) -> RecordUpdate | None:
        """Map an issues-endpoint PR stub to a PullRequestRecord.

        The issues endpoint's PR stub lacks merge state/head/base refs, so a
        second ``get_pull`` call fills those in — acceptable since this only
        runs once per changed PR (the outer fetch is already ``since``-filtered).
        """
        c = self.c
        owner, repo_name = repo.owner.login, repo.name
        try:
            pr_res = await c.runtime.ds_call(c.data_source.get_pull, owner, repo_name, issue.number)
            if not pr_res.success or not pr_res.data:
                self.logger.error(
                    "Failed to fetch full PR #%s for %s: %s", issue.number, repo.full_name, pr_res.error
                )
                return None
            pr = pr_res.data

            external_id = f"{repo.id}/pull/{pr.number}"
            async with c.data_store_provider.transaction() as tx_store:
                existing_record = await tx_store.get_record_by_external_id(
                    connector_id=c.connector_id, external_id=external_id,
                )
            is_new = existing_record is None
            metadata_changed = bool(existing_record and existing_record.record_name != pr.title)
            is_updated = existing_record is not None

            label_names: list[str] = [getattr(label, "name", str(label)) for label in (pr.labels or [])]
            assignee_logins: list[str] = [
                getattr(a, "login", None) for a in (pr.assignees or []) if getattr(a, "login", None)
            ]
            status = "merged" if getattr(pr, "merged", False) else pr.state

            pr_record = PullRequestRecord(
                id=existing_record.id if existing_record else str(uuid.uuid4()),
                record_name=pr.title,
                external_record_id=external_id,
                record_type=RecordType.PULL_REQUEST.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=self._datetime_to_epoch_ms(pr.updated_at),
                source_created_at=self._datetime_to_epoch_ms(pr.created_at),
                version=0,
                external_record_group_id=f"{repo.id}-pull-requests",
                org_id=c.data_entities_processor.org_id,
                record_group_type=RecordGroupType.PROJECT.value,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=pr.html_url,
                status=status,
                mergeable=str(pr.mergeable) if pr.mergeable is not None else None,
                merged_by=getattr(pr.merged_by, "login", None) if getattr(pr, "merged_by", None) else None,
                external_revision_id=str(self._datetime_to_epoch_ms(pr.updated_at)),
                preview_renderable=False,
                labels=label_names,
                assignee=assignee_logins,
                last_commit_sha=getattr(pr.head, "sha", None) if getattr(pr, "head", None) else None,
                inherit_permissions=True,
            )
            if not self._prs_indexing_enabled():
                pr_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            return RecordUpdate(
                record=pr_record,
                is_new=is_new, is_updated=is_updated, is_deleted=False,
                metadata_changed=metadata_changed, content_changed=True, permissions_changed=False,
                old_permissions=[], new_permissions=[], external_record_id=external_id,
            )
        except Exception as e:
            self.logger.error(
                "Error processing PR #%s for %s: %s", getattr(issue, "number", "?"), repo.full_name, e, exc_info=True
            )
            return None

    @staticmethod
    def _datetime_to_epoch_ms(dt: Any) -> int:
        from app.utils.time_conversion import get_epoch_timestamp_in_ms
        if dt is None:
            return get_epoch_timestamp_in_ms()
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return round(dt.timestamp() * 1000)

    # ------------------------------------------------------------------
    # Content streaming (block building)
    # ------------------------------------------------------------------

    async def build_pull_request_blocks(self, record: Record) -> bytes:
        """Build BlocksContainer JSON bytes for a PR record: description, commits,
        file diffs (with review comment threads attached), and conversation comments."""
        c = self.c
        external_group_id: str = getattr(record, "external_record_group_id", None) or ""
        if not external_group_id:
            raise Exception("Repository id not found on pull request record.")
        repo_id = int(external_group_id.split("-")[0])
        pr_number = int(str(record.external_record_id).rsplit("/", 1)[-1])

        repo_res = await c.runtime.ds_call(c.data_source.get_repo_by_id, repo_id)
        if not repo_res.success or not repo_res.data:
            raise Exception(f"Failed to resolve repo id={repo_id} for record {record.external_record_id}: {repo_res.error}")
        owner, repo_name = repo_res.data.owner.login, repo_res.data.name

        pr_res = await c.runtime.ds_call(c.data_source.get_pull, owner, repo_name, pr_number)
        if not pr_res.success or not pr_res.data:
            raise Exception(f"Failed to fetch PR for record {record.external_record_id}: {pr_res.error}")
        pull_request = pr_res.data

        markdown_raw: str = getattr(pull_request, "body", "") or ""
        body_with_images = await c.comments.embed_images_as_base64(markdown_raw)
        child_records, remaining = await c.comments.make_child_records_of_attachments(markdown_raw, record)

        bg_0 = BlockGroup(
            index=0,
            name=record.record_name,
            type=GroupType.TEXT_SECTION.value,
            format=DataFormat.MARKDOWN.value,
            sub_type=GroupSubType.CONTENT.value,
            source_group_id=record.weburl,
            data=f"{pull_request.title}\n\n{body_with_images}",
            source_modified_date=getattr(pull_request, "updated_at", None),
            requires_processing=True,
            children_records=child_records,
        )
        block_groups: list[BlockGroup] = [bg_0]
        next_index = 1

        commit_blocks, commits_bg = await c.comments.build_pr_commit_blocks(
            owner, repo_name, pr_number, parent_index=next_index,
        )
        blocks = []
        if commits_bg is not None:
            block_groups.append(commits_bg)
            blocks.extend(commit_blocks)
            next_index += 1

        if self._comments_indexing_enabled():
            # parent_index=0 keeps these groups' declared parent as bg_0 (the
            # description) regardless of whether a commits section preceded
            # them; start_index is the actual next free slot so their indices
            # never collide with the commits BlockGroup allocated above.
            comment_bgs, diff_blocks, comment_remaining = await c.comments.build_pr_comment_and_diff_blocks(
                owner, repo_name, pr_number, pull_request, parent_index=0, record=record,
                start_index=next_index,
            )
            block_groups.extend(comment_bgs)
            blocks.extend(diff_blocks)
            remaining.extend(comment_remaining)

        await self._process_remaining_file_records(remaining)
        blocks_container = BlocksContainer(blocks=blocks, block_groups=block_groups)
        return blocks_container.model_dump_json(indent=2).encode(GitHubLiterals.UTF_8.value)

    async def _process_remaining_file_records(self, remaining: list[RecordUpdate]) -> None:
        c = self.c
        if not remaining:
            return
        for i in range(0, len(remaining), c.batch_size):
            batch = remaining[i : i + c.batch_size]
            try:
                await c.data_entities_processor.on_new_records([(ru.record, ru.new_permissions) for ru in batch])
            except Exception as e:
                self.logger.error("Error persisting PR attachment file records: %s", e, exc_info=True)

    # ------------------------------------------------------------------
    # Reindex helpers
    # ------------------------------------------------------------------

    async def check_and_fetch_updated_pr_for_reindex(self, record: Record) -> tuple[Record, list[Any]] | None:
        """Fetch a PULL_REQUEST from GitHub; return updated data if source revision changed."""
        c = self.c
        external_group_id = getattr(record, "external_record_group_id", None) or ""
        if not external_group_id:
            self.logger.warning("Cannot reindex-check GitHub PR %s: missing external_record_group_id", record.id)
            return None
        try:
            repo_id = int(external_group_id.split("-")[0])
            number = int(str(record.external_record_id).rsplit("/", 1)[-1])
        except (ValueError, IndexError):
            self.logger.warning("Cannot reindex-check GitHub PR %s: malformed external ids", record.id)
            return None

        repo_res = await c.runtime.ds_call(c.data_source.get_repo_by_id, repo_id)
        if not repo_res.success or not repo_res.data:
            self.logger.error("Failed to resolve repo id=%s for reindex %s: %s", repo_id, record.id, repo_res.error)
            return None
        repo = repo_res.data
        pr_res = await c.runtime.ds_call(c.data_source.get_pull, repo.owner.login, repo.name, number)
        if not pr_res.success or not pr_res.data:
            self.logger.error("Failed to fetch GitHub PR for reindex %s: %s", record.id, pr_res.error)
            return None
        pr = pr_res.data
        new_rev = str(self._datetime_to_epoch_ms(pr.updated_at))
        if getattr(record, "external_revision_id", None) == new_rev:
            return None

        class _IssueStub:
            number = pr.number

        ru = await self.process_pull_request_stub(repo, _IssueStub())
        if not ru:
            return None
        return (ru.record, ru.new_permissions)

    # ------------------------------------------------------------------
    # Indexing flags
    # ------------------------------------------------------------------

    def _prs_indexing_enabled(self) -> bool:
        c = self.c
        if not c.indexing_filters:
            return True
        return c.indexing_filters.is_enabled(IndexingFilterKey.MERGE_REQUESTS)

    def _comments_indexing_enabled(self) -> bool:
        c = self.c
        if not c.indexing_filters:
            return True
        return c.indexing_filters.is_enabled(IndexingFilterKey.COMMENTS)
