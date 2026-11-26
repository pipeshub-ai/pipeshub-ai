"""Without a licence key a company-wide Zoom connector reads no meeting's audience from Zoom."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.connectors.sources.zoom.connector import ZoomConnector
from app.connectors.sources.zoom.models import ZoomMeetingDetail, ZoomParticipant
from app.ee.permission_sync.zoom import ZoomPermissionsMixin
from app.models.permission import PermissionType


def _connector(*, gated: bool) -> ZoomPermissionsMixin:
    connector = type("Connector", (ZoomPermissionsMixin,), {})()
    connector.connector_id = "connector-1"
    connector.logger = MagicMock()
    connector.data_entities_processor = MagicMock()
    connector.data_entities_processor.skips_permission_writes = AsyncMock(return_value=gated)
    connector._list_meeting_participants = AsyncMock(return_value=[ZoomParticipant(user_email="pat@example.com")])
    return connector


DETAIL = ZoomMeetingDetail.model_validate(
    {"settings": {"alternative_hosts": "alt@example.com", "meeting_invitees": [{"email": "inv@example.com"}]}}
)


@pytest.mark.asyncio
async def test_gated_meeting_grants_are_empty_and_participants_unread() -> None:
    connector = _connector(gated=True)

    # Empty, not an error: the meeting record is still written, and the processor keeps its stored grants.
    # Unpacked as the connector's meeting step unpacks it.
    grants, participants_read = await connector._build_meeting_permissions(DETAIL, "uuid", "host@example.com")
    assert (grants, participants_read) == ([], False)

    connector._list_meeting_participants.assert_not_awaited()
    connector.data_entities_processor.skips_permission_writes.assert_awaited_once_with("connector-1")


@pytest.mark.asyncio
async def test_licensed_meeting_grants_are_unchanged() -> None:
    connector = _connector(gated=False)

    grants, _participants_read = await connector._build_meeting_permissions(DETAIL, "uuid", "host@example.com")

    assert [(g.email, g.type) for g in grants] == [
        ("host@example.com", PermissionType.OWNER),
        ("alt@example.com", PermissionType.READ),
        ("pat@example.com", PermissionType.READ),
        ("inv@example.com", PermissionType.READ),
    ]
    connector._list_meeting_participants.assert_awaited_once()
    assert connector._list_meeting_participants.await_args.args[0] == "uuid"


def test_the_owner_grant_on_a_users_meetings_group_stays_free() -> None:
    # Whose meetings a group holds is not read from Zoom's sharing, so it is built by the free connector.
    assert "_build_record_group" in ZoomConnector.__dict__
    assert "_build_meeting_permissions" not in ZoomConnector.__dict__
