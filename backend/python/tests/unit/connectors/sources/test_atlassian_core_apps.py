"""Tests for app.connectors.sources.atlassian.core.apps."""

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.sources.atlassian.core.apps import ConfluenceApp, JiraApp


class TestAtlassianApps:
    """Tests for Atlassian App classes."""

    def test_confluence_app(self):
        app = ConfluenceApp("conn-1")
        assert app.get_app_name() == Connectors.CONFLUENCE
        assert app.get_app_group_name() == AppGroups.ATLASSIAN
        assert app.get_connector_id() == "conn-1"

    def test_jira_app(self):
        app = JiraApp("conn-2")
        assert app.get_app_name() == Connectors.JIRA
        assert app.get_app_group_name() == AppGroups.ATLASSIAN
        assert app.get_connector_id() == "conn-2"
