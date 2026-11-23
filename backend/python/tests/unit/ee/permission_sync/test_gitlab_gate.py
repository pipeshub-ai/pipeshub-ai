"""Without a licence key a company-wide GitLab connector reads no access from GitLab."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.gitlab.issues import IssuesSync
from app.connectors.sources.gitlab.projects import ProjectsSync
from app.connectors.sources.gitlab_personal.connector import GitLabPersonalProjectsSync
from app.ee.permission_sync.gitlab import GitLabProjectsPermissionsMixin

PROJECT = SimpleNamespace(id=42, name="p", path_with_namespace="g/p")


def _connector(*, gated: bool) -> MagicMock:
    c = MagicMock()
    c.connector_id = "connector-1"
    c._project_reader_ids = {}
    c.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    c.data_entities_processor.on_new_record_groups = AsyncMock()
    c.data_entities_processor.has_stored_permissions = AsyncMock(return_value=False)
    c.runtime.ds_call = AsyncMock(return_value=SimpleNamespace(success=True, data=[]))
    c.creator_user_permission = MagicMock(return_value=None)
    return c


def _projects(*, gated: bool) -> ProjectsSync:
    sync = ProjectsSync(_connector(gated=gated))
    sync._build_project_record_groups = MagicMock(return_value=tuple(f"group-{i}" for i in range(5)))
    return sync


@pytest.mark.asyncio
async def test_gated_project_is_written_as_containers_without_a_member_read() -> None:
    sync = _projects(gated=True)

    await sync._sync_project_members_as_pseudo(PROJECT)

    sync.c.runtime.ds_call.assert_not_awaited()
    sync.c.data_entities_processor.on_new_record_groups.assert_awaited_once_with(
        [(f"group-{i}", []) for i in range(5)]
    )
    # No readers were read, so a confidential issue is not given to anyone from a stale list either.
    assert sync.c._project_reader_ids == {}


@pytest.mark.asyncio
async def test_gated_group_members_are_not_read() -> None:
    sync = _projects(gated=True)

    # Empty, not None: None is "could not read", which this is not.
    assert await sync._group_member_permissions("g") == []

    sync.c.runtime.ds_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_gated_confidential_issue_gets_no_author_or_assignee_grant() -> None:
    issues = IssuesSync(_connector(gated=True))
    issue = SimpleNamespace(id=1, iid=1, project_id=42, author={"id": 5}, assignees=[{"id": 6}])

    assert await issues._confidential_exception_permissions(issue) == []

    issues.c.runtime.ds_call.assert_not_awaited()


@pytest.mark.asyncio
async def test_licensed_project_still_reads_its_members() -> None:
    sync = _projects(gated=False)

    await sync._sync_project_members_as_pseudo(PROJECT)

    sync.c.runtime.ds_call.assert_awaited_once()


def test_personal_connector_overrides_every_reading_entry_point() -> None:
    for name in ("_sync_project_members_as_pseudo",):
        assert getattr(GitLabPersonalProjectsSync, name) is not getattr(GitLabProjectsPermissionsMixin, name)
    # The group entry point stays free, but the personal one writes no member grants and reads no members.
    assert GitLabPersonalProjectsSync._ensure_gitlab_group_record_groups is not ProjectsSync._ensure_gitlab_group_record_groups
