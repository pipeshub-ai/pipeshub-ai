"""Unit tests for app.modules.agents.context.retrieval_routing."""

from unittest.mock import MagicMock

from app.modules.agents.context.retrieval_routing import build_routing_guidance


class TestBuildRoutingGuidance:
    def _mock_catalog(self, dups):
        catalog = MagicMock()
        catalog.duplicate_apps.return_value = dups
        return catalog

    def test_no_duplicates_returns_empty(self):
        assert build_routing_guidance(self._mock_catalog([])) == ""

    def test_single_duplicate(self):
        result = build_routing_guidance(self._mock_catalog(["Slack"]))
        assert "Slack" in result
        assert "Multiple connectors" in result
        assert "⚠️" in result

    def test_multiple_duplicates_sorted(self):
        result = build_routing_guidance(self._mock_catalog(["Jira", "Google Drive", "Slack"]))
        idx_google = result.index("Google Drive")
        idx_jira = result.index("Jira")
        idx_slack = result.index("Slack")
        assert idx_google < idx_jira < idx_slack

    def test_empty_set_returns_empty(self):
        assert build_routing_guidance(self._mock_catalog(set())) == ""
