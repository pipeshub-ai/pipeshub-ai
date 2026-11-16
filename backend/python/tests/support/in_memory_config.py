"""A `ConfigurationService` stand-in that keeps values in a dict, writes included."""
from __future__ import annotations

import copy
import os
from typing import Any

# With PERM_SOURCE_ACCOUNTS=1 every connector keeps its accounts as SourceAccount nodes unless a test sets the key itself: the suites that
# write through a processor then run with the source-account writers on, and must still read what they read with them off.
_ACCOUNTS_KEY = "/sourceAccounts/graph"


class InMemoryConfigService:
    def __init__(self) -> None:
        self.values: dict[str, Any] = {}

    async def get_config(self, key: str, default: Any = None, use_cache: bool = True, **_: Any) -> Any:  # noqa: ANN401
        if key.endswith(_ACCOUNTS_KEY) and key not in self.values and os.environ.get("PERM_SOURCE_ACCOUNTS") == "1":
            return {"enabled": True}
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ANN401
        self.values[key] = copy.deepcopy(value)
        return True

    async def create_config_if_absent(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ANN401
        if key in self.values:
            return False
        self.values[key] = copy.deepcopy(value)
        return True

    async def compare_and_set_config(self, key: str, expected: Any, new: Any) -> bool:  # noqa: ANN401
        if self.values.get(key) != expected:
            return False
        self.values[key] = copy.deepcopy(new)
        return True

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return [k for k in self.values if k.startswith(directory)]
