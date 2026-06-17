"""Shared fixtures and helpers for gitlab unit tests.

All test modules in this package import from here rather than
duplicating the mock-connector setup.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Connector mock factory
# ---------------------------------------------------------------------------

def make_mock_connector() -> MagicMock:
    """Build a fully-mocked GitLabConnector-shaped object.

    Returns a MagicMock whose attributes match every attribute that the
    helper classes (ReposSync, ScopeHelper, UsersSync, …) access on
    ``self.c``.  Each test file builds its subject under test by passing
    this mock as the constructor argument.
    """
    c = MagicMock()
    c.connector_id = "gitlab-conn-1"
    c.connector_name = "GITLAB"
    c._gitlab_base_url = "https://gitlab.com"
    c.batch_size = 5
    c.max_concurrent_batches = 5
    c.sync_filters = None
    c.indexing_filters = None
    c._is_admin = False
    c._is_auditor = False
    c._auditor_fallback_warned = False
    c._gitlab_user_id = None
    c.creator_email = None
    c.created_by = "test-user-1"

    # Bound logger
    c.logger = MagicMock()

    # data_source
    c.data_source = MagicMock()

    # data_entities_processor
    dep = MagicMock()
    dep.org_id = "org-1"
    dep.on_new_app_users = AsyncMock()
    dep.on_new_record_groups = AsyncMock()
    dep.on_new_records = AsyncMock()
    dep.on_record_metadata_update = AsyncMock()
    dep.on_records_moved = AsyncMock()
    dep.on_record_deleted = AsyncMock()
    dep.on_folder_deleted = AsyncMock()
    dep.get_record_by_external_id = AsyncMock(return_value=None)
    dep.get_records_by_parent = AsyncMock(return_value=[])
    dep.get_user_by_user_id = AsyncMock(return_value=None)
    dep.reindex_existing_records = AsyncMock()
    c.data_entities_processor = dep

    # runtime helper (used by sync helpers)
    runtime = MagicMock()
    runtime.ds_call = AsyncMock()
    runtime.ds_call_async = AsyncMock()
    runtime.paged_list = AsyncMock()
    runtime.refresh_token_if_needed = AsyncMock()
    runtime.call_with_auth_retry = AsyncMock()
    runtime.force_refresh_oauth_token = AsyncMock(return_value=True)
    c.runtime = runtime

    # record_sync_point
    rsp = MagicMock()
    rsp.read_sync_point = AsyncMock(return_value=None)
    rsp.update_sync_point = AsyncMock()
    c.record_sync_point = rsp

    # datetime_range_from_sync_filter returns (None, None) by default
    c.datetime_range_from_sync_filter = MagicMock(return_value=(None, None))

    # creator_user_permission
    c.creator_user_permission = MagicMock(return_value=None)

    return c


# ---------------------------------------------------------------------------
# pytest fixtures wrapping the factory
# ---------------------------------------------------------------------------

@pytest.fixture()
def mock_connector() -> MagicMock:
    """Pytest fixture that returns a fresh mock connector."""
    return make_mock_connector()


# ---------------------------------------------------------------------------
# Diff / compare response helpers (reused across test files)
# ---------------------------------------------------------------------------

def diff_entry(
    *,
    old_path: str,
    new_path: str,
    deleted_file: bool = False,
    renamed_file: bool = False,
    new_file: bool = False,
) -> dict:
    """Return a dict-shaped GitLab compare diff entry (REST API shape)."""
    return {
        "old_path": old_path,
        "new_path": new_path,
        "deleted_file": deleted_file,
        "renamed_file": renamed_file,
        "new_file": new_file,
    }


def ok_compare(diffs: list) -> MagicMock:
    """Return a successful compare_commits mock response."""
    res = MagicMock()
    res.success = True
    res.data = {"diffs": diffs}
    res.error = None
    return res


def fail_compare(error: str = "not found") -> MagicMock:
    """Return a failed compare_commits mock response."""
    res = MagicMock()
    res.success = False
    res.data = None
    res.error = error
    return res


def tree_entry(
    path: str,
    *,
    name: str | None = None,
    sha: str = "sha-abc",
    entry_type: str = "blob",
) -> dict:
    """Return a repo-tree entry dict (list_repo_tree API shape)."""
    return {
        "path": path,
        "name": name or path.rsplit("/", 1)[-1],
        "id": sha,
        "type": entry_type,
    }


def branch_res(sha: str = "newhead00") -> MagicMock:
    """Return a successful branch-info mock response."""
    res = MagicMock()
    res.success = True
    res.error = None
    res.data = MagicMock()
    res.data.commit = {"id": sha}
    return res


def paged_res(data: list) -> MagicMock:
    """Return a successful paged_list mock response."""
    res = MagicMock()
    res.success = True
    res.data = data
    res.error = None
    return res


def failed_res(error: str = "error") -> MagicMock:
    """Return a failed paged_list / ds_call mock response."""
    res = MagicMock()
    res.success = False
    res.data = None
    res.error = error
    return res
