"""Recommendation 3 (recs audit): what the catalog's tool definitions cost a model, measured, not claimed.

Three shapes of the same catalog (every tool the toolset registry discovers):

* ``mcp_full``: every tool as an MCP ``tools/list`` entry (name, description, input schema), the shape
  ``Tool.to_schema`` emits and the gateway serves when the grant is under the lazy threshold;
* ``gateway_lazy``: what LZ1's gateway lists instead for a large grant (``search_tools`` + ``call_tool``);
* ``file_index``: one line per tool (name and first sentence), the ``/pipeshub/tools.txt`` index the worker
  writes at each turn (``harness_engines.pipeshub.tool_index``) for an engine that reads tools from files.

The measurement says how much of recommendation 3's "150k to 2k tokens" the deferred listing already gives, and
what the ``ph`` folder's index costs a model that reads it whole. The asserts hold the orders of magnitude, not
the exact counts, so a catalog change does not need a new measurement.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from harness_core.gateway.disclosure import (  # pyright: ignore[reportPrivateUsage]
    _CALL,
    _SEARCH,
)
from harness_core.gateway.types import McpTool
from harness_engines.pipeshub import tool_index
from tests.support.token_encoder import encoder


def _schema(tool: dict[str, Any]) -> dict[str, Any]:
    properties: dict[str, Any] = {}
    required: list[str] = []
    for param in tool.get("parameters") or []:
        prop: dict[str, Any] = {"type": param.get("type", "string"), "description": param.get("description", "")}
        for key in ("items", "properties", "enum"):
            if param.get(key) is not None:
                prop[key] = param[key]
        properties[param["name"]] = prop
        if param.get("required"):
            required.append(param["name"])
    return {"type": "object", "properties": properties, "required": required}


@pytest.fixture(scope="module")
def catalog() -> list[tuple[str, dict[str, Any]]]:
    from app.agents.registry.toolset_registry import get_toolset_registry

    registry = get_toolset_registry()
    registry.auto_discover_toolsets()
    return [
        (f"{toolset}.{tool['name']}", tool)
        for toolset, meta in sorted(registry.get_all_toolsets().items())
        for tool in meta.get("tools") or []
    ]


def measure(catalog: list[tuple[str, dict[str, Any]]]) -> dict[str, int]:
    count = encoder()
    full = [{"name": n, "description": t.get("description") or "", "inputSchema": _schema(t)} for n, t in catalog]
    lazy = [{"name": t.name, "description": t.description, "inputSchema": t.input_schema} for t in (_SEARCH, _CALL)]
    index = tool_index(McpTool(n, t.get("description") or "", {}) for n, t in catalog)
    return {
        "tools": len(catalog),
        "mcp_full": len(count(json.dumps(full))),
        "gateway_lazy": len(count(json.dumps(lazy))),
        "file_index": len(count(index)),
    }


def test_r3_the_full_catalog_listing_is_tens_of_thousands_of_tokens_and_the_deferred_listing_is_a_few_hundred(
    catalog: list[tuple[str, dict[str, Any]]],
) -> None:
    sizes = measure(catalog)
    print("R3 tool definition tokens (o200k):", json.dumps(sizes))
    assert sizes["tools"] >= 300, sizes
    assert sizes["mcp_full"] >= 20_000, sizes
    assert sizes["gateway_lazy"] <= 500, sizes
    # A file index of every tool still costs a tenth or more of the full listing: an index the model reads whole
    # is not where the saving comes from; reading only the files it needs is.
    assert sizes["gateway_lazy"] * 20 < sizes["file_index"] < sizes["mcp_full"], sizes
