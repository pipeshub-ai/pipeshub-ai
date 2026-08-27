"""Tests for app.modules.agents.context.connector_detection."""

import pytest

from app.modules.agents.context.connector_detection import (
    _has_clickup_tools,
    _has_confluence_tools,
    _has_connector_from_state,
    _has_github_tools,
    _has_jira_tools,
    _has_mariadb_tools,
    _has_onedrive_tools,
    _has_outlook_tools,
    _has_redshift_tools,
    _has_salesforce_tools,
    _has_sharepoint_tools,
    _has_slack_tools,
    _has_teams_tools,
    _has_zoom_tools,
    derive_active_connectors,
    has_connector,
)


class TestDeriveActiveConnectors:
    def test_empty_list(self):
        assert derive_active_connectors([]) == frozenset()

    def test_extracts_names_lowercased(self):
        toolsets = [{"name": "Jira"}, {"name": "Slack"}]
        result = derive_active_connectors(toolsets)
        assert result == frozenset({"jira", "slack"})

    def test_skips_non_dict_entries(self):
        toolsets = [{"name": "Jira"}, "not-a-dict", 42, None]
        result = derive_active_connectors(toolsets)
        assert result == frozenset({"jira"})

    def test_missing_name_key(self):
        toolsets = [{"type": "connector"}]
        result = derive_active_connectors(toolsets)
        assert result == frozenset({""})

    def test_returns_frozenset(self):
        result = derive_active_connectors([{"name": "Slack"}])
        assert isinstance(result, frozenset)

    def test_none_raises_type_error(self):
        with pytest.raises(TypeError):
            derive_active_connectors(None)


class TestHasConnector:
    def test_exact_match(self):
        connectors = frozenset({"jira"})
        assert has_connector(connectors, "jira") is True

    def test_substring_match(self):
        connectors = frozenset({"jira-cloud"})
        assert has_connector(connectors, "jira") is True

    def test_no_match(self):
        connectors = frozenset({"slack", "jira"})
        assert has_connector(connectors, "github") is False

    def test_empty_connectors(self):
        assert has_connector(frozenset(), "jira") is False

    def test_case_sensitive(self):
        connectors = frozenset({"jira"})
        assert has_connector(connectors, "Jira") is False


class TestHasConnectorFromState:
    def test_found(self):
        state = {"agent_toolsets": [{"name": "Jira Cloud"}]}
        assert _has_connector_from_state(state, "jira") is True

    def test_not_found(self):
        state = {"agent_toolsets": [{"name": "Slack"}]}
        assert _has_connector_from_state(state, "jira") is False

    def test_empty_toolsets(self):
        state = {"agent_toolsets": []}
        assert _has_connector_from_state(state, "jira") is False

    def test_missing_toolsets_key(self):
        state = {}
        assert _has_connector_from_state(state, "jira") is False


class TestIndividualConnectorHelpers:
    """Each legacy wrapper delegates to _has_connector_from_state correctly."""

    @pytest.fixture
    def _state_with(self):
        def _make(name: str) -> dict:
            return {"agent_toolsets": [{"name": name}]}
        return _make

    def test_jira(self, _state_with):
        assert _has_jira_tools(_state_with("Jira Cloud")) is True
        assert _has_jira_tools(_state_with("Slack")) is False

    def test_confluence(self, _state_with):
        assert _has_confluence_tools(_state_with("Confluence")) is True

    def test_slack(self, _state_with):
        assert _has_slack_tools(_state_with("Slack Bot")) is True

    def test_onedrive(self, _state_with):
        assert _has_onedrive_tools(_state_with("OneDrive")) is True

    def test_outlook(self, _state_with):
        assert _has_outlook_tools(_state_with("Outlook")) is True

    def test_teams(self, _state_with):
        assert _has_teams_tools(_state_with("Microsoft Teams")) is True

    def test_github(self, _state_with):
        assert _has_github_tools(_state_with("GitHub")) is True

    def test_mariadb(self, _state_with):
        assert _has_mariadb_tools(_state_with("MariaDB")) is True

    def test_zoom(self, _state_with):
        assert _has_zoom_tools(_state_with("Zoom")) is True

    def test_salesforce(self, _state_with):
        assert _has_salesforce_tools(_state_with("Salesforce CRM")) is True

    def test_clickup(self, _state_with):
        assert _has_clickup_tools(_state_with("ClickUp")) is True

    def test_redshift(self, _state_with):
        assert _has_redshift_tools(_state_with("Amazon Redshift")) is True

    def test_sharepoint(self, _state_with):
        assert _has_sharepoint_tools(_state_with("SharePoint Online")) is True
