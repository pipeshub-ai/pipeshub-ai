# Copyright (c) 2026 PipesHub Inc.
# Licensed under the PipesHub Enterprise License. See the LICENSE file in this
# ee folder. Free for development, testing and evaluation. Production use needs
# a PipesHub licence key.

"""Without a licence key a company-wide ServiceNow, Slack, Outlook or Gmail connector reads no access through these
methods."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config.constants.arangodb import AccessRule
from app.connectors.sources.google.gmail.individual.connector import (
    GoogleGmailIndividualConnector,
)
from app.connectors.sources.google.gmail.team.connector import GoogleGmailTeamConnector
from app.connectors.sources.microsoft.outlook.connector import OutlookConnector
from app.connectors.sources.microsoft.outlook_individual.connector import (
    OutlookIndividualConnector,
)
from app.connectors.sources.servicenow.servicenow.common import _Listing
from app.connectors.sources.servicenow.servicenow.connector import ServiceNowConnector
from app.connectors.sources.slack.individual.connector import SlackIndividualConnector
from app.connectors.sources.slack.team.connector import SlackConnector
from app.ee.permission_sync.gmail_team import GmailWorkspacePermissionsMixin
from app.ee.permission_sync.outlook_team import OutlookPermissionsMixin
from app.ee.permission_sync.servicenow import ServiceNowPermissionsMixin
from app.ee.permission_sync.slack_team import SlackWorkspacePermissionsMixin
from app.models.permission import EntityType, Permission, PermissionType

# Every way the mixins reach the source or a checkpoint; the graph is reached through data_entities_processor.
SOURCE_ATTRS = (
    "admin_data_source", "external_users_client", "rate_limiter", "_fresh_datasource",
    "_iter_table", "_table_get", "_gone_at_source", "user_sync_point", "org_entity_sync_points",
)


def _connector(mixin: type, *, gated: bool) -> object:
    """The mixin alone, with every way it reaches the source, a checkpoint or the graph replaced by a mock."""
    connector = type("Connector", (mixin,), {})()
    connector.connector_id = "connector-1"
    connector.logger = MagicMock()
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    for name in SOURCE_ATTRS:
        setattr(connector, name, AsyncMock() if name.startswith("_") else MagicMock())
    return connector


def _assert_nothing_read(connector: object) -> None:
    connector.data_entities_processor.skips_permission_writes.assert_awaited_with("connector-1")
    for name in SOURCE_ATTRS:
        source = getattr(connector, name)
        assert source.method_calls == [] and source.call_count == 0, name
    # Only the gate was asked: nothing was written either.
    assert [call[0] for call in connector.data_entities_processor.mock_calls] == ["skips_permission_writes"]


def _article() -> MagicMock:
    return MagicMock(sys_id="article-1", kb_knowledge_base="kb-1", can_read_user_criteria="c-1",
                     cannot_read_user_criteria="c-2", ownership_group="g-1")


# Every gated method the free connector calls, and what it gets back when the gate holds.
ENTRY_POINTS = [
    # ServiceNow: groups, roles, organizational entities, admins, knowledge base and article criteria.
    (ServiceNowPermissionsMixin, "_sync_organizational_entities", (), None),
    (ServiceNowPermissionsMixin, "_sync_user_groups", (), _Listing()),
    (ServiceNowPermissionsMixin, "_fetch_all_groups", (), []),
    (ServiceNowPermissionsMixin, "_sync_roles", (), (set(), _Listing())),
    (ServiceNowPermissionsMixin, "_reconcile_user_groups", (set(), _Listing(), _Listing()), None),
    (ServiceNowPermissionsMixin, "_get_admin_users", (), set()),
    (ServiceNowPermissionsMixin, "_load_kb_criteria", (), None),
    (ServiceNowPermissionsMixin, "_fetch_user_criteria", (["c-1"],), []),
    (ServiceNowPermissionsMixin, "_kb_grants", (MagicMock(sys_id="kb-1", owner="u-1", kb_managers="u-2"), {"u-3"}), []),
    (ServiceNowPermissionsMixin, "_article_access", (_article(),), (True, AccessRule.OPEN, [])),
    # Slack: the workspace role, user groups, and each channel's members.
    (SlackWorkspacePermissionsMixin, "_sync_workspace_roles", (), None),
    (SlackWorkspacePermissionsMixin, "_sync_user_groups", (), None),
    (SlackWorkspacePermissionsMixin, "_channel_permissions", ({"id": "C1", "is_private": True},), []),
    (SlackWorkspacePermissionsMixin, "_channel_permissions", ({"id": "C2"},), []),
    (SlackWorkspacePermissionsMixin, "_channel_permissions", ({"id": "D1", "is_im": True, "user": "U1"},), []),
    # Outlook: Microsoft 365 group members, and the group's grant on its group mailbox.
    (OutlookPermissionsMixin, "_get_group_members", ("group-1",), []),
    (OutlookPermissionsMixin, "_get_user_groups", ("user-1",), []),
    (OutlookPermissionsMixin, "_group_mailbox_grants", ("group-1",), []),
    # Gmail: Google groups with their members.
    (GmailWorkspacePermissionsMixin, "_sync_user_groups", (), None),
]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("mixin", "name", "args", "expected"), ENTRY_POINTS,
    ids=[f"{e[0].__name__}.{e[1]}-{i}" for i, e in enumerate(ENTRY_POINTS)],
)
async def test_gated_connector_reads_nothing_from_the_source(
    mixin: type, name: str, args: tuple, expected: object,
) -> None:
    connector = _connector(mixin, gated=True)

    assert await getattr(connector, name)(*args) == expected

    _assert_nothing_read(connector)


@pytest.mark.asyncio
async def test_licensed_outlook_grants_the_group_its_mailbox() -> None:
    connector = _connector(OutlookPermissionsMixin, gated=False)

    grants = await connector._group_mailbox_grants("group-1")

    assert grants == [Permission(external_id="group-1", type=PermissionType.READ, entity_type=EntityType.GROUP)]


@pytest.mark.asyncio
async def test_licensed_outlook_still_reads_group_members() -> None:
    connector = _connector(OutlookPermissionsMixin, gated=False)
    connector.external_users_client.groups_list_transitive_members = AsyncMock(
        return_value=MagicMock(success=True, data=MagicMock(value=["member"], odata_next_link=None)),
    )

    assert await connector._get_group_members("group-1") == ["member"]


@pytest.mark.asyncio
async def test_licensed_gmail_still_lists_google_groups() -> None:
    connector = _connector(GmailWorkspacePermissionsMixin, gated=False)
    connector.admin_data_source.groups_list = AsyncMock(return_value={"groups": []})

    await connector._sync_user_groups()

    connector.admin_data_source.groups_list.assert_awaited_once()


@pytest.mark.asyncio
async def test_licensed_slack_public_channel_grants_the_workspace_role() -> None:
    connector = _connector(SlackWorkspacePermissionsMixin, gated=False)
    connector._fetch_channel_members = AsyncMock(return_value=[])
    connector._guest_user_ids = set()
    # The connector's own helper the licensed path ends with; no authenticator grant is set, so it adds none.
    connector._with_authenticator = SlackConnector._with_authenticator.__get__(connector)

    grants = await connector._channel_permissions({"id": "C2"})

    assert grants == [Permission(entity_type=EntityType.ROLE, external_id="workspace_member", type=PermissionType.READ)]


@pytest.mark.asyncio
async def test_licensed_servicenow_still_reads_kb_criteria() -> None:
    connector = _connector(ServiceNowPermissionsMixin, gated=False)
    connector._split_ids = ServiceNowConnector._split_ids  # free helper the licensed path uses
    connector._kb_criteria = AsyncMock(side_effect=RuntimeError("read"))

    with pytest.raises(RuntimeError):
        await connector._kb_grants(MagicMock(sys_id="kb-1", owner="", kb_managers=""), set())

    connector._kb_criteria.assert_awaited_once_with("kb-1")


@pytest.mark.parametrize(
    ("team", "mixin"),
    [
        (ServiceNowConnector, ServiceNowPermissionsMixin),
        (SlackConnector, SlackWorkspacePermissionsMixin),
        (OutlookConnector, OutlookPermissionsMixin),
        (GoogleGmailTeamConnector, GmailWorkspacePermissionsMixin),
    ],
)
def test_team_connectors_take_their_permission_reading_from_ee(team: type, mixin: type) -> None:
    assert mixin in team.__mro__


@pytest.mark.parametrize(
    ("personal", "mixin"),
    [
        (SlackIndividualConnector, SlackWorkspacePermissionsMixin),
        (OutlookIndividualConnector, OutlookPermissionsMixin),
        (GoogleGmailIndividualConnector, GmailWorkspacePermissionsMixin),
    ],
)
def test_personal_connectors_share_no_permission_reading(personal: type, mixin: type) -> None:
    assert mixin not in personal.__mro__
