"""A `ConfigurationService` stand-in that keeps values in a dict, writes included."""
from __future__ import annotations

import copy
from typing import Any


class InMemoryConfigService:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, default: Any = None, use_cache: bool = True, **_: Any) -> Any:  # noqa: ANN401
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ANN401
        self.values[key] = copy.deepcopy(value)
        return True

    async def create_config_if_absent(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ANN401
        if key in self.values:
            return False
        self.values[key] = copy.deepcopy(value)
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return [k for k in self.values if k.startswith(directory)]
