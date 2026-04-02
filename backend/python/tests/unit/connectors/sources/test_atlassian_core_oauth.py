"""Tests for app.connectors.sources.atlassian.core.oauth."""

from app.connectors.sources.atlassian.core.oauth import AtlassianScope


class TestAtlassianScope:
    """Tests for AtlassianScope enum and helper methods."""

    def test_jira_basic_scopes(self):
        scopes = AtlassianScope.get_jira_basic()
        assert isinstance(scopes, list)
        assert len(scopes) >= 3
        assert "read:jira-work" in scopes
        assert "offline_access" in scopes

    def test_jira_read_access_scopes(self):
        scopes = AtlassianScope.get_jira_read_access()
        assert isinstance(scopes, list)
        assert "read:jira-work" in scopes
        assert "read:jira-user" in scopes
        assert "read:group:jira" in scopes
        assert "read:audit-log:jira" in scopes
        assert "offline_access" in scopes

    def test_confluence_basic_scopes(self):
        scopes = AtlassianScope.get_confluence_basic()
        assert isinstance(scopes, list)
        assert "read:confluence-content.all" in scopes
        assert "offline_access" in scopes

    def test_confluence_read_access_scopes(self):
        scopes = AtlassianScope.get_confluence_read_access()
        assert isinstance(scopes, list)
        assert "read:page:confluence" in scopes
        assert "read:space:confluence" in scopes
        assert "read:permission:confluence" in scopes
        assert "offline_access" in scopes
        assert "read:confluence-content.all" in scopes

    def test_full_access_includes_both_jira_and_confluence(self):
        scopes = AtlassianScope.get_full_access()
        assert isinstance(scopes, list)
        # Jira scopes
        assert "read:jira-work" in scopes
        assert "write:jira-work" in scopes
        # Confluence scopes
        assert "read:confluence-content.all" in scopes
        # Common scopes
        assert "read:account" in scopes
        assert "offline_access" in scopes

    def test_scope_enum_values(self):
        assert AtlassianScope.JIRA_WORK_READ.value == "read:jira-work"
        assert AtlassianScope.CONFLUENCE_CONTENT_READ.value == "read:confluence-content.all"
        assert AtlassianScope.OFFLINE_ACCESS.value == "offline_access"
