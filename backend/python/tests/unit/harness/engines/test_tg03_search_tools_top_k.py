"""TG-03: ``search_tools`` answers the most relevant tools of the grant, cut to the asked top-k, under the names the
engine is shown (namespaced: ``pipeshub_<toolset>_<tool>``, an MCP tool under its server's prefix).

The ranking is the product's (``engine_disclosure``'s ``rank``, the loop's ``ToolsetAwareToolIndex``) over a real
registry; the cut, the clamp and the answer's shape are ``LazyDisclosure.search``'s. The spec's three detail levels
(name, summary, full) were built as one answer per match that carries all three: name, description, input schema.
"""

from __future__ import annotations

import json
from typing import cast

import pytest

from app.agent_loop_lib.tools.registry import ToolRegistry
from app.harness.engines.durable import engine_disclosure
from harness_core.gateway.disclosure import SEARCH_LIMIT
from harness_core.kernel.core.tool_schema import ToolSchema
from harness_core.tool_identity.spec_names import ModelNames
from tests.unit.agent_loop_lib.tools.test_registry import _SimpleTool

MCP = [f"mcp_tracker_ticket_tool_{i:02d}" for i in range(40)]
CONNECTOR = [f"github__{verb}_pull_request" for verb in ("create", "merge", "close", "review", "comment")]
GRANT = ["search_knowledge", *CONNECTOR, *MCP]


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in GRANT:
        registry.register_tool(_SimpleTool(name))
    registry.register_toolset("retrieval", "Knowledge.", ["search_knowledge"])
    registry.register_toolset("github", "GitHub pull requests.", CONNECTOR)
    registry.register_toolset("mcp", "MCP servers.", [])
    registry.register_toolset("mcp_tracker", "Tracker MCP tickets.", MCP, parent="mcp")
    return registry


def _schemas() -> list[ToolSchema]:
    return [ToolSchema(name=n, description=f"{n} full description", input_schema={"type": "object"}) for n in GRANT]


@pytest.fixture(autouse=True)
def _defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("PIPESHUB_ENABLE_LAZY_TOOLS", "PIPESHUB_ENGINE_LAZY_TOOLS_THRESHOLD"):
        monkeypatch.delenv(name, raising=False)


async def _search(arguments: dict[str, object]) -> list[dict[str, object]]:
    disclosure = engine_disclosure(_registry(), GRANT, ["retrieval"], "lazy")
    assert disclosure is not None
    names = ModelNames.spec(GRANT)
    result = await disclosure.search(cast("dict", arguments), _schemas(), names.shown)
    assert not result.is_error, result.text
    return cast("list[dict[str, object]]", json.loads(result.text))


async def test_tg03_1_top_k_cuts_to_the_asked_count_of_the_most_relevant_tools() -> None:
    found = await _search({"query": "merge pull request", "limit": 3})
    assert len(found) == 3
    assert found[0]["name"] == "pipeshub_github_merge_pull_request"
    assert all(str(f["name"]).startswith("pipeshub_github_") for f in found)


async def test_tg03_2_the_default_is_eight_and_any_ask_is_held_between_one_and_twenty() -> None:
    assert len(await _search({"query": "tracker ticket tool"})) == SEARCH_LIMIT
    assert len(await _search({"query": "tracker ticket tool", "limit": 500})) == 20
    assert len(await _search({"query": "tracker ticket tool", "limit": 0})) == 1
    assert len(await _search({"query": "tracker ticket tool", "limit": True})) == SEARCH_LIMIT


async def test_tg03_3_each_match_names_its_namespaced_id_summary_and_full_schema_from_the_grant() -> None:
    found = await _search({"query": "tracker ticket tool", "limit": 20})
    assert {tuple(sorted(f)) for f in found} == {("description", "inputSchema", "name")}
    shown = {str(f["name"]) for f in found}
    # An MCP tool keeps its server's prefix; nothing outside the grant is ever answered.
    assert shown <= set(MCP)
    assert all(f["inputSchema"] == {"type": "object"} and str(f["description"]).endswith("full description") for f in found)
