"""C-29 (CPD-2): the committed release catalog is a fresh dump of the registries that serve each kind.

A ``@Toolset``, an ``@mcp_server`` template, a built-in skill pack or an allowlist entry added,
removed or changed without ``python -m app.control_plane.catalog.dump --write`` fails here, naming
the kind and the ref.
"""

from __future__ import annotations

from app.agents.registry.toolset_registry import Toolset, ToolsetRegistry
from app.connectors.core.registry.tool_builder import ToolProvider
from app.control_plane.catalog.dump import DEFAULT_NODE_OUT, DEFAULT_OUT
from app.control_plane.catalog.release import (
    build_release_catalog,
    catalog_drift,
    render_node_module,
)
from app.control_plane.catalog.release_sources import load_release_sources


def test_c29_committed_catalog_equals_a_fresh_dump() -> None:
    catalogs = build_release_catalog(load_release_sources())
    assert catalog_drift(catalogs, DEFAULT_OUT) == []
    assert DEFAULT_NODE_OUT.read_text(encoding="utf-8") == render_node_module(catalogs), (
        "Node's release data is stale: run python -m app.control_plane.catalog.dump --write"
    )


def test_c29_a_toolset_added_without_regenerating_is_named() -> None:
    @Toolset(name="Unlisted Demo", app_group="Demo", supported_auth_types="NONE", provider=ToolProvider.CONNECTOR)
    class _Unlisted:
        pass

    sources = load_release_sources()
    ToolsetRegistry().register_toolset(_Unlisted)
    drift = catalog_drift(build_release_catalog(sources), DEFAULT_OUT)
    assert len(drift) == 1
    assert drift[0].startswith("toolsets.json: added toolset:pipeshub/unlisteddemo@")


def test_c29_a_changed_toolset_is_named_as_removed_and_added() -> None:
    sources = load_release_sources()
    registry = ToolsetRegistry()
    registry._toolsets["jira"]["description"] = "changed"  # pyright: ignore[reportPrivateUsage]
    drift = catalog_drift(build_release_catalog(sources), DEFAULT_OUT)
    assert [line.split(" ", 2)[1] for line in drift] == ["added", "removed"]
    assert all("toolset:pipeshub/jira@" in line for line in drift)
