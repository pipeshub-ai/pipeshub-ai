# pyright: ignore-file

"""Build expected GitLab graph entities (mirrors the ``gitlab`` connector mapping).

Every builder takes the **raw GitLab REST JSON** — the source of truth — and returns
the entity the connector should have written, so the tests validate source → graph
rather than graph → graph.

Where the connector delegates to a shared platform helper (``detect_language``,
``classify_file_role``, ``parse_timestamp``) this reuses the same helper, exactly as
``JiraExpected`` reuses ``ValueMapper``. Two things are deliberately NOT re-derived:

* ``status`` is GitLab's raw ``state`` string (``opened``/``closed``/``merged``) —
  this connector applies no Status mapping at all, unlike every other one.
* code-file external ids come from a GraphQL ``webPath`` rather than being built by
  the connector, so they are passed in and the test matches their *shape* separately.
"""

from __future__ import annotations

import mimetypes
from typing import Any, Optional

from app.config.constants.arangodb import (
    SUPPORTED_CODE_FILE_EXTENSIONS,
    Connectors,
    MimeTypes,
    OriginTypes,
    PermissionModel,
)
from app.connectors.sources.gitlab.constants import PREVIEW_RENDERABLE_EXTENSIONS
from app.models.entities import (
    AppMetadata,
    CodeFileRecord,
    FileRecord,
    ItemType,
    PullRequestRecord,
    RecordGroup,
    RecordGroupType,
    RecordType,
    TicketRecord,
)
from app.modules.parsers.code_parser.file_role import classify_file_role
from app.modules.parsers.code_parser.lang_config import detect_language
from app.utils.time_conversion import parse_timestamp

# The connector never sets these; the processor stamps them at write time with
# wall-clock values a test cannot know.
PROCESSOR_ASSIGNED_FIELDS = frozenset({"created_at", "updated_at"})

# Written by the indexing pipeline after the record lands, so comparing them races
# the pipeline the same way ``indexing_status`` would — a case that runs before the
# handover sees None and the same case a minute later sees a timestamp.
PIPELINE_ASSIGNED_FIELDS = frozenset({
    "extraction_status", "md5_hash", "processing_started_at",
})

# Blob source timestamps come from a commit-date backfill that runs after the record
# is written, so a freshly-synced blob legitimately has none yet.
CODE_TIMESTAMP_FIELDS = frozenset({"source_created_at", "source_updated_at"})


class GitLabExpected:
    """Expected graph entities for the GitLab connector."""

    # ------------------------------------------------------------------
    # App / record groups
    # ------------------------------------------------------------------

    @staticmethod
    def app_metadata(state: dict[str, Any]) -> AppMetadata:
        """``permission_model`` is the builder default (RECORD_LEVEL) — the connector
        declines APP_LEVEL on purpose, because it syncs real per-project member ACLs."""
        return AppMetadata(
            connector_id=state["connector_id"],
            name=state["connector_name"],
            type="GitLab",
            app_group="GitLab",
            scope="team",
            created_at_timestamp=0,
            updated_at_timestamp=0,
            permission_model=PermissionModel.RECORD_LEVEL.value,
            vector_membership_backfilled=True,
        )

    @staticmethod
    def group_record_group(group: dict[str, Any], *, connector_id: str) -> RecordGroup:
        """A GitLab namespace node, keyed by ``full_path``.

        Note what is missing: ``parent_external_group_id``. Group record groups are
        written flat, so a subgroup's node is never linked under its parent group's —
        every group node is parentless, and therefore every one of them gets its own
        App edge.
        """
        full_path = group["full_path"]
        return RecordGroup(
            id="", org_id="",
            name=group.get("name") or full_path,
            group_type=RecordGroupType.PROJECT.value,
            connector_name=Connectors.GITLAB,
            connector_id=connector_id,
            external_group_id=full_path,
            web_url=group.get("web_url"),
        )

    @staticmethod
    def project_record_group(
        project: dict[str, Any], *, connector_id: str,
        parent_full_path: Optional[str] = None,
    ) -> RecordGroup:
        """The project node.

        ``parent_full_path`` is the *longest* included group path that prefixes the
        project's namespace, so a project in a subgroup hangs off the subgroup node
        rather than the top-level one.
        """
        return RecordGroup(
            id="", org_id="",
            name=project["path_with_namespace"],
            group_type=RecordGroupType.PROJECT.value,
            connector_name=Connectors.GITLAB,
            connector_id=connector_id,
            external_group_id=str(project["id"]),
            parent_external_group_id=parent_full_path,
        )

    @staticmethod
    def child_record_group(
        project: dict[str, Any], *, kind: str, connector_id: str,
    ) -> RecordGroup:
        """One of the three children (``work-items`` / ``merge-requests`` /
        ``code-repository``).

        Unlike GitHub, these do NOT inherit from the project group — the connector
        writes a real, separately-gated ACL onto each one.
        """
        names = {
            "work-items": "Work items",
            "merge-requests": "Merge requests",
            "code-repository": "Code repository",
        }
        if kind not in names:
            raise ValueError(f"unknown child group kind {kind!r}")
        return RecordGroup(
            id="", org_id="",
            name=names[kind],
            group_type=RecordGroupType.PROJECT.value,
            connector_name=Connectors.GITLAB,
            connector_id=connector_id,
            external_group_id=f"{project['id']}-{kind}",
            parent_external_group_id=str(project["id"]),
        )

    # ------------------------------------------------------------------
    # Records
    # ------------------------------------------------------------------

    @staticmethod
    def ticket_record(issue: dict[str, Any], *, connector_id: str) -> TicketRecord:
        """``TicketRecord`` from live issue JSON.

        Note what is absent: this connector sets no assignee, creator or reporter
        fields on a ticket, so no ASSIGNED_TO / CREATED_BY edges can exist.
        """
        updated = parse_timestamp(issue["updated_at"])
        return TicketRecord(
            id="", org_id="",
            record_name=issue["title"],
            record_type=RecordType.TICKET,
            # GitLab's GLOBAL id, not the per-project iid — rename-proof.
            external_record_id=str(issue["id"]),
            external_revision_id=str(updated),
            external_record_group_id=f"{issue['project_id']}-work-items",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GITLAB,
            connector_id=connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=issue.get("web_url"),
            source_created_at=parse_timestamp(issue["created_at"]),
            source_updated_at=updated,
            inherit_permissions=True,
            preview_renderable=False,
            # RAW GitLab state — this connector applies no Status normalisation.
            status=issue.get("state"),
            type=item_type_of(issue.get("issue_type")),
            labels=list(issue.get("labels") or []),
        )

    @staticmethod
    def merge_request_record(
        mr: dict[str, Any], *, connector_id: str,
    ) -> PullRequestRecord:
        """``PullRequestRecord`` from live MR JSON."""
        updated = parse_timestamp(mr["updated_at"])
        merged_by = mr.get("merged_by")
        return PullRequestRecord(
            id="", org_id="",
            record_name=mr["title"],
            record_type=RecordType.PULL_REQUEST,
            external_record_id=str(mr["id"]),
            external_revision_id=str(updated),
            external_record_group_id=f"{mr['project_id']}-merge-requests",
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GITLAB,
            connector_id=connector_id,
            mime_type=MimeTypes.BLOCKS.value,
            weburl=mr.get("web_url"),
            source_created_at=parse_timestamp(mr["created_at"]),
            source_updated_at=updated,
            inherit_permissions=True,
            preview_renderable=False,
            status=mr.get("state"),            # raw: opened / merged / closed
            mergeable=mr.get("merge_status"),  # raw merge_status
            labels=list(mr.get("labels") or []),
            assignee=[a.get("username") for a in (mr.get("assignees") or [])],
            merged_by=merged_by.get("username") if merged_by else None,
            review_name=[r.get("username") for r in (mr.get("reviewers") or [])],
        )

    @staticmethod
    def code_file_record(
        *, project: dict[str, Any], path: str, blob_sha: Optional[str],
        connector_id: str, external_record_id: str, weburl: Optional[str],
    ) -> CodeFileRecord:
        """``CodeFileRecord`` for a blob.

        ``external_record_id`` and ``weburl`` are passed in rather than constructed:
        both originate from the GraphQL ``webPath`` / ``webUrl`` fields, so building
        them here would test our reconstruction rather than the connector. The parent
        id IS derived, because the connector derives it the same way — rewriting the
        ``webPath``'s directory segment from ``/-/blob/`` to ``/-/tree/`` — and that
        rewrite is the thing worth pinning.

        ``language`` and ``file_role`` are populated by this connector; the GitHub
        one leaves both null.
        """
        file_name = path.rsplit("/", 1)[-1]
        extension = blob_extension(file_name)
        return CodeFileRecord(
            id="", org_id="",
            record_name=file_name,
            record_type=RecordType.CODE_FILE,
            external_record_id=external_record_id,
            external_revision_id=str(blob_sha) if blob_sha else "",
            external_record_group_id=f"{project['id']}-code-repository",
            parent_external_record_id=(
                folder_id_of(external_record_id) if "/" in path else None
            ),
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GITLAB,
            connector_id=connector_id,
            mime_type=blob_mime_type(file_name, extension),
            extension=extension,
            preview_renderable=(
                extension in PREVIEW_RENDERABLE_EXTENSIONS if extension else True
            ),
            file_path=path,
            file_hash=blob_sha,
            language=detect_language(file_name),
            file_role=classify_file_role(path, file_name).value,
            inherit_permissions=True,
            weburl=weburl,
        )

    @staticmethod
    def folder_record(
        *, project: dict[str, Any], folder_name: str, external_record_id: str,
        weburl: Optional[str], folder_sha: Optional[str], connector_id: str,
        parent_external_record_id: Optional[str],
    ) -> FileRecord:
        """A directory node.

        ``is_file=False`` paired with ``mime_type=FOLDER`` is what separates a folder
        from a blob; a folder reading back as a file is the corruption this pins.
        """
        return FileRecord(
            id="", org_id="",
            record_name=folder_name,
            record_type=RecordType.FILE,
            external_record_id=external_record_id,
            external_revision_id=str(folder_sha) if folder_sha else "",
            external_record_group_id=f"{project['id']}-code-repository",
            parent_external_record_id=parent_external_record_id,
            version=0,
            origin=OriginTypes.CONNECTOR,
            connector_name=Connectors.GITLAB,
            connector_id=connector_id,
            mime_type=MimeTypes.FOLDER.value,
            preview_renderable=False,
            is_file=False,
            inherit_permissions=True,
            weburl=weburl,
        )


# ---------------------------------------------------------------------------
# Mapping helpers — each mirrors one connector-side function
# ---------------------------------------------------------------------------

def item_type_of(issue_type: Any) -> str:
    """GitLab ``issue_type`` -> ItemType, mirroring ``issues.py:173-177``.

    Hand-rolled in the connector rather than routed through ValueMapper, and only
    two values are recognised; anything else falls back to ISSUE.
    """
    value = (issue_type or "").lower()
    if value == ItemType.INCIDENT.value.lower():
        return ItemType.INCIDENT.value
    if value == ItemType.TASK.value.lower():
        return ItemType.TASK.value
    return ItemType.ISSUE.value


def blob_extension(file_name: str) -> Optional[str]:
    """Mirrors ``repos.py::_blob_extension``: lower-cased, ``None`` when absent.

    ``None`` rather than the whole name matters for ``LICENSE`` / ``Dockerfile``,
    where ``name.split(".")[-1]`` would hand back the filename as the extension.
    """
    base = file_name.rsplit("/", 1)[-1]
    if "." not in base:
        return None
    return base.rsplit(".", 1)[-1].lower()


def blob_mime_type(file_name: str, extension: Optional[str]) -> str:
    """Mirrors ``repos.py::_blob_mime_type``.

    The fallback order matters: a named ``MimeTypes`` member wins, then a guess that
    is itself a known ``MimeTypes`` value, then plain text for a recognised code
    extension, and only then BIN. Defaulting unknown extensions to text instead
    would push binaries through the text parser.
    """
    if extension is None:
        return MimeTypes.PLAIN_TEXT.value

    named = MimeTypes.__members__.get(extension.upper())
    if named is not None:
        return named.value

    guessed, _ = mimetypes.guess_type(file_name)
    if guessed:
        try:
            return MimeTypes(guessed).value
        except ValueError:
            pass

    if extension in SUPPORTED_CODE_FILE_EXTENSIONS:
        return MimeTypes.PLAIN_TEXT.value
    return MimeTypes.BIN.value


def folder_id_of(blob_external_id: str) -> str:
    """The parent folder id for a blob id, as ``repos.py:866-868`` computes it."""
    return blob_external_id.rpartition("/")[0].replace("/-/blob/", "/-/tree/", 1)
