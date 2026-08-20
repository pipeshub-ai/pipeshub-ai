"""
Issue synchronisation for the GitHub Teams connector.

GitHub's ``/issues`` endpoint returns pull requests too, but those stubs lack
head refs and reviewers — recovering them cost a ``get_pull`` per PR, so PR
items are skipped here and ``PullRequestsSync`` pages ``/pulls`` instead.

Responsibilities:
- ``fetch_issues_batched``: fetch + batch-process a repo's issues.
- ``_process_issue_to_ticket``: map a single GitHub issue to a ``TicketRecord``.
- ``process_new_records``: persist a batch and advance the issues/PRs checkpoint.
- ``build_ticket_blocks``: stream/index ticket content as ``BlocksContainer``.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from app.sources.external.github.github_async import GhObject

from app.config.constants.arangodb import (
    Connectors,
    MimeTypes,
    OriginTypes,
    ProgressStatus,
    RecordRelations,
)
from app.connectors.core.base.sync_point.sync_point import generate_record_sync_point_key
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
    ItemType,
    Priority,
    Record,
    RecordGroupType,
    RecordType,
    RelatedExternalRecord,
    Status,
    TicketRecord,
)
from app.connectors.utils.value_mapper import map_priority, map_type

from .common.utils import epoch_ms_or_now, listing_payload
from .constants import ISSUE_PAGE_SIZE
from .models import GitHubLiterals, RecordUpdate

if TYPE_CHECKING:
    from app.connectors.sources.github_teams.connector import GitHubTeamsConnector


def _item_type_from_issue_type(issue_type: Any) -> str | None:
    """GitHub issue type -> ``ItemType`` via the shared ``ValueMapper``.

    Issue types are org-defined and only the built-in three (Bug/Task/Feature)
    are guaranteed; the mapper preserves an unrecognised custom type verbatim
    rather than dropping it.
    """
    if isinstance(issue_type, dict):
        name = issue_type.get("name")
    else:
        name = getattr(issue_type, "name", None)
    if not name:
        return None
    mapped = map_type(str(name))
    return mapped.value if isinstance(mapped, ItemType) else mapped


# https://api.github.com/repos/{owner}/{repo}/issues/{number}
_PARENT_ISSUE_URL_RE = re.compile(r"^https://[^/]+/repos/([^/]+)/([^/]+)/issues/(\d+)$")



def _priority_from_issue_field_values(field_values: Any) -> str | None:
    """Structured Priority from the ``issue_field_values`` inlined on the
    listing payload, or ``None``.

    GitHub inlines org-level issue-field values on the issues listing as an
    additive field (verified against the live API: present with no version
    header, so not version-gated) — zero extra calls, unlike the per-issue
    ``issue-field-values`` endpoint. Absent entirely on orgs without issue
    fields, which resolves to ``None`` and the label fallback.
    """
    if not isinstance(field_values, list):
        return None
    for field_value in field_values:
        if not isinstance(field_value, dict):
            continue
        if (field_value.get("issue_field_name") or "").strip().lower() != "priority":
            continue
        option = field_value.get("single_select_option") or {}
        name = option.get("name") or (
            field_value.get("value") if isinstance(field_value.get("value"), str) else None
        )
        if name:
            # Shared ValueMapper conventions (High -> HIGH, Urgent -> HIGHEST,
            # matching Jira/Linear/Zammad); an unrecognised custom option is
            # preserved verbatim, mirroring the issue-type policy.
            mapped = map_priority(str(name))
            return mapped.value if isinstance(mapped, Priority) else mapped
    return None


def _status_from_issue(issue: Any) -> str:
    """GitHub state (+ ``state_reason``) -> ``Status``.

    GitHub only has open/closed; ``state_reason`` is what distinguishes work
    that was finished from work that was abandoned, and collapsing both to
    "closed" loses that.
    """
    state = (getattr(issue, "state", None) or "").lower()
    if state != "closed":
        return Status.REOPENED.value if state == "reopened" else Status.OPEN.value
    reason = (getattr(issue, "state_reason", None) or "").lower()
    if reason == "not_planned":
        return Status.CANCELLED.value
    if reason == "duplicate":
        return Status.CANCELLED.value
    return Status.DONE.value


class IssuesSync:
    """Handles issue (ticket) synchronisation for ``GitHubTeamsConnector``."""

    def __init__(self, connector: "GitHubTeamsConnector") -> None:
        self.c = connector
        self.logger = connector.logger
        # owner/name -> repo id (or None for a failed lookup, cached so one
        # unreachable parent repo costs one call per sync, not one per sub-issue).
        self._parent_repo_ids: dict[str, int | None] = {}
        self._app_user_emails: dict[str, str] | None = None

    async def get_app_user_emails(self) -> dict[str, str]:
        """AppUser source id (numeric GitHub id) -> email, for bound users only.

        GitHub never exposes other users' emails, but user sync already bound
        some principals to PipesHub identities — one DB read per repo recovers
        those emails for ticket/PR people fields. Reset per repo in
        ``fetch_issues_batched`` so users bound mid-run are picked up.
        """
        if self._app_user_emails is None:
            try:
                users = await self.c.data_entities_processor.get_all_app_users(self.c.connector_id)
            except Exception as e:
                self.logger.warning("Could not load AppUsers for email resolution: %s", e)
                users = []
            self._app_user_emails = {
                u.source_user_id: u.email for u in (users or []) if u.source_user_id and u.email
            }
        return self._app_user_emails

    # ------------------------------------------------------------------
    # Sync entry point
    # ------------------------------------------------------------------

    async def fetch_issues_batched(self, repo: GhObject) -> None:
        """Sync a repo's issues, one page at a time.

        Each page of 100 is fetched, mapped and persisted before the next is
        requested, so records start landing after the first page instead of
        after the last, and memory never holds more than a page. The page IS
        the batch — one build, one persist call, one transaction.

        On a page failure the checkpoint is left untouched and the sweep stops:
        the listing is sorted ``updated asc``, so advancing past a failure
        would skip those issues forever.
        """
        c = self.c
        owner, repo_name = repo.owner.login, repo.name
        self._app_user_emails = None  # refresh per repo; user sync may have bound new users

        # Only the issues watermark: PRs page their own listing against their
        # own checkpoint, so taking the min of both would re-fetch issues from
        # further back than necessary whenever the PR side lagged.
        last_sync_time = await self._get_sync_checkpoint(f"{repo.id}-work-items")
        since_dt = (
            datetime.fromtimestamp(last_sync_time / 1000, tz=timezone.utc)
            if last_sync_time is not None else None
        )

        page = 1
        processed = 0
        watermarks: dict[str, int] = {}
        while True:
            issues_res = await c.runtime.ds_call(
                c.data_source.list_issues, owner, repo_name,
                state="all", since=since_dt, sort="updated", direction="asc",
                per_page=ISSUE_PAGE_SIZE, page=page,
            )
            if not issues_res.success:
                self.logger.error(
                    "Error fetching issues for %s (page %s): %s; not advancing the checkpoint "
                    "so this page is retried next sync.", repo.full_name, page, issues_res.error,
                )
                return
            items = issues_res.data or []
            if not items:
                break

            # The page IS the batch: one build, one persist call, one
            # transaction. Re-chunking by a constant only split it into a
            # full batch plus an under-filled one.
            record_updates = await self._build_issue_records(repo, items)
            if not await self.process_new_records(record_updates, watermarks):
                self.logger.warning(
                    "Issue batch failed for %s (page %s); stopping here so the checkpoint "
                    "stays behind the failure instead of skipping past it.",
                    repo.full_name, page,
                )
                return
            processed += len(items)

            if len(items) < ISSUE_PAGE_SIZE:
                break
            page += 1

        if not processed:
            self.logger.debug("No issues found for %s", repo.full_name)
            return
        self.logger.info(
            "Synced %s issue(s) for %s across %s page(s)", processed, repo.full_name, page,
        )
        for group_id, last_sync_time in watermarks.items():
            await self._update_sync_checkpoint(group_id, last_sync_time)

    # ------------------------------------------------------------------
    # Record building (split issues vs PRs)
    # ------------------------------------------------------------------

    async def _build_issue_records(self, repo: GhObject, issue_batch: list[Any]) -> list[RecordUpdate]:
        c = self.c
        record_updates: list[RecordUpdate] = []
        issues_enabled = self._issues_indexing_enabled()

        for issue in issue_batch:
            # `html_url` is always on the listing payload; the `pull_request`
            # key only appears on PR stubs, so its absence is not proof.
            if "/pull/" in (getattr(issue, "html_url", "") or ""):
                # PRs come from their own listing, which carries head refs and
                # reviewers this stub lacks. Recovering those here cost one
                # get_pull per PR — the single largest expense in a sync.
                continue
            record_update = await self._process_issue_to_ticket(repo, issue)
            if record_update and not issues_enabled:
                record_update.record.indexing_status = ProgressStatus.AUTO_INDEX_OFF.value

            if not record_update:
                continue
            record_updates.append(record_update)

            markdown_raw: str = getattr(issue, "body", "") or ""
            _, attachments = await c.comments.clean_github_content(markdown_raw)
            if attachments:
                # Attachment records inherit the issues indexing filter inside
                # _attachment_file_update — the single construction point that
                # also covers the stream-time comment-attachment path.
                file_updates = await c.comments.make_file_records_from_list(attachments, record_update.record)
                record_updates.extend(file_updates)

        return record_updates

    # ------------------------------------------------------------------
    # Issue -> TicketRecord
    # ------------------------------------------------------------------

    async def _process_issue_to_ticket(self, repo: GhObject, issue: Any) -> RecordUpdate | None:
        """Map a single GitHub issue to a TicketRecord RecordUpdate.

        Deliberately does NOT look up the existing record. ``_process_record``
        already queries the same ``(connector_id, external_record_id)`` key and
        then overwrites ``record.id`` with what it finds, derives the version
        from ``external_revision_id``, and carries weburl/indexing status
        forward — so a lookup here is the same query run twice. It cost one
        Neo4j transaction per issue (~0.68s each, ~33 min on a 3k-item repo)
        to produce an id the processor discards and RecordUpdate flags nothing
        reads.
        """
        c = self.c
        external_id = f"{repo.id}/issues/{issue.number}"
        try:
            label_names: list[str] = [getattr(label, "name", str(label)) for label in (issue.labels or [])]
            assignees = [a for a in (issue.assignees or []) if getattr(a, "login", None)]
            # Numeric ids, not logins: AppUser.source_user_id is the numeric
            # GitHub id, and these fields exist to join against it.
            assignee_ids: list[str] = [
                str(a.id) for a in assignees if getattr(a, "id", None) is not None
            ]
            creator = getattr(issue, "user", None)
            creator_login = getattr(creator, "login", None) if creator else None
            creator_id = (
                str(creator.id) if creator and getattr(creator, "id", None) is not None else None
            )
            emails = await self.get_app_user_emails()
            creator_email = emails.get(creator_id) if creator_id else None
            # GitHub allows up to 10 assignees, but TicketRecord.assignee_email is
            # single-valued and the processor feeds it straight to a user lookup to
            # build the ASSIGNED_TO edge — a joined string matches no user, so
            # co-assigned tickets would get NO edge at all. Carry GitHub's own
            # primary (first) assignee in the paired name/email fields and the
            # complete set in assignee_source_id, which is a list for this reason.
            # Name and email must describe the SAME person, so the email is the
            # primary's or nothing — never the first co-assignee that happens to
            # have resolved.
            primary_assignee = assignees[0] if assignees else None
            primary_assignee_email = (
                emails.get(str(primary_assignee.id))
                if primary_assignee is not None and getattr(primary_assignee, "id", None) is not None
                else None
            )
            # Read type and sub-issue parentage off the raw listing payload —
            # neither field is surfaced as an attribute worth modelling.
            raw_payload: dict[str, Any] = listing_payload(issue)
            issue_type = _item_type_from_issue_type(raw_payload.get("type"))
            parent_external_id = await self._parent_ticket_external_id(
                repo, raw_payload.get("parent_issue_url")
            )
            if parent_external_id and not issue_type:
                issue_type = ItemType.SUBTASK.value
            related_records = await self._related_from_dependencies(
                repo, issue.number, raw_payload.get("issue_dependencies_summary")
            )

            ticket_record = TicketRecord(
                id=str(uuid.uuid4()),
                record_name=issue.title,
                external_record_id=external_id,
                record_type=RecordType.TICKET.value,
                connector_name=c.connector_name,
                connector_id=c.connector_id,
                origin=OriginTypes.CONNECTOR.value,
                source_updated_at=epoch_ms_or_now(issue.updated_at),
                source_created_at=epoch_ms_or_now(issue.created_at),
                version=0,
                external_record_group_id=f"{repo.id}-work-items",
                org_id=c.data_entities_processor.org_id,
                record_group_type=RecordGroupType.PROJECT.value,
                mime_type=MimeTypes.BLOCKS.value,
                weburl=issue.html_url,
                status=_status_from_issue(issue),
                external_revision_id=str(epoch_ms_or_now(issue.updated_at)),
                preview_renderable=False,
                type=issue_type,
                priority=_priority_from_issue_field_values(raw_payload.get("issue_field_values")),
                parent_external_record_id=parent_external_id,
                parent_record_type=RecordType.TICKET.value if parent_external_id else None,
                related_external_records=related_records,
                labels=label_names,
                assignee=(primary_assignee.login if primary_assignee is not None else None),
                assignee_source_id=assignee_ids,
                assignee_email=primary_assignee_email,
                creator_name=creator_login,
                creator_email=creator_email,
                reporter_name=creator_login,
                reporter_email=creator_email,
                reporter_source_id=creator_id,
                creator_source_timestamp=epoch_ms_or_now(issue.created_at),
                is_email_hidden=True,
                inherit_permissions=True,
            )
            return RecordUpdate(
                record=ticket_record,
                is_new=True, is_updated=False, is_deleted=False,
                metadata_changed=False, content_changed=False, permissions_changed=False,
                old_permissions=[], new_permissions=[], external_record_id=external_id,
            )
        except Exception as e:
            self.logger.error("Error processing issue #%s to ticket: %s", getattr(issue, "number", "?"), e, exc_info=True)
            return None

    async def _related_from_dependencies(
        self, repo: GhObject, issue_number: int, summary: Any
    ) -> list[RelatedExternalRecord]:
        """Blocked-by / blocking links as ``RelatedExternalRecord``s.

        Only the ``blocking`` side is read. GitHub stores one dependency but
        reports it from both ends (the blocker's ``blocking`` and the blocked
        issue's ``blocked_by``), so fetching both would write two inverse edges
        for a single user-visible link — and cost twice the calls. The blocker's
        side alone covers every link whose blocker is in sync scope.

        Gated for free by ``issue_dependencies_summary`` on the listing payload:
        an issue that blocks nothing (the overwhelming majority) costs zero calls.
        Each returned item embeds ``repository.id``, so cross-repo targets map
        to ``{repo_id}/issues/{n}`` with no extra lookup. Errors degrade to no
        links — never fail the ticket.
        """
        if not isinstance(summary, dict) or not summary.get("blocking"):
            return []
        try:
            res = await self.c.runtime.ds_call(
                self.c.data_source.list_issue_blocking, repo.owner.login, repo.name, issue_number
            )
        except Exception as e:
            self.logger.warning(
                "Could not list blocking dependencies for %s#%s: %s",
                repo.full_name, issue_number, e,
            )
            return []
        if res.success is not True:
            self.logger.warning(
                "Could not list blocking dependencies for %s#%s: %s",
                repo.full_name, issue_number, getattr(res, "error", "unknown"),
            )
            return []
        related: list[RelatedExternalRecord] = []
        for item in res.data or []:
            if not isinstance(item, dict) or not item.get("number"):
                continue
            target_repo_id = (item.get("repository") or {}).get("id") or repo.id
            related.append(RelatedExternalRecord(
                external_record_id=f"{target_repo_id}/issues/{item['number']}",
                record_type=RecordType.TICKET,
                record_name=item.get("title"),
                relation_type=RecordRelations.BLOCKS,
            ))
        return related

    async def _parent_ticket_external_id(
        self, repo: GhObject, parent_issue_url: str | None
    ) -> str | None:
        """Sub-issue parent URL -> the parent ticket's external id, or ``None``.

        The payload gives an API URL keyed by owner/name; ticket external ids
        are keyed by numeric repo id so they survive renames. Same-repo parents
        (the common case) convert for free; a cross-repo parent needs one
        ``get_repo`` lookup per distinct repo per sync, cached including
        failures so an unreachable repo cannot turn into a per-issue call.
        """
        if not parent_issue_url:
            return None
        match = _PARENT_ISSUE_URL_RE.match(parent_issue_url)
        if not match:
            self.logger.debug("Unrecognised parent_issue_url shape: %s", parent_issue_url)
            return None
        owner, name, number = match.group(1), match.group(2), int(match.group(3))
        if f"{owner}/{name}".lower() == (getattr(repo, "full_name", "") or "").lower():
            return f"{repo.id}/issues/{number}"

        c = self.c
        key = f"{owner}/{name}".lower()
        if key not in self._parent_repo_ids:
            res = await c.runtime.ds_call(c.data_source.get_repo, owner, name)
            self._parent_repo_ids[key] = (
                int(res.data.id) if res.success and res.data else None
            )
            if self._parent_repo_ids[key] is None:
                self.logger.warning(
                    "Could not resolve repo %s for cross-repo sub-issue parents: %s",
                    key, getattr(res, "error", "unknown"),
                )
        parent_repo_id = self._parent_repo_ids[key]
        return f"{parent_repo_id}/issues/{number}" if parent_repo_id is not None else None

    # ------------------------------------------------------------------
    # Record persistence + checkpoint advancement
    # ------------------------------------------------------------------

    async def process_new_records(
        self, batch_records: list[RecordUpdate], watermarks: dict[str, int] | None = None,
    ) -> bool:
        """Persist one page's records in a single call, returning success.

        Deliberately does NOT re-chunk. ``on_new_records`` opens one
        transaction and loops the list inside it, so the caller's page already
        defines the unit; splitting it again by a constant produced a full
        batch plus an under-filled one — two transactions where one would do.
        The list mixes tickets/PRs with their attachment FileRecords, which
        ``_process_record`` dispatches on by type.

        When ``watermarks`` is supplied the caller owns checkpoint
        advancement: the highest ``source_updated_at`` per record group is
        accumulated there and committed once the sweep completes. Advancing
        per page is unsafe because the listing is sorted ``updated asc``, so a
        later page's timestamp would commit *past* an earlier failure and
        those items would never be re-fetched.
        """
        c = self.c
        if not batch_records:
            return True
        batch_sent = [(ru.record, ru.new_permissions) for ru in batch_records]
        try:
            await c.data_entities_processor.on_new_records(batch_sent)
        except Exception as e:
            self.logger.error("Error processing batch of GitHub issue/PR records: %s", e, exc_info=True)
            return False

        for record_update in batch_records:
            if record_update.record.record_type not in (RecordType.TICKET.value, RecordType.PULL_REQUEST.value):
                continue
            last_sync_time = record_update.record.source_updated_at
            group_id = record_update.record.external_record_group_id
            if not (group_id and last_sync_time):
                continue
            if watermarks is None:
                await self._update_sync_checkpoint(group_id, last_sync_time)
            else:
                watermarks[group_id] = max(watermarks.get(group_id, 0), last_sync_time)
        return True

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

        comment_bgs, comment_remaining = await c.comments.build_issue_comment_blocks(
            owner, repo_name, issue_number, parent_index=0, record=record,
        )
        block_groups.extend(comment_bgs)
        remaining.extend(comment_remaining)

        await self.process_new_records(remaining)
        wire_block_group_parent_children(block_groups)
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
        new_rev = str(epoch_ms_or_now(issue.updated_at))
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
