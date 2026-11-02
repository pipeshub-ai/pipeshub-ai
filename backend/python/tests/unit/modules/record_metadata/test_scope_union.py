"""The per-process union cache: what it reads, how often, and that it learns every version."""

import asyncio

import pytest

from app.config.constants.arangodb import Connectors
from app.models.record_metadata import PropertyType
from app.modules.record_metadata.builtin import TICKET_PROPERTIES
from app.modules.record_metadata.projection import (
    BUILTIN_GEN,
    EMPTY_UNION,
    ScopeUnionCache,
    UnionUnavailable,
)

from .projection_world import defs, registry

pytestmark = pytest.mark.unit

KB = Connectors.KNOWLEDGE_BASE


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def _cache(ttl: float = 60.0) -> tuple[ScopeUnionCache, Clock]:
    clock = Clock()
    return ScopeUnionCache(ttl=ttl, clock=clock), clock


async def test_builtin_scopes_read_no_configuration() -> None:
    reg, config = registry()
    cache, _ = _cache()
    for _ in range(50):
        union = await cache.union_for(reg, "org-1", "conn-1", Connectors.JIRA)
    assert config.reads == []
    assert union.gen == BUILTIN_GEN and set(union.types) == {p.name for p in TICKET_PROPERTIES if p.filterable or p.facetable}


async def test_other_connectors_have_no_union_and_read_nothing() -> None:
    reg, config = registry()
    cache, _ = _cache()
    assert await cache.union_for(reg, "org-1", "drive-1", Connectors.GOOGLE_DRIVE) is EMPTY_UNION
    assert config.reads == []


async def test_registry_reads_are_bounded() -> None:
    """A KB with 100 versions: 101 reads once (100 versions and the probe past them), then at most one read
    per TTL, and a new version costs two (the version and the probe past it)."""
    reg, config = registry()
    for i in range(100):
        await reg.register("org-1", "kb-1", defs((f"p{i}", PropertyType.STRING)))
    config.reads.clear()
    cache, clock = _cache()

    union = await cache.union_for(reg, "org-1", "kb-1", KB)
    assert union.gen == 100 and len(config.reads) == 101
    config.reads.clear()

    for _ in range(1_000):
        await cache.union_for(reg, "org-1", "kb-1", KB)
    assert config.reads == []

    for _ in range(5):
        clock.now += 61
        await cache.union_for(reg, "org-1", "kb-1", KB)
    assert len(config.reads) == 5

    config.reads.clear()
    await reg.register("org-1", "kb-1", defs(("extra", PropertyType.STRING)))
    config.reads.clear()
    clock.now += 61
    union = await cache.union_for(reg, "org-1", "kb-1", KB)
    assert union.gen == 101 and len(config.reads) == 2


async def test_union_sees_v3_after_v1_reregistered() -> None:
    """``latest`` is the last registration: after v3 and then v1 again it points at v1, and a cache that
    follows it would never learn v3."""
    reg, config = registry()
    v1, v2, v3 = defs(("a", PropertyType.STRING)), defs(("a", PropertyType.STRING), ("b", PropertyType.STRING)), \
        defs(("a", PropertyType.STRING), ("b", PropertyType.STRING), ("c", PropertyType.STRING))
    await reg.register("org-1", "kb-1", v1)
    await reg.register("org-1", "kb-1", v2)
    cache, clock = _cache()
    assert (await cache.union_for(reg, "org-1", "kb-1", KB)).gen == 2

    await reg.register("org-1", "kb-1", v3)
    await reg.register("org-1", "kb-1", v1)  # latest is version 1 again
    assert (await reg.get("org-1", "kb-1")).schema_id.endswith(":1")
    config.reads.clear()
    clock.now += 61

    union = await cache.union_for(reg, "org-1", "kb-1", KB)

    assert union.gen == 3 and set(union.types) == {"a", "b", "c"}
    assert len(config.reads) <= 2
    config.reads.clear()
    clock.now += 61
    await cache.union_for(reg, "org-1", "kb-1", KB)
    assert len(config.reads) <= 2


async def test_a_local_registration_invalidates_at_once() -> None:
    reg, _ = registry()
    await reg.register("org-1", "kb-1", defs(("a", PropertyType.STRING)))
    cache, _ = _cache()
    assert (await cache.union_for(reg, "org-1", "kb-1", KB)).gen == 1
    await reg.register("org-1", "kb-1", defs(("a", PropertyType.STRING), ("b", PropertyType.STRING)))
    assert (await cache.union_for(reg, "org-1", "kb-1", KB)).gen == 1  # within the TTL: not yet known
    cache.invalidate("org-1", "kb-1")
    assert (await cache.union_for(reg, "org-1", "kb-1", KB)).gen == 2


async def test_scopes_are_kept_apart_per_org_and_connector() -> None:
    reg, _ = registry()
    await reg.register("org-1", "kb-1", defs(("a", PropertyType.STRING)))
    cache, _ = _cache()
    assert not (await cache.union_for(reg, "org-1", "kb-1", KB)).empty
    assert (await cache.union_for(reg, "org-1", "kb-2", KB)).empty


async def test_concurrent_callers_share_one_probe() -> None:
    reg, config = registry()
    await reg.register("org-1", "kb-1", defs(("a", PropertyType.STRING)))
    config.reads.clear()
    cache, _ = _cache()
    await asyncio.gather(*(cache.union_for(reg, "org-1", "kb-1", KB) for _ in range(20)))
    assert len(config.reads) == 2


async def test_an_unreadable_registry_with_nothing_cached_is_unavailable() -> None:
    reg, config = registry()
    config.fail_reads = True
    cache, _ = _cache()
    with pytest.raises(UnionUnavailable):
        await cache.union_for(reg, "org-1", "kb-1", KB)


async def test_an_unreadable_registry_keeps_serving_what_was_cached_and_retries_after_the_ttl() -> None:
    reg, config = registry()
    await reg.register("org-1", "kb-1", defs(("a", PropertyType.STRING)))
    cache, clock = _cache()
    await cache.union_for(reg, "org-1", "kb-1", KB)
    config.fail_reads = True
    config.reads.clear()
    clock.now += 61
    assert (await cache.union_for(reg, "org-1", "kb-1", KB)).gen == 1
    await cache.union_for(reg, "org-1", "kb-1", KB)
    assert len(config.reads) == 1
    config.fail_reads = False
    await reg.register("org-1", "kb-1", defs(("a", PropertyType.STRING), ("b", PropertyType.STRING)))
    clock.now += 61
    assert (await cache.union_for(reg, "org-1", "kb-1", KB)).gen == 2
