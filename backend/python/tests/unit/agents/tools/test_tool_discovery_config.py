"""
Comprehensive tests for app.agents.tools.config

Covers:
- ToolCategory enum (all members)
- ToolMetadata model (defaults, required fields, custom values)
- AppConfiguration model (defaults, custom values)
- ToolDiscoveryConfig class methods:
    get_app_config, is_essential_tool, add_app_config,
    disable_app, enable_app, get_enabled_apps
- APP_CONFIGS, ESSENTIAL_TOOL_PATTERNS, SKIP_FILES class attributes
"""

import pytest
from copy import deepcopy

from app.agents.tools.config import (
    AppConfiguration,
    ToolCategory,
    ToolDiscoveryConfig,
    ToolMetadata,
)


# ---------------------------------------------------------------------------
# ToolCategory enum
# ---------------------------------------------------------------------------

class TestToolCategory:
    def test_all_values(self):
        assert ToolCategory.COMMUNICATION.value == "communication"
        assert ToolCategory.PROJECT_MANAGEMENT.value == "project_management"
        assert ToolCategory.DOCUMENTATION.value == "documentation"
        assert ToolCategory.CALENDAR.value == "calendar"
        assert ToolCategory.FILE_STORAGE.value == "file_storage"
        assert ToolCategory.CODE_MANAGEMENT.value == "code_management"
        assert ToolCategory.CODE_EXECUTION.value == "code_execution"
        assert ToolCategory.UTILITY.value == "utility"
        assert ToolCategory.SEARCH.value == "search"
        assert ToolCategory.KNOWLEDGE.value == "knowledge"

    def test_member_count(self):
        # CODE_EXECUTION was added to host sandbox / coding tools
        # (coding_sandbox, database_sandbox, image_generator). Keep this
        # assertion pinned to the enum size so adding a new category
        # without updating the tests fails loudly.
        assert len(ToolCategory) == 10


# ---------------------------------------------------------------------------
# ToolMetadata model
# ---------------------------------------------------------------------------

class TestToolMetadata:
    def test_required_fields(self):
        meta = ToolMetadata(
            app_name="slack",
            tool_name="send_message",
            description="Send a message",
            category=ToolCategory.COMMUNICATION,
        )
        assert meta.app_name == "slack"
        assert meta.tool_name == "send_message"
        assert meta.description == "Send a message"
        assert meta.category is ToolCategory.COMMUNICATION

    def test_defaults(self):
        meta = ToolMetadata(
            app_name="a",
            tool_name="t",
            description="d",
            category=ToolCategory.UTILITY,
        )
        assert meta.is_essential is False
        assert meta.requires_auth is True
        assert meta.dependencies == []
        assert meta.tags == []

    def test_custom_optional_fields(self):
        meta = ToolMetadata(
            app_name="jira",
            tool_name="create_ticket",
            description="Creates a Jira ticket",
            category=ToolCategory.PROJECT_MANAGEMENT,
            is_essential=True,
            requires_auth=False,
            dependencies=["auth_tool"],
            tags=["jira", "project"],
        )
        assert meta.is_essential is True
        assert meta.requires_auth is False
        assert meta.dependencies == ["auth_tool"]
        assert meta.tags == ["jira", "project"]


# ---------------------------------------------------------------------------
# AppConfiguration model
# ---------------------------------------------------------------------------

class TestAppConfiguration:
    def test_required_field_only(self):
        cfg = AppConfiguration(app_name="test_app")
        assert cfg.app_name == "test_app"
        assert cfg.enabled is True
        assert cfg.subdirectories == []
        assert cfg.client_builder is None
        assert cfg.service_configs == {}

    def test_full_config(self):
        cfg = AppConfiguration(
            app_name="google",
            enabled=False,
            subdirectories=["gmail", "calendar"],
            client_builder="GoogleClient",
            service_configs={"gmail": {"version": "v1"}},
        )
        assert cfg.app_name == "google"
        assert cfg.enabled is False
        assert cfg.subdirectories == ["gmail", "calendar"]
        assert cfg.client_builder == "GoogleClient"
        assert cfg.service_configs == {"gmail": {"version": "v1"}}


# ---------------------------------------------------------------------------
# ToolDiscoveryConfig class
# ---------------------------------------------------------------------------

class TestToolDiscoveryConfigAttributes:
    def test_app_configs_dict_is_populated(self):
        assert isinstance(ToolDiscoveryConfig.APP_CONFIGS, dict)
        assert len(ToolDiscoveryConfig.APP_CONFIGS) > 0

    def test_known_apps_present(self):
        for name in ["confluence", "jira", "slack", "notion", "google", "microsoft"]:
            assert name in ToolDiscoveryConfig.APP_CONFIGS

    def test_google_has_subdirectories(self):
        google = ToolDiscoveryConfig.APP_CONFIGS["google"]
        assert "gmail" in google.subdirectories
        assert "calendar" in google.subdirectories
        assert "drive" in google.subdirectories
        assert "meet" in google.subdirectories

    def test_google_service_configs(self):
        google = ToolDiscoveryConfig.APP_CONFIGS["google"]
        assert "gmail" in google.service_configs
        assert google.service_configs["gmail"]["service_name"] == "gmail"
        assert google.service_configs["gmail"]["version"] == "v1"

    def test_microsoft_has_subdirectories(self):
        ms = ToolDiscoveryConfig.APP_CONFIGS["microsoft"]
        assert "one_drive" in ms.subdirectories
        assert "sharepoint" in ms.subdirectories

    def test_essential_tool_patterns(self):
        assert "calculator." in ToolDiscoveryConfig.ESSENTIAL_TOOL_PATTERNS
        assert "web_search" in ToolDiscoveryConfig.ESSENTIAL_TOOL_PATTERNS
        assert "get_current_datetime" in ToolDiscoveryConfig.ESSENTIAL_TOOL_PATTERNS
        assert "retrieval.search_internal_knowledge" in ToolDiscoveryConfig.ESSENTIAL_TOOL_PATTERNS

    def test_skip_files(self):
        assert "__init__.py" in ToolDiscoveryConfig.SKIP_FILES
        assert "config.py" in ToolDiscoveryConfig.SKIP_FILES
        assert "base.py" in ToolDiscoveryConfig.SKIP_FILES


class TestGetAppConfig:
    def test_existing_app(self):
        cfg = ToolDiscoveryConfig.get_app_config("slack")
        assert cfg is not None
        assert cfg.app_name == "slack"
        assert cfg.client_builder == "SlackClient"

    def test_nonexistent_app(self):
        cfg = ToolDiscoveryConfig.get_app_config("nonexistent_app_xyz")
        assert cfg is None


class TestIsEssentialTool:
    def test_calculator_tool(self):
        assert ToolDiscoveryConfig.is_essential_tool("calculator.add") is True

    def test_web_search(self):
        assert ToolDiscoveryConfig.is_essential_tool("web_search") is True

    def test_get_current_datetime(self):
        assert ToolDiscoveryConfig.is_essential_tool("utility.get_current_datetime") is True

    def test_retrieval_search(self):
        assert ToolDiscoveryConfig.is_essential_tool("retrieval.search_internal_knowledge") is True

    def test_non_essential(self):
        assert ToolDiscoveryConfig.is_essential_tool("slack.send_message") is False

    def test_empty_name(self):
        assert ToolDiscoveryConfig.is_essential_tool("") is False

    def test_partial_match_not_matching(self):
        assert ToolDiscoveryConfig.is_essential_tool("not_a_calculator") is False


class TestAddAppConfig:
    def test_add_new_config(self):
        # Save original and restore after test
        original = ToolDiscoveryConfig.APP_CONFIGS.copy()
        try:
            new_cfg = AppConfiguration(
                app_name="test_new_app",
                client_builder="TestClient",
            )
            ToolDiscoveryConfig.add_app_config(new_cfg)
            assert "test_new_app" in ToolDiscoveryConfig.APP_CONFIGS
            assert ToolDiscoveryConfig.APP_CONFIGS["test_new_app"].client_builder == "TestClient"
        finally:
            ToolDiscoveryConfig.APP_CONFIGS = original

    def test_update_existing_config(self):
        original = ToolDiscoveryConfig.APP_CONFIGS.copy()
        try:
            updated = AppConfiguration(
                app_name="slack",
                client_builder="NewSlackClient",
                enabled=False,
            )
            ToolDiscoveryConfig.add_app_config(updated)
            assert ToolDiscoveryConfig.APP_CONFIGS["slack"].client_builder == "NewSlackClient"
            assert ToolDiscoveryConfig.APP_CONFIGS["slack"].enabled is False
        finally:
            ToolDiscoveryConfig.APP_CONFIGS = original


class TestDisableApp:
    def test_disable_existing_app(self):
        original_enabled = ToolDiscoveryConfig.APP_CONFIGS["jira"].enabled
        try:
            ToolDiscoveryConfig.disable_app("jira")
            assert ToolDiscoveryConfig.APP_CONFIGS["jira"].enabled is False
        finally:
            ToolDiscoveryConfig.APP_CONFIGS["jira"].enabled = original_enabled

    def test_disable_nonexistent_app_no_error(self):
        # Should not raise
        ToolDiscoveryConfig.disable_app("completely_unknown_app")


class TestEnableApp:
    def test_enable_existing_app(self):
        original_enabled = ToolDiscoveryConfig.APP_CONFIGS["jira"].enabled
        try:
            ToolDiscoveryConfig.APP_CONFIGS["jira"].enabled = False
            ToolDiscoveryConfig.enable_app("jira")
            assert ToolDiscoveryConfig.APP_CONFIGS["jira"].enabled is True
        finally:
            ToolDiscoveryConfig.APP_CONFIGS["jira"].enabled = original_enabled

    def test_enable_nonexistent_app_no_error(self):
        # Should not raise
        ToolDiscoveryConfig.enable_app("completely_unknown_app")


class TestGetEnabledApps:
    def test_all_enabled_by_default(self):
        enabled = ToolDiscoveryConfig.get_enabled_apps()
        assert isinstance(enabled, list)
        # All default apps are enabled
        for name in ToolDiscoveryConfig.APP_CONFIGS:
            assert name in enabled

    def test_disabled_app_excluded(self):
        original_enabled = ToolDiscoveryConfig.APP_CONFIGS["slack"].enabled
        try:
            ToolDiscoveryConfig.APP_CONFIGS["slack"].enabled = False
            enabled = ToolDiscoveryConfig.get_enabled_apps()
            assert "slack" not in enabled
        finally:
            ToolDiscoveryConfig.APP_CONFIGS["slack"].enabled = original_enabled

    def test_all_apps_represented(self):
        """Every app in APP_CONFIGS that is enabled should appear."""
        enabled = set(ToolDiscoveryConfig.get_enabled_apps())
        for name, cfg in ToolDiscoveryConfig.APP_CONFIGS.items():
            if cfg.enabled:
                assert name in enabled


class TestAllAppConfigsValid:
    """Verify structural properties of every entry in APP_CONFIGS."""

    def test_all_configs_are_app_configuration(self):
        for name, cfg in ToolDiscoveryConfig.APP_CONFIGS.items():
            assert isinstance(cfg, AppConfiguration), f"{name} is not AppConfiguration"

    def test_app_name_matches_key(self):
        for key, cfg in ToolDiscoveryConfig.APP_CONFIGS.items():
            assert cfg.app_name == key, f"Key {key} != app_name {cfg.app_name}"

    def test_client_builders_present_for_connector_apps(self):
        """Apps that represent real connectors should have a client_builder."""
        connector_apps = [
            "confluence", "jira", "slack", "notion", "clickup",
            "google", "microsoft", "outlook", "teams", "onedrive",
            "linear", "mariadb", "redshift", "dropbox", "github", "zoom",
        ]
        for name in connector_apps:
            cfg = ToolDiscoveryConfig.APP_CONFIGS[name]
            assert cfg.client_builder is not None, f"{name} missing client_builder"

    def test_utility_apps_no_client_builder(self):
        """Utility apps should not require a client_builder."""
        utility_apps = ["calculator", "utility", "retrieval", "knowledge_hub"]
        for name in utility_apps:
            cfg = ToolDiscoveryConfig.APP_CONFIGS[name]
            assert cfg.client_builder is None, f"{name} unexpectedly has client_builder"
