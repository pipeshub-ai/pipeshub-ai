"""Every toolset module the agents load at startup imports and defines a toolset.

discover_toolsets logs a module that fails to import and carries on, so a
broken toolset disappears from agents with nothing failing. This test is the
place that failure shows up instead.
"""

import importlib
import inspect

import pytest

from app.agents.registry.toolset_registry import STANDARD_TOOLSET_MODULES


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
