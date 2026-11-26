"""A configuration service over a dict, shaped like ``ConfigurationService`` where the vault touches it."""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any


@dataclass
class DictConfig:
    values: dict[str, Any] = field(default_factory=dict[str, Any])
    writes: list[str] = field(default_factory=list[str])

    async def get_config(self, key: str, default: Any = None, use_cache: bool = False, **_: Any) -> Any:  # noqa: ANN401
        return copy.deepcopy(self.values.get(key, default))

    async def set_config(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ANN401 - ttl_seconds, keep_in_cache
        self.values[key] = copy.deepcopy(value)
        self.writes.append(key)
        return True

    async def create_config_if_absent(self, key: str, value: Any, **_: Any) -> bool:  # noqa: ANN401 - ttl_seconds
        if key in self.values:
            return False
        return await self.set_config(key, value)

    async def delete_config(self, key: str) -> bool:
        return self.values.pop(key, None) is not None

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return sorted(k for k in self.values if k.startswith(directory.rstrip("/") + "/"))


TOOLSET_INSTANCES = "/services/toolset-instances"


def owned_toolsets(values: dict[str, Any], org_id: str = "org-1") -> dict[str, Any]:
    """``values`` plus the instance list naming every ``/services/toolsets/{instance}/…`` record's instance as
    ``org_id``'s, as the toolsets admin writes it (a toolset credential resolves only for its own org)."""
    instances = sorted({key.split("/")[3] for key in values if key.startswith("/services/toolsets/")})
    return {**values, TOOLSET_INSTANCES: [{"_id": i, "orgId": org_id, "toolsetType": "jira"} for i in instances]}
