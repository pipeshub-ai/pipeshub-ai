"""
Comprehensive tests for app.agents.constants.toolset_constants

Covers:
- ToolsetType enum values and string membership
- AuthType enum values and string membership
- ToolCategory enum values and string membership
- get_toolset_config_path
- get_toolset_instance_users_prefix
- get_user_toolsets_prefix (deprecated, returns "")
- get_toolset_instances_path
- get_toolset_oauth_config_path (delegates to normalize_toolset_type)
- normalize_app_name
- normalize_toolset_type
"""

import pytest

from app.agents.constants.toolset_constants import (
    AuthType,
    ToolCategory,
    ToolsetType,
    get_toolset_config_path,
    get_toolset_instance_users_prefix,
    get_toolset_instances_path,
    get_toolset_oauth_config_path,
    get_user_toolsets_prefix,
    normalize_app_name,
    normalize_toolset_type,
)


# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------

class TestToolsetTypeEnum:
    def test_values(self):
        assert ToolsetType.APP.value == "app"
        assert ToolsetType.FILE.value == "file"
        assert ToolsetType.WEB_SEARCH.value == "web_search"
        assert ToolsetType.DATABASE.value == "database"
        assert ToolsetType.UTILITY.value == "utility"

    def test_is_str_subclass(self):
        assert isinstance(ToolsetType.APP, str)

    def test_all_members(self):
        expected = {"APP", "FILE", "WEB_SEARCH", "DATABASE", "UTILITY"}
        assert set(ToolsetType.__members__.keys()) == expected


class TestAuthTypeEnum:
    def test_values(self):
        assert AuthType.OAUTH.value == "OAUTH"
        assert AuthType.API_TOKEN.value == "API_TOKEN"
        assert AuthType.BEARER_TOKEN.value == "BEARER_TOKEN"
        assert AuthType.USERNAME_PASSWORD.value == "USERNAME_PASSWORD"
        assert AuthType.NONE.value == "NONE"

    def test_is_str_subclass(self):
        assert isinstance(AuthType.OAUTH, str)

    def test_all_members(self):
        expected = {"OAUTH", "API_TOKEN", "BEARER_TOKEN", "USERNAME_PASSWORD", "NONE"}
        assert set(AuthType.__members__.keys()) == expected


class TestToolCategoryEnum:
    def test_values(self):
        assert ToolCategory.KNOWLEDGE.value == "knowledge"
        assert ToolCategory.ACTION.value == "action"
        assert ToolCategory.UTILITY.value == "utility"

    def test_is_str_subclass(self):
        assert isinstance(ToolCategory.KNOWLEDGE, str)

    def test_all_members(self):
        expected = {"KNOWLEDGE", "ACTION", "UTILITY"}
        assert set(ToolCategory.__members__.keys()) == expected


# ---------------------------------------------------------------------------
# Path helper tests
# ---------------------------------------------------------------------------

class TestGetToolsetConfigPath:
    def test_basic(self):
        result = get_toolset_config_path("inst-1", "user-42")
        assert result == "/services/toolsets/inst-1/user-42"

    def test_empty_strings(self):
        result = get_toolset_config_path("", "")
        assert result == "/services/toolsets//"

    def test_special_characters(self):
        result = get_toolset_config_path("inst/special", "user@org")
        assert result == "/services/toolsets/inst/special/user@org"


class TestGetToolsetInstanceUsersPrefix:
    def test_basic(self):
        result = get_toolset_instance_users_prefix("inst-abc")
        assert result == "/services/toolsets/inst-abc/"

    def test_empty_instance_id(self):
        result = get_toolset_instance_users_prefix("")
        assert result == "/services/toolsets//"


class TestGetUserToolsetsPrefix:
    def test_returns_empty_string(self):
        """Deprecated function always returns empty string."""
        assert get_user_toolsets_prefix("any-user") == ""

    def test_returns_empty_for_empty_user(self):
        assert get_user_toolsets_prefix("") == ""


class TestGetToolsetInstancesPath:
    def test_ignores_org_id(self):
        """Single-org mode ignores org_id."""
        result = get_toolset_instances_path("org-123")
        assert result == "/services/toolset-instances"

    def test_different_org_ids_same_result(self):
        assert get_toolset_instances_path("a") == get_toolset_instances_path("b")


class TestGetToolsetOauthConfigPath:
    def test_basic(self):
        result = get_toolset_oauth_config_path("jira")
        assert result == "/services/oauths/toolsets/jira"

    def test_normalizes_uppercase(self):
        result = get_toolset_oauth_config_path("SLACK")
        assert result == "/services/oauths/toolsets/slack"

    def test_normalizes_whitespace(self):
        result = get_toolset_oauth_config_path("  Jira  ")
        assert result == "/services/oauths/toolsets/jira"


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

class TestNormalizeAppName:
    def test_basic_lowercase(self):
        assert normalize_app_name("Slack") == "slack"

    def test_removes_spaces(self):
        assert normalize_app_name("Slack Workspace") == "slackworkspace"

    def test_removes_underscores(self):
        assert normalize_app_name("my_app") == "myapp"

    def test_removes_spaces_and_underscores(self):
        assert normalize_app_name("My Cool_App Name") == "mycoolappname"

    def test_already_normalized(self):
        assert normalize_app_name("jira") == "jira"

    def test_empty_string(self):
        assert normalize_app_name("") == ""

    def test_only_spaces_and_underscores(self):
        assert normalize_app_name("  _ _ ") == ""


class TestNormalizeToolsetType:
    def test_basic_lowercase(self):
        assert normalize_toolset_type("JIRA") == "jira"

    def test_strips_whitespace(self):
        assert normalize_toolset_type("  slack  ") == "slack"

    def test_combined(self):
        assert normalize_toolset_type("  Notion ") == "notion"

    def test_empty_string(self):
        assert normalize_toolset_type("") == ""

    def test_already_normalized(self):
        assert normalize_toolset_type("confluence") == "confluence"
