"""link_new_users runs each connector's own user steps and leaves a sync's bookkeeping alone."""
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.core.base.connector.connector_service import BaseConnector
from app.connectors.sources.atlassian.confluence_cloud.connector import (
    ConfluenceConnector,
)
from app.connectors.sources.atlassian.confluence_datacenter.connector import (
    ConfluenceDataCenterConnector,
)
from app.connectors.sources.atlassian.jira_cloud.connector import JiraConnector
from app.connectors.sources.atlassian.jira_data_center.connector import (
    JiraDataCenterConnector,
)


@pytest.mark.asyncio
async def test_no_steps_is_a_noop() -> None:
    c = MagicMock(user_link_steps=())
    assert await BaseConnector.link_new_users(c) is None


@pytest.mark.asyncio
async def test_steps_run_in_order() -> None:
    calls: list[str] = []
    c = MagicMock(user_link_steps=("a", "b"))
    c.a = AsyncMock(side_effect=lambda: calls.append("a"))
    c.b = AsyncMock(side_effect=lambda: calls.append("b"))
    await BaseConnector.link_new_users(c)
    assert calls == ["a", "b"]


def test_confluence_data_center_links_with_its_user_and_group_steps() -> None:
    assert ConfluenceDataCenterConnector.user_link_steps == ("_sync_users", "_sync_user_groups")


def test_confluence_cloud_withdraws_inactive_users_as_its_last_link_step() -> None:
    assert ConfluenceConnector.user_link_steps == ("_sync_users", "_sync_user_groups", "_reconcile_inactive_app_users")


def _jira(cls: type) -> MagicMock:
    c = MagicMock()
    c._keep_sync_state = lambda *names: BaseConnector._keep_sync_state(c, *names)
    c._SYNC_STATE = cls._SYNC_STATE
    c._kept_unread_access = True
    c._kept_after_refusal = [("projects", "")]
    c._user_read_unfinished = True
    c._user_bulk_forbidden = True
    c._group_bulk_forbidden = False
    c._app_roles_unread = True
    c._projects_naming_unread_roles = {"P"}
    c._dc_name_to_source_id = {"x": "y"}

    async def fetch() -> list:
        c._kept_unread_access = c._user_read_unfinished = False  # what a fetch does to the flags
        c._kept_after_refusal.append(("x", "y"))
        return ["u"]

    c._fetch_users = fetch
    c._app_roles_mapping = None
    c._fetch_application_roles_to_groups_mapping = AsyncMock(return_value={"jira-software": []})

    async def licensed(users: list, mapping: dict) -> list:
        return [u for u in users if u == "u"]

    c._keep_licensed_users = licensed
    c._sync_user_groups = AsyncMock(return_value={"g": ["u"]})
    c._project_keys_filter = lambda: (None, None)
    c._list_projects_with_filter = AsyncMock(return_value=[{"key": "P"}])
    c._fetch_filtered_projects = AsyncMock(return_value=([(MagicMock(short_name="P"), None)], [{"key": "P"}]))
    c._sync_project_roles = AsyncMock()
    c._sync_project_lead_roles = AsyncMock()
    c.data_entities_processor.on_new_app_users = AsyncMock()
    return c


@pytest.mark.asyncio
async def test_jira_cloud_pass_writes_users_groups_roles_and_keeps_the_sync_state() -> None:
    c = _jira(JiraConnector)
    await JiraConnector._link_users_groups_and_roles(c)
    c.data_entities_processor.on_new_app_users.assert_awaited_once_with(["u"])
    c._sync_user_groups.assert_awaited_once_with(["u"])
    c._sync_project_roles.assert_awaited_once_with(["P"], ["u"], {"g": ["u"]})
    c._sync_project_lead_roles.assert_awaited_once_with([{"key": "P"}], ["u"])
    c._fetch_filtered_projects.assert_not_awaited()  # no permission scheme reads in a link pass
    assert (c._kept_unread_access, c._user_read_unfinished, c._app_roles_unread) == (True, True, True)
    assert c._kept_after_refusal == [("projects", "")]
    assert c._projects_naming_unread_roles == {"P"}
    assert c._app_roles_mapping is None  # the pass leaves the full sync's mapping as it found it


@pytest.mark.asyncio
async def test_jira_cloud_pass_links_only_users_with_product_access() -> None:
    c = _jira(JiraConnector)
    c._fetch_users = AsyncMock(return_value=["u", "unlicensed"])
    await JiraConnector._link_users_groups_and_roles(c)
    c.data_entities_processor.on_new_app_users.assert_awaited_once_with(["u"])
    c._sync_user_groups.assert_awaited_once_with(["u"])


@pytest.mark.asyncio
async def test_jira_cloud_pass_keeps_stored_roles_when_groups_were_unreadable() -> None:
    c = _jira(JiraConnector)
    c._sync_user_groups = AsyncMock(return_value=None)
    await JiraConnector._link_users_groups_and_roles(c)
    c._sync_project_roles.assert_not_awaited()


@pytest.mark.asyncio
async def test_jira_data_center_pass_keeps_the_sync_state() -> None:
    c = _jira(JiraDataCenterConnector)
    await JiraDataCenterConnector._link_users_and_groups(c)
    c._sync_user_groups.assert_awaited_once_with(["u"])
    assert (c._kept_unread_access, c._user_read_unfinished) == (True, True)
    assert c._kept_after_refusal == [("projects", "")]
    assert c._dc_name_to_source_id == {"x": "y"}
