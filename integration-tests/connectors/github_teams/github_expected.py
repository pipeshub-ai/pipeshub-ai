# pyright: ignore-file

"""Build expected GitHub Teams graph entities (mirrors the ``github_teams`` mapping).

Every builder takes the **raw GitHub REST JSON** — the source of truth — and produces
the entity the connector should have written. That direction matters: the test then
validates source → graph, not graph → graph.

Where the connector delegates to a shared platform mapper (``map_priority``,
``map_type``) this reuses the same mapper, exactly as ``JiraExpected`` reuses
``ValueMapper``. Where the mapping is a GitHub-specific decision — open/closed plus
``state_reason`` → Status, ``merged_at`` → DONE — it is re-derived here rather than
imported, so a change to the connector's own rule shows up as a test failure instead
of silently agreeing with itself.

Fields the connector never sets are left at their model defaults, so they compare
equal against whatever the processor wrote (the same convention ``jira_expected``
follows).
"""

from __future__ import annotations

from typing import Any, Optional

from app.config.constants.arangodb import (
    Connectors,
    ExtensionTypes,
    MimeTypes,
    OriginTypes,
    PermissionModel,
    get_mime_type_for_extension,
)
from app.connectors.utils.value_mapper import map_priority, map_type
from app.models.entities import (
    AppMetadata,
    CodeFileRecord,
    FileRecord,
    ItemType,
    Priority,
    PullRequestRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    Status,
    TicketRecord,
)
from app.utils.time_conversion import datetime_to_epoch_ms

# The connector never sets these; the processor stamps them with wall-clock values at
# write time, so a test cannot know them ahead of time. Pass as ``skip_compare``.
PROCESSOR_ASSIGNED_FIELDS = frozenset({"created_at", "updated_at"})

# Same set the connector derives its preview flag from — the shared ExtensionTypes
# enum, not a copy of the connector's private constant.
_PREVIEW_RENDERABLE_EXTENSIONS = frozenset(ext.value for ext in ExtensionTypes)


def epoch_ms(value: Any) -> Optional[int]:
    """ISO-8601 → epoch ms, mirroring the connector's ``epoch_ms_or_now``.

    The connector falls back to *now* when the timestamp is missing; a fixture issue
    always has both timestamps, so a None here means the payload is malformed and the
    assertion should fail loudly rather than silently comparing against wall-clock.
    """
    if value is None:
        return None
    return datetime_to_epoch_ms(value)


class GitHubExpected:
    """Expected graph entities for the GitHub Teams connector.

    Add new entity builders here as ``@staticmethod`` methods.
    """

    # ------------------------------------------------------------------
    # App / record groups
    # ------------------------------------------------------------------

    @staticmethod
    def app_metadata_for_full_sync_baseline(state: dict[str, Any]) -> AppMetadata:
        """Expected apps document. ``permission_model`` is the builder default
        (RECORD_LEVEL): the connector never calls ``with_permission_model``."""
        return AppMetadata(
            connector_id=state["connector_id"],
            name=state["connector_name"],
            type="GitHub Teams",
            app_group="Github",
            scope="team",
            created_at_timestamp=0,
            updated_at_timestamp=0,
            permission_model=PermissionModel.RECORD_LEVEL.value,
            vector_membership_backfilled=True,
        )

    @staticmethod
    def org_record_group(*, org_login: str, org_id: int, connector_id: str) -> RecordGroup:
        """The org group — the ONLY group with no parent, and therefore the only one
        the platform links to the App node."""
        return RecordGroup(
            id="", org_id="",
            name=org_login,
            group_type=RecordGroupType.REPOSITORY.value,
            connector_name=Connectors.GITHUB_TEAMS,
            connector_id=connector_id,
            external_group_id=f"org-{org_id}",
            web_url=f"https://github.com/{org_login}",
        )

    @staticmethod
    def repo_record_group(repo: dict[str, Any], *, connector_id: str) -> RecordGroup:
        """The repo group: the single point where this repo's ACL lives.

        It deliberately does NOT inherit permissions — its parent (the org group)
        holds the union of every repo's grants, so inheriting would leak each repo
        to every other repo's users.
        """
        return RecordGroup(
            id="", org_id="",
            name=repo["full_name"],
            group_type=RecordGroupType.REPOSITORY.value,
            connector_name=Connectors.GITHUB_TEAMS,
            connector_id=connector_id,
            external_group_id=str(repo["id"]),
            parent_external_group_id=f"org-{repo['owner']['id']}",
            web_url=repo.get("html_url"),
        )

    @staticmethod
    def child_record_group(
        repo: dict[str, Any], *, kind: str, connector_id: str
    ) -> RecordGroup:
        """One of the three child groups. Each carries an EMPTY ACL and inherits from
        the repo group, so a record resolves in two hops.

        ``kind`` is one of ``work-items`` / ``pull-requests`` / ``code-repository``.
        """
        names = {
            "work-items": "Issues",
            "pull-requests": "Pull requests",
            "code-repository": "Code repository",
        }
        if kind not in names:
            raise ValueError(f"unknown child group kind {kind!r}")
        return RecordGroup(
            id="", org_id="",
            name=names[kind],
            group_type=RecordGroupType.PROJECT.value,
            connector_name=Connectors.GITHUB_TEAMS,
            connector_id=connector_id,
            external_group_id=f"{repo['id']}-{kind}",
            parent_external_group_id=str(repo["id"]),
            inherit_permissions=True,
        )

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    @staticmethod
    def ticket_record(
        issue: dict[str, Any],
        *,
        connector_id: str,
        repo_id: int,
        emails: Optional[dict[str, str]] = None,
        parent_external_record_id: Optional[str] = None,
    ) -> TicketRecord:
        """``TicketRecord`` from live issue JSON.

        ``emails`` maps GitHub numeric id (as str) → resolved PipesHub email, which is
        what the connector uses to fill creator/assignee emails; GitHub itself exposes
        logins, not addresses. An unresolved id yields None, never a borrowed address.
        """
        emails = emails or {}
        updated_ms = epoch_ms(issue.get("updated_at"))
        created_ms = epoch_ms(issue.get("created_at"))

        assignees = [a for a in (issue.get("assignees") or []) if a.get("login")]
        primary = assignees[0] if assignees else None
        # GitHub allows 10 assignees but TicketRecord.assignee_email is single-valued
        # and feeds a user lookup for the ASSIGNED_TO edge. Name and email must
        # describe the SAME person, so the email is the primary's or nothing.
        primary_email = emails.get(str(primary["id"])) if primary else None

        creator = issue.get("user") or {}
        creator_id = str(creator["id"]) if creator.get("id") is not None else None
        creator_login = creator.get("login")
        creator_email = emails.get(creator_id) if creator_id else None

        item_type = _item_type(issue.get("type"))
        if parent_external_record_id and not item_type:
            item_type = ItemType.SUBTASK.value

        return TicketRecord(
            id="", org_id="",
            record_name=issue.get("title"),
            record_type=RecordType.TICKET,
            external_record_id=f"{repo_id}/issues/{issue['number']}",
            external_revision_id=str(updated_ms) if updated_ms else None,
            external_record_group_id=f"{repo_id}-work-items",
            parent_external_record_id=parent_external_record_id,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GITHUB_TEAMS,
            connector_id=connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=issue.get("html_url"),
            source_created_at=created_ms,
            source_updated_at=updated_ms,
            inherit_permissions=True,
            preview_renderable=False,
            status=_issue_status(issue),
            priority=_issue_priority(issue.get("issue_field_values")),
            type=item_type,
            labels=[_label_name(x) for x in (issue.get("labels") or [])],
            assignee=primary.get("login") if primary else None,
            assignee_source_id=[str(a["id"]) for a in assignees if a.get("id") is not None],
            assignee_email=primary_email,
            creator_name=creator_login,
            creator_email=creator_email,
            reporter_name=creator_login,
            reporter_email=creator_email,
            reporter_source_id=creator_id,
            creator_source_timestamp=created_ms,
            is_email_hidden=True,
        )

    @staticmethod
    def pull_request_record(
        pr: dict[str, Any],
        *,
        connector_id: str,
        repo_id: int,
        emails: Optional[dict[str, str]] = None,
        from_listing: bool = True,
    ) -> PullRequestRecord:
        """``PullRequestRecord`` from live PR JSON.

        ``from_listing=True`` models the sync path, which reads the ``/pulls`` LIST
        payload: that payload omits ``mergeable`` and ``merged_by``, so both stay
        None. The reindex path fetches the single PR and does fill them — pass
        ``from_listing=False`` with single-PR JSON to assert that variant.
        """
        emails = emails or {}
        updated_ms = epoch_ms(pr.get("updated_at"))
        created_ms = epoch_ms(pr.get("created_at"))

        assignees = [a for a in (pr.get("assignees") or []) if a.get("login")]
        reviewers = [r for r in (pr.get("requested_reviewers") or []) if r.get("login")]
        author = pr.get("user") or {}
        author_id = str(author["id"]) if author.get("id") is not None else None

        merged_by = pr.get("merged_by")
        mergeable = pr.get("mergeable")

        return PullRequestRecord(
            id="", org_id="",
            record_name=pr.get("title"),
            record_type=RecordType.PULL_REQUEST,
            # Singular "pull", unlike the plural "issues" above. The asymmetry is real
            # and load-bearing for anything that parses these ids.
            external_record_id=f"{repo_id}/pull/{pr['number']}",
            external_revision_id=str(updated_ms) if updated_ms else None,
            external_record_group_id=f"{repo_id}-pull-requests",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GITHUB_TEAMS,
            connector_id=connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=pr.get("html_url"),
            source_created_at=created_ms,
            source_updated_at=updated_ms,
            inherit_permissions=True,
            preview_renderable=False,
            status=_pr_status(pr),
            mergeable=None if from_listing or mergeable is None else str(mergeable),
            merged_by=None if from_listing or not isinstance(merged_by, dict) else merged_by.get("login"),
            labels=[_label_name(x) for x in (pr.get("labels") or [])],
            assignee=[a["login"] for a in assignees],
            assignee_email=[
                emails[str(a["id"])] for a in assignees
                if a.get("id") is not None and str(a["id"]) in emails
            ],
            review_name=[r["login"] for r in reviewers],
            review_email=[
                emails[str(r["id"])] for r in reviewers
                if r.get("id") is not None and str(r["id"]) in emails
            ],
            creator_name=author.get("login"),
            creator_email=emails.get(author_id) if author_id else None,
            last_commit_sha=(pr.get("head") or {}).get("sha"),
        )

    @staticmethod
    def code_file_record(
        *,
        repo: dict[str, Any],
        path: str,
        sha: str,
        connector_id: str,
    ) -> CodeFileRecord:
        """``CodeFileRecord`` for a blob at ``path``.

        External ids for code carry a LEADING slash (``/{repo_id}/blob/{path}``) while
        issues and PRs do not. Both are anchored on the numeric repo id so they survive
        a repo rename.
        """
        name = path.rsplit("/", 1)[-1]
        raw_ext = name.rsplit(".", 1)[-1] if "." in name else ""
        parent_path = path.rpartition("/")[0] if "/" in path else None
        repo_id = repo["id"]
        mime_type = get_mime_type_for_extension(
            raw_ext, fallback=MimeTypes.PLAIN_TEXT.value,
        )
        # An extensionless file (LICENSE, Dockerfile) is treated as renderable.
        preview_renderable = (
            raw_ext.lower() in _PREVIEW_RENDERABLE_EXTENSIONS if raw_ext else True
        )
        return CodeFileRecord(
            id="", org_id="",
            record_name=name,
            record_type=RecordType.CODE_FILE,
            external_record_id=f"/{repo_id}/blob/{path}",
            external_revision_id=str(sha),
            external_record_group_id=f"{repo_id}-code-repository",
            parent_external_record_id=(
                f"/{repo_id}/tree/{parent_path}" if parent_path else None
            ),
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GITHUB_TEAMS,
            connector_id=connector_id,
            mime_type=mime_type,
            weburl=f"{repo['html_url']}/blob/{repo['default_branch']}/{path}",
            inherit_permissions=True,
            preview_renderable=preview_renderable,
            # None, not "", for extensionless names like LICENSE or Dockerfile.
            extension=raw_ext.lower() or None,
            file_path=path,
            file_hash=str(sha),
        )

    @staticmethod
    def folder_record(
        *, repo: dict[str, Any], path: str, sha: str, connector_id: str
    ) -> FileRecord:
        """Folder record. ``is_file=False`` is the field that a past incident flipped
        to True by rebuilding records from bare graph nodes."""
        name = path.rsplit("/", 1)[-1]
        parent_path = path.rpartition("/")[0] if "/" in path else None
        repo_id = repo["id"]
        return FileRecord(
            id="", org_id="",
            record_name=name,
            record_type=RecordType.FILE,
            external_record_id=f"/{repo_id}/tree/{path}",
            external_revision_id=str(sha) if sha else "",
            external_record_group_id=f"{repo_id}-code-repository",
            parent_external_record_id=(
                f"/{repo_id}/tree/{parent_path}" if parent_path else None
            ),
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GITHUB_TEAMS,
            connector_id=connector_id,
            mime_type=MimeTypes.FOLDER.value,
            weburl=f"{repo['html_url']}/tree/{repo['default_branch']}/{path}",
            inherit_permissions=True,
            preview_renderable=False,
            is_file=False,
        )


# ---------------------------------------------------------------------------
# Mapping helpers — re-derived, not imported from the connector
# ---------------------------------------------------------------------------

def _label_name(label: Any) -> str:
    return label.get("name") if isinstance(label, dict) else str(label)


def _issue_status(issue: dict[str, Any]) -> str:
    """GitHub state (+ ``state_reason``) → Status.

    GitHub only has open/closed; ``state_reason`` is what separates work that was
    finished from work that was abandoned, and collapsing both to "closed" loses that.
    """
    state = (issue.get("state") or "").lower()
    if state != "closed":
        return Status.REOPENED.value if state == "reopened" else Status.OPEN.value
    reason = (issue.get("state_reason") or "").lower()
    if reason in ("not_planned", "duplicate"):
        return Status.CANCELLED.value
    return Status.DONE.value


def _pr_status(pr: dict[str, Any]) -> str:
    """Merge state comes from ``merged_at``, never ``merged``: the LIST payload omits
    ``merged`` entirely, so reading it would both be wrong and force a per-PR fetch."""
    if pr.get("merged_at") is not None:
        return Status.DONE.value
    if (pr.get("state") or "").lower() == "closed":
        return Status.CANCELLED.value
    return Status.OPEN.value


def _issue_priority(field_values: Any) -> Optional[str]:
    """Priority from the org-level ``issue_field_values`` inlined on the listing.

    Absent on orgs without issue fields, which is why every priority assertion in the
    suite is skip-guarded rather than required.
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
            mapped = map_priority(str(name))
            return mapped.value if isinstance(mapped, Priority) else mapped
    return None


def _item_type(issue_type: Any) -> Optional[str]:
    """GitHub issue type → ItemType via the shared mapper, preserving unrecognised
    custom types verbatim rather than dropping them."""
    name = issue_type.get("name") if isinstance(issue_type, dict) else None
    if not name:
        return None
    mapped = map_type(str(name))
    return mapped.value if isinstance(mapped, ItemType) else mapped


# GitHub collaborator permission booleans, highest privilege first. Every one of
# these maps to a PermissionType; a collaborator with a custom repository role has
# none of them set, maps to nothing, and correctly receives no edge.
_COLLABORATOR_ROLE_KEYS = ("admin", "maintain", "push", "triage", "pull")


def expected_repo_grant_emails(
    collaborators: list[dict[str, Any]], emails: dict[str, str]
) -> set[str]:
    """Emails that should hold a PERMISSION edge on a repo's record group.

    Two reasons this is a set of emails rather than a count of collaborators:
    a principal with no PipesHub identity has nothing to grant to, and the
    connector dedupes grants per (entity_type, email) — so two GitHub accounts
    resolving to one address produce one edge, not two.
    """
    granted: set[str] = set()
    for collaborator in collaborators:
        source_id = str(collaborator.get("id"))
        email = emails.get(source_id)
        if not email:
            continue
        perms = collaborator.get("permissions") or {}
        if any(perms.get(key) for key in _COLLABORATOR_ROLE_KEYS):
            granted.add(email)
    return granted
