"""Contract suite for P2.8 (``impl/P2.8-mcp-hub.md``): :class:`ToolSchemaCacheContract` (SC-1..SC-4)
runs on every ``ToolSchemaCache``."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol

import pytest

from harness_core.mcp_hub.manifest import ToolDescriptor
from harness_core.mcp_hub.types import CachedTools, SchemaKey

if TYPE_CHECKING:
    from harness_core.mcp_hub.ports import ToolSchemaCache


class ToolSchemaCacheHarness(Protocol):
    @property
    def cache(self) -> ToolSchemaCache: ...

    async def elapse(self, seconds: float) -> None: ...


def key(server: str = "srv-1", *, tenant: str = "org-1", owner: str = "user-1", config: str = "cfg-1") -> SchemaKey:
    return SchemaKey(tenant, server, owner, config)


def tools(*names: str, ttl_s: float = 60.0) -> CachedTools:
    return CachedTools(
        tools=tuple(
            ToolDescriptor(n, f"{n} tool", {"type": "object", "properties": {"q": {"type": "string"}}}) for n in names
        ),
        server_version="1.2.3",
        protocol_version="2025-11-25",
        ttl_s=ttl_s,
    )


class ToolSchemaCacheContract:
    @pytest.fixture
    def harness(self) -> ToolSchemaCacheHarness:
        raise NotImplementedError("provide a harness fixture")

    async def test_sc1_a_put_entry_is_returned_as_it_was_put(self, harness: ToolSchemaCacheHarness) -> None:
        entry = tools("search", "fetch")
        await harness.cache.put(key(), entry)
        assert await harness.cache.get(key()) == entry

    async def test_sc1_a_put_replaces_the_entry(self, harness: ToolSchemaCacheHarness) -> None:
        await harness.cache.put(key(), tools("search"))
        await harness.cache.put(key(), tools("search", "fetch"))
        cached = await harness.cache.get(key())
        assert cached is not None and [t.name for t in cached.tools] == ["search", "fetch"]

    @pytest.mark.parametrize(
        "other",
        [key(owner="user-2"), key(config="cfg-2"), key(tenant="org-2"), key("srv-2")],
        ids=["owner", "config", "tenant", "server"],
    )
    async def test_sc2_any_other_key_misses(self, harness: ToolSchemaCacheHarness, other: SchemaKey) -> None:
        await harness.cache.put(key(), tools("search"))
        assert await harness.cache.get(other) is None

    async def test_sc3_an_entry_past_its_ttl_misses(self, harness: ToolSchemaCacheHarness) -> None:
        await harness.cache.put(key(), tools("search", ttl_s=30.0))
        await harness.elapse(29.0)
        assert await harness.cache.get(key()) is not None
        await harness.elapse(2.0)
        assert await harness.cache.get(key()) is None

    async def test_sc4_invalidate_drops_every_entry_of_the_server_only(self, harness: ToolSchemaCacheHarness) -> None:
        for entry_key in (key(), key(owner="user-2"), key(config="cfg-2")):
            await harness.cache.put(entry_key, tools("search"))
        await harness.cache.put(key("srv-2"), tools("other"))
        await harness.cache.put(key(tenant="org-2"), tools("other"))
        assert await harness.cache.invalidate("org-1", "srv-1") == 3
        assert await harness.cache.get(key()) is None
        assert await harness.cache.get(key(owner="user-2")) is None
        assert await harness.cache.get(key("srv-2")) is not None
        assert await harness.cache.get(key(tenant="org-2")) is not None
        assert await harness.cache.invalidate("org-1", "srv-1") == 0


__all__ = ["ToolSchemaCacheContract", "ToolSchemaCacheHarness", "key", "tools"]
