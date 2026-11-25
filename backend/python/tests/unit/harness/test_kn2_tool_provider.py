# pyright: reportPrivateUsage=false
"""KN2: whether a tool is the platform's or a connector's is declared on its registration, never read from its name.

Cases KN2-1..KN2-7 and KN2-12. The registry, the loader and ``ToolsetSource`` are the
production ones; only the client factories (the network boundary) and the per-request tools are stood in for.
"""

from __future__ import annotations

import logging
import types
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.agent_loop_lib.tools.decorators import (
    tool,  # pyright: ignore[reportUnknownVariableType]
)
from app.agents.agent_loop.context import AgentContext
from app.agents.agent_loop.tool_adapter import PipesHubStructuredToolAdapter
from app.agents.agent_loop.tool_loader import PipesHubToolLoader, toolset_provider_tags
from app.agents.registry.toolset_registry import Toolset
from app.agents.tools.factories.registry import ClientFactoryRegistry
from app.connectors.core.registry.tool_builder import ToolProvider, ToolsetBuilder
from app.harness.assembly.assembler import build_catalog
from app.harness.assembly.tool_sources import ToolsetSource, declared_provider
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.provenance.rules import NO_CONTENT_RULE
from harness_core.tools.registry import ToolRegistry
from harness_core.tools.tags import (
    PROVIDER_TAG_KEY,
    PROVIDERS,
    TAG_EFFECT_READ,
    TAG_PROVIDER_CONNECTOR,
    TAG_PROVIDER_PLATFORM,
)
from tests.support.toolset_registry import discovered_toolsets
from tests.support.web_tools import SLACK_CONTEXT_TOOLS, product_tools

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from harness_core.tools.base import Tag

SLACK_THREAD, SLACK_NEARBY = SLACK_CONTEXT_TOOLS


def _providers(tags: Iterable[Tag]) -> list[str]:
    return [tag.value for tag in tags if tag.key == PROVIDER_TAG_KEY]


def undeclared(tools: Mapping[str, Iterable[Tag]]) -> list[str]:
    """``name [provider tags]`` for every tool without exactly one valid provider tag."""
    return [
        f"{name} {found}"
        for name, tags in tools.items()
        if len(found := _providers(tags)) != 1 or found[0] not in PROVIDERS
    ]


def _structured(name: str) -> Any:  # noqa: ANN401 - a LangChain StructuredTool
    from langchain_core.tools import StructuredTool

    async def run() -> str:
        return "ok"

    return StructuredTool.from_function(coroutine=run, name=name, description=name)


# KN2-1, KN2-2, KN2-12: the registration


def test_kn2_1_every_toolset_declares_its_provider() -> None:
    discovered = discovered_toolsets()
    assert len(discovered) >= 29
    providers = {name: meta.get("provider") for name, meta in discovered.items()}
    assert {n: p for n, p in providers.items() if p not in PROVIDERS} == {}
    # A connector's tools are built from its configured instance's client; the platform's need none.
    with_factory = {n for n in discovered if ClientFactoryRegistry.get_factory(n) is not None}
    assert {n for n, p in providers.items() if p == ToolProvider.CONNECTOR.value} == with_factory


def test_kn2_2_a_toolset_without_a_provider_is_refused() -> None:
    with pytest.raises(TypeError):
        Toolset(name="Undeclared", app_group="Demo", supported_auth_types="NONE")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="mcp"):
        Toolset(name="Other", app_group="Demo", supported_auth_types="NONE", provider="mcp")  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        ToolsetBuilder("Undeclared")  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="mcp"):
        ToolsetBuilder("Other", provider="mcp")  # type: ignore[arg-type]

    @Toolset(name="Declared", app_group="Demo", supported_auth_types="NONE", provider=ToolProvider.PLATFORM)
    class _Declared:
        pass

    assert _Declared._toolset_metadata["provider"] == "platform"  # type: ignore[attr-defined]


async def test_kn2_12_only_a_connector_toolset_is_suggested_for_attaching(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.agents.agent_loop.lazy_tools_wiring import PipesHubGlobalCatalogFallback

    toolsets = {
        "desk": {"provider": "connector", "isInternal": False, "class": None, "tools": [{"name": "find_ticket"}]},
        # Shown to people (not internal), but the platform's own: nothing to attach.
        "notes": {"provider": "platform", "isInternal": False, "class": None, "tools": [{"name": "find_note"}]},
    }
    registry = types.SimpleNamespace(get_all_toolsets=lambda: toolsets)
    monkeypatch.setattr("app.agents.registry.toolset_registry.get_toolset_registry", lambda: registry)
    hits = await PipesHubGlobalCatalogFallback().search("find", limit=5)
    assert [hit.toolset for hit in hits] == ["desk"]


# KN2-3, KN2-4, KN2-6: the tools as they are registered


def _registered_toolset_tools() -> dict[str, tuple[Tag, ...]]:
    """Every toolset's tools with the tags the loader registers them with (the scan reads ``@tool`` metadata; no
    toolset is constructed)."""
    from app.agent_loop_lib.tools.toolset import ToolsetBuilder as Scan
    from app.agents.agent_loop.tool_loader import (
        _has_tool_decorated_methods,
        _infer_path_prefix,
    )

    found: dict[str, tuple[Tag, ...]] = {}
    for ts_name, meta in sorted(discovered_toolsets().items()):
        cls = meta.get("class")
        if cls is None or not _has_tool_decorated_methods(cls):
            continue
        prefix = _infer_path_prefix(cls, fallback_name=ts_name)
        scan = Scan(cls.__new__(cls), name=prefix.rsplit("/", 1)[-1], description="", path_prefix=prefix)
        found.update({t.name: toolset_provider_tags(meta) + tuple(t.tags) for t in scan.tools})
    return found


def test_kn2_3_every_tool_the_toolset_source_registers_declares_its_provider() -> None:
    tools = _registered_toolset_tools() | {name: tuple(t.tags) for name, t in product_tools().items()}
    assert len(tools) >= 370
    assert undeclared(tools) == []


def test_kn2_3_the_check_lists_a_tool_without_one() -> None:
    planted = PipesHubStructuredToolAdapter(_structured("mystery"), "slack", "mystery", effect=TAG_EFFECT_READ)
    doubled = PipesHubStructuredToolAdapter(_structured("both"), "x", "both", provider=TAG_PROVIDER_PLATFORM)
    tags = {
        planted.name: tuple(planted.tags),
        doubled.name: (TAG_PROVIDER_CONNECTOR, *doubled.tags),
        SLACK_THREAD: tuple(product_tools()[SLACK_THREAD].tags),
    }
    assert undeclared(tags) == ["slack__mystery []", "x__both ['connector', 'platform']"]
    assert declared_provider(tags["x__both"]) is None


class _Desk:
    def __init__(self, client: object) -> None:
        self.client = client

    @tool(path="/tools/desk/find_ticket", short_description="Find", description="Find a ticket")  # pyright: ignore[reportUntypedFunctionDecorator]
    async def find_ticket(self) -> tuple[bool, str]:
        return True, "[]"


class _Notes:
    def __init__(self, state: object = None) -> None:
        self.state = state

    @tool(path="/tools/notes/find_note", short_description="Find", description="Find a note")  # pyright: ignore[reportUntypedFunctionDecorator]
    async def find_note(self) -> tuple[bool, str]:
        return True, "[]"


class _Factory:
    async def create_client(self, config_service: object, logger: object, config: object, state: object) -> object:
        return {}


async def test_kn2_4_the_loader_registers_a_toolsets_tools_with_its_declared_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    toolsets = {
        "desk": {"class": _Desk, "provider": "connector", "isInternal": False, "description": "Desk"},
        "notes": {"class": _Notes, "provider": "platform", "isInternal": True, "description": "Notes"},
    }
    monkeypatch.setattr(
        "app.agents.registry.toolset_registry.get_toolset_registry",
        lambda: types.SimpleNamespace(get_all_toolsets=lambda: toolsets),
    )
    monkeypatch.setattr(ClientFactoryRegistry, "_factories", {"desk": _Factory()})
    monkeypatch.setattr(ClientFactoryRegistry, "_initialized", True)
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=logging.getLogger("tests.kn2"),
        agent_toolsets=[{"name": "desk", "instanceId": "desk-1"}], toolset_configs={"desk-1": {"auth": {}}},
    )
    registry = await PipesHubToolLoader().load(context)
    assert {name: _providers(registry.tags_for_name(name)) for name in ("desk__find_ticket", "notes__find_note")} == {
        "desk__find_ticket": ["connector"],
        "notes__find_note": ["platform"],
    }


@pytest.mark.parametrize("name", SLACK_CONTEXT_TOOLS)
def test_kn2_6_the_slack_context_tools_are_platform_reads_that_send_nothing_out(name: str) -> None:
    tags = product_tools()[name].tags
    effect = classify_tool_effect(name, tags)
    assert (effect.effect, effect.sink.egress, declared_provider(tags)) == ("read", "none", "platform")


# KN2-5: ToolsetSource reads the declaration


def _slack_context() -> AgentContext:
    context = AgentContext(
        org_id="org-1", user_id="user-1", user_email="u@example.com", logger=logging.getLogger("tests.kn2"),
    )
    context.tool_state.update(
        {"config_service": object(), "has_slack_connector": True, "has_slack_knowledge": True, "org_id": "org-1"}
    )
    return context


async def test_kn2_5_toolset_source_grants_by_the_declared_provider_not_the_group(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from app.agents.agent_loop import tool_loader

    build = tool_loader._build_dynamic_tools

    def planted(context: AgentContext) -> list[Any]:
        return [
            *build(context),
            # A connector's tool built without a configured binding, in the Slack connector's group.
            PipesHubStructuredToolAdapter(_structured("post"), "slack", "post", provider=TAG_PROVIDER_CONNECTOR),
            # The platform's own tool in a group named after a connector.
            PipesHubStructuredToolAdapter(_structured("summary"), "jira", "summary", provider=TAG_PROVIDER_PLATFORM),
            PipesHubStructuredToolAdapter(_structured("mystery"), "slack", "mystery", effect=TAG_EFFECT_READ),
        ]

    monkeypatch.setattr(tool_loader, "_build_dynamic_tools", planted)
    run = types.SimpleNamespace(org_id="org-1", session_id="ses-1", run_id="run-1", tool_ids=(), mcp_instances=(), mcp_at_first_use=())
    env = types.SimpleNamespace(context=_slack_context(), web_search_provider=None, granted_toolsets=(), unavailable=())
    registry = ToolRegistry()
    with caplog.at_level(logging.WARNING, logger="app.harness.assembly"):
        sourced = {t.name: (t.tool_id, t.grant) for t in await ToolsetSource().load(env, run, registry)}  # type: ignore[arg-type]
    planted_names = (SLACK_THREAD, SLACK_NEARBY, "slack__post", "jira__summary", "slack__mystery")
    assert {name: sourced[name] for name in planted_names} == {
        SLACK_THREAD: ("pipeshub@slack/fetch_slack_thread", None),
        SLACK_NEARBY: ("pipeshub@slack/fetch_slack_nearby_messages", None),
        "slack__post": ("slack@unknown/post", "slack@unknown/post"),
        "jira__summary": ("pipeshub@jira/summary", None),
        "slack__mystery": ("slack@unknown/mystery", "slack@unknown/mystery"),
    }
    # The run's registry keeps each tool's declaration, as every later reader of the run's tools sees it.
    assert {name: declared_provider(registry.tags_for_name(name)) for name in planted_names} == {
        SLACK_THREAD: "platform", SLACK_NEARBY: "platform", "slack__post": "connector", "jira__summary": "platform",
        "slack__mystery": None,
    }
    assert undeclared({n: registry.tags_for_name(n) for n in registry.names() if n != "slack__mystery"}) == []
    granted = set(build_catalog(run, registry, list(_sourced(sourced))).effects())  # type: ignore[arg-type]
    assert {SLACK_THREAD, SLACK_NEARBY, "jira__summary"} <= granted
    assert granted.isdisjoint({"slack__post", "slack__mystery"})
    assert [r.getMessage() for r in caplog.records if "tools.provider_undeclared" in r.getMessage()] != []
    assert all("slack__mystery" in r.getMessage() for r in caplog.records if "provider_undeclared" in r.getMessage())


def _sourced(sourced: Mapping[str, tuple[str, str | None]]) -> Iterable[Any]:
    from app.harness.assembly.tool_sources import SourcedTool

    return (SourcedTool(name, tool_id, grant, provenance=NO_CONTENT_RULE) for name, (tool_id, grant) in sourced.items())


# KN2-7: the release catalog


def test_kn2_7_each_catalog_tool_carries_its_toolsets_provider_outside_its_digest() -> None:
    from app.agents.registry.catalog_source import ToolsetsCatalogSource
    from app.control_plane.catalog.items import ToolDescriptor
    from tests.support.toolset_registry import toolset_registry_restored

    with toolset_registry_restored():
        from app.agents.registry.toolset_registry import ToolsetRegistry

        ToolsetRegistry._instance = None
        registry = ToolsetRegistry()
        registry.auto_discover_toolsets()
        items = ToolsetsCatalogSource(registry).items()
        declared = {name: meta["provider"] for name, meta in registry.get_all_toolsets().items()}
    wrong = {
        (item.name, d.name): d.provider
        for item in items
        for d in cast("tuple[ToolDescriptor, ...]", item.descriptors)
        if d.provider != declared[item.name]
    }
    assert wrong == {}
    assert sum(len(item.descriptors) for item in items) >= 368
    shape = {"name": "t", "description": "d", "input_schema": {"type": "object"}, "effect": "read", "sink": None}
    plain, declared_tool = ToolDescriptor(**shape), ToolDescriptor(**shape, provider="connector")  # type: ignore[arg-type]
    assert plain.digest == declared_tool.digest
    assert declared_tool.to_json()["provider"] == "connector"
    assert "provider" not in plain.to_json()
