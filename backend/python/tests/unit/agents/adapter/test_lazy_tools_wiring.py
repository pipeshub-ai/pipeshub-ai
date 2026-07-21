"""`app/agents/agent_loop/lazy_tools_wiring.py` — env-driven activation
(flag + threshold + scope parsing), re-parenting of pre-registered connector
`ToolsetGroup`s under `CONNECTORS_PARENT`, meta-tool/preloading registration
idempotency, the full `make_lazy_tools_decider` decision flow (including
`essential_names` exclusion), and `PipesHubGlobalCatalogFallback` adapting
`ToolsetRegistry` for `search_tools`' global-catalog fallback."""

from __future__ import annotations

from types import SimpleNamespace
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
    """A minimal connector-shaped tool — only `name`/`path` matter to
    `group_connector_toolsets`, which groups by pre-registered `ToolsetGroup`
    membership, not by any attribute on the tool itself."""

    def __init__(self, app_name: str, tool_name: str) -> None:
        self._app_name = app_name
        self._tool_name = tool_name

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
    """No toolset registration at all — an internal/coordination tool that
    must stay ungrouped (always visible) regardless of lazy disclosure."""

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
    """Mirrors what `PipesHubToolLoader.load()` actually does: register each
    tool AND a matching top-level `ToolsetGroup` per connector."""
    registry = ToolRegistry()
    for tool in (
        _ConnectorTool("jira", "create_issue"),
        _ConnectorTool("jira", "search_issues"),
        _ConnectorTool("slack", "send_message"),
        _UngroupableTool(),
    ):
        registry.register_tool(tool)
    registry.register_toolset("jira", "Jira issue tracker", ["jira_create_issue", "jira_search_issues"])
    registry.register_toolset("slack", "Slack messaging", ["slack_send_message"])
    return registry


class TestEnvParsing:
    def test_lazy_tools_enabled_defaults_to_true(self, monkeypatch) -> None:
        monkeypatch.delenv("PIPESHUB_ENABLE_LAZY_TOOLS", raising=False)
        assert lazy_tools_enabled() is True

    def test_lazy_tools_enabled_false_when_flag_set_to_false(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
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
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")
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
        # Ungroupable tool was never registered as a toolset, so it never
        # ends up "hidden" behind fetch_tools.
        assert "write_todos" not in registry.grouped_tool_names()

    def test_returns_false_when_nothing_groupable(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_UngroupableTool())
        grouped_anything = group_connector_toolsets(registry, ["write_todos"])
        assert grouped_anything is False
        assert registry.has_toolsets() is False

    def test_excludes_essential_toolset_names_from_grouping(self) -> None:
        registry = _registry_with_connectors()
        names = ["jira_create_issue", "jira_search_issues", "slack_send_message"]

        grouped_anything = group_connector_toolsets(registry, names, exclude=frozenset({"jira"}))

        assert grouped_anything is True
        assert {g.name for g in registry.children_of("connectors")} == {"slack"}
        jira_group = next(g for g in registry.toolsets() if g.name == "jira")
        assert jira_group.parent is None

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
        # The whole jira group is pulled in once ANY of its members is
        # present in the grant, not just the one that matched.
        assert set(registry.tools_in_toolset("jira")) == {"jira_create_issue", "jira_search_issues"}


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
        assert registry.has_toolsets() is True  # pre-registered groups untouched, just not re-parented
        assert registry.children_of("connectors") == []

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
        assert {g.name for g in registry.children_of("connectors")} == {"jira", "slack"}

    def test_essential_names_are_excluded_from_grouping(self, monkeypatch) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "true")
        monkeypatch.setenv("PIPESHUB_LAZY_TOOLS_THRESHOLD", "1")
        registry = _registry_with_connectors()
        decide = make_lazy_tools_decider(apply=True, essential_names=frozenset({"jira"}))

        names, disclosure = decide(
            registry, ["jira_create_issue", "jira_search_issues", "slack_send_message"],
        )

        assert disclosure == "lazy"
        assert {g.name for g in registry.children_of("connectors")} == {"slack"}

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


class _FakeToolsetRegistry:
    def __init__(self, toolsets: dict[str, dict[str, Any]]) -> None:
        self._toolsets = toolsets

    def get_all_toolsets(self) -> dict[str, dict[str, Any]]:
        return self._toolsets


def _toolset_meta(*, tools: list[dict[str, str]], is_internal: bool = False) -> dict[str, Any]:
    return {"isInternal": is_internal, "class": None, "tools": tools}


class TestPipesHubGlobalCatalogFallback:
    async def test_search_wraps_global_registry_hits(self, monkeypatch) -> None:
        fake_registry = _FakeToolsetRegistry({
            "confluence": _toolset_meta(tools=[
                {"name": "search_pages", "description": "Search Confluence pages"},
                {"name": "get_page", "description": ""},
            ]),
        })
        monkeypatch.setattr(
            "app.agents.registry.toolset_registry.get_toolset_registry", lambda: fake_registry,
        )

        fallback = PipesHubGlobalCatalogFallback()
        hits = await fallback.search("confluence wiki", limit=5)

        assert hits[0].name == "confluence__search_pages"
        assert hits[0].toolset == "confluence"
        assert hits[0].description == "Search Confluence pages"
        assert hits[0].reason == "not_attached"
        # Falls back to a generated description when the registry tool has none.
        assert hits[1].description == "confluence get_page"

    async def test_search_respects_limit(self, monkeypatch) -> None:
        fake_registry = _FakeToolsetRegistry({
            "jira": _toolset_meta(tools=[
                {"name": "create_issue", "description": "Create issue"},
                {"name": "search_issues", "description": "Search issues"},
                {"name": "delete_issue", "description": "Delete issue"},
            ]),
        })
        monkeypatch.setattr(
            "app.agents.registry.toolset_registry.get_toolset_registry", lambda: fake_registry,
        )

        fallback = PipesHubGlobalCatalogFallback()
        hits = await fallback.search("issue", limit=2)

        assert len(hits) == 2

    async def test_search_skips_internal_toolsets(self, monkeypatch) -> None:
        fake_registry = _FakeToolsetRegistry({
            "retrieval": _toolset_meta(
                is_internal=True,
                tools=[{"name": "search_knowledge", "description": "Search the knowledge base"}],
            ),
        })
        monkeypatch.setattr(
            "app.agents.registry.toolset_registry.get_toolset_registry", lambda: fake_registry,
        )

        fallback = PipesHubGlobalCatalogFallback()
        hits = await fallback.search("search", limit=5)

        assert hits == []

    async def test_search_reports_not_authenticated_from_context_failures(self, monkeypatch) -> None:
        fake_registry = _FakeToolsetRegistry({
            "jira": _toolset_meta(tools=[
                {"name": "create_issue", "description": "Create a Jira issue"},
            ]),
        })
        monkeypatch.setattr(
            "app.agents.registry.toolset_registry.get_toolset_registry", lambda: fake_registry,
        )
        context = SimpleNamespace(toolset_load_failures={"jira": "not_authenticated"})

        fallback = PipesHubGlobalCatalogFallback(context)
        hits = await fallback.search("jira issue", limit=5)

        assert hits[0].reason == "not_authenticated"
        assert "authenticate" in hits[0].description.lower()

    async def test_search_defaults_to_not_attached_without_context(self, monkeypatch) -> None:
        fake_registry = _FakeToolsetRegistry({
            "jira": _toolset_meta(tools=[
                {"name": "create_issue", "description": "Create a Jira issue"},
            ]),
        })
        monkeypatch.setattr(
            "app.agents.registry.toolset_registry.get_toolset_registry", lambda: fake_registry,
        )

        fallback = PipesHubGlobalCatalogFallback(None)
        hits = await fallback.search("jira issue", limit=5)

        assert hits[0].reason == "not_attached"
