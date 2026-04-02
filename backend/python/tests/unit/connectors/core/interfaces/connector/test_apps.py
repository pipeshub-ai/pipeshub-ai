"""
Comprehensive tests for app.connectors.core.interfaces.connector.apps

Covers:
- App class: __init__, get_app_name, get_app_group_name, get_connector_id
- AppGroup class: __init__, get_app_group_name, get_connector_id, apps attribute
"""

import pytest
from unittest.mock import MagicMock

from app.config.constants.arangodb import AppGroups, Connectors
from app.connectors.core.interfaces.connector.apps import App, AppGroup


# ---------------------------------------------------------------------------
# App class tests
# ---------------------------------------------------------------------------

class TestApp:
    """Tests for the App data class."""

    def test_init_stores_attributes(self):
        app = App(
            app_name=Connectors.GOOGLE_DRIVE,
            app_group_name=AppGroups.GOOGLE_WORKSPACE,
            connector_id="conn-123",
        )
        assert app.app_name is Connectors.GOOGLE_DRIVE
        assert app.app_group_name is AppGroups.GOOGLE_WORKSPACE
        assert app.connector_id == "conn-123"

    def test_get_app_name(self):
        app = App(
            app_name=Connectors.SLACK,
            app_group_name=AppGroups.NOTION,
            connector_id="c1",
        )
        assert app.get_app_name() is Connectors.SLACK

    def test_get_app_group_name(self):
        app = App(
            app_name=Connectors.JIRA,
            app_group_name=AppGroups.ATLASSIAN,
            connector_id="c2",
        )
        assert app.get_app_group_name() is AppGroups.ATLASSIAN

    def test_get_connector_id(self):
        app = App(
            app_name=Connectors.ONEDRIVE,
            app_group_name=AppGroups.MICROSOFT,
            connector_id="abc-def-456",
        )
        assert app.get_connector_id() == "abc-def-456"

    def test_multiple_apps_independent(self):
        app1 = App(Connectors.GOOGLE_DRIVE, AppGroups.GOOGLE_WORKSPACE, "id1")
        app2 = App(Connectors.OUTLOOK, AppGroups.MICROSOFT, "id2")
        assert app1.get_app_name() is not app2.get_app_name()
        assert app1.get_connector_id() != app2.get_connector_id()

    def test_connector_id_empty_string(self):
        app = App(Connectors.NOTION, AppGroups.NOTION, "")
        assert app.get_connector_id() == ""


# ---------------------------------------------------------------------------
# AppGroup class tests
# ---------------------------------------------------------------------------

class TestAppGroup:
    """Tests for the AppGroup class."""

    def test_init_stores_attributes(self):
        apps = [
            App(Connectors.GOOGLE_DRIVE, AppGroups.GOOGLE_WORKSPACE, "c1"),
            App(Connectors.GOOGLE_MAIL, AppGroups.GOOGLE_WORKSPACE, "c2"),
        ]
        group = AppGroup(
            app_group_name=AppGroups.GOOGLE_WORKSPACE,
            apps=apps,
            connector_id="group-conn-1",
        )
        assert group.app_group_name is AppGroups.GOOGLE_WORKSPACE
        assert group.apps is apps
        assert group.connector_id == "group-conn-1"

    def test_get_app_group_name(self):
        group = AppGroup(
            app_group_name=AppGroups.ATLASSIAN,
            apps=[],
            connector_id="g1",
        )
        assert group.get_app_group_name() is AppGroups.ATLASSIAN

    def test_get_connector_id(self):
        group = AppGroup(
            app_group_name=AppGroups.MICROSOFT,
            apps=[],
            connector_id="ms-conn",
        )
        assert group.get_connector_id() == "ms-conn"

    def test_apps_list_empty(self):
        group = AppGroup(AppGroups.DROPBOX, [], "d1")
        assert group.apps == []

    def test_apps_list_with_multiple_apps(self):
        app1 = App(Connectors.CONFLUENCE, AppGroups.ATLASSIAN, "a1")
        app2 = App(Connectors.JIRA, AppGroups.ATLASSIAN, "a2")
        group = AppGroup(AppGroups.ATLASSIAN, [app1, app2], "atl-conn")
        assert len(group.apps) == 2
        assert group.apps[0].get_app_name() is Connectors.CONFLUENCE
        assert group.apps[1].get_app_name() is Connectors.JIRA

    def test_connector_id_empty_string(self):
        group = AppGroup(AppGroups.NOTION, [], "")
        assert group.get_connector_id() == ""
