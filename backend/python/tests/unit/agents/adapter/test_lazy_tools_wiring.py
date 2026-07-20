"""`app/agents/agent_loop/lazy_tools_wiring.py` — env-driven activation
(flag + threshold + scope parsing), connector grouping into toolsets,
meta-tool/preloading registration idempotency, the full
`make_lazy_tools_decider` decision flow, and `PipesHubGlobalCatalogFallback`
adapting `_global_tools_registry` for `search_tools`' global-catalog
fallback."""

from __future__ import annotations

from typing import Any

from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.registry import ToolRegistry
from app.agents.agent_loop.lazy_tools_wiring import (
    META_TOOL_NAMES,
    PipesHubGlobalCatalogFallback,
    group_connector_toolsets,
    lazy_tools_enabled,
    lazy_tools_scope,
    lazy_tools_threshold,
    make_lazy_tools_decider,
    register_lazy_tool_meta_tools,
    should_apply_lazy_tools,
)


class _ConnectorTool(Tool):
    """Stands in for `PipesHubToolAdapter` — only `app_name` (read via
    `getattr`, not an `isinstance` check) and the base `Tool` identity
    matter to `group_connector_toolsets`."""

    def __init__(self, app_name: str, tool_name: str) -> None:
        self._app_name = app_name
        self._tool_name = tool_name

    @property
    def app_name(self) -> str:
        return self._app_name

    @property
    def name(self) -> str:
        return f"{self._app_name}_{self._tool_name}"

    @property
    def short_description(self) -> str:
        return f"{self._tool_name} on {self._app_name}"

    @property
    def description(self) -> str:
        return self.short_description

    @property
    def path(self) -> str:
        return f"/connectors/{self._app_name}/{self._tool_name}"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data=self.name)


class _UngroupableTool(Tool):
    """No `app_name` at all — an internal/coordination tool that must
    stay ungrouped (always visible) regardless of lazy disclosure."""

    @property
    def name(self) -> str:
        return "write_todos"

    @property
    def short_description(self) -> str:
        return "Write todos"

    @property
    def description(self) -> str:
        return "Write todos"

    @property
    def path(self) -> str:
        return "/planning/write_todos"

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: Any) -> ToolOutput:
        return ToolOutput(success=True, data="ok")


def _registry_with_connectors() -> ToolRegistry:
    registry = ToolRegistry()
    for tool in (
        _ConnectorTool("jira", "create_issue"),
        _ConnectorTool("jira", "search_issues"),
        _ConnectorTool("slack", "send_message"),
        _UngroupableTool(),
    ):
        registry.register_tool(tool)
    return registry


class TestEnvParsing:
    def test_lazy_tools_enabled_defaults_to_false(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_ENABLE_LAZY_TOOLS", raising=False)
        assert lazy_tools_enabled() is False

    def test_lazy_tools_enabled_true_when_flag_set(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        assert lazy_tools_enabled() is True

    def test_lazy_tools_threshold_defaults_to_20(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", raising=False)
        assert lazy_tools_threshold() == 20

    def test_lazy_tools_threshold_reads_custom_value(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "5")
        assert lazy_tools_threshold() == 5

    def test_lazy_tools_threshold_falls_back_on_invalid_value(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "not-a-number")
        assert lazy_tools_threshold() == 20

    def test_lazy_tools_scope_defaults_to_top_level(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_LAZY_TOOLS_SCOPE", raising=False)
        assert lazy_tools_scope() == "top_level"

    def test_lazy_tools_scope_accepts_domain_and_both(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_SCOPE", "domain")
        assert lazy_tools_scope() == "domain"
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_SCOPE", "both")
        assert lazy_tools_scope() == "both"

    def test_lazy_tools_scope_falls_back_on_invalid_value(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_SCOPE", "nonsense")
        assert lazy_tools_scope() == "top_level"


class TestShouldApplyLazyTools:
    def test_false_when_flag_disabled_even_above_threshold(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_ENABLE_LAZY_TOOLS", raising=False)
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "5")
        assert should_apply_lazy_tools(100) is False

    def test_false_when_below_threshold_even_with_flag_enabled(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "20")
        assert should_apply_lazy_tools(10) is False

    def test_true_when_flag_enabled_and_above_threshold(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "5")
        assert should_apply_lazy_tools(6) is True


class TestGroupConnectorToolsets:
    def test_groups_connector_tools_under_connectors_parent(self) -> None:
        registry = _registry_with_connectors()
        names = ["jira_create_issue", "jira_search_issues", "slack_send_message", "write_todos"]

        grouped_anything = group_connector_toolsets(registry, names)

        assert grouped_anything is True
        assert {g.name for g in registry.children_of("connectors")} == {"jira", "slack"}
        assert set(registry.tools_in_toolset("jira")) == {"jira_create_issue", "jira_search_issues"}
        assert set(registry.tools_in_toolset("slack")) == {"slack_send_message"}
        # Ungroupable tool never becomes part of any toolset.
        assert "write_todos" not in registry.grouped_tool_names()

    def test_returns_false_when_nothing_groupable(self) -> None:
        registry = _registry_with_connectors()
        grouped_anything = group_connector_toolsets(registry, ["write_todos"])
        assert grouped_anything is False
        assert registry.has_toolsets() is False

    def test_never_groups_internal_and_dynamic_app_names(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_ConnectorTool("internal", "some_tool"))
        registry.register_tool(_ConnectorTool("dynamic", "web_search"))
        grouped_anything = group_connector_toolsets(registry, ["internal_some_tool", "dynamic_web_search"])
        assert grouped_anything is False

    def test_idempotent_across_repeated_calls(self) -> None:
        registry = _registry_with_connectors()
        names = ["jira_create_issue", "jira_search_issues", "slack_send_message"]
        group_connector_toolsets(registry, names)
        group_connector_toolsets(registry, names)
        assert set(registry.tools_in_toolset("jira")) == {"jira_create_issue", "jira_search_issues"}
        assert len(registry.children_of("connectors")) == 2

    def test_skips_names_not_present_in_registry(self) -> None:
        registry = _registry_with_connectors()
        grouped_anything = group_connector_toolsets(registry, ["jira_create_issue", "not_a_real_tool"])
        assert grouped_anything is True
        assert registry.tools_in_toolset("jira") == ["jira_create_issue"]


class TestRegisterLazyToolMetaTools:
    def test_registers_all_three_meta_tools(self) -> None:
        registry = ToolRegistry()
        register_lazy_tool_meta_tools(registry)
        assert set(META_TOOL_NAMES) <= set(registry.names())

    def test_second_call_on_same_registry_is_a_no_op(self) -> None:
        registry = ToolRegistry()
        register_lazy_tool_meta_tools(registry)
        register_lazy_tool_meta_tools(registry)  # must not raise
        assert set(META_TOOL_NAMES) <= set(registry.names())


class TestMakeLazyToolsDecider:
    def test_apply_false_always_passes_through_unchanged(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "0")
        registry = _registry_with_connectors()
        decide = make_lazy_tools_decider(apply=False)

        names, disclosure = decide(registry, ["jira_create_issue", "slack_send_message"])

        assert disclosure == "eager"
        assert names == ["jira_create_issue", "slack_send_message"]
        assert registry.has_toolsets() is False

    def test_apply_true_below_threshold_passes_through_unchanged(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "20")
        registry = _registry_with_connectors()
        decide = make_lazy_tools_decider(apply=True)

        names, disclosure = decide(registry, ["jira_create_issue", "slack_send_message"])

        assert disclosure == "eager"
        assert names == ["jira_create_issue", "slack_send_message"]

    def test_apply_true_above_threshold_groups_and_augments_with_meta_tools(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "1")
        registry = _registry_with_connectors()
        decide = make_lazy_tools_decider(apply=True)

        original = ["jira_create_issue", "jira_search_issues", "slack_send_message"]
        names, disclosure = decide(registry, original)

        assert disclosure == "lazy"
        assert set(original) <= set(names)
        assert set(META_TOOL_NAMES) <= set(names)
        assert registry.has_toolsets() is True

    def test_apply_true_but_nothing_groupable_stays_eager(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "0")
        registry = _registry_with_connectors()
        decide = make_lazy_tools_decider(apply=True)

        names, disclosure = decide(registry, ["write_todos"])

        assert disclosure == "eager"
        assert names == ["write_todos"]

    def test_does_not_duplicate_meta_tool_names_already_present(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "0")
        registry = _registry_with_connectors()
        decide = make_lazy_tools_decider(apply=True)

        names, _ = decide(registry, ["jira_create_issue", "list_toolsets"])

        assert names.count("list_toolsets") == 1


class _FakeGlobalTool:
    def __init__(self, app_name: str, tool_name: str, description: str) -> None:
        self.app_name = app_name
        self.tool_name = tool_name
        self.description = description


class _FakeGlobalRegistry:
    def __init__(self, tools: list[_FakeGlobalTool]) -> None:
        self._tools = tools
        self.queries: list[str] = []

    def search_tools(self, query: str | None = None) -> list[_FakeGlobalTool]:
        self.queries.append(query)
        return self._tools


class TestPipesHubGlobalCatalogFallback:
    async def test_search_wraps_global_registry_hits(self, monkeypatch) -> None:
        fake_registry = _FakeGlobalRegistry([
            _FakeGlobalTool("confluence", "search_pages", "Search Confluence pages"),
            _FakeGlobalTool("confluence", "get_page", ""),
        ])
        monkeypatch.setattr("app.agents.tools.registry._global_tools_registry", fake_registry)

        fallback = PipesHubGlobalCatalogFallback()
        hits = await fallback.search("confluence wiki", limit=5)

        assert fake_registry.queries == ["confluence wiki"]
        assert hits[0].name == "confluence__search_pages"
        assert hits[0].toolset == "confluence"
        assert hits[0].description == "Search Confluence pages"
        assert hits[0].reason == "not_attached"
        # Falls back to a generated description when the registry tool has none.
        assert hits[1].description == "confluence get_page"

    async def test_search_respects_limit(self, monkeypatch) -> None:
        fake_registry = _FakeGlobalRegistry([
            _FakeGlobalTool("jira", "create_issue", "Create issue"),
            _FakeGlobalTool("jira", "search_issues", "Search issues"),
            _FakeGlobalTool("jira", "delete_issue", "Delete issue"),
        ])
        monkeypatch.setattr("app.agents.tools.registry._global_tools_registry", fake_registry)

        fallback = PipesHubGlobalCatalogFallback()
        hits = await fallback.search("jira", limit=2)

        assert len(hits) == 2
