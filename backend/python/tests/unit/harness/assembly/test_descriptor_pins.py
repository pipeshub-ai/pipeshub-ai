"""CPS PN-1, PN-2, PN-4..PN-8, PN-10 and QA-3 PN-13..PN-15: a run binds to the descriptors and approvals admission
bound (R2 F4), over everything that decides what a call can do.

The definition route's pinning (``pin_tool_descriptors``) and the claim's check (the item runtimes, run by
the real ``PipesHubRuntimeAssembler``) read the same ``ToolDescriptors``: the release's toolset registry (the assembly
world's catalog, answered by the registry's own discovery) and the MCP hub's approvals in configuration (the
configuration fake). Digests are checked against ``contracts/harness/vectors/descriptor-pins.json``, which Node's
half reads too.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.agents.run_definition import build_run_definition, pin_tool_descriptors
from app.control_plane.catalog.digests import tool_descriptor_digest
from app.harness.assembly.assembler import PipesHubRuntimeAssembler
from app.harness.assembly.descriptor_pins import LiveMcpDescriptors, ToolDescriptors
from app.harness.mcp_hub.manifests import descriptor_of
from harness_core.contracts import dump_contract, validate_contract
from harness_core.contracts.generated.run_spec import RunSpec
from harness_core.driver import CapabilityLost
from harness_core.mcp_hub.manifest import ApprovedManifest, ToolDescriptor
from harness_core.testing import ModelScript, Reply, ScriptedModel, Turn
from harness_core.testing.durable import ScriptedModelPort
from harness_core.tools.tags import (
    TAG_EFFECT_READ,
    TAG_EFFECT_WRITE,
    TAG_SINK_EXTERNAL,
    TAG_SINK_SELF,
)
from tests.cohesion.support import REPO_ROOT
from tests.support.mcp_tool_admission import approve_tools, mcp_approvals_required  # noqa: F401 - the fixture, used by name

from . import releases
from . import world as w

if TYPE_CHECKING:
    from collections.abc import Iterator

    from tests.support.config_service import FakeConfigurationService

VECTORS: dict[str, Any] = json.loads((REPO_ROOT / "contracts/harness/vectors/descriptor-pins.json").read_text())
JIRA_SEARCH = f"jira@{w.JIRA}/search_issues"
MCP_PRS = f"mcp@{w.MCP}/list_prs"


@pytest.fixture(autouse=True)
def _deployment() -> Iterator[None]:
    with w.deployment():
        yield


@pytest.fixture
def approvals_govern() -> Iterator[None]:
    """The hub on at the claim: its approvals are what an MCP pin is checked against (discovery stays the world's)."""
    with patch("app.harness.assembly.assembler.mcp_hub_enabled", return_value=True):
        yield


@pytest.fixture
def hub_off() -> Iterator[None]:
    with patch("app.harness.assembly.assembler.mcp_hub_enabled", return_value=False):
        yield


def _approve(config: FakeConfigurationService, *tools: ToolDescriptor) -> None:
    approve_tools(w.MCP, tools)


def _prs(description: str = "List pull requests.") -> ToolDescriptor:
    return descriptor_of(w.MCP_TOOLS[w.MCP][0].model_copy(update={"description": description}))


def _v2(spec: RunSpec, tools: dict[str, str], mcp: dict[str, str]) -> RunSpec:
    raw = dump_contract(spec)
    raw["schemaVersion"] = 3
    for grant in raw["grants"]["tools"]:
        if grant["toolId"] in tools:
            grant.update(descriptorDigest=tools[grant["toolId"]], digestVersion=2)
    for server in raw["extensions"]["x.pipeshub"]["mcp"]:
        if server["instanceId"] in mcp:
            server.update(manifestDigest=mcp[server["instanceId"]], digestVersion=2)
    return validate_contract(RunSpec, raw)


async def _build(services: w.Services, spec: RunSpec) -> None:
    port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
    await PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port)).build(w.claim(spec))  # type: ignore[arg-type]


def _jira_digest(services: w.Services) -> str:
    digest = ToolDescriptors(services.config).toolset_tool("jira", "search_issues")
    assert digest is not None
    return digest


RELEASED_CHANGES: dict[str, dict[str, Any]] = {
    "an enum that gained a value": {"statuses": ("open", "closed", "deleted")},
    "array items of another type": {"items": {"type": "object"}},
    "a nested property of another type": {"sprint": {"type": "object", "properties": {"id": {"type": "string"}}}},
    "a nested property no longer required": {"required": ()},
    "a read that became a write": {"tags": (TAG_EFFECT_WRITE, TAG_SINK_SELF)},
    "a sink that became external": {"tags": (TAG_EFFECT_READ, TAG_SINK_EXTERNAL)},
}
def _release(monkeypatch: pytest.MonkeyPatch, **declared: Any) -> None:  # noqa: ANN401
    releases.deploy(monkeypatch, jira=releases.jira(**declared))


def _toolset_class(jira: type) -> type:
    from app.agents.registry.toolset_registry import Toolset
    from app.connectors.core.registry.tool_builder import ToolProvider

    return Toolset(name="QA3 Jira", app_group="Demo", supported_auth_types="NONE", provider=ToolProvider.CONNECTOR)(jira)


class TestDescriptors:
    @pytest.mark.parametrize("case", VECTORS["descriptor"], ids=lambda c: c["name"])
    def test_pn1_a_descriptor_digest_is_the_shared_vector(self, case: dict[str, Any]) -> None:
        tool = case["tool"]
        assert ToolDescriptor(tool["name"], tool["description"], tool["inputSchema"]).digest() == case["digest"]

    def test_pn1_a_changed_parameter_changes_the_digest_and_key_order_does_not(self) -> None:
        changed, required = VECTORS["descriptor"][0], VECTORS["descriptor"][1]
        assert changed["digest"] != required["digest"]
        reordered = dict(reversed(list(changed["tool"]["inputSchema"].items())))
        assert ToolDescriptor(changed["tool"]["name"], changed["tool"]["description"], reordered).digest() == changed["digest"]

    def test_pn1_a_toolset_tool_s_digest_covers_what_its_code_declares(self, monkeypatch: pytest.MonkeyPatch) -> None:
        services = w.Services()
        declared = _jira_digest(services)
        original = w.ToolsetCatalog.get_toolset_metadata

        def widened(self: w.ToolsetCatalog, name: str, serialize: bool = True) -> dict[str, Any] | None:
            metadata = original(self, name, serialize)
            for tool in (metadata or {}).get("tools", []):
                tool["parameters"] = [*tool["parameters"], {"name": "project", "type": "string", "required": False}]
            return metadata

        monkeypatch.setattr(w.ToolsetCatalog, "get_toolset_metadata", widened)
        assert _jira_digest(services) != declared
        assert ToolDescriptors(services.config).toolset_tool("jira", "no_such_tool") is None

    @pytest.mark.parametrize("family", VECTORS["change"], ids=lambda f: f["name"])
    def test_pn13_every_field_that_decides_a_call_moves_the_digest_and_provider_does_not(
        self, family: dict[str, Any]
    ) -> None:
        base = family["base"]
        assert tool_descriptor_digest(base["tool"]) == base["digest"]
        for case in family["changed"]:
            assert tool_descriptor_digest(case["tool"]) == case["digest"], case["name"]
        digests = [base["digest"], *(case["digest"] for case in family["changed"])]
        assert len(set(digests)) == len(digests), "two different tools share a digest"
        for case in family["unchanged"]:
            assert tool_descriptor_digest(case["tool"]) == base["digest"], case["name"]

    @pytest.mark.parametrize("change", RELEASED_CHANGES, ids=str)
    def test_pn14_a_toolset_tool_s_digest_moves_with_every_constraint_its_code_declares(
        self, change: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services = w.Services()
        _release(monkeypatch)
        declared = _jira_digest(services)
        _release(monkeypatch)
        assert _jira_digest(services) == declared, "the same declaration is the same digest"

        _release(monkeypatch, **RELEASED_CHANGES[change])

        assert _jira_digest(services) != declared

    def test_pn14_the_registry_serves_every_tool_s_parameters_as_its_code_declares_them(self) -> None:
        """The digest is taken over the registry's serialized tools: its sanitizer must not drop a schema member."""
        from app.agents.registry.toolset_registry import ToolsetRegistry

        registry = ToolsetRegistry()
        assert registry.register_toolset(_toolset_class(releases.jira()))
        served = (registry.get_toolset_metadata("QA3 Jira") or {})["tools"][0]["parameters"]
        assert {p["name"]: {k: p[k] for k in p if k not in ("name", "type", "description", "required")} for p in served} == {
            "status": {"enum": ["open", "closed"]},
            "labels": {"items": {"type": "string"}},
            "filter": {"properties": {"assignee": {"type": "string"}, "sprint": releases.SPRINT}, "requiredProperties": ["assignee"]},
        }

    def test_pn14_no_released_tool_loses_a_schema_member_on_its_way_to_the_digest(self) -> None:
        from app.agents.registry.toolset_registry import ToolsetRegistry

        registry = ToolsetRegistry()
        registry.auto_discover_toolsets()
        lossy = [
            f"{name}.{declared['name']}"
            for name in registry.get_all_toolsets()
            for declared, served in zip(
                (registry.get_toolset_metadata(name, serialize=False) or {})["tools"],
                (registry.get_toolset_metadata(name, serialize=True) or {})["tools"],
                strict=True,
            )
            if declared["parameters"] != served["parameters"]
        ]
        assert lossy == []

    @pytest.mark.parametrize("case", VECTORS["mcpManifestMoves"], ids=lambda c: c["name"])
    def test_mcp26d_a_pin_moves_with_its_granted_tools_approvals_only(self, case: dict[str, Any]) -> None:
        pinned = ApprovedManifest(case["approved"], "p_admin", 1).granted_digest(case["granted"])
        current = ApprovedManifest(case["reapproved"], "p_admin", 2).granted_digest(case["granted"])
        assert (pinned, current) == (case["pinned"], case["current"])
        granted_moved = any(case["approved"].get(name) != case["reapproved"].get(name) for name in case["granted"])
        assert (pinned != current) is granted_moved

    @pytest.mark.parametrize("case", VECTORS["mcpManifest"], ids=lambda c: c["name"])
    def test_pn4_the_granted_manifest_digest_is_the_shared_vector(self, case: dict[str, Any]) -> None:
        assert ApprovedManifest(case["approved"], "p_admin", 1).granted_digest(case["granted"]) == case["digest"]


@pytest.mark.usefixtures("mcp_approvals_required")
class TestDefinition:
    async def test_pn2_the_definition_binds_toolset_descriptors_and_approved_mcp_entries(self) -> None:
        services = w.Services()
        _approve(services.config, _prs())
        definition = build_run_definition(
            w.agent_doc(), agent_key=w.AGENT, actions_enabled=True, mcp_enabled=True, web_search_provider=None
        )

        pinned = await pin_tool_descriptors(definition, ToolDescriptors(services.config), mcp_approvals=True)

        jira = {tool.name: tool.descriptor_digest for tool in pinned.toolsets[0].tools}
        assert jira["search_issues"] == _jira_digest(services) and jira["create_issue"] is not None
        assert pinned.mcp_servers[0].tools[0].descriptor_digest == _prs().digest()
        wire = pinned.model_dump(by_alias=True, mode="json")
        assert wire["mcpServers"][0]["tools"][0]["descriptorDigest"] == _prs().digest()

    async def test_pn2_without_approvals_governing_mcp_or_an_approval_an_mcp_tool_binds_nothing(self) -> None:
        services = w.Services()
        definition = build_run_definition(
            w.agent_doc(), agent_key=w.AGENT, actions_enabled=True, mcp_enabled=True, web_search_provider=None
        )
        unapproved = await pin_tool_descriptors(definition, ToolDescriptors(services.config), mcp_approvals=True)
        _approve(services.config, _prs())
        hub_off = await pin_tool_descriptors(definition, ToolDescriptors(services.config), mcp_approvals=False)

        for pinned in (unapproved, hub_off):
            assert pinned.mcp_servers[0].tools[0].descriptor_digest is None
            assert "descriptorDigest" not in pinned.model_dump(by_alias=True)["mcpServers"][0]["tools"][0]


@pytest.mark.usefixtures("mcp_approvals_required")
class TestClaim:
    @pytest.mark.usefixtures("approvals_govern")
    async def test_pn7_unchanged_descriptors_and_approvals_build(self) -> None:
        services = w.Services()
        _approve(services.config, _prs())
        approved = {"list_prs": _prs().digest()}
        spec = _v2(
            w.pinned_spec(tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP]),
            {JIRA_SEARCH: _jira_digest(services), MCP_PRS: _prs().digest()},
            {w.MCP: ApprovedManifest(approved, "p_admin", 1).granted_digest(approved) or ""},
        )
        await _build(services, spec)

    async def test_pn5_a_toolset_schema_that_changed_since_admission_is_capability_lost(self) -> None:
        services = w.Services()
        admitted = VECTORS["descriptor"][0]["digest"]
        with pytest.raises(CapabilityLost) as lost:
            await _build(services, _v2(w.pinned_spec(tool_ids=[JIRA_SEARCH]), {JIRA_SEARCH: admitted}, {}))
        assert lost.value.changed == (f"tool:{JIRA_SEARCH}",)

    @pytest.mark.parametrize("change", RELEASED_CHANGES, ids=str)
    async def test_pn15_a_toolset_tool_whose_schema_effect_or_sink_changed_since_admission_is_capability_lost(
        self, change: str, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        services = w.Services()
        _release(monkeypatch)
        spec = _v2(w.pinned_spec(tool_ids=[JIRA_SEARCH]), {JIRA_SEARCH: _jira_digest(services)}, {})
        await _build(services, spec)

        _release(monkeypatch, **RELEASED_CHANGES[change])

        with pytest.raises(CapabilityLost) as lost:
            await _build(services, spec)
        assert lost.value.changed == (f"tool:{JIRA_SEARCH}",)

    @pytest.mark.usefixtures("hub_off")
    async def test_mcplive_tools_discovered_at_admission_build_while_they_are_unchanged(self) -> None:
        services = w.Services()
        live = {w.MCP: {"list_prs": _prs().digest()}}
        definition = await pin_tool_descriptors(
            build_run_definition(
                w.agent_doc(), agent_key=w.AGENT, actions_enabled=True, mcp_enabled=True, web_search_provider=None
            ),
            LiveMcpDescriptors(ToolDescriptors(services.config), live),
            mcp_approvals=True,
        )
        assert definition.mcp_servers[0].tools[0].descriptor_digest == _prs().digest()
        spec = _v2(
            w.pinned_spec(tool_ids=[MCP_PRS], mcp=[w.MCP]),
            {MCP_PRS: _prs().digest()},
            {w.MCP: ApprovedManifest(live[w.MCP], "live", 0).granted_digest(["list_prs"]) or ""},
        )
        await _build(services, spec)

    @pytest.mark.usefixtures("hub_off")
    async def test_mcplive_a_tool_that_changed_since_admission_is_withheld_and_never_registered(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("PIPESHUB_ENABLE_LAZY_TOOLS", "false")  # listed at the claim, as D-LAZY-4 keeps it here
        # WP-84 (WP-79 D2 with the hub off): a server that answered with the tool at another digest withheld it; the
        # run never registers or calls what was not pinned, and a run that had it fails at once (WP84-MCP-7).
        services = w.Services()
        admitted = {"list_prs": _prs().digest()}
        spec = _v2(
            w.pinned_spec(tool_ids=[MCP_PRS], mcp=[w.MCP]),
            {MCP_PRS: _prs().digest()},
            {w.MCP: ApprovedManifest(admitted, "live", 0).granted_digest(["list_prs"]) or ""},
        )
        changed = w.MCP_TOOLS[w.MCP][0].model_copy(update={"description": "List pull requests, and close them."})
        monkeypatch.setitem(w.MCP_TOOLS, w.MCP, [changed])

        port = ScriptedModelPort(ScriptedModel(ModelScript(id="t", turns=[Turn(reply=Reply(text="ok"))])))
        runtime = await PipesHubRuntimeAssembler(services, model_factory=w.model_factory(port)).build(w.claim(spec))  # type: ignore[arg-type]
        assert runtime.capability_check is not None
        catalog = runtime.capability_check._catalog  # type: ignore[attr-defined]
        try:
            assert MCP_PRS not in {t.tool_id for t in catalog.tools}
            assert "mcp_github_list_prs" not in catalog.registry.names()
            assert catalog.withheld == {MCP_PRS: "GitHub"}
        finally:
            if runtime.close is not None:
                await runtime.close()

    async def test_pn8_unversioned_pins_are_read_and_not_verified(self) -> None:
        await _build(w.Services(), w.pinned_spec(tool_ids=[JIRA_SEARCH, MCP_PRS], mcp=[w.MCP]))


def test_pn10_a_digest_version_this_build_does_not_know_is_refused() -> None:
    raw = dump_contract(w.pinned_spec(tool_ids=[JIRA_SEARCH]))
    raw["grants"]["tools"][0]["digestVersion"] = 3
    with pytest.raises(ValidationError):
        validate_contract(RunSpec, raw)
