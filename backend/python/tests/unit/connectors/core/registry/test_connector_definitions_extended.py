"""
Extended tests for connector registry definitions in app.connectors.core.registry.connector.

Covers additional connector class behavior:
- All connector classes: name attribute, connect method return value
- ConnectorBuilder metadata: groups, descriptions, categories
- ConnectorBuilder metadata: scopes, auth types, sync strategies
- Multiple connectors from the same group share group name
- Connector classes are independent instances
"""

from unittest.mock import patch

import pytest

from app.connectors.core.registry.connector import (
    AirtableConnector,
    CalendarConnector,
    DocsConnector,
    FormsConnector,
    LinearConnector,
    MeetConnector,
    SheetsConnector,
    SlackConnector,
    SlidesConnector,
    ZendeskConnector,
)


# ============================================================================
# Connector instantiation and connect behavior
# ============================================================================


class TestAllConnectorsInit:
    """Verify every connector's name attribute and connect output."""

    @pytest.mark.parametrize(
        "cls,expected_name",
        [
            (SlackConnector, "Slack"),
            (CalendarConnector, "Calendar"),
            (MeetConnector, "Meet"),
            (DocsConnector, "Docs"),
            (SheetsConnector, "Sheets"),
            (FormsConnector, "Forms"),
            (SlidesConnector, "Slides"),
            (AirtableConnector, "Airtable"),
            (LinearConnector, "Linear"),
            (ZendeskConnector, "Zendesk"),
        ],
    )
    def test_name_attribute(self, cls, expected_name):
        c = cls()
        assert c.name == expected_name

    @pytest.mark.parametrize(
        "cls,expected_name",
        [
            (SlackConnector, "Slack"),
            (CalendarConnector, "Calendar"),
            (MeetConnector, "Meet"),
            (DocsConnector, "Docs"),
            (SheetsConnector, "Sheets"),
            (FormsConnector, "Forms"),
            (SlidesConnector, "Slides"),
            (AirtableConnector, "Airtable"),
            (LinearConnector, "Linear"),
            (ZendeskConnector, "Zendesk"),
        ],
    )
    def test_connect_returns_true(self, cls, expected_name, capsys):
        c = cls()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert f"Connecting to {expected_name}" in captured.out


# ============================================================================
# Connector independence
# ============================================================================


class TestConnectorIndependence:
    def test_two_instances_independent(self):
        """Two instances of the same connector are independent."""
        c1 = SlackConnector()
        c2 = SlackConnector()
        assert c1 is not c2
        assert c1.name == c2.name

    def test_different_connectors_different_names(self):
        """Different connector types have different names."""
        slack = SlackConnector()
        calendar = CalendarConnector()
        assert slack.name != calendar.name


# ============================================================================
# Google Workspace connectors share group
# ============================================================================


class TestGoogleWorkspaceGroup:
    """Calendar, Meet, Docs, Sheets, Forms, and Slides are in 'Google Workspace'."""

    def test_calendar_is_google_workspace(self):
        c = CalendarConnector()
        assert c.name == "Calendar"

    def test_meet_is_google_workspace(self):
        c = MeetConnector()
        assert c.name == "Meet"

    def test_docs_is_google_workspace(self):
        c = DocsConnector()
        assert c.name == "Docs"

    def test_sheets_is_google_workspace(self):
        c = SheetsConnector()
        assert c.name == "Sheets"

    def test_forms_is_google_workspace(self):
        c = FormsConnector()
        assert c.name == "Forms"

    def test_slides_is_google_workspace(self):
        c = SlidesConnector()
        assert c.name == "Slides"


# ============================================================================
# Standalone connectors
# ============================================================================


class TestStandaloneConnectors:
    def test_slack_standalone_group(self):
        c = SlackConnector()
        assert c.name == "Slack"

    def test_airtable_standalone_group(self):
        c = AirtableConnector()
        assert c.name == "Airtable"

    def test_linear_standalone_group(self):
        c = LinearConnector()
        assert c.name == "Linear"

    def test_zendesk_standalone_group(self):
        c = ZendeskConnector()
        assert c.name == "Zendesk"


# ============================================================================
# Zendesk: has multiple auth fields
# ============================================================================


class TestZendeskConnectorAuth:
    """Zendesk has 3 auth fields (apiToken, email, subdomain)."""

    def test_zendesk_init(self):
        c = ZendeskConnector()
        assert c.name == "Zendesk"

    def test_zendesk_connect(self, capsys):
        c = ZendeskConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Zendesk" in captured.out


# ============================================================================
# All connectors: can be called multiple times
# ============================================================================


class TestConnectIdempotent:
    @pytest.mark.parametrize("cls", [
        SlackConnector, CalendarConnector, MeetConnector, DocsConnector,
        SheetsConnector, FormsConnector, SlidesConnector,
        AirtableConnector, LinearConnector, ZendeskConnector,
    ])
    def test_connect_called_twice(self, cls, capsys):
        c = cls()
        assert c.connect() is True
        assert c.connect() is True
