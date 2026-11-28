"""WP84-MCP-13: the engine host hands its kernel the attempt's granted tools as the loop observes them, a tool its
MCP server withdrew marked with the server's label (WP-79 D3 on engine runs; the kernel's half is
``tests/unit/harness_core/engines/durable/test_wp84_engine_withdrawn.py``)."""

from __future__ import annotations

from app.harness.assembly.assembler import Catalog
from app.harness.engines.durable import granted_capabilities
from harness_core.kernel.durable.capability import withdrawn_mark
from harness_core.tools.registry import ToolRegistry


async def test_wp84_mcp_13_the_engine_host_observes_a_withheld_tool_as_withdrawn_by_its_server() -> None:
    catalog = Catalog(ToolRegistry(), (), withheld={"mcp@mcp-9/search": "GitHub"})
    observe = granted_capabilities(catalog)
    assert await observe() == {"tool:mcp@mcp-9/search": withdrawn_mark("GitHub")}


async def test_wp79_return_2_the_engine_host_withholds_a_returned_tool_from_its_registry() -> None:
    from app.harness.assembly.tool_sources import SourcedTool
    from app.harness.engines.durable import withholding
    from harness_core.provenance.rules import MCP_RULE
    from tests.unit.harness_core.tools.test_registry_restricted import _Named

    registry = ToolRegistry()
    registry.register_tool(_Named("mcp_github_search"))
    tool_id = "mcp@mcp-9/search"
    catalog = Catalog(registry, (SourcedTool("mcp_github_search", tool_id, tool_id, "external", provenance=MCP_RULE),))

    await withholding(catalog)({f"tool:{tool_id}": withdrawn_mark("GitHub")})

    assert not registry.has("mcp_github_search")
    assert await granted_capabilities(catalog)() == {f"tool:{tool_id}": withdrawn_mark("GitHub")}
