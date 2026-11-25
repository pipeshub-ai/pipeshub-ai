"""MCPFIX F3: an agent's attachment resolves by the server's own tool name, whatever name the model sees.

The hub exports a tool under at most 52 characters now (64 before): a tool whose exported name was longer reaches
the model under a shorter one. An attachment saved before names the old exported name and the raw name; the loader
works the exported name out again from the raw one, so it still loads, and one that names neither does not.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from harness_core.mcp_hub import export_name
from harness_core.mcp_hub.names import EXPORT_NAME_MAX
from harness_core.testing.clock import ManualClock
from tests.unit.harness.credentials.support import DictConfig
from tests.unit.harness.mcp_hub.support import (
    URL,
    FakeServer,
    context,
    hub_on,
    load,
    serve,
    teardown,
)

if TYPE_CHECKING:
    from app.agent_loop_lib.tools.registry import ToolRegistry

RAW = "generate_quarterly_revenue_report_summary_east"
# What the hub exported this tool as before the bound moved: the plain name, 55 characters.
OLD_EXPORTED = f"mcp_fake_{RAW}"


def _report() -> str:
    return "report"


@pytest.fixture
def server(monkeypatch: pytest.MonkeyPatch) -> FakeServer:
    made = FakeServer()
    made.mcp.add_tool(_report, name=RAW, description="A report.", structured_output=False)
    _ = serve(monkeypatch, {URL: made})
    _ = hub_on(monkeypatch, ManualClock())
    return made


def _mcp_names(registry: ToolRegistry) -> set[str]:
    return {name for name in registry.names() if name.startswith("mcp_")}


async def test_f3_4_an_attachment_saved_under_the_old_longer_name_still_loads(server: FakeServer) -> None:
    config = DictConfig()
    exported = export_name("fake", RAW)
    assert len(OLD_EXPORTED) > EXPORT_NAME_MAX >= len(exported) and exported != OLD_EXPORTED
    ctx = context(config, tools=[{"name": RAW, "fullName": OLD_EXPORTED}])
    registry = await load(ctx)
    assert _mcp_names(registry) == {exported}
    result = await registry.resolve_by_name(exported).execute()
    assert result.success and result.data == "report"
    await teardown(ctx)


async def test_f3_5_an_attachment_that_names_another_tool_loads_nothing_of_this_one(server: FakeServer) -> None:
    config = DictConfig()
    ctx = context(config, tools=[{"name": "search", "fullName": "mcp_fake_search"}])
    assert _mcp_names(await load(ctx)) == {"mcp_fake_search"}
    await teardown(ctx)
