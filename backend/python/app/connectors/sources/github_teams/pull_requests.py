"""
Pull-request synchronisation for the GitHub Teams connector.

Owns:
- ``fetch_prs_batched``: page the ``/pulls`` listing newest-updated-first,
  stopping at the checkpoint. Each page is one API call and one batch.
- ``process_pull_request``: map a listing entry to a ``PullRequestRecord``.
- ``build_pull_request_blocks``: stream/index full PR content (description,
  commits, file diffs, review comments) as a ``BlocksContainer``.
- Reindex-check helper mirroring ``IssuesSync``'s.

PRs are NOT taken from the shared ``/issues`` listing: those stubs lack head
refs and reviewers, so each one cost a ``get_pull`` — the single largest expense
in a sync of a busy repo.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.sources.external.github.github_async import GhObject

from app.config.constants.arangodb import MimeTypes, OriginTypes, ProgressStatus
from app.connectors.core.registry.filters import IndexingFilterKey
from app.models.blocks import (
    BlockGroup,
    BlocksContainer,
    DataFormat,
    GroupSubType,
    GroupType,
    wire_block_group_parent_children,
)
from app.models.entities import (
    PullRequestRecord,
    Record,
    RecordGroupType,
    RecordType,
    Status,
)

from .common.utils import epoch_ms_or_now, listing_payload
from .constants import PR_PAGE_SIZE
from .models import GitHubLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


def _status_from_pr(pr: Any) -> str:
    """GitHub PR state -> ``Status``, matching the ticket mapping.

    Tickets store normalised ``Status`` values, so PRs must too — one connector
    emitting ``'OPEN'`` for issues and ``'open'``/``'merged'`` for PRs breaks
    anything that filters on status. Merged -> DONE and closed-unmerged ->
    CANCELLED keeps the distinction the raw ``'merged'`` marker carried.

    Merge state comes from ``merged_at``, never ``merged``: the LIST payload
    omits ``merged``, while ``merged_at`` is on both list and single-PR payloads.
    """
    if getattr(pr, "merged_at", None) is not None:
        return Status.DONE.value
    state = (getattr(pr, "state", None) or "").lower()
    if state == "closed":
        return Status.CANCELLED.value
    return Status.OPEN.value


class PullRequestsSync:
    """Handles pull-request synchronisation for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger

    # ------------------------------------------------------------------
    # PR stub -> PullRequestRecord (invoked from IssuesSync's shared batch)
    # ------------------------------------------------------------------

    async def fetch_prs_batched(self, repo: GhObject) -> None:
        """Sync a repo's pull requests from the PR listing, one page at a time.

        PRs used to ride the shared ``/issues`` listing and then pay one
        ``get_pull`` each to recover head refs and reviewers — 2,950 sequential
        calls on a repo this size, ~90 minutes, most of an hourly rate budget.
        The list endpoint carries every field we store except ``mergeable`` and
        ``merged_by``, so paging it costs one call per 100 PRs instead.

        ``/pulls`` has no ``since`` parameter, hence ``sort=updated`` +
        ``direction=desc`` with an early break at the checkpoint: the first PR
        older than the watermark means every later one is too.

        The fetched page IS the batch — no re-chunking. The checkpoint is
        committed once after the sweep; any page or batch failure returns
        before that write so already-done pages are retried next sync.
        """
        c = self.c
        owner, repo_name = repo.owner.login, repo.name
        group_id = f"{repo.id}-pull-requests"
        since_dt = await self._checkpoint_datetime(group_id)

        page = 1
        watermark = 0
        processed = 0
        while True:
            res = await c.runtime.ds_call(
                c.data_source.list_pulls, owner, repo_name,
                state="all", sort="updated", direction="desc",
                per_page=PR_PAGE_SIZE, page=page,
            )
            if not res.success:
                self.logger.error(
                    "Error fetching pull requests for %s (page %s): %s; not advancing the "
                    "checkpoint so this page is retried next sync.",
                    repo.full_name, page, res.error,
                )
                return
            prs = res.data or []
            if not prs:
                break

            fresh = [p for p in prs if self._is_after(p, since_dt)]
            if fresh:
                record_updates = await self._build_pr_records(repo, fresh)
                if not await c.issues.process_new_records(record_updates, {}):
                    self.logger.warning(
                        "Pull-request batch failed for %s (page %s); stopping here so the "
                        "checkpoint stays behind the failure rather than skipping past it.",
                        repo.full_name, page,
                    )
                    return
                processed += len(fresh)
                watermark = max(watermark, *(epoch_ms_or_now(p.updated_at) for p in fresh))

            # Two independent stop conditions: a filtered-out PR means we
            # crossed the checkpoint (descending order, so everything after is
            # older too), and a short page means the listing is exhausted.
            if len(fresh) < len(prs) or len(prs) < PR_PAGE_SIZE:
                break
            page += 1

        if processed:
            self.logger.info(
                "Synced %s pull request(s) for %s across %s page(s)", processed, repo.full_name, page,
            )
            await c.issues._update_sync_checkpoint(group_id, watermark)

    def _is_after(self, pr: Any, since_dt: datetime | None) -> bool:
        if since_dt is None:
            return True
        updated_at = getattr(pr, "updated_at", None)
        if updated_at is None:
            return True
        if updated_at.tzinfo is None:
            updated_at = updated_at.replace(tzinfo=timezone.utc)
        return updated_at > since_dt

    async def _checkpoint_datetime(self, group_id: str) -> datetime | None:
        last_ms = await self.c.issues._get_sync_checkpoint(group_id)
        if last_ms is None:
            return None
        return datetime.fromtimestamp(last_ms / 1000, tz=timezone.utc)

    async def _build_pr_records(self, repo: GhObject, prs: list[Any]) -> list[RecordUpdate]:
        c = self.c
        record_updates: list[RecordUpdate] = []
        for pr in prs:
            record_update = await self.process_pull_request(repo, pr)
            if not record_update:
                continue
            record_updates.append(record_update)

            markdown_raw: str = getattr(pr, "body", "") or ""
            _, attachments = await c.comments.clean_github_content(markdown_raw)
            if attachments:
                # Attachment records inherit the PRs indexing filter inside
                # _attachment_file_update — the single construction point that
                # also covers the stream-time comment-attachment path.
                file_updates = await c.comments.make_file_records_from_list(
                    attachments, record_update.record
                )
                record_updates.extend(file_updates)
        return record_updates

    async def process_pull_request(self, repo: GhObject, pr: Any) -> RecordUpdate | None:
        """Map a ``PullRequest`` to a ``PullRequestRecord``.

        No existing-record lookup here — see the note in
        ``IssuesSync._process_issue_to_ticket``: ``_process_record`` runs the
        same query and overwrites ``record.id`` with the result.
        """
        c = self.c
        try:
            external_id = f"{repo.id}/pull/{pr.number}"
            # Merge state lives only on the single-PR payload, so a PR from the
            # listing leaves these empty rather than paying a GET each to fill
            # them; the reindex path fetches the full PR and does get them.
            raw: dict[str, Any] = listing_payload(pr)
            mergeable_raw = raw.get("mergeable")
            merged_by_raw = raw.get("merged_by")

            label_names: list[str] = [getattr(label, "name", str(label)) for label in (pr.labels or [])]
            assignees = [a for a in (pr.assignees or []) if getattr(a, "login", None)]
            assignee_logins: list[str] = [a.login for a in assignees]
            # Every field below is on the `/pulls` listing payload, so none of
            # these reads can complete the object. GitHub exposes logins, not
            # emails — but user sync bound some principals to PipesHub
            # identities, and that map recovers their emails by numeric id.
            author = getattr(pr, "user", None)
            author_login = getattr(author, "login", None) if author else None
            author_id = (
                str(author.id) if author and getattr(author, "id", None) is not None else None
            )
            reviewers = [
                r for r in (getattr(pr, "requested_reviewers", None) or [])
                if getattr(r, "login", None)
            ]
            reviewer_logins: list[str] = [r.login for r in reviewers]
            emails = await c.issues.get_app_user_emails()
            assignee_emails = [
                emails[str(a.id)] for a in assignees
                if getattr(a, "id", None) is not None and str(a.id) in emails
            ]
            reviewer_emails = [
                emails[str(r.id)] for r in reviewers
                if getattr(r, "id", None) is not None and str(r.id) in emails
            ]
            status = _status_from_pr(pr)

            pr_record = PullRequestRecord(
                id=str(uuid.uuid4()),
                record_name=pr.title,
                external_record_id=external_id,
                record_type=RecordType.PULL_REQUEST.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=epoch_ms_or_now(pr.updated_at),
                source_created_at=epoch_ms_or_now(pr.created_at),
                version=0,
                external_record_group_id=f"{repo.id}-pull-requests",
                org_id=c.data_entities_processor.org_id,
                record_group_type=RecordGroupType.PROJECT.value,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=pr.html_url,
                status=status,
                mergeable=str(mergeable_raw) if mergeable_raw is not None else None,
                merged_by=merged_by_raw.get("login") if isinstance(merged_by_raw, dict) else None,
                external_revision_id=str(epoch_ms_or_now(pr.updated_at)),
                preview_renderable=False,
                labels=label_names,
                assignee=assignee_logins,
                assignee_email=assignee_emails,
                creator_name=author_login,
                creator_email=emails.get(author_id) if author_id else None,
                review_name=reviewer_logins,
                review_email=reviewer_emails,
                last_commit_sha=getattr(pr.head, "sha", None) if getattr(pr, "head", None) else None,
                inherit_permissions=True,
            )
            if not self._prs_indexing_enabled():
                pr_record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            return RecordUpdate(
                record=pr_record,
                is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False, permissions_changed=False,
                old_permissions=[], new_permissions=[], external_record_id=external_id,
            )
        except Exception as e:
            self.logger.error(
                "Error processing PR #%s for %s: %s", getattr(pr, "number", "?"), repo.full_name, e, exc_info=True
            )
            return None

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
            owner, repo_name, pr_number, index=next_index, parent_index=0,
        )
        blocks = []
        if commits_bg is not None:
            block_groups.append(commits_bg)
            blocks.extend(commit_blocks)
            next_index += 1

        # parent_index=0 keeps these under bg_0; start_index avoids colliding
        # with the commits BlockGroup allocated above.
        comment_bgs, comment_remaining = await c.comments.build_pr_comment_and_diff_blocks(
            owner, repo_name, pr_number, pull_request, parent_index=0, record=record,
            start_index=next_index,
        )
        block_groups.extend(comment_bgs)
        remaining.extend(comment_remaining)

        await c.issues.process_new_records(remaining)
        wire_block_group_parent_children(block_groups)
        blocks_container = BlocksContainer(blocks=blocks, block_groups=block_groups)
        return blocks_container.model_dump_json(indent=2).encode(GitHubLiterals.UTF_8.value)

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
        new_rev = str(epoch_ms_or_now(pr.updated_at))
        if getattr(record, "external_revision_id", None) == new_rev:
            return None

        ru = await self.process_pull_request(repo, pr)
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
