"""The release sources the dump loads, by dotted path: each lives beside its kind's registry, and the
catalog core imports none of them. A new kind appends one entry here."""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.control_plane.catalog.ports import ReleaseItemSource

RELEASE_SOURCES: tuple[str, ...] = (
    "app.agents.registry.catalog_source:release_source",
    "app.agents.mcp.catalog_source:release_source",
    "app.agents.agent_loop.skills.catalog_source:release_source",
    "app.harness.engines.catalog_source:release_source",
)


def load_release_sources(paths: tuple[str, ...] = RELEASE_SOURCES) -> list[ReleaseItemSource]:
    sources: list[ReleaseItemSource] = []
    for path in paths:
        module, _, factory = path.partition(":")
        sources.append(getattr(importlib.import_module(module), factory)())
    return sources
