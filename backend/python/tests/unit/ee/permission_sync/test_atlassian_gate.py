"""Without a licence key a company-wide Jira or Confluence connector reads no access from the source."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.atlassian.confluence_datacenter_personal.connector import (
    ConfluenceDataCenterPersonalConnector,
)
from app.connectors.sources.atlassian.jira_cloud_personal.connector import (
    JiraCloudPersonalConnector,
)
from app.connectors.sources.atlassian.jira_data_center_personal.connector import (
    JiraDataCenterPersonalConnector,
)
from app.ee.permission_sync.confluence_cloud import ConfluenceCloudPermissionsMixin
from app.ee.permission_sync.confluence_datacenter import (
    ConfluenceDataCenterPermissionsMixin,
)
from app.ee.permission_sync.jira_cloud import JiraCloudPermissionsMixin
from app.ee.permission_sync.jira_data_center import (
    GroupMemberships,
    JiraDataCenterPermissionsMixin,
)
from app.ee.permission_sync.jira_issue_security import (
    IssueSecurityContext,
    SchemeKnowledge,
    plan_level_reread,
)

NOT_ENFORCED = IssueSecurityContext(knowledge=SchemeKnowledge.NONE, enforce=False)
UNLICENSED = IssueSecurityContext(knowledge=SchemeKnowledge.NONE, enforce=False, unlicensed=True, browse_holders=None)


def _connector(mixin: type, *, gated: bool) -> object:
    """The mixin alone, with every way it reaches the source or the graph replaced by a mock."""
    connector = type("Connector", (mixin,), {"_enforces_issue_security": True})()
    connector.connector_id = "connector-1"
    connector.logger = MagicMock()
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    connector.data_source = MagicMock()
    connector._get_fresh_datasource = AsyncMock()
    connector._call_with_retry = AsyncMock()
    connector._fetch_group_members = AsyncMock()
    connector.notify = AsyncMock()
    return connector


def _assert_nothing_read(connector: object) -> None:
    connector.data_entities_processor.skips_permission_writes.assert_awaited_with("connector-1")
    assert connector.data_source.method_calls == []  # ``if not self.data_source`` is not a read
    connector._get_fresh_datasource.assert_not_awaited()
    connector._call_with_retry.assert_not_awaited()
    connector._fetch_group_members.assert_not_awaited()
    connector.notify.assert_not_awaited()
    # Only the gate was asked: nothing was written either.
    assert [call[0] for call in connector.data_entities_processor.mock_calls] == ["skips_permission_writes"]


# Every method the free connector calls that reads the source, and what it gets back when the gate
# holds: no grants (the processor keeps the stored ones), no groups or roles, and issue security not read.
JIRA = [
    ("_fetch_project_permission_scheme", ("KEY", {}, {}), []),
    ("_sync_project_roles", (["KEY"], [], {}), None),
    ("_sync_project_lead_roles", ([{"key": "KEY", "lead": {"accountId": "a"}}], []), None),
    ("_load_issue_security", ("KEY",), UNLICENSED),
]
CONFLUENCE = [
    ("_sync_user_groups", (), None),
    ("_sync_permission_changes_from_audit_log", (), None),
    ("_fetch_page_permissions", ("page-1",), []),
]
ENTRY_POINTS = [
    *((JiraCloudPermissionsMixin, *entry) for entry in JIRA),
    (JiraCloudPermissionsMixin, "_sync_user_groups", ([],), {}),
    *((JiraDataCenterPermissionsMixin, *entry) for entry in JIRA),
    (JiraDataCenterPermissionsMixin, "_sync_user_groups", ([],), GroupMemberships({})),
    *((ConfluenceCloudPermissionsMixin, *entry) for entry in CONFLUENCE),
    (ConfluenceCloudPermissionsMixin, "_fetch_space_permissions", ("space-1", "Space"), []),
    *((ConfluenceDataCenterPermissionsMixin, *entry) for entry in CONFLUENCE),
    (ConfluenceDataCenterPermissionsMixin, "_fetch_space_permissions", ("KEY", "Space", "space-1"), []),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mixin", "name", "args", "expected"), ENTRY_POINTS, ids=[f"{e[0].__name__}.{e[1]}" for e in ENTRY_POINTS],
)
async def test_gated_connector_reads_nothing_from_the_source(
    mixin: type, name: str, args: tuple, expected: object,
) -> None:
    connector = _connector(mixin, gated=True)

    assert await getattr(connector, name)(*args) == expected

    _assert_nothing_read(connector)


@pytest.mark.asyncio
async def test_gated_jira_cloud_forgets_the_groups_and_roles_of_an_earlier_read() -> None:
    connector = _connector(JiraCloudPermissionsMixin, gated=True)
    connector._group_ids_by_name = {"old": "1"}
    connector._group_members = {"1": []}
    connector._role_ids = {"old": "2"}
    connector._role_members = {"2": []}

    await connector._sync_user_groups([])
    await connector._sync_project_roles(["KEY"], [], {})

    assert (connector._group_ids_by_name, connector._group_members) == ({}, {})
    assert (connector._role_ids, connector._role_members) == ({}, {})


@pytest.mark.asyncio
async def test_gated_confluence_cloud_does_not_call_the_last_group_read_complete() -> None:
    connector = _connector(ConfluenceCloudPermissionsMixin, gated=True)
    connector._group_read_complete = True  # a licensed sync earlier in this process

    await connector._sync_user_groups()

    assert connector._group_read_complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mixin", [JiraCloudPermissionsMixin, JiraDataCenterPermissionsMixin])
async def test_licensed_jira_still_reads_issue_security(mixin: type) -> None:
    connector = _connector(mixin, gated=False)
    connector._scheme_members = {}
    response = MagicMock(status=404)
    response.json.return_value = {"errorMessages": ["no scheme"]}
    connector._call_with_retry.return_value = response
    connector._security_read = AsyncMock(return_value=(404, {"errorMessages": ["no scheme"]}))

    context = await connector._load_issue_security("KEY")

    assert context.enforce is True
    assert connector._call_with_retry.await_count + connector._security_read.await_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mixin", [ConfluenceCloudPermissionsMixin, ConfluenceDataCenterPermissionsMixin])
async def test_licensed_confluence_still_reads_page_restrictions(mixin: type) -> None:
    connector = _connector(mixin, gated=False)
    response = MagicMock(status=200)
    response.json.return_value = {"viewContentRestrictions": {"results": []}}
    datasource = MagicMock()
    datasource.get_page_relevant_view_restrictions_v1 = AsyncMock(return_value=response)
    connector._get_fresh_datasource.return_value = datasource
    connector._read_all_restrictions = AsyncMock(return_value=[])

    assert await connector._fetch_page_permissions("page-1") == []

    assert connector._read_all_restrictions.await_count + datasource.get_page_relevant_view_restrictions_v1.await_count == 1


@pytest.mark.parametrize(
    ("personal", "mixin"),
    [
        (JiraCloudPersonalConnector, JiraCloudPermissionsMixin),
        (JiraDataCenterPersonalConnector, JiraDataCenterPermissionsMixin),
    ],
)
def test_personal_jira_overrides_stay_in_the_free_tree_and_win(personal: type, mixin: type) -> None:
    mro = personal.__mro__
    assert mro.index(personal) < mro.index(mixin)
    assert personal._enforces_issue_security is False
    # The overrides that keep a personal connector from reading schemes, groups and roles.
    for name in ("run_sync", "_fetch_projects"):
        assert next(cls for cls in mro if name in vars(cls)) is personal
        assert name not in vars(mixin)


@pytest.mark.asyncio
@pytest.mark.parametrize("personal", [JiraCloudPersonalConnector, JiraDataCenterPersonalConnector])
async def test_personal_jira_enforces_no_issue_security_without_asking_the_gate(personal: type) -> None:
    connector = object.__new__(personal)
    connector.connector_id = "connector-1"
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=False)
    connector._call_with_retry = AsyncMock()
    connector._get_fresh_datasource = AsyncMock()

    assert await connector._load_issue_security("KEY") == NOT_ENFORCED

    connector.data_entities_processor.skips_permission_writes.assert_not_awaited()
    connector._call_with_retry.assert_not_awaited()
    connector._get_fresh_datasource.assert_not_awaited()


def test_personal_confluence_data_center_shares_no_permission_reading() -> None:
    assert ConfluenceDataCenterPermissionsMixin not in ConfluenceDataCenterPersonalConnector.__mro__


@pytest.mark.parametrize("stored", [{"10": "abc", "*": "retry"}, {}, None])
@pytest.mark.parametrize("is_new_project", [False, True])
def test_unread_issue_security_keeps_the_stored_levels(stored: dict | None, is_new_project: bool) -> None:
    """Unlicensed, the levels are not read. "No scheme" would re-read every stored issue and save an empty map; this
    re-reads nothing and saves back what is stored, so the first licensed read compares against it."""
    assert plan_level_reread(UNLICENSED, stored, is_new_project=is_new_project, app_roles_mapping=None) == (
        None, None, dict(stored or {}))
    assert UNLICENSED.browse_fingerprint() == "?"  # holders unknown: the first licensed read re-reads what they name
    if stored and not is_new_project:
        assert plan_level_reread(NOT_ENFORCED, stored, is_new_project=False, app_roles_mapping=None)[0] == "project"
