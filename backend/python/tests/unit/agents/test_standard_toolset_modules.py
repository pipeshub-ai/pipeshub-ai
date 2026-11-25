"""Every toolset module the agents load at startup imports and defines a toolset.

discover_toolsets logs a module that fails to import and carries on, so a
broken toolset disappears from agents with nothing failing. This test is the
place that failure shows up instead.
"""

import importlib
import inspect
import json
from pathlib import Path

import pytest

from app.agents.registry.toolset_registry import (
    STANDARD_TOOLSET_MODULES,
    ToolsetRegistry,
)

# The gateway's AGENT_CREATE_TOOLSET_NAMES is the release catalog's listed toolsets (es_validators.ts reads them
# through listedReleaseNames), generated from these registries (C-29).
_RELEASE_TOOLSETS = Path(__file__).resolve().parents[5] / "contracts/control-plane/catalog/toolsets.json"


@pytest.mark.parametrize("module_path", STANDARD_TOOLSET_MODULES)
def test_module_imports_and_defines_a_toolset(module_path: str) -> None:
    module = importlib.import_module(module_path)
    toolsets = [
        name
        for name, obj in inspect.getmembers(module)
        if inspect.isclass(obj)
        and hasattr(obj, "_toolset_metadata")
        # A class imported from elsewhere would hide that this module defines none.
        and obj.__module__ == module_path
    ]
    assert toolsets, f"{module_path} defines no toolset class"


def test_the_list_names_each_module_once() -> None:
    assert len(set(STANDARD_TOOLSET_MODULES)) == len(STANDARD_TOOLSET_MODULES)


def test_the_agent_gateway_accepts_every_toolset_the_builder_offers() -> None:
    # The agent builder lists every non-internal toolset, but the Node gateway
    # rejects any name missing from AGENT_CREATE_TOOLSET_NAMES, so a toolset
    # registered here and not there can be added to an agent but never saved.
    items = json.loads(_RELEASE_TOOLSETS.read_text(encoding="utf-8"))["items"]
    gateway_names = {item["name"] for item in items if not item["display"].get("hidden")}

    normalize = ToolsetRegistry()._normalize_toolset_name
    offered = {
        normalize(obj._toolset_metadata["name"])
        for module_path in STANDARD_TOOLSET_MODULES
        for _, obj in inspect.getmembers(importlib.import_module(module_path), inspect.isclass)
        if getattr(obj, "_toolset_metadata", None) is not None
        and obj.__module__ == module_path
        and not obj._toolset_metadata.get("isInternal", False)
    }
    assert offered - gateway_names == set()
