"""TSC-21 (CPD-7, R2 F21) and FB-2: toolset instance writes keep what they did not change.

The instance list is one KV document for every org. Each write loaded only its org's rows and wrote that
subset back as the whole list, so a create in org A erased org B's instances (FB-2), and two concurrent
writes in one org kept only the later one's view (F21). A barrier holds both writers after their read, so
the interleaving is forced, not timed.
"""

from __future__ import annotations

import asyncio
import copy
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import app.edition_config  # noqa: F401 - binds the routes in the order their circular imports need

INSTANCES = "/services/toolset-instances"


class KvConfig:
    """A configuration service over a dict; ``hold_reads`` parks that many list reads until all arrived."""

    def __init__(self, instances: list[dict[str, Any]]) -> None:
        self.data: dict[str, Any] = {INSTANCES: instances}
        self._parties = 0
        self._arrived = 0
        self._all_read = asyncio.Event()

    def hold_reads(self, parties: int) -> None:
        self._parties = parties

    async def get_config(self, key: str, default: object = None, use_cache: bool = False, **_: object) -> object:
        value = copy.deepcopy(self.data.get(key, default))
        if key == INSTANCES and self._arrived < self._parties:
            self._arrived += 1
            if self._arrived == self._parties:
                self._all_read.set()
            await self._all_read.wait()
        return value

    async def set_config(self, key: str, value: object) -> bool:
        self.data[key] = copy.deepcopy(value)
        return True

    async def delete_config(self, key: str) -> bool:
        self.data.pop(key, None)
        return True

    async def list_keys_in_directory(self, directory: str) -> list[str]:
        return [k for k in self.data if k.startswith(directory)]

    def instances(self) -> list[dict[str, Any]]:
        return self.data[INSTANCES]


def _request(name: str) -> MagicMock:
    request = MagicMock()
    body = {"instanceName": name, "toolsetType": "calculator", "authType": "NONE"}
    request.body = AsyncMock(return_value=json.dumps(body).encode())
    registry = MagicMock()
    registry.get_toolset_metadata.return_value = {
        "name": "calculator", "display_name": "Calculator", "supported_auth_types": ["NONE"], "tools": [],
    }
    request.app.state.toolset_registry = registry
    return request


def _as_admin_of(org_id: str):  # noqa: ANN202 - a patch context
    return patch.multiple(
        "app.api.routes.toolsets",
        _get_user_context=MagicMock(return_value={"user_id": "u1", "org_id": org_id}),
        _check_user_is_admin=AsyncMock(return_value=True),
    )


async def test_fb2_a_create_in_one_org_keeps_every_other_orgs_instances() -> None:
    from app.api.routes.toolsets import create_toolset_instance

    config = KvConfig([{"_id": "b-1", "orgId": "org-b", "toolsetType": "jira", "instanceName": "B's Jira"}])
    with _as_admin_of("org-a"):
        await create_toolset_instance(_request("Calc"), config_service=config)
    assert sorted(i["orgId"] for i in config.instances()) == ["org-a", "org-b"]


async def test_tsc21_two_concurrent_creates_in_one_org_are_both_kept() -> None:
    from app.api.routes.toolsets import create_toolset_instance

    config = KvConfig([])
    config.hold_reads(2)
    with _as_admin_of("org-a"):
        await asyncio.gather(
            create_toolset_instance(_request("Calc one"), config_service=config),
            create_toolset_instance(_request("Calc two"), config_service=config),
        )
    assert sorted(i["instanceName"] for i in config.instances()) == ["Calc one", "Calc two"]


async def test_tsc21_a_delete_racing_a_create_removes_only_its_instance() -> None:
    from app.api.routes.toolsets import create_toolset_instance, delete_toolset_instance

    config = KvConfig([{"_id": "a-1", "orgId": "org-a", "toolsetType": "calculator", "instanceName": "Old"}])
    config.hold_reads(2)
    graph = MagicMock()
    graph.check_toolset_instance_in_use = AsyncMock(return_value=[])
    delete_request = MagicMock()
    with _as_admin_of("org-a"), patch("app.api.routes.toolsets._get_graph_provider", return_value=graph):
        await asyncio.gather(
            delete_toolset_instance("a-1", delete_request, config_service=config),
            create_toolset_instance(_request("New"), config_service=config),
        )
    assert [i["instanceName"] for i in config.instances()] == ["New"]
