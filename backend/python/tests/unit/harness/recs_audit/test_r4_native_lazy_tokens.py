"""R4 (recs2): what fixing the native loop's tool list for the session costs and saves, measured on the real catalog.

Same method as ``test_r3_tool_definition_tokens.py``: o200k_base tokens of the JSON a model is sent, over every tool
``ToolsetRegistry.auto_discover_toolsets()`` finds.

* ``meta``: the meta-tools a lazy grant lists (``list_toolsets``, ``fetch_tools``, ``search_tools``, and since recs2
  ``call_tool``): what lazy disclosure lists on top of the essentials.
* Before recs2 every toolset ``fetch_tools`` loaded joined the list of every later request of the run; now its
  schemas come back once, in the result, and the list stays as it was. ``toolset_median`` / ``toolset_max`` are what
  one loaded toolset added to each later request.
* ``session_before`` / ``session_after``: the tool-list tokens of a ten-request session that loads three median
  toolsets on its first request, before and after (the list sent ten times, less the provider's cached prefix).
"""

from __future__ import annotations

import json
import statistics
from collections import defaultdict
from typing import Any

from app.agent_loop_lib.tools.builtin.lazy_toolsets import (
    CallToolTool,
    FetchToolsTool,
    ListToolsetsTool,
    SearchToolsTool,
)
from app.agent_loop_lib.tools.registry import ToolRegistry
from tests.support.token_encoder import encoder
from tests.unit.harness.recs_audit.test_r3_tool_definition_tokens import (
    _schema,  # pyright: ignore[reportPrivateUsage]
    catalog,  # noqa: F401 - the module fixture
)

REQUESTS = 10
LOADED = 3


def _tokens(entries: list[dict[str, Any]]) -> int:
    return len(encoder()(json.dumps(entries)))


def measure(catalog: list[tuple[str, dict[str, Any]]]) -> dict[str, int]:  # noqa: F811
    registry = ToolRegistry()
    meta = [ListToolsetsTool(registry), FetchToolsTool(registry), SearchToolsTool(registry), CallToolTool()]
    listed = [
        {"name": s.name, "description": s.description, "inputSchema": s.input_schema}
        for s in (tool.to_schema() for tool in meta)
    ]
    by_toolset: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for name, tool in catalog:
        by_toolset[name.split(".", 1)[0]].append(
            {"name": name, "description": tool.get("description") or "", "inputSchema": _schema(tool)}
        )
    sizes = sorted(_tokens(tools) for tools in by_toolset.values())
    median, meta_tokens = int(statistics.median(sizes)), _tokens(listed)
    call_tool = _tokens(listed[-1:])
    return {
        "toolsets": len(sizes),
        "meta": meta_tokens,
        "call_tool": call_tool,
        "toolset_median": median,
        "toolset_max": sizes[-1],
        "session_before": REQUESTS * meta_tokens - call_tool * REQUESTS + (REQUESTS - 1) * LOADED * median,
        "session_after": REQUESTS * meta_tokens,
    }


def test_r4_a_fixed_list_costs_one_small_schema_and_saves_every_loaded_toolset_on_every_later_request(
    catalog: list[tuple[str, dict[str, Any]]],  # noqa: F811
) -> None:
    sizes = measure(catalog)
    print("R4 native lazy tool-list tokens (o200k):", json.dumps(sizes))
    assert sizes["toolsets"] >= 25, sizes
    assert sizes["call_tool"] <= 120, sizes
    assert sizes["meta"] <= 1_000, sizes
    # One loaded toolset alone costs more than the meta-tools' addition many times over.
    assert sizes["toolset_median"] > sizes["call_tool"], sizes
    assert sizes["session_after"] < sizes["session_before"], sizes
