"""`ToolRegistry` — hierarchical toolsets (`parent`, recursive
`tools_in_toolset`/`toolset_overview`/`toolset_detail`) and provider-backed
deferred materialization (`register_toolset_provider`, `materialize`/
`materialize_many`, `discover()` including unmaterialized summaries)."""

from __future__ import annotations

import asyncio

from app.agent_loop_lib.tools.base import Tool, ToolOutput, ToolParameter
from app.agent_loop_lib.tools.provider import EagerToolsetProvider
from app.agent_loop_lib.tools.registry import ToolRegistry


class _SimpleTool(Tool):
    def __init__(self, name: str, path: str | None = None) -> None:
        self._name = name
        self._path = path or f"/toolsets/test/{name}"

    @property
    def name(self) -> str:
        return self._name

    @property
    def short_description(self) -> str:
        return f"{self._name} short"

    @property
    def description(self) -> str:
        return f"{self._name} full description"

    @property
    def path(self) -> str:
        return self._path

    @property
    def parameters(self) -> list[ToolParameter]:
        return []

    async def execute(self, **kwargs: object) -> ToolOutput:
        return ToolOutput(success=True, data=self._name)


def _registry_with_hierarchy() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("create_issue", "search_issues", "send_message"):
        registry.register_tool(_SimpleTool(name))
    registry.register_toolset("connectors", "Connected apps.", [])
    registry.register_toolset("jira", "Jira issue tracking.", ["create_issue", "search_issues"], parent="connectors")
    registry.register_toolset("slack", "Slack messaging.", ["send_message"], parent="connectors")
    return registry


class TestHierarchy:
    def test_children_of_returns_direct_children_only(self) -> None:
        registry = _registry_with_hierarchy()
        children = {g.name for g in registry.children_of("connectors")}
        assert children == {"jira", "slack"}
        assert registry.children_of("jira") == []

    def test_children_of_none_returns_top_level_groups(self) -> None:
        registry = _registry_with_hierarchy()
        top_level = {g.name for g in registry.children_of(None)}
        assert top_level == {"connectors"}

    def test_tools_in_toolset_resolves_category_to_union_of_children(self) -> None:
        registry = _registry_with_hierarchy()
        names = set(registry.tools_in_toolset("connectors"))
        assert names == {"create_issue", "search_issues", "send_message"}

    def test_tools_in_toolset_leaf_group_returns_own_tools_only(self) -> None:
        registry = _registry_with_hierarchy()
        assert set(registry.tools_in_toolset("jira")) == {"create_issue", "search_issues"}

    def test_tools_in_toolset_unknown_name_returns_empty(self) -> None:
        registry = _registry_with_hierarchy()
        assert registry.tools_in_toolset("unknown") == []

    def test_tools_in_toolset_guards_against_parent_cycles(self) -> None:
        registry = ToolRegistry()
        registry.register_toolset("a", "A", [], parent="b")
        registry.register_toolset("b", "B", [], parent="a")
        # Must terminate instead of infinitely recursing.
        assert registry.tools_in_toolset("a") == []

    def test_toolset_overview_renders_tree_with_children(self) -> None:
        registry = _registry_with_hierarchy()
        overview = registry.toolset_overview()
        assert len(overview) == 1
        connectors_entry = overview[0]
        assert connectors_entry["name"] == "connectors"
        assert connectors_entry["tool_count"] == 3
        child_names = {c["name"] for c in connectors_entry["children"]}
        assert child_names == {"jira", "slack"}

    def test_toolset_overview_flat_registry_has_no_children_key(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_SimpleTool("run_code"))
        registry.register_toolset("sandbox", "Sandbox tools.", ["run_code"])
        overview = registry.toolset_overview()
        assert "children" not in overview[0]

    def test_toolset_detail_returns_per_tool_one_liners_recursively(self) -> None:
        registry = _registry_with_hierarchy()
        detail = registry.toolset_detail("connectors")
        assert detail is not None
        detail_names = {t["name"] for t in detail["tools"]}
        assert detail_names == {"create_issue", "search_issues", "send_message"}
        assert set(detail["children"]) == {"jira", "slack"}

    def test_toolset_detail_leaf_group_has_no_children(self) -> None:
        registry = _registry_with_hierarchy()
        detail = registry.toolset_detail("jira")
        assert detail is not None
        assert detail["children"] == []
        assert {t["name"] for t in detail["tools"]} == {"create_issue", "search_issues"}

    def test_toolset_detail_unknown_name_returns_none(self) -> None:
        registry = _registry_with_hierarchy()
        assert registry.toolset_detail("unknown") is None

    def test_grouped_tool_names_only_counts_leaf_membership(self) -> None:
        registry = _registry_with_hierarchy()
        assert registry.grouped_tool_names() == {"create_issue", "search_issues", "send_message"}


class TestToolsetProvider:
    def test_register_toolset_provider_exposes_summary_without_materializing(self) -> None:
        registry = ToolRegistry()
        provider = EagerToolsetProvider(
            "jira_mcp", "Jira via MCP.", [_SimpleTool("mcp_create_issue", "/mcp/jira/mcp_create_issue")],
        )
        registry.register_toolset_provider(provider)

        assert registry.is_provider_backed("mcp_create_issue")
        assert not registry.is_materialized("mcp_create_issue")
        assert registry.has("mcp_create_issue") is False
        detail = registry.toolset_detail("jira_mcp")
        assert detail is not None
        assert detail["tools"] == [{"name": "mcp_create_issue", "short_description": "mcp_create_issue short"}]

    def test_discover_includes_unmaterialized_provider_summaries(self) -> None:
        registry = ToolRegistry()
        provider = EagerToolsetProvider(
            "jira_mcp", "Jira via MCP.", [_SimpleTool("mcp_create_issue", "/mcp/jira/mcp_create_issue")],
        )
        registry.register_toolset_provider(provider)
        names = {s.name for s in registry.discover()}
        assert "mcp_create_issue" in names

    async def test_materialize_builds_and_caches_the_real_tool(self) -> None:
        registry = ToolRegistry()
        tool = _SimpleTool("mcp_create_issue", "/mcp/jira/mcp_create_issue")
        provider = EagerToolsetProvider("jira_mcp", "Jira via MCP.", [tool])
        registry.register_toolset_provider(provider)

        materialized = await registry.materialize("mcp_create_issue")
        assert materialized is tool
        assert registry.is_materialized("mcp_create_issue")
        assert registry.has("mcp_create_issue")
        # Second call is a cached no-op, not a second provider round trip.
        again = await registry.materialize("mcp_create_issue")
        assert again is tool

    async def test_materialize_many_skips_plain_and_already_materialized_names(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_SimpleTool("plain_tool"))
        provider = EagerToolsetProvider(
            "jira_mcp", "Jira via MCP.", [_SimpleTool("mcp_create_issue", "/mcp/jira/mcp_create_issue")],
        )
        registry.register_toolset_provider(provider)

        await registry.materialize_many(["plain_tool", "mcp_create_issue"])
        assert registry.is_materialized("mcp_create_issue")
        assert registry.resolve_by_name("mcp_create_issue").name == "mcp_create_issue"

    def test_register_toolset_provider_rejects_duplicate_names(self) -> None:
        from app.agent_loop_lib.tools.errors import DuplicateToolNameError

        registry = ToolRegistry()
        registry.register_tool(_SimpleTool("mcp_create_issue"))
        provider = EagerToolsetProvider(
            "jira_mcp", "Jira via MCP.", [_SimpleTool("mcp_create_issue", "/mcp/jira/mcp_create_issue")],
        )
        try:
            registry.register_toolset_provider(provider)
            raise AssertionError("expected DuplicateToolNameError")
        except DuplicateToolNameError:
            pass


class _SlowProvider:
    """A `ToolsetProvider` whose `materialize()` has a genuine `await`
    point (unlike `EagerToolsetProvider`) and counts how many times it was
    actually invoked per name — the seam `ToolRegistry.materialize()`'s
    TOCTOU race lives in (see fix-registry-races)."""

    def __init__(self, name: str, tools: list[Tool]) -> None:
        from app.agent_loop_lib.tools.provider import ToolsetSummary

        self._summary = ToolsetSummary(name=name, description=f"{name} desc")
        self._tools_by_name = {tool.name: tool for tool in tools}
        self.materialize_calls: dict[str, int] = {n: 0 for n in self._tools_by_name}
        self._release = asyncio.Event()

    def summary(self):
        return self._summary

    def list_tools(self):
        return [tool.to_summary() for tool in self._tools_by_name.values()]

    def release(self) -> None:
        self._release.set()

    async def materialize(self, name: str) -> Tool:
        self.materialize_calls[name] += 1
        await self._release.wait()
        return self._tools_by_name[name]


class TestMaterializeConcurrency:
    async def test_concurrent_materialize_calls_for_same_name_only_build_once(self) -> None:
        tool = _SimpleTool("mcp_create_issue", "/mcp/jira/mcp_create_issue")
        provider = _SlowProvider("jira_mcp", [tool])
        registry = ToolRegistry()
        registry.register_toolset_provider(provider)

        task_a = asyncio.create_task(registry.materialize("mcp_create_issue"))
        task_b = asyncio.create_task(registry.materialize("mcp_create_issue"))
        # Let both tasks reach `await self._release.wait()` inside
        # `materialize()` before releasing them — this is the window where
        # an unlocked `materialize()` would let both pass the "not yet
        # registered" check and both call `register_tool`.
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        provider.release()
        results = await asyncio.gather(task_a, task_b)

        assert results == [tool, tool]
        assert provider.materialize_calls["mcp_create_issue"] == 1
        assert registry.is_materialized("mcp_create_issue")


class TestRegisterToolIfAbsent:
    def test_registers_when_absent(self) -> None:
        registry = ToolRegistry()
        tool = _SimpleTool("new_tool")

        registered = registry.register_tool_if_absent(tool)

        assert registered is True
        assert registry.has("new_tool")

    def test_no_op_when_already_present_by_name(self) -> None:
        registry = ToolRegistry()
        registry.register_tool(_SimpleTool("dynamic_tool", "/dynamic/first"))

        # A different Tool INSTANCE with the same name — simulates two
        # concurrent callers each building their own instance and racing
        # to register it (`hooks/citations.py`'s `_FetchFullRecordTool`).
        registered = registry.register_tool_if_absent(_SimpleTool("dynamic_tool", "/dynamic/second"))

        assert registered is False
        # The original registration wins; no DuplicateToolNameError raised.
        assert registry.resolve("/dynamic/first").name == "dynamic_tool"
        assert not registry.has_path("/dynamic/second")
