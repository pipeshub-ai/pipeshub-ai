"""H5 ST-1/ST-2: reading or replacing the process toolset registry in a test leaves it as it was."""

from __future__ import annotations

from app.agents.registry.toolset_registry import ToolsetRegistry, get_toolset_registry
from tests.support.toolset_registry import (
    discovered_toolsets,
    toolset_registry_restored,
)


class _Fake:
    _toolset_metadata: dict[str, object] = {
        "name": "h5fake",
        "appGroup": "x",
        "supportedAuthTypes": ["NONE"],
        "description": "",
        "category": "utility",
        "config": {},
        "tools": [],
        "isInternal": False,
    }


def test_st2_a_replaced_and_filled_registry_is_put_back() -> None:
    before = get_toolset_registry()
    toolsets = before.get_all_toolsets()
    with toolset_registry_restored():
        ToolsetRegistry._instance = None  # pyright: ignore[reportPrivateUsage]
        replaced = ToolsetRegistry()
        replaced.register_toolset(_Fake)
        before.register_toolset(_Fake)
        assert get_toolset_registry() is replaced
    assert get_toolset_registry() is before
    assert before.get_all_toolsets() == toolsets


def test_st1_discovery_ignores_what_earlier_tests_left_and_leaves_it() -> None:
    with toolset_registry_restored():
        polluted = get_toolset_registry()
        polluted._toolsets = {}  # pyright: ignore[reportPrivateUsage]
        polluted.register_toolset(_Fake)
        found = discovered_toolsets()
        assert "h5fake" not in found
        assert len(found) > 20
        assert get_toolset_registry() is polluted
        assert list(polluted.get_all_toolsets()) == ["h5fake"]
