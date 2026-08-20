"""Shared fixtures and helpers for github_teams unit tests.

All test modules in this package import from here rather than duplicating
the mock-connector setup (mirrors ``tests/unit/connectors/sources/test_gitlab/conftest.py``).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def make_mock_connector() -> MagicMock:
    """Build a fully-mocked GitHubTeamsConnector-shaped object.

    Returns a MagicMock whose attributes match every attribute that the
    helper classes (ReposSync, UsersSync, ProjectsSync, IssuesSync, ...)
    access on ``self.c``. Each test file builds its subject under test by
    passing this mock as the constructor argument.
    """
    c = MagicMock()
    from app.config.constants.arangodb import Connectors

    c.connector_id = "github-conn-1"
    c.connector_name = Connectors.GITHUB_TEAMS.value
    c.batch_size = 5
    c.max_concurrent_batches = 5
    c.sync_filters = None
    c.indexing_filters = None
    c._github_login = None
    c.creator_email = None
    c.created_by = "test-user-1"

    c.logger = MagicMock()
    c.data_source = MagicMock()

    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_new_user_groups = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_records_moved = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_record_group_deleted = AsyncMock()
    dep.on_records_deleted_cascade = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_records_by_parent = AsyncMock(return_value=[])
    dep.get_user_by_user_id = AsyncMock(return_value=None)
    dep.get_all_app_users = AsyncMock(return_value=[])
    dep.migrate_group_to_user_by_external_id = AsyncMock()
    dep.reindex_existing_records = AsyncMock()
    c.data_entities_processor = dep

    runtime = MagicMock()
    runtime.ds_call = AsyncMock()
    runtime.search_call = AsyncMock()
    runtime.refresh_token_if_needed = AsyncMock()
    runtime.force_refresh_oauth_token = AsyncMock(return_value=True)
    c.runtime = runtime

    rsp = MagicMock()
    rsp.read_sync_point = AsyncMock(return_value=None)
    rsp.update_sync_point = AsyncMock()
    c.record_sync_point = rsp

    c.creator_user_permission = MagicMock(return_value=None)

    # transaction() context manager -> tx_store mock
    tx_store = MagicMock()
    tx_store.get_user_by_source_id = AsyncMock(return_value=None)
    tx_store.get_user_group_by_external_id = AsyncMock(return_value=None)
    tx_store.get_record_group_by_external_id = AsyncMock(return_value=None)
    tx_store.get_records_by_status = AsyncMock(return_value=[])
    tx_store.get_nodes_by_filters = AsyncMock(return_value=[])
    tx_store.get_record_by_external_id = AsyncMock(return_value=None)
    tx_store.batch_update_nodes = AsyncMock(return_value=True)
    tx_store.ensure_team_app_edge = AsyncMock()

    class _TxCtx:
        async def __aenter__(self) -> MagicMock:
            return tx_store

        async def __aexit__(self, *exc: object) -> None:
            return None

    data_store_provider = MagicMock()
    data_store_provider.transaction = MagicMock(return_value=_TxCtx())
    c.data_store_provider = data_store_provider
    c.tx_store = tx_store  # convenience handle for assertions in tests

    # Cross-helper calls (e.g. pull_requests -> issues.process_new_records).
    # A bare MagicMock attribute is not awaitable, so these must be AsyncMocks.
    c.issues.process_new_records = AsyncMock(return_value=True)
    c.issues.fetch_issues_batched = AsyncMock()
    c.issues.get_app_user_emails = AsyncMock(return_value={})
    c.pull_requests.process_pull_request = AsyncMock(return_value=None)
    c.pull_requests.process_pull_request = AsyncMock(return_value=None)
    c.pull_requests._prs_indexing_enabled = MagicMock(return_value=True)

    return c


@pytest.fixture()
def mock_connector() -> MagicMock:
    return make_mock_connector()


# ---------------------------------------------------------------------------
# PyGithub-shaped object builders
# ---------------------------------------------------------------------------

def make_repo(
    *,
    repo_id: int = 1001,
    owner_login: str = "acme",
    owner_id: int = 9001,
    owner_type: str = "Organization",
    name: str = "widgets",
    default_branch: str = "main",
    full_name: str | None = None,
) -> SimpleNamespace:
    """Return a Repository-shaped SimpleNamespace with the attributes the
    connector reads (``id``, ``name``, ``full_name``, ``owner.login``,
    ``owner.id``, ``owner.type``, ``default_branch``, ``html_url``)."""
    fn = full_name or f"{owner_login}/{name}"
    return SimpleNamespace(
        id=repo_id,
        name=name,
        full_name=fn,
        owner=SimpleNamespace(login=owner_login, id=owner_id, type=owner_type),
        default_branch=default_branch,
        html_url=f"https://github.com/{fn}",
    )


def make_named_user(
    *, user_id: int = 1, login: str = "octocat", email: str | None = None,
    permissions: SimpleNamespace | None = None, completed: bool = False,
) -> SimpleNamespace:
    return SimpleNamespace(
        id=user_id, login=login, email=email, name=login,
        permissions=permissions, completed=completed,
    )


def make_tree_element(path: str, *, sha: str = "sha-abc", entry_type: str = "blob", size: int | None = 100) -> SimpleNamespace:
    return SimpleNamespace(path=path, sha=sha, type=entry_type, size=size, mode="100644")


def make_git_tree(entries: list[SimpleNamespace], *, truncated: bool = False, sha: str = "root-sha") -> SimpleNamespace:
    return SimpleNamespace(tree=entries, truncated=truncated, sha=sha)


def make_compare_file(
    *, filename: str, status: str, previous_filename: str | None = None, sha: str = "sha-x",
) -> SimpleNamespace:
    return SimpleNamespace(filename=filename, status=status, previous_filename=previous_filename, sha=sha)


def make_comparison(files: list[SimpleNamespace], *, status: str = "ahead", total_commits: int = 1) -> SimpleNamespace:
    return SimpleNamespace(files=files, status=status, total_commits=total_commits, commits=[MagicMock() for _ in range(total_commits)])


def ok_response(data: object) -> MagicMock:
    res = MagicMock()
    res.success = True
    res.data = data
    res.error = None
    return res


def failed_response(error: str = "error", status_code: int | None = None) -> MagicMock:
    res = MagicMock()
    res.success = False
    res.data = None
    res.error = error
    # Real GitHubResponse carries the HTTP status; callers distinguish a
    # structural 403/404 from a transient failure by it.
    res.status_code = status_code
    return res
