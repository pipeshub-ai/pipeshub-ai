"""CPD2-20..CPD2-23: each kind's release source turns the registry that serves it into catalog items."""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, Any, cast

import pytest

from app.agent_loop_lib.tools.base import ParameterType, Tag, ToolParameter
from app.agent_loop_lib.tools.decorators import tool
from app.agents.agent_loop.skills.builtin_seeder import (
    SEED_IDENTITY,
    BuiltinSkillSeeder,
)
from app.agents.agent_loop.skills.bundle_source import GraphSkillSource
from app.agents.agent_loop.skills.catalog_source import SkillPacksCatalogSource
from app.agents.agent_loop.skills.graph_store import GraphSkillStore
from app.agents.mcp.catalog_source import McpTemplatesCatalogSource
from app.agents.mcp.models import MCPAuthMode, MCPServerTemplate, MCPTransport
from app.agents.registry.catalog_source import ToolsetsCatalogSource
from app.agents.registry.toolset_registry import Toolset, ToolsetRegistry
from app.connectors.core.registry.tool_builder import ToolProvider
from app.control_plane.catalog.digests import (
    descriptor_set_digest,
    engine_digest,
    tool_descriptor_digest,
)
from app.harness.assembly.descriptor_pins import ToolDescriptors
from app.harness.engines.catalog_source import EnginesCatalogSource
from harness_core.engines.types import EngineVersion
from harness_core.skills import SkillRef
from harness_engines.release import EngineRelease
from tests.unit.agents.adapter.test_skills_graph_store import FakeGraphProvider

if TYPE_CHECKING:
    from pathlib import Path

_CREATE_PARAMS = [
    ToolParameter(name="title", type=ParameterType.STRING, description="Title", required=True),
    ToolParameter(name="labels", type=ParameterType.ARRAY, description="Labels", required=False, items={"type": "string"}),
]


@Toolset(
    provider=ToolProvider.CONNECTOR,
    name="Demo Desk",
    app_group="Demo",
    supported_auth_types=["OAUTH", "API_TOKEN"],
    description="Demo ticketing",
    config={"iconPath": "/icons/demo.svg", "auth": {"schemas": {"OAUTH": {"fields": []}}}},
)
class _DemoDesk:
    @tool(
        path="/tools/demo/create_ticket",
        short_description="Create a ticket",
        description="Create a ticket in Demo Desk.",
        parameters=_CREATE_PARAMS,
        tags=[Tag("effect", "write"), Tag("sink", "self")],
    )
    async def create_ticket(self, title: str, labels: list[str] | None = None) -> str:
        return title

    @tool(
        path="/tools/demo/list_tickets",
        short_description="List tickets",
        description="List tickets.",
        parameters=[],
        tags=[Tag("effect", "read")],
    )
    async def list_tickets(self) -> list[str]:
        return []


@Toolset(name="Demo Internal", app_group="Internal", supported_auth_types="NONE", internal=True, provider=ToolProvider.PLATFORM)
class _DemoInternal:
    @tool(path="/tools/demo_internal/ping", short_description="Ping", description="Ping.", parameters=[])
    async def ping(self) -> str:
        return "pong"


@pytest.fixture
def registry() -> ToolsetRegistry:
    registry = ToolsetRegistry()
    registry._toolsets = {}  # pyright: ignore[reportPrivateUsage]
    registry.register_toolset(_DemoDesk)
    registry.register_toolset(_DemoInternal)
    return registry


def test_cpd2_20_one_item_per_toolset_with_its_tools(registry: ToolsetRegistry) -> None:
    items = {item.name: item for item in ToolsetsCatalogSource(registry).items()}
    assert set(items) == {"demodesk", "demointernal"}
    desk = items["demodesk"].to_json()
    assert desk["kind"] == "toolset"
    assert desk["ref"] == f"toolset:pipeshub/demodesk@{desk['version']}"
    assert desk["display"] == {
        "name": "Demo Desk",
        "description": "Demo ticketing",
        "icon": "/icons/demo.svg",
        "categories": ["app", "Demo"],
        "hidden": False,
    }
    assert desk["credentialRequirements"] == [
        {"mode": "oauth", "owners": ["agent", "user"], "provider": "demodesk"},
        {"mode": "api_token", "owners": ["agent", "user"], "provider": None},
    ]
    assert desk["configSchema"]["iconPath"] == "/icons/demo.svg"
    tools = {d["name"]: d for d in desk["descriptors"]["items"]}
    assert desk["descriptors"]["kind"] == "tool"
    declared = registry.get_toolset_metadata("demodesk", serialize=True)["tools"]  # type: ignore[index]
    create = next(t for t in declared if t["name"] == "create_ticket")
    assert tools["create_ticket"] == {
        "name": "create_ticket",
        "description": create["description"],
        "inputSchema": {"parameters": create["parameters"]},
        "effect": "write",
        "sink": "self",
        "provider": "connector",
        "digest": tool_descriptor_digest(tools["create_ticket"]),
    }
    assert [p["name"] for p in tools["create_ticket"]["inputSchema"]["parameters"]] == ["title", "labels"]
    assert tools["list_tickets"]["effect"] == "read"
    assert tools["list_tickets"]["sink"] is None
    assert desk["riskClass"] == "write-external"
    assert desk["descriptorSetDigest"] == descriptor_set_digest((n, d["digest"]) for n, d in tools.items())


def test_cpd2_20_internal_toolsets_are_hidden_and_untagged_tools_rank_as_writes(registry: ToolsetRegistry) -> None:
    internal = next(i for i in ToolsetsCatalogSource(registry).items() if i.name == "demointernal").to_json()
    assert internal["display"]["hidden"] is True
    assert internal["credentialRequirements"] == [{"mode": "none", "owners": ["agent", "user"], "provider": None}]
    assert internal["descriptors"]["items"][0]["effect"] is None
    assert internal["riskClass"] == "write-external"


def test_cpd2_20_a_tool_change_is_a_new_version(registry: ToolsetRegistry) -> None:
    before = {i.name: i for i in ToolsetsCatalogSource(registry).items()}["demodesk"]
    registry._toolsets["demodesk"]["description"] = "Changed"  # pyright: ignore[reportPrivateUsage]
    after = {i.name: i for i in ToolsetsCatalogSource(registry).items()}["demodesk"]
    assert after.version != before.version


class _Templates:
    def __init__(self, *templates: MCPServerTemplate) -> None:
        self._templates = templates

    def list_templates(self) -> list[MCPServerTemplate]:
        return list(self._templates)


def test_cpd2_21_one_item_per_mcp_template_without_descriptors() -> None:
    template = MCPServerTemplate(
        type_id="demo_mcp",
        display_name="Demo MCP",
        description="A demo server",
        icon="/icons/demo.svg",
        transport=MCPTransport.STREAMABLE_HTTP,
        default_auth_mode=MCPAuthMode.OAUTH,
        supported_auth_modes=[MCPAuthMode.OAUTH, MCPAuthMode.API_TOKEN],
        default_url="https://mcp.example.com/mcp",
        tags=["project"],
    )
    (item,) = McpTemplatesCatalogSource(_Templates(template)).items()
    wire = item.to_json()
    assert wire["ref"].startswith("mcp:pipeshub/demo_mcp@")
    assert wire["display"] == {
        "name": "Demo MCP",
        "description": "A demo server",
        "icon": "/icons/demo.svg",
        "categories": ["project"],
        "hidden": False,
    }
    assert wire["descriptors"] == {"kind": "tool", "items": []}
    assert wire["descriptorSetDigest"] == descriptor_set_digest([])
    assert wire["configSchema"]["transport"] == "streamable_http"
    assert wire["configSchema"]["defaultUrl"] == "https://mcp.example.com/mcp"
    assert "typeId" not in wire["configSchema"] and "displayName" not in wire["configSchema"]
    assert [c["mode"] for c in wire["credentialRequirements"]] == ["oauth", "api_token"]
    assert wire["riskClass"] == "write-external"


_PACK = """---
name: demo-pack
description: Use demo-pack when demoing
metadata:
  agent-loop:
    source: builtin
    pack_name: demo-pack
    pack_version: 1.2.0
---

Demo instructions.
"""


@pytest.fixture
def packs_root(tmp_path: Path) -> str:
    pack = tmp_path / "packs" / "demo-pack"
    os.makedirs(pack / "scripts")
    (pack / "SKILL.md").write_text(_PACK, encoding="utf-8")
    (pack / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    return str(tmp_path / "packs")


async def test_cpd2_22_skill_item_digest_is_what_admission_pins_for_the_seeded_copy(packs_root: str) -> None:
    seeder = BuiltinSkillSeeder(packs_root)
    (item,) = SkillPacksCatalogSource(seeder).items()
    wire = item.to_json()
    assert wire["ref"] == "skill:pipeshub/demo-pack@1.2.0"
    (descriptor,) = wire["descriptors"]["items"]
    assert wire["descriptors"]["kind"] == "skill-bundle"
    assert set(descriptor["files"]) == {"SKILL.md", "scripts/run.py"}

    graph = FakeGraphProvider()
    await seeder.sync(GraphSkillStore(graph, "org-1", SEED_IDENTITY, allow_builtin_writes=True))  # type: ignore[arg-type]
    pinned = await GraphSkillSource(graph).content_digest("org-1", SkillRef("demo-pack"))  # type: ignore[arg-type]
    assert descriptor["digest"] == pinned


def test_cpd2_23_one_item_per_allowlisted_engine_version() -> None:
    release = EngineRelease(
        version=EngineVersion(kind="opencode", version="1.18.34", binary_digest="sha256:" + "a" * 64),
        image="pxh-opencode-tools:1.18.34",
        supply="release",
    )
    (item,) = EnginesCatalogSource([release]).items()
    wire = item.to_json()
    assert wire["ref"] == "engine:pipeshub/opencode@1.18.34"
    (descriptor,) = wire["descriptors"]["items"]
    assert descriptor == {
        "name": "opencode",
        "kind": "opencode",
        "version": "1.18.34",
        "binaryDigest": "sha256:" + "a" * 64,
        "optionsDigest": None,
        "digest": engine_digest(descriptor),
    }
    assert wire["riskClass"] == "execute"
    assert wire["configSchema"] is None
    assert wire["credentialRequirements"] == []


def test_cpd2_20_a_toolset_tool_s_catalog_digest_is_its_run_pin_digest(registry: ToolsetRegistry) -> None:
    pins = ToolDescriptors(cast("Any", None))
    for item in ToolsetsCatalogSource(registry).items():
        for declared in item.to_json()["descriptors"]["items"]:  # type: ignore[index]
            assert declared["digest"] == pins.toolset_tool(item.name, declared["name"]), (item.name, declared["name"])
