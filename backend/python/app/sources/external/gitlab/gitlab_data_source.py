"""
Cleaned-up GitLab data source: the authoritative async/sync wrapper over python-gitlab.

This file is the sibling of ``gitlab_.py`` and replaces it after the refactor swap.
Only methods actively used by the GitLab connector (gitlab/) or the GitLab agent
action (app/agents/actions/gitlab/gitlab.py) are retained.  Unused CRUD methods
(branches, tags, pipelines, releases, milestones, labels, member mutations, group
mutations) have been removed.

Rule: do not add new methods speculatively here.  If a new use-case requires a new
method, route through ``code-generator/gitlab.py`` to regenerate and then cherry-pick.

Usage note
----------
The ``# Used by:`` comment blocks above each method are a snapshot in time and will
drift as the codebase evolves.  Their purpose is to make the initial mapping of
``connector → data-source`` calls discoverable at a glance; treat them as hints,
not guarantees.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import httpx
from gitlab import Gitlab

from app.sources.client.gitlab.gitlab import GitLabResponse

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from datetime import datetime


class GitLabDataSource:
    """
    Typed wrapper over python-gitlab for common GitLab read operations.

    Accepts either a python-gitlab ``Gitlab`` instance *or* any object that
    exposes ``.get_sdk() -> Gitlab`` (e.g. ``GitLabClient``).  Pass
    ``base_url`` to override the GitLab instance host used for GraphQL and
    direct HTTP calls (required for self-managed / GitLab EE deployments).

    All synchronous methods return ``GitLabResponse``; async methods (GraphQL,
    image fetch, attachment streaming) are also provided.
    """

    def __init__(
        self,
        client_or_sdk: Gitlab | object,
        base_url: str = "https://gitlab.com",
    ) -> None:
        # Accept either a raw SDK or a wrapper that exposes .get_sdk()
        if hasattr(client_or_sdk, "get_sdk"):
            sdk_fn = getattr(client_or_sdk, "get_sdk")
            self._sdk: Gitlab = cast(Gitlab, sdk_fn())
            token_fn = getattr(client_or_sdk, "get_token", None)
            if token_fn:
                self.token = token_fn()
        else:
            self._sdk = cast(Gitlab, client_or_sdk)
            self.token = None

        self._base_url = base_url.rstrip("/")
        # Shared async HTTP client — created lazily, reused across all GraphQL
        # and direct HTTP calls to avoid per-request TCP+TLS setup overhead.
        # Callers must use aclose() to tear it down.
        self._http_client: httpx.AsyncClient | None = None

    # ------------------------------------------------------------------
    # HTTP client lifecycle
    # ------------------------------------------------------------------

    @property
    def http_client(self) -> httpx.AsyncClient:
        """Return the lazily-initialised shared async HTTP client."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                follow_redirects=True,
                timeout=30.0,
            )
        return self._http_client

    async def aclose(self) -> None:
        """Close the shared HTTP client.  Call when this data source is no longer needed."""
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()
            self._http_client = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _project(self, project_id: int | str) -> object:
        """Fetch a project manager object by numeric ID or full path."""
        return self._sdk.projects.get(project_id)

    @staticmethod
    def _params(**kwargs: object) -> dict[str, object]:
        """Build a kwargs dict, dropping None values and empty containers."""
        out: dict[str, object] = {}
        for k, v in kwargs.items():
            if v is None:
                continue
            if isinstance(v, (list, dict)) and len(v) == 0:
                continue
            out[k] = v
        return out

    # ------------------------------------------------------------------
    # User
    # ------------------------------------------------------------------

    # Used by:
    #   gitlab/connector.py  - GitLabConnector.init (resolve current user)
    #   gitlab/connector.py  - GitLabConnector.test_connection_and_access
    #   gitlab/users.py      - UsersSync._resolve_creator_identity
    def get_user(self, user_id: int | str | None = None) -> GitLabResponse:
        """Return the authenticated user (no args) or any user by numeric/string ID.

        ``data`` is a python-gitlab ``CurrentUser`` (no args) or ``User`` object.
        GitLab OMITS ``is_admin`` and ``is_auditor`` from the response when the
        caller does not have those flags — read them with ``getattr(user, 'is_admin', False)``.
        """
        try:
            if user_id is None:
                self._sdk.auth()
                return GitLabResponse(success=True, data=self._sdk.user)
            return GitLabResponse(success=True, data=self._sdk.users.get(user_id))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Projects
    # ------------------------------------------------------------------

    # Used by:
    #   gitlab/projects.py - ProjectsSync._resolve_projects_with_filters (lookup by path)
    def get_project(self, project_id: int | str) -> GitLabResponse:
        """Return a single project by numeric ID or full path.

        Returns ``success=False`` on 403/404 instead of re-raising so callers
        can handle stale filter entries without aborting the whole sync.
        """
        try:
            return GitLabResponse(success=True, data=self._project(project_id))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/projects.py - ProjectsSync._resolve_projects_with_filters (unscoped)
    #   gitlab/users.py    - UsersSync._sync_users_unscoped
    #   gitlab/scope.py    - ScopeHelper._paged_list_projects_with_role_fallback
    #   gitlab/filters.py  - FiltersOps._gitlab_project_filter_options
    def list_projects(
        self,
        search: str | None = None,
        *,
        membership: bool | None = None,
        min_access_level: int | None = None,
        owned: bool | None = None,
        starred: bool | None = None,
        simple: bool | None = None,
        get_all: bool | None = None,
        iterator: bool | None = None,
        page: int | None = None,
        per_page: int | None = None,
        order_by: str | None = None,
        sort: str | None = None,
        pagination: str | None = None,
        search_namespaces: bool | None = None,
    ) -> GitLabResponse:
        """List accessible projects with optional filters.

        Key flags:
        - ``membership=True``: only projects where the caller has a membership row.
        - ``min_access_level=10``: all projects where the caller is Guest+.  On some
          GitLab versions ``membership`` silently omits Guest-level projects; prefer
          ``min_access_level=10`` for picker UIs.
        - ``pagination='keyset'`` + ``order_by='id'`` + ``sort='asc'``: keyset
          pagination — strongly recommended for full-scan sweeps; per-page cost stays
          constant regardless of offset depth.
        - ``iterator=True``: return a lazy ``GitlabList`` instead of materialising all
          pages; preferred on large tenants so callers can log progress.
        """
        try:
            extra: dict[str, object] = {}
            if iterator is not None:
                extra["iterator"] = iterator
            params = self._params(
                search=search,
                membership=membership,
                min_access_level=min_access_level,
                owned=owned,
                starred=starred,
                simple=simple,
                page=page,
                per_page=per_page,
                order_by=order_by,
                sort=sort,
                pagination=pagination,
                search_namespaces=search_namespaces,
            )
            return GitLabResponse(
                success=True,
                data=self._sdk.projects.list(get_all=get_all, **extra, **params),
            )
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/projects.py - ProjectsSync._resolve_projects_with_filters (group-scoped)
    #   gitlab/filters.py  - FiltersOps._gitlab_project_filter_options (scoped picker)
    def list_group_projects(
        self,
        group_id: int | str,
        *,
        include_subgroups: bool = True,
        search: str | None = None,
        get_all: bool | None = None,
        iterator: bool | None = None,
        page: int | None = None,
        per_page: int | None = None,
        order_by: str | None = None,
        sort: str | None = None,
        simple: bool | None = None,
    ) -> GitLabResponse:
        """List projects belonging to a group, optionally including all subgroups.

        ``simple=True`` returns the smaller project payload (id, path, name only),
        which is enough for picker UIs.  ``iterator=True`` returns a lazy
        ``GitlabList`` instead of materialising all pages.
        """
        try:
            g = self._sdk.groups.get(group_id, lazy=True)
            extra: dict[str, object] = {}
            if iterator is not None:
                extra["iterator"] = iterator
            params = self._params(
                include_subgroups=include_subgroups,
                search=search,
                page=page,
                per_page=per_page,
                order_by=order_by,
                sort=sort,
                simple=simple,
            )
            return GitLabResponse(
                success=True,
                data=g.projects.list(get_all=get_all, **extra, **params),
            )
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   app/agents/actions/gitlab/gitlab.py - GitLab.create_project
    def create_project(
        self,
        name: str,
        namespace_id: int | None = None,
        visibility: str | None = None,
        description: str | None = None,
        initialize_with_readme: bool | None = None,
        default_branch: str | None = None,
    ) -> GitLabResponse:
        """Create a new project.  Returns the created ``Project`` in ``data``."""
        try:
            payload = self._params(
                name=name,
                namespace_id=namespace_id,
                visibility=visibility,
                description=description,
                initialize_with_readme=initialize_with_readme,
                default_branch=default_branch,
            )
            return GitLabResponse(success=True, data=self._sdk.projects.create(payload))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   app/agents/actions/gitlab/gitlab.py - GitLab.update_project
    def update_project(
        self,
        project_id: int | str,
        name: str | None = None,
        description: str | None = None,
        visibility: str | None = None,
        default_branch: str | None = None,
        topics: list[str] | None = None,
    ) -> GitLabResponse:
        """Update mutable project fields.  Returns the updated ``Project`` in ``data``."""
        try:
            p = self._project(project_id)
            changed = False
            if name is not None:
                setattr(p, "name", name)
                changed = True
            if description is not None:
                setattr(p, "description", description)
                changed = True
            if visibility is not None:
                setattr(p, "visibility", visibility)
                changed = True
            if default_branch is not None:
                setattr(p, "default_branch", default_branch)
                changed = True
            if topics is not None and len(topics) > 0:
                setattr(p, "topics", topics)
                changed = True
            if changed:
                p.save()
            return GitLabResponse(success=True, data=p)
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   app/agents/actions/gitlab/gitlab.py - GitLab.delete_project
    def delete_project(self, project_id: int | str) -> GitLabResponse:
        """Delete a project.  Returns ``True`` in ``data`` on success."""
        try:
            self._project(project_id).delete()
            return GitLabResponse(success=True, data=True)
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Issues
    # ------------------------------------------------------------------

    # Used by:
    #   gitlab/issues.py - IssuesSync._fetch_issues_batched
    def list_issues(
        self,
        project_id: int | str,
        state: str | None = None,
        labels: list[str] | None = None,
        search: str | None = None,
        author_id: int | None = None,
        assignee_id: int | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        order_by: str | None = None,
        sort: str | None = None,
        get_all: bool | None = None,
    ) -> GitLabResponse:
        """List project issues with optional filters.  ``data`` is a list of ``ProjectIssue``."""
        try:
            p = self._sdk.projects.get(project_id)
            params = self._params(
                state=state,
                labels=labels,
                search=search,
                author_id=author_id,
                assignee_id=assignee_id,
                updated_after=updated_after,
                updated_before=updated_before,
                created_after=created_after,
                created_before=created_before,
                order_by=order_by,
                sort=sort,
            )
            return GitLabResponse(success=True, data=p.issues.list(get_all=get_all, **params))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/issues.py - IssuesSync._build_ticket_blocks
    #   gitlab/issues.py - IssuesSync._check_and_fetch_updated_record_for_reindex
    def get_issue(self, project_id: int | str, issue_iid: int) -> GitLabResponse:
        """Fetch a single issue by IID.  ``data`` is a ``ProjectIssue``."""
        try:
            return GitLabResponse(success=True, data=self._project(project_id).issues.get(issue_iid))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   app/agents/actions/gitlab/gitlab.py - GitLab.create_issue
    def create_issue(
        self,
        project_id: int | str,
        title: str,
        description: str | None = None,
        labels: list[str] | None = None,
        assignee_ids: list[int] | None = None,
        milestone_id: int | None = None,
    ) -> GitLabResponse:
        """Create an issue.  Returns the created ``ProjectIssue`` in ``data``."""
        try:
            payload = self._params(
                title=title,
                description=description,
                labels=labels,
                assignee_ids=assignee_ids,
                milestone_id=milestone_id,
            )
            return GitLabResponse(success=True, data=self._project(project_id).issues.create(payload))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   app/agents/actions/gitlab/gitlab.py - GitLab.update_issue
    def update_issue(
        self,
        project_id: int | str,
        issue_iid: int,
        title: str | None = None,
        description: str | None = None,
        labels: list[str] | None = None,
        state_event: str | None = None,
    ) -> GitLabResponse:
        """Update issue fields.  Use ``state_event='close'`` / ``'reopen'`` to change state."""
        try:
            p = self._project(project_id)
            issue = p.issues.get(issue_iid)
            changed = False
            if title is not None:
                setattr(issue, "title", title)
                changed = True
            if description is not None:
                setattr(issue, "description", description)
                changed = True
            if labels is not None and len(labels) > 0:
                setattr(issue, "labels", labels)
                changed = True
            if state_event is not None:
                setattr(issue, "state_event", state_event)
                changed = True
            if changed:
                issue.save()
            return GitLabResponse(success=True, data=issue)
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   app/agents/actions/gitlab/gitlab.py - GitLab.delete_issue
    def delete_issue(self, project_id: int | str, issue_iid: int) -> GitLabResponse:
        """Delete an issue.  Returns ``True`` in ``data`` on success."""
        try:
            self._project(project_id).issues.delete(issue_iid)
            return GitLabResponse(success=True, data=True)
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/issues.py    - IssuesSync._build_ticket_blocks
    #   gitlab/attachments.py - AttachmentsHelper.make_files_records_from_notes
    def list_issue_notes(
        self, project_id: int | str, issue_iid: int, get_all: bool | None = None
    ) -> GitLabResponse:
        """List notes (comments) on an issue.  ``data`` is a list of ``ProjectIssueNote``."""
        try:
            p = self._sdk.projects.get(project_id)
            return GitLabResponse(success=True, data=p.issues.get(issue_iid).notes.list(get_all=get_all))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Merge Requests
    # ------------------------------------------------------------------

    # Used by:
    #   gitlab/merge_requests.py - MergeRequestsSync._fetch_prs_batched
    def list_merge_requests(
        self,
        project_id: int | str,
        state: str | None = None,
        labels: list[str] | None = None,
        search: str | None = None,
        author_id: int | None = None,
        assignee_id: int | None = None,
        order_by: str | None = None,
        sort: str | None = None,
        updated_after: datetime | None = None,
        updated_before: datetime | None = None,
        created_after: datetime | None = None,
        created_before: datetime | None = None,
        get_all: bool | None = None,
    ) -> GitLabResponse:
        """List merge requests with optional filters.  ``data`` is a list of ``ProjectMergeRequest``."""
        try:
            p = self._sdk.projects.get(project_id)
            params = self._params(
                state=state,
                labels=labels,
                search=search,
                author_id=author_id,
                assignee_id=assignee_id,
                order_by=order_by,
                sort=sort,
                updated_after=updated_after,
                updated_before=updated_before,
                created_after=created_after,
                created_before=created_before,
            )
            return GitLabResponse(success=True, data=p.mergerequests.list(get_all=get_all, **params))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/merge_requests.py - MergeRequestsSync._build_pull_request_blocks
    #   gitlab/merge_requests.py - MergeRequestsSync._check_and_fetch_updated_record_for_reindex
    #   gitlab/comments.py       - CommentsHelper._build_merge_request_comment_blocks
    def get_merge_request(self, project_id: int | str, mr_iid: int) -> GitLabResponse:
        """Fetch a single merge request by IID.  ``data`` is a ``ProjectMergeRequest``."""
        try:
            return GitLabResponse(
                success=True,
                data=self._project(project_id).mergerequests.get(id=mr_iid),
            )
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/comments.py - CommentsHelper._build_merge_request_comment_blocks
    #   gitlab/attachments.py - AttachmentsHelper.make_files_records_from_notes
    def list_merge_request_notes(
        self, project_id: int | str, mr_iid: int, get_all: bool | None = None
    ) -> GitLabResponse:
        """List notes (comments) on a merge request.  ``data`` is a list of ``ProjectMergeRequestNote``."""
        try:
            p = self._sdk.projects.get(project_id, lazy=True)
            mr = p.mergerequests.get(id=mr_iid, lazy=True)
            return GitLabResponse(success=True, data=mr.notes.list(get_all=get_all))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/comments.py - CommentsHelper._build_merge_request_comment_blocks
    def list_merge_request_changes(
        self, project_id: int | str, mr_iid: int
    ) -> GitLabResponse:
        """Return the diff payload for a merge request (file-level changes).

        ``data`` is a dict with a ``changes`` list; each entry has
        ``new_path``, ``old_path``, ``diff``, ``new_file``, ``deleted_file``,
        ``too_large``, and ``generated_file`` fields.
        """
        try:
            p = self._sdk.projects.get(project_id, lazy=True)
            mr = p.mergerequests.get(id=mr_iid, lazy=True)
            return GitLabResponse(success=True, data=mr.changes(get_all=True))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/merge_requests.py - MergeRequestsSync._build_pull_request_blocks
    def list_merge_requests_commits(
        self, project_id: int | str, mr_iid: int, get_all: bool | None = None
    ) -> GitLabResponse:
        """List commits included in a merge request.  ``data`` is a list of ``ProjectCommit``."""
        try:
            p = self._sdk.projects.get(project_id, lazy=True)
            mr = p.mergerequests.get(id=mr_iid, lazy=True)
            return GitLabResponse(success=True, data=mr.commits(get_all=get_all))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   app/agents/actions/gitlab/gitlab.py - GitLab.create_merge_request
    def create_merge_request(
        self,
        project_id: int | str,
        source_branch: str,
        target_branch: str,
        title: str,
        description: str | None = None,
        labels: list[str] | None = None,
        assignee_id: int | None = None,
        assignee_ids: list[int] | None = None,
        remove_source_branch: bool | None = None,
        draft: bool | None = None,
    ) -> GitLabResponse:
        """Create a merge request.  Returns the created ``ProjectMergeRequest`` in ``data``."""
        try:
            payload = self._params(
                source_branch=source_branch,
                target_branch=target_branch,
                title=title,
                description=description,
                labels=labels,
                assignee_id=assignee_id,
                assignee_ids=assignee_ids,
                remove_source_branch=remove_source_branch,
                draft=draft,
            )
            return GitLabResponse(success=True, data=self._project(project_id).mergerequests.create(payload))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   app/agents/actions/gitlab/gitlab.py - GitLab.merge_merge_request
    def merge_merge_request(
        self,
        project_id: int | str,
        mr_iid: int,
        merge_when_pipeline_succeeds: bool | None = None,
        squash: bool | None = None,
    ) -> GitLabResponse:
        """Accept (merge) a merge request.  Returns the merge result in ``data``."""
        try:
            mr = self._project(project_id).mergerequests.get(mr_iid)
            params = self._params(
                merge_when_pipeline_succeeds=merge_when_pipeline_succeeds,
                squash=squash,
            )
            return GitLabResponse(success=True, data=mr.merge(**params))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Repository (REST)
    # ------------------------------------------------------------------

    # Used by:
    #   gitlab/repos.py - ReposSync._sync_repo_main (get HEAD SHA)
    def get_branch(self, project_id: int | str, branch: str) -> GitLabResponse:
        """Fetch a branch object.  ``data`` has a ``commit`` field with the HEAD SHA."""
        try:
            return GitLabResponse(success=True, data=self._project(project_id).branches.get(branch))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/repos.py - ReposSync._sync_repo_incremental
    def compare_commits(
        self,
        project_id: int | str,
        from_sha: str,
        to_sha: str,
        straight: bool = False,
    ) -> GitLabResponse:
        """Compare two refs and return changed-file diffs.

        Wraps ``GET /projects/:id/repository/compare``.  ``data`` includes
        ``diffs``, ``commits``, and related fields.
        """
        try:
            return GitLabResponse(
                success=True,
                data=self._project(project_id).repository_compare(from_sha, to_sha, straight=straight),
            )
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/repos.py - ReposSync._code_file_source_timestamps
    def list_commits_for_path(
        self,
        project_id: int | str,
        path: str,
        ref_name: str | None = None,
        *,
        per_page: int = 100,
    ) -> GitLabResponse:
        """Return the newest and oldest commit date for a repository path (1–2 REST pages).

        ``data`` is a dict with ``newest_committed_date``, ``oldest_committed_date``,
        and ``commit_count``.
        """
        try:
            p = self._project(project_id)
            query_data: dict[str, object] = {"path": path, "per_page": per_page, "page": 1}
            if ref_name is not None:
                query_data["ref_name"] = ref_name

            first_resp = self._sdk.http_request("get", p.commits.path, query_data=query_data)
            first_resp.raise_for_status()
            first_payload = first_resp.json()
            if not isinstance(first_payload, list):
                message = (
                    first_payload.get("message") if isinstance(first_payload, dict) else "Unexpected commits response"
                )
                return GitLabResponse(success=False, error=str(message))

            first_page: list[dict[str, object]] = first_payload
            if not first_page:
                return GitLabResponse(
                    success=True,
                    data={"newest_committed_date": None, "oldest_committed_date": None, "commit_count": 0},
                )

            total_pages = int(first_resp.headers.get("X-Total-Pages") or 1)
            commit_count = int(first_resp.headers.get("X-Total") or len(first_page))
            newest = first_page[0]
            if total_pages <= 1:
                oldest = first_page[-1]
            else:
                last_resp = self._sdk.http_request(
                    "get", p.commits.path, query_data={**query_data, "page": total_pages}
                )
                last_resp.raise_for_status()
                last_payload = last_resp.json()
                if not isinstance(last_payload, list):
                    message = (
                        last_payload.get("message") if isinstance(last_payload, dict) else "Unexpected commits response"
                    )
                    return GitLabResponse(success=False, error=str(message))
                last_page: list[dict[str, object]] = last_payload
                oldest = last_page[-1] if last_page else first_page[-1]

            return GitLabResponse(
                success=True,
                data={
                    "newest_committed_date": newest.get("committed_date"),
                    "oldest_committed_date": oldest.get("committed_date"),
                    "commit_count": commit_count,
                },
            )
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/repos.py - ReposSync._sync_repo_incremental (incremental diff rename detection)
    #   gitlab/repos.py - ReposSync._upsert_code_files_by_paths
    def list_repo_tree(
        self,
        project_id: int | str,
        ref: str | None = None,
        path: str | None = None,
        recursive: bool | None = None,
        get_all: bool | None = None,
        iterator: bool | None = None,
    ) -> GitLabResponse:
        """List the repository tree (folders and files).

        ``get_all=True`` materialises all pages in memory; ``iterator=True``
        returns a lazy ``GitlabList`` instead.  The two are mutually exclusive.
        """
        try:
            p = self._sdk.projects.get(project_id)
            payload = self._params(
                ref=ref,
                path=path,
                recursive=recursive,
                get_all=get_all,
                iterator=iterator,
            )
            return GitLabResponse(success=True, data=p.repository_tree(**payload))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/repos.py    - ReposSync._sync_repo_full (file content download)
    #   gitlab/comments.py - CommentsHelper._build_merge_request_comment_blocks
    def get_file_content(
        self, project_id: int | str, file_path: str, ref: str = "HEAD"
    ) -> GitLabResponse:
        """Fetch the content of a repository file at a given ref.

        ``data`` is a python-gitlab ``ProjectFile`` with a base64-encoded ``content`` attribute.
        """
        try:
            p = self._sdk.projects.get(project_id)
            return GitLabResponse(success=True, data=p.files.get(**self._params(ref=ref, file_path=file_path)))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Repository (GraphQL)
    # ------------------------------------------------------------------

    # Used by:
    #   gitlab/repos.py - ReposSync._sync_repo_full (folder tree via GraphQL)
    async def get_repo_tree_g(
        self, project_id: str, ref: str | None = "HEAD", after_cursor: str = ""
    ) -> GitLabResponse:
        """Fetch the repository *folder* tree via GraphQL (cursor-paginated).

        Returns ``data`` as raw bytes of the JSON response body.  The caller is
        responsible for parsing: ``json.loads(response.data)['data']['project']...``.
        GraphQL is used here instead of REST because the REST tree endpoint
        materialises every file for recursive calls, which is prohibitively slow
        on large repos; GraphQL's ``paginatedTree`` gives cursor-based progress.
        """
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        query = """
        query ($fullPath: ID!, $branch: String!, $afterCursor: String!) {
          project(fullPath: $fullPath) {
            name
            repository {
              rootRef
              paginatedTree(recursive: true, ref: $branch, after: $afterCursor) {
                nodes {
                  trees {
                    nodes { name path sha type webPath webUrl }
                  }
                }
                pageInfo { endCursor hasNextPage }
              }
            }
          }
        }
        """
        try:
            resp = await self.http_client.post(
                f"{self._base_url}/api/graphql",
                headers=headers,
                json={"query": query, "variables": {"fullPath": project_id, "branch": ref, "afterCursor": after_cursor}},
            )
            resp.raise_for_status()
            return GitLabResponse(success=True, data=resp.content)
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/repos.py - ReposSync._fetch_blob_page (file blob tree via GraphQL)
    async def get_file_tree_g(
        self,
        project_id: int | str,
        ref: str | None = "HEAD",
        after_cursor: str = "",
    ) -> GitLabResponse:
        """Fetch the repository *blob* (file) tree via GraphQL (cursor-paginated).

        Same return shape as ``get_repo_tree_g`` but queries ``blobs`` instead
        of ``trees``, so only actual files are returned (folders are excluded).
        """
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json",
        }
        query = """
        query ($fullPath: ID!, $branch: String!, $afterCursor: String!) {
          project(fullPath: $fullPath) {
            name
            repository {
              rootRef
              paginatedTree(recursive: true, ref: $branch, after: $afterCursor) {
                nodes {
                  blobs {
                    nodes { name path sha type webPath webUrl }
                  }
                }
                pageInfo { endCursor hasNextPage }
              }
            }
          }
        }
        """
        try:
            resp = await self.http_client.post(
                f"{self._base_url}/api/graphql",
                headers=headers,
                json={"query": query, "variables": {"fullPath": project_id, "branch": ref or "HEAD", "afterCursor": after_cursor}},
            )
            resp.raise_for_status()
            return GitLabResponse(success=True, data=resp.content)
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Members
    # ------------------------------------------------------------------

    # Used by:
    #   gitlab/projects.py - ProjectsSync._sync_project_members_as_pseudo
    #   gitlab/projects.py - ProjectsSync._group_permissions_from_child_projects
    #   gitlab/users.py    - UsersSync._sync_users_unscoped
    #   gitlab/users.py    - UsersSync._sync_users_scoped
    def list_project_members_all(
        self,
        project_id: str | int,
        get_all: bool | None = None,
        iterator: bool | None = None,
    ) -> GitLabResponse:
        """List all project members including inherited ones (``/members/all``).

        ``iterator=True`` returns a lazy ``GitlabList`` for streaming pagination —
        preferred on projects with large inherited-membership sets.
        """
        try:
            p = self._project(project_id)
            extra: dict[str, object] = {}
            if iterator is not None:
                extra["iterator"] = iterator
            return GitLabResponse(success=True, data=p.members_all.list(get_all=get_all, **extra))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/users.py    - UsersSync._sync_users_unscoped
    #   gitlab/users.py    - UsersSync._sync_users_scoped
    #   gitlab/projects.py - ProjectsSync._group_permissions_from_child_projects
    def list_group_members_all(
        self,
        group_id: int | str,
        get_all: bool | None = None,
        iterator: bool | None = None,
    ) -> GitLabResponse:
        """List all group members including inherited ones (``/members/all``).

        ``iterator=True`` returns a lazy ``GitlabList`` for streaming pagination.
        """
        try:
            g = self._sdk.groups.get(group_id, lazy=True)
            extra: dict[str, object] = {}
            if iterator is not None:
                extra["iterator"] = iterator
            return GitLabResponse(success=True, data=g.members_all.list(get_all=get_all, **extra))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Groups
    # ------------------------------------------------------------------

    # Used by:
    #   gitlab/users.py    - UsersSync._sync_users_unscoped
    #   gitlab/users.py    - UsersSync._sync_users_scoped (NOT_IN path)
    #   gitlab/scope.py    - ScopeHelper._paged_list_groups_with_role_fallback
    #   gitlab/scope.py    - ScopeHelper._expand_groups_with_descendants
    #   gitlab/filters.py  - FiltersOps._gitlab_group_filter_options
    def list_groups(
        self,
        search: str | None = None,
        get_all: bool | None = None,
        iterator: bool | None = None,
        owned: bool | None = None,
        min_access_level: int | None = None,
        all_available: bool | None = None,
        page: int | None = None,
        per_page: int | None = None,
        order_by: str | None = None,
        sort: str | None = None,
    ) -> GitLabResponse:
        """List groups visible to the authenticated user.

        Key flags:
        - ``min_access_level=10``: only groups where the caller is Guest+ (has a
          membership row).  Does NOT work for EE Auditors whose access flows from a
          user-level flag rather than a membership row.
        - ``all_available=True``: every group the caller can read, including those
          accessible via EE Auditor / Admin role.  Required for admin/auditor syncs.
        - ``iterator=True``: returns a lazy ``GitlabList``; strongly preferred on
          large EE tenants where ``get_all=True`` may block silently for hours.

        Note: keyset pagination is only supported for unauthenticated ``/groups``
        calls.  For authenticated callers, use ``per_page=100`` for efficiency.
        """
        try:
            extra: dict[str, object] = {}
            if iterator is not None:
                extra["iterator"] = iterator
            params = self._params(
                search=search,
                owned=owned,
                min_access_level=min_access_level,
                all_available=all_available,
                page=page,
                per_page=per_page,
                order_by=order_by,
                sort=sort,
            )
            return GitLabResponse(
                success=True,
                data=self._sdk.groups.list(get_all=get_all, **extra, **params),
            )
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/scope.py    - ScopeHelper._expand_groups_with_descendants (auditor fallback)
    def list_descendant_groups(
        self,
        group_id: int | str,
        search: str | None = None,
        get_all: bool | None = None,
        iterator: bool | None = None,
        owned: bool | None = None,
        min_access_level: int | None = None,
        all_available: bool | None = None,
        page: int | None = None,
        per_page: int | None = None,
        order_by: str | None = None,
        sort: str | None = None,
    ) -> GitLabResponse:
        """List descendant groups of a group (full subtree, parent excluded).

        Wraps ``GET /groups/:id/descendant_groups``.  Used primarily to recover
        subgroups whose access is inherited from an ancestor's Reporter+ membership
        when the EE Auditor listing path misses them.  Returns
        ``GroupDescendantGroup`` objects with ``id``, ``name``, ``full_path``, etc.
        """
        try:
            g = self._sdk.groups.get(group_id, lazy=True)
            extra: dict[str, object] = {}
            if iterator is not None:
                extra["iterator"] = iterator
            params = self._params(
                search=search,
                owned=owned,
                min_access_level=min_access_level,
                all_available=all_available,
                page=page,
                per_page=per_page,
                order_by=order_by,
                sort=sort,
            )
            return GitLabResponse(
                success=True,
                data=g.descendant_groups.list(get_all=get_all, **extra, **params),
            )
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # Used by:
    #   gitlab/projects.py   - ProjectsSync._ensure_gitlab_group_record_groups
    #   gitlab/users.py      - UsersSync._sync_users_scoped (per-group member walk)
    def get_group(self, group_id: int | str) -> GitLabResponse:
        """Fetch a group by numeric ID or full path.  ``data`` is a python-gitlab ``Group``."""
        try:
            return GitLabResponse(success=True, data=self._sdk.groups.get(group_id))
        except Exception as e:
            return GitLabResponse(success=False, error=str(e))

    # ------------------------------------------------------------------
    # Direct HTTP (images and attachment streaming)
    # ------------------------------------------------------------------

    # Used by:
    #   gitlab/attachments.py - AttachmentsHelper.embed_images_as_base64
    async def get_img_bytes(self, image_url: str) -> GitLabResponse:
        """Fetch raw image bytes from a GitLab attachment URL.

        Returns ``GitLabResponse`` with ``data`` as ``bytes`` on success, or
        ``success=False`` with the HTTP status or error message.
        """
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "*/*"}
        try:
            resp = await self.http_client.get(image_url, headers=headers)
            resp.raise_for_status()
            return GitLabResponse(success=True, data=resp.content)
        except httpx.HTTPStatusError as e:
            return GitLabResponse(
                success=False,
                error=f"HTTP {e.response.status_code} fetching image from {image_url}",
            )
        except Exception as e:
            return GitLabResponse(success=False, error=f"Error fetching image from {image_url}: {e}")

    # Used by:
    #   gitlab/attachments.py - AttachmentsHelper._fetch_attachment_content
    async def get_attachment_files_content(
        self, weburl: str
    ) -> AsyncGenerator[bytes, None]:
        """Stream raw bytes for an attachment file from its web URL.

        Yields 64 KB chunks so large attachments do not buffer in memory.
        Raises ``httpx.HTTPStatusError`` on non-2xx responses.
        """
        headers = {"Authorization": f"Bearer {self.token}", "Accept": "application/octet-stream"}
        async with self.http_client.stream("GET", weburl, headers=headers) as response:
            response.raise_for_status()
            async for chunk in response.aiter_bytes(chunk_size=65536):
                yield chunk
