from __future__ import annotations

from typing import Any

from app.agent_loop_lib.core.exceptions import RegistryError


class PluginRegistry:
    """Maps string keys to arbitrary implementations."""

    def __init__(self) -> None:
        self._store: dict[str, Any] = {}

    def register(self, key: str, impl: Any) -> None:
        self._store[key] = impl

    def resolve(self, key: str) -> Any:
        if key not in self._store:
            raise RegistryError(f"No implementation registered for '{key}'")
        return self._store[key]

    def has(self, key: str) -> bool:
        return key in self._store

    def keys(self) -> list[str]:
        return list(self._store.keys())
