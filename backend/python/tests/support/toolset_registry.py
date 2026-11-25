"""The process-wide ``ToolsetRegistry`` singleton, read or replaced by a test without leaking into the next one.

Toolset loading, the factories and the agent routes read ``get_toolset_registry()``. A test that swaps the singleton
(``ToolsetRegistry._instance = None``) or fills it leaves every later test in that pytest process with its toolsets:
a reader that discovers only into an empty registry then sees a handful of fakes instead of every toolset (H5).
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import TYPE_CHECKING, Any

from app.agents.registry.toolset_registry import ToolsetRegistry

if TYPE_CHECKING:
    from collections.abc import Iterator


@contextmanager
def toolset_registry_restored() -> Iterator[None]:
    """Whatever the body does to the registry, the process gets the instance and the toolsets it had back."""
    instance = ToolsetRegistry._instance  # pyright: ignore[reportPrivateUsage]
    held = dict(instance._toolsets) if instance is not None and instance._initialized else None  # pyright: ignore[reportPrivateUsage]
    try:
        yield
    finally:
        ToolsetRegistry._instance = instance  # pyright: ignore[reportPrivateUsage]
        if instance is not None and held is not None:
            instance._toolsets = held  # pyright: ignore[reportPrivateUsage]


def discovered_toolsets() -> dict[str, dict[str, Any]]:
    """Every toolset auto-discovery registers, from a registry of its own, whatever earlier tests left behind."""
    with toolset_registry_restored():
        ToolsetRegistry._instance = None  # pyright: ignore[reportPrivateUsage]
        registry = ToolsetRegistry()
        registry.auto_discover_toolsets()
        return registry.get_all_toolsets()


__all__ = ["discovered_toolsets", "toolset_registry_restored"]
