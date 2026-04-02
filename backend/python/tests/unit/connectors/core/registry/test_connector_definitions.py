"""
Tests for connector registry definitions in app.connectors.core.registry.connector.

Covers instantiation and connect() for all connector classes:
- SlackConnector
- CalendarConnector
- MeetConnector
- DocsConnector
- SheetsConnector
- FormsConnector
- SlidesConnector
- AirtableConnector
- LinearConnector
- ZendeskConnector
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


class TestSlackConnector:
    def test_init(self):
        c = SlackConnector()
        assert c.name == "Slack"

    def test_connect(self, capsys):
        c = SlackConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Slack" in captured.out


class TestCalendarConnector:
    def test_init(self):
        c = CalendarConnector()
        assert c.name == "Calendar"

    def test_connect(self, capsys):
        c = CalendarConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Calendar" in captured.out


class TestMeetConnector:
    def test_init(self):
        c = MeetConnector()
        assert c.name == "Meet"

    def test_connect(self, capsys):
        c = MeetConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Meet" in captured.out


class TestDocsConnector:
    def test_init(self):
        c = DocsConnector()
        assert c.name == "Docs"

    def test_connect(self, capsys):
        c = DocsConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Docs" in captured.out


class TestSheetsConnector:
    def test_init(self):
        c = SheetsConnector()
        assert c.name == "Sheets"

    def test_connect(self, capsys):
        c = SheetsConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Sheets" in captured.out


class TestFormsConnector:
    def test_init(self):
        c = FormsConnector()
        assert c.name == "Forms"

    def test_connect(self, capsys):
        c = FormsConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Forms" in captured.out


class TestSlidesConnector:
    def test_init(self):
        c = SlidesConnector()
        assert c.name == "Slides"

    def test_connect(self, capsys):
        c = SlidesConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Slides" in captured.out


class TestAirtableConnector:
    def test_init(self):
        c = AirtableConnector()
        assert c.name == "Airtable"

    def test_connect(self, capsys):
        c = AirtableConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Airtable" in captured.out


class TestLinearConnector:
    def test_init(self):
        c = LinearConnector()
        assert c.name == "Linear"

    def test_connect(self, capsys):
        c = LinearConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Linear" in captured.out


class TestZendeskConnector:
    def test_init(self):
        c = ZendeskConnector()
        assert c.name == "Zendesk"

    def test_connect(self, capsys):
        c = ZendeskConnector()
        result = c.connect()
        assert result is True
        captured = capsys.readouterr()
        assert "Connecting to Zendesk" in captured.out
