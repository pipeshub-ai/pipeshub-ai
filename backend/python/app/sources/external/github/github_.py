from __future__ import annotations

import base64
import datetime
import difflib
from collections.abc import Sequence
from typing import List, Optional

from github import (
    Github,  # type: ignore
)
from github.AuthenticatedUser import AuthenticatedUser  # type: ignore
from github.Branch import Branch  # type: ignore
from github.Commit import Commit  # type: ignore
from github.ContentFile import ContentFile  # type: ignore
from github.Deployment import Deployment  # type: ignore
from github.DeploymentStatus import DeploymentStatus  # type: ignore
from github.File import File  # type: ignore
from github.GitBlob import GitBlob  # type: ignore
from github.GitRef import GitRef  # type: ignore
from github.GitRelease import GitRelease  # type: ignore
from github.GitTag import GitTag  # type: ignore
from github.GitTree import GitTree  # type: ignore
from github.Hook import Hook  # type: ignore
from github.Invitation import Invitation  # type: ignore
from github.Issue import Issue  # type: ignore
from github.IssueComment import IssueComment  # type: ignore
from github.Label import Label  # type: ignore
from github.NamedUser import NamedUser  # type: ignore
from github.Organization import Organization  # type: ignore
from github.PullRequest import PullRequest  # type: ignore
from github.PullRequestComment import PullRequestComment  # type: ignore
from github.PullRequestReview import PullRequestReview  # type: ignore
from github.RateLimit import RateLimit  # type: ignore
from github.Repository import Repository  # type: ignore
from github.Tag import Tag  # type: ignore
from github.Team import Team  # type: ignore
from github.Workflow import Workflow  # type: ignore
from github.WorkflowRun import WorkflowRun  # type: ignore

from app.sources.client.github.github import GitHubResponse


class GitHubDataSource:
    """Strict, typed wrapper over PyGithub for common GitHub business operations.

    Accepts either a PyGithub `Github` instance *or* any object with `.get_sdk() -> Github`.
    """

    def __init__(self, client: object) -> None:
        if isinstance(client, Github):
            self._sdk: Github = client
        else:
            get_sdk = getattr(client, "get_sdk", None)
            if get_sdk is None or not callable(get_sdk):
                raise TypeError("client must be a github.Github or expose get_sdk() -> Github")
            sdk = get_sdk()
            if not isinstance(sdk, Github):
                raise TypeError("get_sdk() must return a github.Github instance")
            self._sdk = sdk

    # -----------------------
    # Internal helpers
    # -----------------------
    def _repo(self, owner: str, repo: str) -> Repository:
        return self._sdk.get_repo(f"{owner}/{repo}")

    @staticmethod
    def _not_none(**params: object) -> dict[str, object]:
        return {k: v for k, v in params.items() if v is not None}

    @staticmethod
    def _issues_only(items: list) -> list:
        """Filter to items that are issues (pull_request is None), excluding PRs."""
        return [i for i in items if getattr(i, "pull_request", None) is None]


    def get_authenticated(self) -> GitHubResponse[AuthenticatedUser]:
        """Return the authenticated user."""
        try:
            user = self._sdk.get_user()
            return GitHubResponse(success=True, data=user)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_user(self, login: str) -> GitHubResponse[NamedUser]:
        """Get a user by login."""
        try:
            user = self._sdk.get_user(login)
            return GitHubResponse(success=True, data=user)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_organization(self, org: str) -> GitHubResponse[Organization]:
        """Get an organization by login."""
        try:
            org_obj = self._sdk.get_organization(org)
            return GitHubResponse(success=True, data=org_obj)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_owner(self, login: str, kind: str = "user") -> GitHubResponse[NamedUser | Organization]:
        """Get a user or organization by login (the 'owner' of repos). Use login='me' for the authenticated user."""
        try:
            if kind == "organization":
                obj = self._sdk.get_organization(login)
            else:
                obj = self._sdk.get_user() if (login or "").strip().lower() == "me" else self._sdk.get_user(login)
            return GitHubResponse(success=True, data=obj)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def list_user_repos(
        self,
        user: str,
        type: str = "owner",
        per_page: Optional[int] = None,
        page: Optional[int] = None,
    ) -> GitHubResponse[list[Repository]]:
        """List repositories for a given user. When both per_page and page are omitted, returns all repos. When either is passed, returns one page (default 10 per page, max 50). Pass the login from get_owner(owner='me') result; do not pass 'me' here."""
        try:
            u = self._sdk.get_user(user)
            paginated = u.get_repos(type=type)
            if per_page is None and page is None:
                repos = list(paginated)
                return GitHubResponse(success=True, data=repos)
            _per_page = 10 if per_page is None else min(50, max(1, per_page))
            _page = 1 if page is None else max(1, page)
            if hasattr(paginated, "get_page"):
                page_items = paginated.get_page(_page - 1)
            else:
                page_items = list(paginated)[(_page - 1) * _per_page : (_page - 1) * _per_page + _per_page]
            repos = page_items[:_per_page] if isinstance(page_items, list) else list(page_items)[:_per_page]
            return GitHubResponse(success=True, data=repos)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_repo(self, owner: str, repo: str) -> GitHubResponse[Repository]:
        """Get a repository."""
        try:
            r = self._repo(owner, repo)
            return GitHubResponse(success=True, data=r)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_org_repos(self, org: str, type: str = "all") -> GitHubResponse[list[Repository]]:
        """List repositories for an organization."""
        try:
            o = self._sdk.get_organization(org)
            repos = list(o.get_repos(type=type))
            return GitHubResponse(success=True, data=repos)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_repo(self, name: str, private: bool = True, description: str | None = None, auto_init: bool = True) -> GitHubResponse[Repository]:
        """Create a repository under the authenticated user."""
        try:
            params = self._not_none(description=description)
            repo = self._sdk.get_user().create_repo(name=name, private=private, auto_init=auto_init, **params)
            return GitHubResponse(success=True, data=repo)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_issues(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        labels: Sequence[str] | None = None,
        assignee: str | None = None,
        since: str | None = None,
        per_page: Optional[int] = None,
        page: Optional[int] = None,
    ) -> GitHubResponse[list[Issue]]:
        """List issues with filters. When both per_page and page are None (e.g. from connector), returns all issues. Otherwise returns one page (default 10 per page, max 50)."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(labels=labels, assignee=assignee, since=since)
            paginated = r.get_issues(state=state, **params)
            if per_page is None and page is None:
                issues = list(paginated)
                return GitHubResponse(success=True, data=issues)
            _per_page = 10 if per_page is None else min(50, max(1, per_page))
            _page = 1 if page is None else max(1, page)
            if hasattr(paginated, "get_page"):
                page_items = paginated.get_page(_page - 1)
            else:
                page_items = list(paginated)[(_page - 1) * _per_page : (_page - 1) * _per_page + _per_page]
            issues = page_items[:_per_page] if isinstance(page_items, list) else list(page_items)[:_per_page]
            return GitHubResponse(success=True, data=issues)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def list_issues_only(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        labels: Sequence[str] | None = None,
        assignee: str | None = None,
        since: str | None = None,
        per_page: int = 10,
        page: int = 1,
    ) -> GitHubResponse[list[Issue]]:
        """List only issues (exclude PRs). Always returns one page (default 10 per page, max 50) by over-fetching API pages and filtering out PRs."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(labels=labels, assignee=assignee, since=since)
            paginated = r.get_issues(state=state, **params)

            _per_page = min(50, max(1, per_page))
            _page = max(1, page)
            skip = (_page - 1) * _per_page
            needed = skip + _per_page
            accumulator: list = []
            api_page_index = 0
            api_page_size = 30
            while len(accumulator) < needed:
                if hasattr(paginated, "get_page"):
                    raw = paginated.get_page(api_page_index)
                else:
                    all_items = list(paginated)
                    filtered_all = self._issues_only(all_items)
                    result = filtered_all[skip : skip + _per_page]
                    return GitHubResponse(success=True, data=result)
                if not raw:
                    break
                batch = raw if isinstance(raw, list) else list(raw)
                accumulator.extend(self._issues_only(batch))
                if len(batch) < api_page_size:
                    break
                api_page_index += 1
            result = accumulator[skip : skip + _per_page]
            return GitHubResponse(success=True, data=result)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_issue(self, owner: str, repo: str, number: int) -> GitHubResponse[Issue]:
        """Get a single issue."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            return GitHubResponse(success=True, data=issue)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_issue(self, owner: str, repo: str, title: str, body: str | None = None, assignees: Sequence[str] | None = None, labels: Sequence[str] | None = None) -> GitHubResponse[Issue]:
        """Create an issue."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(body=body, assignees=assignees, labels=labels)
            issue = r.create_issue(title=title, **params)
            return GitHubResponse(success=True, data=issue)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def update_issue(
        self,
        owner: str,
        repo: str,
        number: int,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        assignees: Sequence[str] | None = None,
        labels: Sequence[str] | None = None,
    ) -> GitHubResponse[Issue]:
        """Update an existing issue. Only pass fields to change (title, body, state, assignees, labels)."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            params = self._not_none(title=title, body=body, state=state, assignees=assignees, labels=labels)
            if not params:
                return GitHubResponse(success=True, data=issue)
            issue.edit(**params)
            return GitHubResponse(success=True, data=issue)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def close_issue(self, owner: str, repo: str, number: int) -> GitHubResponse[Issue]:
        """Close an issue."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            issue.edit(state="closed")
            return GitHubResponse(success=True, data=issue)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def add_labels_to_issue(self, owner: str, repo: str, number: int, labels: Sequence[str]) -> GitHubResponse[list[Label]]:
        """Add labels to an issue."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            out = list(issue.add_to_labels(*labels))
            return GitHubResponse(success=True, data=out)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_issue_comments(self, owner: str, repo: str, number: int,since:datetime.datetime|None=None) -> GitHubResponse[list[IssueComment]]:
        """List comments on an issue."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            if since is None:
                comments=list(issue.get_comments())
            else:
                comments = list(issue.get_comments(since=since))
            return GitHubResponse(success=True, data=comments)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_issue_comment(self, owner: str, repo: str, number: int, comment_id: int) -> GitHubResponse[IssueComment]:
        """Get a single issue comment by ID."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number=number)
            comment = issue.get_comment(id=comment_id)
            return GitHubResponse(success=True, data=comment)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def create_issue_comment(self, owner: str, repo: str, number: int, body: str) -> GitHubResponse[IssueComment]:
        """Create a comment on an issue."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            comment = issue.create_comment(body)
            return GitHubResponse(success=True, data=comment)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def edit_issue_comment(self, owner: str, repo: str, number: int, comment_id: int, body: str) -> GitHubResponse[IssueComment]:
        """Edit an existing issue comment."""
        try:
            r = self._repo(owner, repo)
            issue = r.get_issue(number)
            comment = issue.get_comment(id=comment_id)
            comment.edit(body)
            return GitHubResponse(success=True, data=comment)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def list_pulls(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        head: str | None = None,
        base: str | None = None,
        per_page: Optional[int] = None,
        page: Optional[int] = None,
    ) -> GitHubResponse[list[PullRequest]]:
        """List PRs. When both per_page and page are None (e.g. from connector), returns all PRs. Otherwise returns one page (default 10 per page, max 50)."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(head=head, base=base)
            paginated = r.get_pulls(state=state, **params)
            if per_page is None and page is None:
                pulls = list(paginated)
                return GitHubResponse(success=True, data=pulls)
            _per_page = 10 if per_page is None else min(50, max(1, per_page))
            _page = 1 if page is None else max(1, page)
            if hasattr(paginated, "get_page"):
                page_items = paginated.get_page(_page - 1)
            else:
                page_items = list(paginated)[(_page - 1) * _per_page : (_page - 1) * _per_page + _per_page]
            pulls = page_items[:_per_page] if isinstance(page_items, list) else list(page_items)[:_per_page]
            return GitHubResponse(success=True, data=pulls)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_pull(self, owner: str, repo: str, number: int) -> GitHubResponse[PullRequest]:
        """Get a PR."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            return GitHubResponse(success=True, data=pr)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_pull_commits(self, owner: str, repo: str, number: int) -> GitHubResponse[list[Commit]]:
        """Get commits of a PR."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            commits = list(pr.get_commits())
            return GitHubResponse(success=True, data=commits)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_pull_file_changes(
        self,
        owner: str,
        repo: str,
        number: int,
        fetch_full_content: bool = True,
        max_changes_per_file: int = 10000,  # NEW: Skip files with excessive changes
        max_diff_lines: int = 10000,        # NEW: Truncate very long diffs
        context_lines: int = 2,            # NEW: Configurable context (GitHub default)
    ) -> GitHubResponse[list[File]]:
        """
        Get file changes of a PR with complete diffs and safety limits.
        
        Args:
            owner: Repository owner
            repo: Repository name
            number: Pull request number
            fetch_full_content: If True, fetches complete diffs for truncated files
            max_changes_per_file: Skip files with more than this many total changes
                                (default 3000). These are likely full rewrites or
                                generated files that would overflow context.
            max_diff_lines: Truncate diffs longer than this many lines (default 5000)
                        to prevent context overflow on massive refactors.
            context_lines: Number of context lines around each change (default 3,
                        standard GitHub format).
        
        Returns:
            GitHubResponse containing list of File objects with complete diffs,
            respecting safety limits to prevent context overflow.
        
        Safety Features:
            - Skips files with >max_changes_per_file total changes
            - Truncates diffs longer than max_diff_lines
            - Uses minimal context to reduce token usage
            - Logs warnings for skipped/truncated files
        """
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            files = list(pr.get_files())

            # Fast path: return as-is if full content fetching disabled
            if not fetch_full_content:
                return GitHubResponse(success=True, data=files)

            # Process each file with safety checks
            enhanced_files = []
            skipped_count = 0
            truncated_count = 0

            for file_obj in files:
                raw_data = file_obj.raw_data if hasattr(file_obj, 'raw_data') else {}

                filename = raw_data.get("filename", "")
                status = raw_data.get("status", "")
                patch = raw_data.get("patch", "")
                additions = raw_data.get("additions", 0)
                deletions = raw_data.get("deletions", 0)
                total_changes = additions + deletions

                # SAFETY CHECK 1: Skip files with excessive changes
                if total_changes > max_changes_per_file:
                    import logging
                    logging.warning(
                        f"SKIP: {filename} has {total_changes} changes "
                        f"(exceeds limit of {max_changes_per_file}). "
                        f"Likely a full rewrite or generated file."
                    )

                    # Add explanatory note in the patch
                    enhanced_raw_data = dict(raw_data)
                    enhanced_raw_data["patch"] = (
                        f"[SKIPPED: File has {total_changes:,} total changes "
                        f"(+{additions:,} -{deletions:,}), exceeding safety limit of "
                        f"{max_changes_per_file:,}. This is likely a complete rewrite, "
                        f"generated file, or vendor dependency. Manual review recommended.]"
                    )
                    enhanced_raw_data["_skipped_large_file"] = True
                    enhanced_raw_data["_skip_reason"] = "excessive_changes"

                    if hasattr(file_obj, '_rawData'):
                        file_obj._rawData = enhanced_raw_data
                    elif hasattr(file_obj, 'raw_data'):
                        object.__setattr__(file_obj, '_raw_data', enhanced_raw_data)

                    enhanced_files.append(file_obj)
                    skipped_count += 1
                    continue

                # Detect truncated patches
                is_truncated = False

                if total_changes > 0 and not patch:
                    is_truncated = True
                elif patch and any(marker in patch.lower() for marker in [
                    "diff too large", "binary file", "file is too large", "large diffs"
                ]):
                    is_truncated = True
                elif total_changes > 1000 and len(patch) < 500:
                    is_truncated = True

                # Skip fetching for removed/renamed files
                if not is_truncated or status in ("removed", "renamed"):
                    enhanced_files.append(file_obj)
                    continue

                # Fetch full diff with safety limits
                try:
                    full_diff = self._generate_full_diff_for_file(
                        owner=owner,
                        repo=repo,
                        pr=pr,
                        filename=filename,
                        status=status,
                        max_diff_lines=max_diff_lines,
                        context_lines=context_lines,
                    )

                    if full_diff:
                        # Check if diff was truncated
                        was_truncated = "[TRUNCATED]" in full_diff
                        if was_truncated:
                            truncated_count += 1

                        # Replace the truncated patch
                        enhanced_raw_data = dict(raw_data)
                        enhanced_raw_data["patch"] = full_diff
                        enhanced_raw_data["_full_content_fetched"] = True
                        if was_truncated:
                            enhanced_raw_data["_diff_truncated"] = True

                        if hasattr(file_obj, '_rawData'):
                            file_obj._rawData = enhanced_raw_data
                        elif hasattr(file_obj, 'raw_data'):
                            object.__setattr__(file_obj, '_raw_data', enhanced_raw_data)

                    enhanced_files.append(file_obj)

                except Exception as e:
                    import logging
                    logging.warning(f"Failed to fetch full content for {filename}: {e}")
                    enhanced_files.append(file_obj)

            # Log summary
            if skipped_count > 0 or truncated_count > 0:
                import logging
                logging.info(
                    f"PR #{number} file processing: "
                    f"{len(enhanced_files)} total files, "
                    f"{skipped_count} skipped (excessive changes), "
                    f"{truncated_count} truncated (exceeded max_diff_lines)"
                )

            return GitHubResponse(success=True, data=enhanced_files)

        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def _generate_full_diff_for_file(
        self,
        owner: str,
        repo: str,
        pr: PullRequest,
        filename: str,
        status: str,
        max_diff_lines: int = 5000,
        context_lines: int = 3,
    ) -> Optional[str]:
        """
        Generate a complete unified diff with safety limits.
        
        Args:
            owner: Repository owner
            repo: Repository name
            pr: PullRequest object
            filename: Path to the file
            status: File status (added, modified, removed)
            max_diff_lines: Maximum lines in the diff (default 5000)
            context_lines: Number of context lines (default 3)
        
        Returns:
            Complete unified diff as string, truncated if exceeds max_diff_lines,
            or None if generation fails.
        """
        try:
            base_sha = pr.base.sha
            head_sha = pr.head.sha

            if not base_sha or not head_sha:
                return None

            # Fetch file content from base and head commits
            base_content = ""
            if status != "added":
                base_content = self._fetch_file_content_at_ref(
                    owner, repo, filename, base_sha
                )

            head_content = ""
            if status != "removed":
                head_content = self._fetch_file_content_at_ref(
                    owner, repo, filename, head_sha
                )

            # Generate unified diff
            base_lines = base_content.splitlines(keepends=True) if base_content else []
            head_lines = head_content.splitlines(keepends=True) if head_content else []

            diff_iterator = difflib.unified_diff(
                base_lines,
                head_lines,
                fromfile=f"a/{filename}",
                tofile=f"b/{filename}",
                lineterm="",
                n=context_lines,  # Configurable context
            )

            # Collect diff lines with limit
            diff_lines = []
            for i, line in enumerate(diff_iterator):
                if i >= max_diff_lines:
                    # Truncate and add marker
                    remaining = sum(1 for _ in diff_iterator)  # Count remaining
                    diff_lines.append(
                        f"\n... [TRUNCATED: {remaining} more lines omitted to prevent "
                        f"context overflow. This diff exceeds {max_diff_lines} lines. "
                        f"Consider reviewing the file directly on GitHub.] ...\n"
                    )
                    import logging
                    logging.warning(
                        f"Diff for {filename} truncated at {max_diff_lines} lines "
                        f"({remaining} lines omitted)"
                    )
                    break
                diff_lines.append(line)

            return "".join(diff_lines)

        except Exception as e:
            import logging
            logging.debug(f"Error generating full diff for {filename}: {e}")
            return None


    def _fetch_file_content_at_ref(
        self,
        owner: str,
        repo: str,
        path: str,
        ref: str,
    ) -> str:
        """
        Fetch file content from a specific commit/ref.
        
        Args:
            owner: Repository owner
            repo: Repository name
            path: File path in the repository
            ref: Git reference (commit SHA, branch, tag)
        
        Returns:
            File content as string, or empty string if file doesn't exist or is binary.
        """
        try:
            r = self._repo(owner, repo)
            content = r.get_contents(path, ref=ref)

            # Handle directory response
            if isinstance(content, list):
                return ""

            # Decode content
            if hasattr(content, 'decoded_content'):
                return content.decoded_content.decode("utf-8", errors="replace")
            elif hasattr(content, 'content'):
                decoded_bytes = base64.b64decode(content.content)
                return decoded_bytes.decode("utf-8", errors="replace")

            return ""

        except Exception as e:
            import logging
            logging.debug(f"Could not fetch {path} at {ref}: {e}")
            return ""

    def get_pull_reviews(self, owner: str, repo: str, number: int) -> GitHubResponse[list[PullRequestReview]]:
        """Get reviews of a PR (approve, request changes, comment with body)."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            reviews = list(pr.get_reviews())
            return GitHubResponse(success=True, data=reviews)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def create_pull_request_review(
        self,
        owner: str,
        repo: str,
        number: int,
        event: str,
        body: Optional[str] = None,
    ) -> GitHubResponse[PullRequestReview]:
        """Submit a PR review: APPROVE, REQUEST_CHANGES, or COMMENT. Optional body for the review summary."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            review = pr.create_review(event=event, body=body or "")
            return GitHubResponse(success=True, data=review)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_pull_review_comments(self, owner: str, repo: str, number: int) -> GitHubResponse[list[PullRequestComment]]:
        """Get review comments of a PR."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            comments = list(pr.get_review_comments())
            return GitHubResponse(success=True, data=comments)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def create_pull_request_review_comment(
        self,
        owner: str,
        repo: str,
        number: int,
        body: str,
        commit_id: str,
        path: str,
        line: int | None = None,
        side: str | None = None,
        in_reply_to: int | None = None,
    ) -> GitHubResponse[PullRequestComment]:
        """Create a review comment on a PR (line comment) or reply to a comment."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            params: dict[str, object] = {"body": body, "commit": commit_id, "path": path}
            if line is not None:
                params["line"] = line
            if side is not None:
                params["side"] = side
            if in_reply_to is not None:
                params["in_reply_to"] = in_reply_to
            comment = pr.create_review_comment(**params)
            return GitHubResponse(success=True, data=comment)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def create_pull_request_review_comment_reply(
        self, owner: str, repo: str, number: int, comment_id: int, body: str
    ) -> GitHubResponse[PullRequestComment]:
        """Reply to an existing PR review comment."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            comment = pr.create_review_comment_reply(comment_id=comment_id, body=body)
            return GitHubResponse(success=True, data=comment)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def edit_pull_request_review_comment(
        self, owner: str, repo: str, number: int, comment_id: int, body: str
    ) -> GitHubResponse[PullRequestComment]:
        """Edit a PR review comment."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            comment = pr.get_review_comment(comment_id)
            comment.edit(body)
            return GitHubResponse(success=True, data=comment)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def create_pull(self, owner: str, repo: str, title: str, head: str, base: str, body: str | None = None, draft: bool = False) -> GitHubResponse[PullRequest]:
        """Create a PR."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(body=body)
            pr = r.create_pull(title=title, head=head, base=base, draft=draft, **params)
            return GitHubResponse(success=True, data=pr)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def merge_pull(self, owner: str, repo: str, number: int, commit_message: str | None = None, merge_method: str = "merge") -> GitHubResponse[bool]:
        """Merge a PR (merge/squash/rebase)."""
        try:
            r = self._repo(owner, repo)
            pr = r.get_pull(number)
            params = self._not_none(commit_message=commit_message, merge_method=merge_method)
            ok = pr.merge(**params)
            return GitHubResponse(success=True, data=bool(ok))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_releases(self, owner: str, repo: str) -> GitHubResponse[list[GitRelease]]:
        """List releases."""
        try:
            r = self._repo(owner, repo)
            rel = list(r.get_releases())
            return GitHubResponse(success=True, data=rel)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_release_by_tag(self, owner: str, repo: str, tag: str) -> GitHubResponse[GitRelease]:
        """Get release by tag."""
        try:
            r = self._repo(owner, repo)
            rel = r.get_release(tag)
            return GitHubResponse(success=True, data=rel)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_release(self, owner: str, repo: str, tag: str, name: str | None = None, body: str | None = None, draft: bool = False, prerelease: bool = False) -> GitHubResponse[GitRelease]:
        """Create a release."""
        try:
            r = self._repo(owner, repo)
            _name = name if name is not None else tag
            _msg = body if body is not None else ""
            rel = r.create_git_release(tag=tag, name=_name, message=_msg, draft=draft, prerelease=prerelease)
            return GitHubResponse(success=True, data=rel)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_branches(self, owner: str, repo: str) -> GitHubResponse[list[Branch]]:
        """List branches."""
        try:
            r = self._repo(owner, repo)
            branches = list(r.get_branches())
            return GitHubResponse(success=True, data=branches)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_branch(self, owner: str, repo: str, branch: str) -> GitHubResponse[Branch]:
        """Get one branch."""
        try:
            r = self._repo(owner, repo)
            b = r.get_branch(branch)
            return GitHubResponse(success=True, data=b)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_tags(self, owner: str, repo: str) -> GitHubResponse[list[Tag]]:
        """List tags."""
        try:
            r = self._repo(owner, repo)
            tags = list(r.get_tags())
            return GitHubResponse(success=True, data=tags)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def get_file_contents(self, owner: str, repo: str, path: str, ref: str | None = None) -> GitHubResponse[ContentFile]:
        """Get file contents."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(ref=ref)
            content = r.get_contents(path, **params)
            return GitHubResponse(success=True, data=content)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def create_file(self, owner: str, repo: str, path: str, message: str, content: bytes, branch: str | None = None) -> GitHubResponse[dict[str, object]]:
        """Create a file."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(branch=branch)
            result = r.create_file(path=path, message=message, content=content, **params)
            return GitHubResponse(success=True, data=result)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def update_file(self, owner: str, repo: str, path: str, message: str, content: bytes, sha: str, branch: str | None = None) -> GitHubResponse[dict[str, object]]:
        """Update a file."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(branch=branch)
            result = r.update_file(path=path, message=message, content=content, sha=sha, **params)
            return GitHubResponse(success=True, data=result)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def delete_file(self, owner: str, repo: str, path: str, message: str, sha: str, branch: str | None = None) -> GitHubResponse[dict[str, object]]:
        """Delete a file."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(branch=branch)
            result = r.delete_file(path=path, message=message, sha=sha, **params)
            return GitHubResponse(success=True, data=result)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_collaborators(self, owner: str, repo: str) -> GitHubResponse[list[NamedUser]]:
        """List collaborators."""
        try:
            r = self._repo(owner, repo)
            users = list(r.get_collaborators())
            return GitHubResponse(success=True, data=users)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def add_collaborator(self, owner: str, repo: str, username: str, permission: str = "push") -> GitHubResponse[bool]:
        """Add collaborator."""
        try:
            r = self._repo(owner, repo)
            ok = r.add_to_collaborators(username, permission=permission)
            return GitHubResponse(success=True, data=bool(ok))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def remove_collaborator(self, owner: str, repo: str, username: str) -> GitHubResponse[bool]:
        """Remove collaborator."""
        try:
            r = self._repo(owner, repo)
            r.remove_from_collaborators(username)
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_repo_teams(self, owner: str, repo: str) -> GitHubResponse[list[Team]]:
        """List teams with access to the repo."""
        try:
            r = self._repo(owner, repo)
            teams = list(r.get_teams())
            return GitHubResponse(success=True, data=teams)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_repo_hooks(self, owner: str, repo: str) -> GitHubResponse[list[Hook]]:
        """List repo webhooks."""
        try:
            r = self._repo(owner, repo)
            hooks = list(r.get_hooks())
            return GitHubResponse(success=True, data=hooks)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_repo_hook(self, owner: str, repo: str, name: str, config: dict[str, str], events: Sequence[str] | None = None, active: bool = True) -> GitHubResponse[Hook]:
        """Create a webhook."""
        try:
            r = self._repo(owner, repo)
            ev = list(events) if events is not None else ["push"]
            hook = r.create_hook(name=name, config=config, events=ev, active=active)
            return GitHubResponse(success=True, data=hook)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def edit_repo_hook(self, owner: str, repo: str, hook_id: int, config: dict[str, str] | None = None, events: Sequence[str] | None = None, active: bool | None = None) -> GitHubResponse[Hook]:
        """Edit webhook."""
        try:
            r = self._repo(owner, repo)
            hook = r.get_hook(hook_id)
            params = self._not_none(config=config, events=list(events) if events is not None else None, active=active)
            hook.edit(**params)
            return GitHubResponse(success=True, data=hook)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def ping_repo_hook(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[bool]:
        """Ping webhook."""
        try:
            r = self._repo(owner, repo)
            hook = r.get_hook(hook_id)
            hook.ping()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def test_repo_hook(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[bool]:
        """Test webhook delivery."""
        try:
            r = self._repo(owner, repo)
            hook = r.get_hook(hook_id)
            hook.test()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_hook_deliveries(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[list[dict[str, object]]]:
        """List webhook deliveries."""
        try:
            r = self._repo(owner, repo)
            deliveries = list(r.get_hook_deliveries(hook_id))
            return GitHubResponse(success=True, data=deliveries)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_hook_delivery(self, owner: str, repo: str, hook_id: int, delivery_id: int) -> GitHubResponse[dict[str, object]]:
        """Get a webhook delivery."""
        try:
            r = self._repo(owner, repo)
            delivery = r.get_hook_delivery(hook_id, delivery_id)
            return GitHubResponse(success=True, data=delivery)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def delete_repo_hook(self, owner: str, repo: str, hook_id: int) -> GitHubResponse[bool]:
        """Delete webhook."""
        try:
            r = self._repo(owner, repo)
            hook = r.get_hook(hook_id)
            hook.delete()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_workflows(self, owner: str, repo: str) -> GitHubResponse[list[Workflow]]:
        """List workflows."""
        try:
            r = self._repo(owner, repo)
            workflows = list(r.get_workflows())
            return GitHubResponse(success=True, data=workflows)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_workflow(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[Workflow]:
        """Get a workflow."""
        try:
            r = self._repo(owner, repo)
            wf = r.get_workflow(workflow_id)
            return GitHubResponse(success=True, data=wf)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def enable_workflow(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[bool]:
        """Enable a workflow."""
        try:
            wf = self._repo(owner, repo).get_workflow(workflow_id)
            wf.enable()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def disable_workflow(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[bool]:
        """Disable a workflow."""
        try:
            wf = self._repo(owner, repo).get_workflow(workflow_id)
            wf.disable()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def dispatch_workflow(self, owner: str, repo: str, workflow_id: int, ref: str, inputs: dict[str, str] | None = None) -> GitHubResponse[bool]:
        """Dispatch a workflow."""
        try:
            wf = self._repo(owner, repo).get_workflow(workflow_id)
            params = self._not_none(inputs=inputs)
            ok = wf.create_dispatch(ref=ref, **params)
            return GitHubResponse(success=True, data=bool(ok))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_workflow_runs(self, owner: str, repo: str, workflow_id: int) -> GitHubResponse[list[WorkflowRun]]:
        """List runs for a workflow."""
        try:
            wf = self._repo(owner, repo).get_workflow(workflow_id)
            runs = list(wf.get_runs())
            return GitHubResponse(success=True, data=runs)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def rerun_workflow_run(self, owner: str, repo: str, run_id: int) -> GitHubResponse[bool]:
        """Re-run a workflow run."""
        try:
            run = self._repo(owner, repo).get_workflow_run(run_id)
            ok = run.rerun()
            return GitHubResponse(success=True, data=bool(ok))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def cancel_workflow_run(self, owner: str, repo: str, run_id: int) -> GitHubResponse[bool]:
        """Cancel a workflow run."""
        try:
            run = self._repo(owner, repo).get_workflow_run(run_id)
            ok = run.cancel()
            return GitHubResponse(success=True, data=bool(ok))
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_deployment(self, owner: str, repo: str, ref: str, task: str = "deploy", auto_merge: bool = False, required_contexts: Sequence[str] | None = None, environment: str | None = None, description: str | None = None) -> GitHubResponse[Deployment]:
        """Create a deployment. (Requires suitable permissions)"""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(task=task, auto_merge=auto_merge, required_contexts=list(required_contexts) if required_contexts is not None else None, environment=environment, description=description)
            dep = r.create_deployment(ref=ref, **params)
            return GitHubResponse(success=True, data=dep)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_deployments(self, owner: str, repo: str) -> GitHubResponse[list[Deployment]]:
        """List deployments."""
        try:
            r = self._repo(owner, repo)
            deps = list(r.get_deployments())
            return GitHubResponse(success=True, data=deps)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_deployment(self, owner: str, repo: str, deployment_id: int) -> GitHubResponse[Deployment]:
        """Get a deployment."""
        try:
            r = self._repo(owner, repo)
            dep = r.get_deployment(deployment_id)
            return GitHubResponse(success=True, data=dep)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_deployment_status(self, owner: str, repo: str, deployment_id: int, state: str, target_url: str | None = None, description: str | None = None, environment: str | None = None, environment_url: str | None = None, auto_inactive: bool | None = None) -> GitHubResponse[DeploymentStatus]:
        """Create a deployment status."""
        try:
            dep = self._repo(owner, repo).get_deployment(deployment_id)
            params = self._not_none(target_url=target_url, description=description, environment=environment, environment_url=environment_url, auto_inactive=auto_inactive)
            st = dep.create_status(state=state, **params)
            return GitHubResponse(success=True, data=st)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_commit(self, owner: str, repo: str, sha: str) -> GitHubResponse[Commit]:
        """Get a commit."""
        try:
            c = self._repo(owner, repo).get_commit(sha=sha)
            return GitHubResponse(success=True, data=c)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_commit_status(self, owner: str, repo: str, sha: str, state: str, target_url: str | None = None, description: str | None = None, context: str | None = None) -> GitHubResponse[bool]:
        """Create a commit status."""
        try:
            commit = self._repo(owner, repo).get_commit(sha=sha)
            params = self._not_none(target_url=target_url, description=description, context=context)
            commit.create_status(state=state, **params)
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_topics(self, owner: str, repo: str) -> GitHubResponse[list[str]]:
        """Get repository topics."""
        try:
            r = self._repo(owner, repo)
            topics = list(r.get_topics())
            return GitHubResponse(success=True, data=topics)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def replace_topics(self, owner: str, repo: str, topics: Sequence[str]) -> GitHubResponse[list[str]]:
        """Replace repository topics."""
        try:
            r = self._repo(owner, repo)
            out = r.replace_topics(list(topics))
            return GitHubResponse(success=True, data=out)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_clones_traffic(self, owner: str, repo: str) -> GitHubResponse[dict[str, object]]:
        """Get clone traffic."""
        try:
            r = self._repo(owner, repo)
            data = r.get_clones_traffic()
            return GitHubResponse(success=True, data=data.__dict__ if hasattr(data, "__dict__") else data)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_views_traffic(self, owner: str, repo: str) -> GitHubResponse[dict[str, object]]:
        """Get views traffic."""
        try:
            r = self._repo(owner, repo)
            data = r.get_views_traffic()
            return GitHubResponse(success=True, data=data.__dict__ if hasattr(data, "__dict__") else data)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_forks(self, owner: str, repo: str) -> GitHubResponse[list[Repository]]:
        """List forks."""
        try:
            r = self._repo(owner, repo)
            forks = list(r.get_forks())
            return GitHubResponse(success=True, data=forks)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_fork(self, owner: str, repo: str, org: str | None = None) -> GitHubResponse[Repository]:
        """Create a fork."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(organization=org)
            fork = r.create_fork(**params)
            return GitHubResponse(success=True, data=fork)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_contributors(self, owner: str, repo: str) -> GitHubResponse[list[NamedUser]]:
        """List contributors."""
        try:
            r = self._repo(owner, repo)
            users = list(r.get_contributors())
            return GitHubResponse(success=True, data=users)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def list_assignees(self, owner: str, repo: str) -> GitHubResponse[list[NamedUser]]:
        """List potential assignees."""
        try:
            r = self._repo(owner, repo)
            users = list(r.get_assignees())
            return GitHubResponse(success=True, data=users)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

    def list_pending_invitations(self, owner: str, repo: str) -> GitHubResponse[List[Invitation]]:
        """List pending repo invitations."""
        try:
            r = self._repo(owner, repo)
            inv = list(r.get_pending_invitations())
            return GitHubResponse(success=True, data=inv)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def remove_invitation(self, owner: str, repo: str, invitation_id: int) -> GitHubResponse[bool]:
        """Remove a repo invitation."""
        try:
            r = self._repo(owner, repo)
            r.remove_invitation(invitation_id)
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    # DependabotAlert not available in older PyGithub versions
    def list_dependabot_alerts(self, owner: str, repo: str) -> GitHubResponse[List[object]]:
        """List Dependabot alerts for a repo."""
        try:
            r = self._repo(owner, repo)
            alerts = list(r.get_dependabot_alerts())
            return GitHubResponse(success=True, data=alerts)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    # DependabotAlert not available in older PyGithub versions
    def get_dependabot_alert(self, owner: str, repo: str, alert_number: int) -> GitHubResponse[object]:
        """Get a single Dependabot alert."""
        try:
            r = self._repo(owner, repo)
            alert = r.get_dependabot_alert(alert_number)
            return GitHubResponse(success=True, data=alert)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_git_ref(self, owner: str, repo: str, ref: str, sha: str) -> GitHubResponse[GitRef]:
        """Create a git ref."""
        try:
            r = self._repo(owner, repo)
            gitref = r.create_git_ref(ref=ref, sha=sha)
            return GitHubResponse(success=True, data=gitref)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_git_ref(self, owner: str, repo: str, ref: str) -> GitHubResponse[GitRef]:
        """Get a git ref."""
        try:
            r = self._repo(owner, repo)
            out = r.get_git_ref(ref)
            return GitHubResponse(success=True, data=out)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def delete_git_ref(self, owner: str, repo: str, ref: str) -> GitHubResponse[bool]:
        """Delete a git ref."""
        try:
            ref_obj = self._repo(owner, repo).get_git_ref(ref)
            ref_obj.delete()
            return GitHubResponse(success=True, data=True)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_git_blob(self, owner: str, repo: str, content: str, encoding: str = "utf-8") -> GitHubResponse[GitBlob]:
        """Create a git blob."""
        try:
            r = self._repo(owner, repo)
            blob = r.create_git_blob(content, encoding)
            return GitHubResponse(success=True, data=blob)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_git_tree(self, owner: str, repo: str, tree: list[tuple[str, str, str]], base_tree: str | None = None) -> GitHubResponse[GitTree]:
        """Create a git tree."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(base_tree=base_tree)
            out = r.create_git_tree(tree=tree, **params)
            return GitHubResponse(success=True, data=out)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def create_git_tag(self, owner: str, repo: str, tag: str, message: str, object_sha: str, type: str, tagger: dict[str, str] | None = None) -> GitHubResponse[GitTag]:
        """Create a git tag object."""
        try:
            r = self._repo(owner, repo)
            params = self._not_none(tagger=tagger)
            out = r.create_git_tag(tag=tag, message=message, object=object_sha, type=type, **params)
            return GitHubResponse(success=True, data=out)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def search_repositories(
        self,
        query: str,
        per_page: Optional[int] = None,
        page: Optional[int] = None,
    ) -> GitHubResponse[list[Repository]]:
        """Search repositories. Default 10 per page, max 50."""
        try:
            paginated = self._sdk.search_repositories(query)
            _per_page = 10 if per_page is None else min(50, max(1, per_page))
            _page = 1 if page is None else max(1, page)
            if hasattr(paginated, "get_page"):
                page_items = paginated.get_page(_page - 1)
            else:
                page_items = list(paginated)[(_page - 1) * _per_page : (_page - 1) * _per_page + _per_page]
            res = page_items[:_per_page] if isinstance(page_items, list) else list(page_items)[:_per_page]
            return GitHubResponse(success=True, data=res)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))


    def get_rate_limit(self) -> GitHubResponse[RateLimit]:
        """Get current rate limit."""
        try:
            limit = self._sdk.get_rate_limit()
            return GitHubResponse(success=True, data=limit)
        except Exception as e:
            return GitHubResponse(success=False, error=str(e))

