"""Tests for app.connectors.sources.microsoft.common.apps."""

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.sources.microsoft.common.apps import (
    MicrosoftAppGroup,
    MicrosoftTeamsApp,
    OneDriveApp,
    OutlookApp,
    OutlookCalendarApp,
    SharePointOnlineApp,
)


class TestMicrosoftApps:
    """Tests for Microsoft App classes."""

    def test_onedrive_app_name(self):
        app = OneDriveApp("conn-1")
        assert app.get_app_name() == Connectors.ONEDRIVE
        assert app.get_app_group_name() == AppGroups.MICROSOFT
        assert app.get_connector_id() == "conn-1"

    def test_sharepoint_app_name(self):
        app = SharePointOnlineApp("conn-2")
        assert app.get_app_name() == Connectors.SHAREPOINT_ONLINE
        assert app.get_app_group_name() == AppGroups.MICROSOFT

    def test_outlook_app_name(self):
        app = OutlookApp("conn-3")
        assert app.get_app_name() == Connectors.OUTLOOK
        assert app.get_app_group_name() == AppGroups.MICROSOFT

    def test_outlook_calendar_app_name(self):
        app = OutlookCalendarApp("conn-4")
        assert app.get_app_name() == Connectors.OUTLOOK_CALENDAR

    def test_microsoft_teams_app_name(self):
        app = MicrosoftTeamsApp("conn-5")
        assert app.get_app_name() == Connectors.MICROSOFT_TEAMS

    def test_microsoft_app_group(self):
        group = MicrosoftAppGroup("conn-6")
        assert group.get_app_group_name() == AppGroups.MICROSOFT
        assert len(group.apps) == 5
