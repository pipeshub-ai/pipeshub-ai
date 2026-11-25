"""QA-23 (CG-23, TSC-G3): the toolset instance list race across replicas (R2 F21's remaining window).

CPD-7 replays a write onto a fresh read under a process lock (TSC-21). Two replicas hold two locks: when both read
the list fresh before either writes, the later write drops the earlier one's instance. A cyclic barrier holds every
list read until both replicas are there, so each replica's load and fresh read meet the other's.
"""

from __future__ import annotations

import asyncio
import contextlib
from unittest.mock import patch

import pytest

from tests.unit.api.routes.test_toolset_instances_race import (
    INSTANCES,
    KvConfig,
    _as_admin_of,
    _request,
)


class ReplicaKv(KvConfig):
    def __init__(self) -> None:
        super().__init__([])
        self.barrier = asyncio.Barrier(2)

    async def get_config(self, key: str, default: object = None, use_cache: bool = False, **kwargs: object) -> object:
        value = await super().get_config(key, default, use_cache, **kwargs)
        if key == INSTANCES:
            await self.barrier.wait()
        return value


@pytest.mark.xfail(strict=True, reason="QA23-F4: no compare-and-set on the shared list across replicas; owner CPD-7 store-of-record switch")
async def test_qa23_6_two_replicas_creating_at_once_keep_both_instances() -> None:
    from app.api.routes.toolsets import create_toolset_instance

    config = ReplicaKv()
    # Each replica has its own process lock: none is shared.
    with _as_admin_of("org-a"), patch("app.api.routes.toolsets._INSTANCE_WRITES", contextlib.nullcontext()):
        await asyncio.gather(
            create_toolset_instance(_request("Calc one"), config_service=config),
            create_toolset_instance(_request("Calc two"), config_service=config),
        )
    assert sorted(i["instanceName"] for i in config.instances()) == ["Calc one", "Calc two"]
