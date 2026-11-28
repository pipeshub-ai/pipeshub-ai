"""SEC-B-02-FU-1 (ADR-OWD-09 D-MCP-OPENWORLD 4): an org admin marks an MCP instance as inside the organization.

Since SEC-B-02 a read-only MCP tool that does not declare ``openWorldHint: false`` is ``external``, so after a private
read the flow guard holds it. For a server the org runs itself (an internal wiki, a team tracker) that asked on every
read, and the only way out was the server's own claim. The admin's instance setting now keeps its tools' arguments
inside (sink ``self``), whatever the hints; it never makes a write a read, and only a stored ``True`` counts.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.agents.agent_loop.mcp_tool_adapter import MCPToolAdapter
from app.agents.mcp.boundary import inside_organization
from app.agents.mcp.models import MCPToolInfo
from harness_core.effects.flow import FlowGuardedPolicy, FlowRule
from harness_core.effects.types import allow_all
from harness_core.kernel.core.messages import ToolCall
from harness_core.kernel.durable.effects import classify_tool_effect
from harness_core.logs import HarnessLog
from harness_core.mcp_hub.annotations import hint_tags

READ_ONLY = {"readOnlyHint": True}


def _adapter(hints: dict[str, Any] | None, instance: dict[str, Any]) -> MCPToolAdapter:
    server = SimpleNamespace(instance_id="inst-1", display_name="Wiki", name="wiki", instance=instance)
    info = MCPToolInfo(name="search", namespaced_name="mcp_wiki_search", description="Search the wiki.",
                       input_schema={}, annotations=hints)
    return MCPToolAdapter(server, info, session_manager=None)  # type: ignore[arg-type]


def _effect(hints: dict[str, Any] | None, instance: dict[str, Any]) -> Any:  # noqa: ANN401
    tool = _adapter(hints, instance)
    return classify_tool_effect(tool.name, tool.tags)


class _PrivateAndUntrusted:
    def source_records(self, run_id: str) -> frozenset[str]:
        return frozenset({"rec-private-1"})

    def untrusted_read(self, run_id: str) -> bool:
        return True


@pytest.mark.parametrize(("hints", "effect"), [(READ_ONLY, "read"), (None, "write"), ({"openWorldHint": True}, "write")])
def test_io1_an_instance_inside_the_organization_keeps_every_tools_arguments_inside(
    hints: dict[str, Any] | None, effect: str
) -> None:
    inside = _effect(hints, {"insideOrganization": True})
    assert (inside.effect, inside.sink.egress) == (effect, "self"), "the effect is the hints', never widened to a read"
    outside = _effect(hints, {})
    assert (outside.effect, outside.sink.egress) == (effect, "external")


@pytest.mark.parametrize("value", ["true", 1, "yes", None, False, {"x": 1}])
def test_io2_only_a_stored_true_is_the_admins_choice(value: object) -> None:
    assert inside_organization({"insideOrganization": value}) is False
    assert _effect(READ_ONLY, {"insideOrganization": value}).sink.egress == "external"
    assert inside_organization(None) is False


def test_io3_the_mapping_takes_the_setting_and_keeps_the_hints_default_without_it() -> None:
    assert [t.value for t in hint_tags(READ_ONLY)][1] != [t.value for t in hint_tags(READ_ONLY, inside_organization=True)][1]
    assert hint_tags({"openWorldHint": False}) == hint_tags({}, inside_organization=True)


@pytest.mark.parametrize("level", ["ask", "deny"])
def test_io4_after_a_private_read_the_flow_guard_lets_an_inside_servers_read_through(level: str) -> None:
    policy = FlowGuardedPolicy(allow_all, FlowRule(level), _PrivateAndUntrusted(), "run-1",  # type: ignore[arg-type]
                               HarnessLog.named("tests.r43mcp.inside"))
    call = ToolCall(id="c", name="mcp_wiki_search", arguments={"query": "the private record"})
    assert policy(call, _effect(READ_ONLY, {})) == level
    assert policy(call, _effect(READ_ONLY, {"insideOrganization": True})) == "allow"
