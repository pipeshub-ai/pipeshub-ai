"""What the adapter returns, and the bounds that make the resolver fail closed instead of working without limit."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from app.services.permissions.adapters.graph import GraphPrincipalView
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.fakes import InMemoryHierarchyView, InMemoryPrincipalView
from app.services.permissions.ports import AppFacts, Principal
from app.services.permissions.viewer import DEFAULT_MAX_TERMS, ViewerScopeResolver

ORG, USER, APP = "org-1", "u-1", "app-1"


class _Provider:
    def __init__(self, found: SimpleNamespace, delay: float = 0.0) -> None:
        self._found, self._delay = found, delay

    async def check_access(self, user_key: str, org_id: str, **_kw: object) -> SimpleNamespace:
        await asyncio.sleep(self._delay)
        return self._found


def _who() -> Principal:
    return Principal(frozenset({"u:u-1"}), frozenset({APP}), context=(USER, {}))


async def test_a_placeholder_the_graph_check_lists_is_not_readable() -> None:
    """`check_access` lists a placeholder stub among the reachable ids so a browse can open it; it is not content."""
    found = SimpleNamespace(node_ids=frozenset({"real", "stub"}), placeholder_ids=frozenset({"stub"}))
    view = GraphPrincipalView(_Provider(found))
    assert await view.readable(ORG, _who(), frozenset({"real", "stub"})) == {"real"}


async def test_a_check_that_takes_too_long_is_unavailable() -> None:
    found = SimpleNamespace(node_ids=frozenset({"a"}), placeholder_ids=frozenset())
    view = GraphPrincipalView(_Provider(found, delay=0.3), check_timeout_seconds=0.05)
    with pytest.raises(ViewerScopesUnavailable) as err:
        await view.readable(ORG, _who(), frozenset({"a"}))
    assert err.value.reason == "check_timeout"


async def test_a_structure_read_that_takes_too_long_is_unavailable_and_forgotten() -> None:
    class Slow(InMemoryHierarchyView):
        slow = True

        async def record_group_ids(self, org_id: str, app_id: str) -> frozenset[str]:
            if self.slow:
                await asyncio.sleep(0.3)
            return await super().record_group_ids(org_id, app_id)

    hierarchy = Slow(ORG, {APP: frozenset({"g1"})}, (), {APP: AppFacts(False, "DRIVE")})
    principals = InMemoryPrincipalView({(ORG, USER): Principal(frozenset({"u:u-1"}), frozenset({APP}))}, {(ORG, USER): frozenset({"g1"})})
    resolver = ViewerScopeResolver(hierarchy, principals, structure_timeout_seconds=0.05)
    with pytest.raises(ViewerScopesUnavailable) as err:
        await resolver.resolve(ORG, USER, version=1)
    assert err.value.reason == "timeout"
    hierarchy.slow = False
    await asyncio.sleep(0.1)
    got = await resolver.resolve(ORG, USER, version=1)           # the failed read was not kept
    assert got.readable_groups == {"g1"}


async def test_more_terms_than_the_default_bound_is_unavailable_never_a_wide_scope() -> None:
    groups = frozenset(f"g{i}" for i in range(DEFAULT_MAX_TERMS + 1))
    hierarchy = InMemoryHierarchyView(ORG, {APP: groups}, (), {APP: AppFacts(False, "DRIVE")})
    principals = InMemoryPrincipalView({(ORG, USER): Principal(frozenset({"u:u-1"}), frozenset({APP}))}, {(ORG, USER): groups})
    with pytest.raises(ViewerScopesUnavailable) as err:
        await ViewerScopeResolver(hierarchy, principals).resolve(ORG, USER)
    assert err.value.reason.startswith("too_many_terms")


async def test_a_cache_started_without_redis_connects_when_redis_comes_back() -> None:
    """A process that began while Redis was down must not stay without a cache for good: it would never bump."""
    import fakeredis

    from app.services.permissions.cache import ViewerScopeCache

    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    up = {"yes": False}

    async def connect() -> object:
        if not up["yes"]:
            raise ConnectionError("redis is down")
        return lambda: client

    cache = ViewerScopeCache(None, connect=connect)
    cache.DOWN_BACKOFF_SECONDS = 0.0
    assert await cache.version("org-1") is None          # down: no version, so a search is legacy
    await cache.bump("org-1")                            # nothing to bump, nothing to break
    up["yes"] = True
    before = await cache.version("org-1")
    assert before is not None
    await cache.bump("org-1")
    assert await cache.version("org-1") == before + 1


async def test_a_failed_chunk_leaves_no_sibling_check_running() -> None:
    running: set[str] = set()

    class Provider:
        async def check_access(self, _user: str, _org: str, *, node_ids: list[str], **_kw: object) -> SimpleNamespace:
            if node_ids == ["a"]:
                raise RuntimeError("boom")
            running.add(node_ids[0])
            try:
                await asyncio.sleep(5)
            finally:
                running.discard(node_ids[0])
            return SimpleNamespace(node_ids=frozenset(), placeholder_ids=frozenset())

    view = GraphPrincipalView(Provider(), chunk=1, parallel=3)  # type: ignore[arg-type]
    with pytest.raises(ViewerScopesUnavailable) as err:
        await view.readable(ORG, _who(), frozenset({"a", "b", "c"}))
    assert err.value.reason == "graph_error" and running == set()
