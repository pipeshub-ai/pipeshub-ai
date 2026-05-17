"""Tests for app.connectors.sources.atlassian.core.apps."""

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.sources.atlassian.core.apps import (
    ConfluenceApp,
    ConfluenceDataCenterApp,
    JiraApp,
)


class TestAtlassianApps:
    """Tests for Atlassian App classes."""

    def test_confluence_app(self):
        app = ConfluenceApp("conn-1")
        assert app.get_app_name() == Connectors.CONFLUENCE
        assert app.get_app_group_name() == AppGroups.ATLASSIAN
        assert app.get_connector_id() == "conn-1"

    def test_confluence_data_center_app(self):
        app = ConfluenceDataCenterApp("conn-dc-1")
        assert app.get_app_name() == Connectors.CONFLUENCE_DATA_CENTER
        assert app.get_app_group_name() == AppGroups.ATLASSIAN
        assert app.get_connector_id() == "conn-dc-1"

    def test_jira_app(self):
        app = JiraApp("conn-2")
        assert app.get_app_name() == Connectors.JIRA
        assert app.get_app_group_name() == AppGroups.ATLASSIAN
        assert app.get_connector_id() == "conn-2"
