"""Resolver, version-keyed cache, mode and invalidation hooks of the viewer-scope layer (no store)."""

from __future__ import annotations

import asyncio
import json

import fakeredis
import pytest

from app.services.cache import invalidation_hooks as hooks
from app.services.permissions import (
    AclFilterMode,
    Principal,
    ScopeNode,
    ViewerScopeResolver,
    ViewerScopes,
    ViewerScopeService,
    ViewerScopesUnavailable,
    scopes_or_legacy,
)
from app.services.permissions.cache import ViewerScopeCache
from app.services.permissions.fakes import InMemoryHierarchyView, InMemoryPrincipalView
from app.services.permissions.ports import AppFacts

ORG, USER, APP = "org-1", "ana", "app-1"
TOKENS = frozenset({"u:ana", "g:team"})


def _views(
    admitted: frozenset[str], *, scopes: tuple[ScopeNode, ...] | None = None
) -> tuple[InMemoryHierarchyView, InMemoryPrincipalView]:
    hierarchy = InMemoryHierarchyView(
        ORG, {APP: frozenset({"g1", "g2"})},
        scopes if scopes is not None else (ScopeNode("s1", APP, True), ScopeNode("s2", APP, False)),
        {APP: AppFacts(False, "CONFLUENCE")})
    principals = InMemoryPrincipalView({(ORG, USER): Principal(TOKENS, frozenset({APP}))}, {(ORG, USER): admitted})
    return hierarchy, principals


async def test_resolver_sorts_the_graph_answers_into_scopes() -> None:
    hierarchy, principals = _views(frozenset({"g1", "s2"}))
    got = await ViewerScopeResolver(hierarchy, principals).resolve(ORG, USER)
    assert got.gated_apps == {APP} and got.open_apps == frozenset() and got.readable_groups == {"g1"}
    assert got.readable_scopes == {"s2"} and got.unreadable_restricted == {"s1"}
    assert got.principal_tokens == TOKENS and got.readable_containers == {"g1"}  # the gate alone opens no container


async def test_an_unrestricted_scope_the_viewer_cannot_read_is_not_listed() -> None:
    hierarchy, principals = _views(frozenset())
    got = await ViewerScopeResolver(hierarchy, principals).resolve(ORG, USER)
    assert got.unreadable_restricted == {"s1"} and got.readable_scopes == frozenset()


async def test_a_port_cannot_widen_the_answer() -> None:
    hierarchy, principals = _views(frozenset({"g1", "not-asked"}))
    got = await ViewerScopeResolver(hierarchy, principals).resolve(ORG, USER)
    assert got.readable_groups == {"g1"} and "not-asked" not in got.readable_scopes | got.readable_groups


async def test_no_gated_app_asks_the_hierarchy_nothing() -> None:
    hierarchy, principals = _views(frozenset({"g1"}))
    hierarchy.failure = RuntimeError("must not be called")
    nobody = InMemoryPrincipalView({(ORG, USER): Principal(TOKENS, frozenset())})
    got = await ViewerScopeResolver(hierarchy, nobody).resolve(ORG, USER)
    assert got.readable_containers == frozenset() and got.principal_tokens == TOKENS


async def test_another_organisation_sees_nothing_of_this_one() -> None:
    hierarchy, principals = _views(frozenset({"g1", "s1"}))
    principals.principals[("org-2", USER)] = Principal(TOKENS, frozenset({APP}))
    got = await ViewerScopeResolver(hierarchy, principals).resolve("org-2", USER)
    assert got.readable_groups == frozenset() and got.readable_scopes == frozenset()


@pytest.mark.parametrize("which", ["hierarchy", "principals"])
async def test_a_failing_port_is_unavailable_never_empty(which: str) -> None:
    hierarchy, principals = _views(frozenset({"g1"}))
    (hierarchy if which == "hierarchy" else principals).failure = ConnectionError("down")
    with pytest.raises(ViewerScopesUnavailable):
        await ViewerScopeResolver(hierarchy, principals).resolve(ORG, USER)


@pytest.mark.parametrize(("org", "user"), [("", USER), (ORG, "")])
async def test_no_viewer_is_unavailable(org: str, user: str) -> None:
    with pytest.raises(ViewerScopesUnavailable):
        await ViewerScopeResolver(*_views(frozenset())).resolve(org, user)


def test_scopes_round_trip_and_reject_damage() -> None:
    s = ViewerScopes(ORG, USER, frozenset({APP}), frozenset({APP}), frozenset({"g1"}), frozenset({"s"}), frozenset({"r"}), TOKENS,
                     frozenset({"p"}), frozenset({"legacy-app"}), 7)
    assert ViewerScopes.from_json(s.to_json()) == s
    gone = json.loads(s.to_json())
    del gone["legacy"]
    with pytest.raises(KeyError):          # an entry that does not say which apps it leaves out is a miss, never "none"
        ViewerScopes.from_json(json.dumps(gone))
    with pytest.raises(ValueError):
        ViewerScopes.from_json("{")
    with pytest.raises(KeyError):
        ViewerScopes.from_json("{}")


# ---- cache ---------------------------------------------------------------------------------------------------

class Graph:
    """A mutable stand-in for the graph: what the viewer may read now."""

    def __init__(self, admitted: set[str]) -> None:
        self.admitted = admitted
        self.resolves = 0

    def service(self, cache: ViewerScopeCache | None) -> ViewerScopeService:
        graph = self

        class Principals(InMemoryPrincipalView):
            async def readable(self, org_id, user_key, node_ids):  # noqa: ANN202
                graph.resolves += 1
                return frozenset(node_ids & graph.admitted)

        hierarchy, _ = _views(frozenset())
        principals = Principals({(ORG, USER): Principal(TOKENS, frozenset({APP}))})
        return ViewerScopeService(ViewerScopeResolver(hierarchy, principals), cache)


def _cache(server: fakeredis.FakeServer | None = None, **kw) -> ViewerScopeCache:
    client = fakeredis.FakeAsyncRedis(server=server or fakeredis.FakeServer(), decode_responses=True)
    return ViewerScopeCache(lambda: client, **kw)


async def test_a_second_call_is_served_from_the_cache() -> None:
    graph = Graph({"g1"})
    service = graph.service(_cache())
    first, second = await service.resolve_now(ORG, USER), await service.resolve_now(ORG, USER)
    assert first == second and graph.resolves == 1 and first.version > 0


async def test_a_revoke_is_visible_on_the_next_call() -> None:
    graph, cache = Graph({"g1", "s2"}), _cache()
    service = graph.service(cache)
    before = await service.resolve_now(ORG, USER)
    graph.admitted.discard("s2")
    assert (await service.resolve_now(ORG, USER)) == before  # nothing bumped: the cache still answers (old, wider)
    await cache.bump(ORG)
    after = await service.resolve_now(ORG, USER)
    assert "s2" in before.readable_scopes and "s2" not in after.readable_scopes and after.version == before.version + 1


async def test_a_bump_of_one_organisation_leaves_another_cached_and_a_global_bump_drops_all() -> None:
    graph, cache = Graph({"g1"}), _cache()
    service = graph.service(cache)
    await service.resolve_now(ORG, USER)
    await cache.bump("org-other")
    await service.resolve_now(ORG, USER)
    assert graph.resolves == 1
    await cache.bump(None)
    await service.resolve_now(ORG, USER)
    assert graph.resolves == 2


async def test_a_bump_during_a_resolve_does_not_poison_the_new_version() -> None:
    graph, cache = Graph({"g1", "s2"}), _cache()
    service = graph.service(cache)
    resolve = graph.service(cache)._resolver.resolve

    # The graph changes and the event bumps while a resolve that started on the old version is in flight.
    started: list[ViewerScopes] = []

    async def slow(org: str, user: str, *, version: int = -1) -> ViewerScopes:
        got = await resolve(org, user, version=version)
        graph.admitted.discard("s2")
        await cache.bump(ORG)
        started.append(got)
        return got

    service._resolver.resolve = slow  # type: ignore[method-assign]
    await service.resolve_now(ORG, USER)
    service._resolver.resolve = resolve  # type: ignore[method-assign]
    after = await service.resolve_now(ORG, USER)
    assert "s2" in started[0].readable_scopes and "s2" not in after.readable_scopes
    assert after.version == started[0].version + 1


class DownRedis:
    async def mget(self, keys):  # noqa: ANN201
        raise ConnectionError("redis down")

    get = set = incr = mget


async def test_with_redis_down_the_answer_is_the_graphs_and_never_wider() -> None:
    graph = Graph({"g1"})
    live = await graph.service(None).resolve_now(ORG, USER)
    down = ViewerScopeCache(lambda: DownRedis())  # type: ignore[arg-type,return-value]
    got = await graph.service(down).resolve_now(ORG, USER)
    assert got.readable_groups == live.readable_groups and got.readable_scopes == live.readable_scopes
    assert got.unreadable_restricted == live.unreadable_restricted and got.version == -1
    graph.admitted.clear()  # a revoke while Redis is down: the very next call sees it
    assert (await graph.service(down).resolve_now(ORG, USER)).readable_groups == frozenset()


async def test_a_lost_bump_stops_this_process_serving_cached_scopes_until_it_lands() -> None:
    server = fakeredis.FakeServer()
    good = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    flaky: list[object] = [good]
    cache = ViewerScopeCache(lambda: flaky[0], ttl_seconds=5)  # type: ignore[arg-type,return-value]
    cache.BUMP_RETRY_DELAYS_SECONDS = (0.01,)
    graph = Graph({"g1", "s2"})
    service = graph.service(cache)
    await service.resolve_now(ORG, USER)
    flaky[0] = DownRedis()
    await cache.bump(ORG)  # the revoke's bump is lost
    graph.admitted.discard("s2")
    assert "s2" not in (await service.resolve_now(ORG, USER)).readable_scopes  # served from the graph
    flaky[0] = good  # Redis is back: the retry lands the bump
    cache._down_until = 0.0
    await asyncio.sleep(0.1)
    assert int(await good.get(f"{cache.KEY_PREFIX}:ver:{ORG}")) > 1_000_000_000_000_000  # base + the landed bump
    assert "s2" not in (await service.resolve_now(ORG, USER)).readable_scopes
    await cache.aclose()


async def test_a_damaged_or_foreign_entry_is_a_miss() -> None:
    server = fakeredis.FakeServer()
    cache = _cache(server)
    graph = Graph({"g1"})
    service = graph.service(cache)
    got = await service.resolve_now(ORG, USER)
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    key = f"{cache.KEY_PREFIX}:vs:{ORG}:{USER}:{got.version}"
    await client.set(key, "not json")
    await service.resolve_now(ORG, USER)
    assert graph.resolves == 2
    await client.set(key, got.with_version(9).to_json())  # an entry of another version under this key
    await service.resolve_now(ORG, USER)
    assert graph.resolves == 3


# ---- mode ----------------------------------------------------------------------------------------------------

@pytest.mark.parametrize(("raw", "mode"), [
    (None, AclFilterMode.LEGACY), ("", AclFilterMode.LEGACY), ("off", AclFilterMode.LEGACY),
    ("legacy", AclFilterMode.LEGACY), ("shadow", AclFilterMode.SHADOW), (" Scopes ", AclFilterMode.SCOPES),
    ("enforce", AclFilterMode.LEGACY)])
def test_mode_defaults_to_legacy_and_a_typo_is_legacy(raw: str | None, mode: AclFilterMode) -> None:
    assert AclFilterMode.from_env({} if raw is None else {"ACL_FILTER_MODE": raw}) is mode


async def test_legacy_mode_never_touches_the_service_and_unavailable_means_legacy() -> None:
    graph = Graph({"g1"})
    service = graph.service(None)
    assert await scopes_or_legacy(AclFilterMode.LEGACY, service, ORG, USER) is None and graph.resolves == 0
    assert await scopes_or_legacy(AclFilterMode.SHADOW, service, ORG, USER) is None  # no cache: nothing to serve
    cached = graph.service(_cache())
    assert await scopes_or_legacy(AclFilterMode.SHADOW, cached, ORG, USER) is None  # a miss is legacy, at once
    await cached.wait_for_fills()
    assert await scopes_or_legacy(AclFilterMode.SHADOW, cached, ORG, USER) is not None  # the fill landed
    broken = ViewerScopeService(ViewerScopeResolver(*_views(frozenset())), None)
    broken._resolver._principals.failure = ConnectionError("x")  # type: ignore[attr-defined]
    assert await scopes_or_legacy(AclFilterMode.SCOPES, broken, ORG, USER) is None


# ---- hooks ---------------------------------------------------------------------------------------------------

async def test_permission_events_bump_the_acl_version_once_registered() -> None:
    cache = _cache()
    await hooks.notify_connector_permissions_changed("c", ORG)  # nothing registered: a no-op
    hooks.init_viewer_scope_version(cache)
    try:
        base = await cache.version(ORG)
        assert base is not None
        await hooks.notify_connector_permissions_changed("c", ORG)
        await hooks.notify_connector_sync_completed("c", ORG)
        await hooks.notify_kb_records_changed("kb", ORG)
        assert await cache.version(ORG) == base + 3
        await hooks.notify_connector_permissions_changed("c", None)  # no organisation: every organisation moves
        assert await cache.version(ORG) == base + 4  # a global bump moves every organisation
    finally:
        hooks.reset_viewer_scope_version()


# ---- structure memo and term bound -----------------------------------------------------------------------------

async def test_the_structure_is_read_once_per_version_and_never_without_one() -> None:
    hierarchy, principals = _views(frozenset({"g1"}))
    resolver = ViewerScopeResolver(hierarchy, principals)
    await resolver.resolve(ORG, USER, version=3)
    await resolver.resolve(ORG, USER, version=3)
    assert hierarchy.calls == [("groups", APP), ("scopes", APP)] or sorted(hierarchy.calls) == [("groups", APP), ("scopes", APP)]
    await resolver.resolve(ORG, USER, version=4)
    assert len(hierarchy.calls) == 4  # a new version reads it again
    await resolver.resolve(ORG, USER)
    await resolver.resolve(ORG, USER)
    assert len(hierarchy.calls) == 8  # no version (counter unreadable): every call reads the graph


async def test_a_bound_on_terms_fails_over_instead_of_trimming() -> None:
    hierarchy, principals = _views(frozenset({"g1", "g2", "s2"}))
    assert await ViewerScopeResolver(hierarchy, principals, max_terms=100).resolve(ORG, USER)
    with pytest.raises(ViewerScopesUnavailable) as caught:
        await ViewerScopeResolver(hierarchy, principals, max_terms=3).resolve(ORG, USER)
    assert caught.value.reason.startswith("too_many_terms")


async def test_a_slow_resolve_is_the_legacy_path_and_nothing_is_cached() -> None:
    graph = Graph({"g1"})
    service = graph.service(_cache())
    service._timeout = 0.05
    original = service._resolver.resolve

    async def slow(org: str, user: str, *, version: int = -1) -> ViewerScopes:
        await asyncio.sleep(1)
        return await original(org, user, version=version)

    service._resolver.resolve = slow  # type: ignore[method-assign]
    with pytest.raises(ViewerScopesUnavailable) as caught:
        await service.resolve_now(ORG, USER)
    assert caught.value.reason == "timeout" and graph.resolves == 0


async def test_a_node_only_a_grant_can_open_is_asked_about_only_when_the_viewer_holds_one() -> None:
    scopes = (ScopeNode("s1", APP, True, needs_grant=True), ScopeNode("s2", APP, True, needs_grant=True),
              ScopeNode("s3", APP, False))
    hierarchy = InMemoryHierarchyView(ORG, {APP: frozenset({"g1"})}, scopes, {APP: AppFacts(False, "JIRA")})
    principals = InMemoryPrincipalView(
        {(ORG, USER): Principal(TOKENS, frozenset({APP}), granted_ids=frozenset({"s2"}))},
        {(ORG, USER): frozenset({"g1", "s1", "s2", "s3"})})  # the graph would admit s1 too: the resolver never asks
    got = await ViewerScopeResolver(hierarchy, principals).resolve(ORG, USER)
    assert principals.calls == [3]  # g1, s2, s3
    assert got.readable_scopes == {"s2", "s3"} and got.unreadable_restricted == {"s1"}


async def test_a_viewer_that_times_out_leaves_the_structure_read_running_for_the_next() -> None:
    hierarchy, principals = _views(frozenset({"g1", "s2"}))
    original = hierarchy.record_group_ids

    async def slow(org_id: str, app_id: str):  # noqa: ANN202
        await asyncio.sleep(0.3)
        return await original(org_id, app_id)

    hierarchy.record_group_ids = slow  # type: ignore[method-assign]
    service = ViewerScopeService(ViewerScopeResolver(hierarchy, principals), _cache(), timeout_seconds=0.05)
    with pytest.raises(ViewerScopesUnavailable):
        await service.resolve_now(ORG, USER)
    await asyncio.sleep(0.4)  # the shielded read finishes meanwhile
    got = await service.resolve_now(ORG, USER)
    assert got.readable_groups == {"g1"}
    assert [c for c in hierarchy.calls if c[0] == "groups"] == [("groups", APP)]  # read once, shared by both calls


# ---- round 2: memo age and bound, lost counters, per-bump deadlines, the miss path, one gate read ---------------

async def test_the_structure_memo_expires_with_age_even_if_the_version_never_moves() -> None:
    hierarchy, principals = _views(frozenset({"g1"}))
    resolver = ViewerScopeResolver(hierarchy, principals, memo_max_age_seconds=0.05)
    await resolver.resolve(ORG, USER, version=3)
    await resolver.resolve(ORG, USER, version=3)
    assert len(hierarchy.calls) == 2  # groups and scopes, read once for two resolves
    await asyncio.sleep(0.08)
    # a scope restricted after the memo was filled shows up once the memo has aged out
    hierarchy.scopes = (*hierarchy.scopes, ScopeNode("s-new", APP, True))
    got = await resolver.resolve(ORG, USER, version=3)
    assert "s-new" in got.unreadable_restricted


async def test_the_structure_memo_is_bounded() -> None:
    hierarchy, principals = _views(frozenset({"g1"}))
    principals.principals[(ORG, USER)] = Principal(TOKENS, frozenset({"app-1", "app-2", "app-3"}))
    resolver = ViewerScopeResolver(hierarchy, principals, memo_max_entries=2)
    await resolver.resolve(ORG, USER, version=1)
    assert len(resolver._memo) == 2


async def test_a_lost_version_counter_never_makes_an_old_entry_current() -> None:
    server = fakeredis.FakeServer()
    client = fakeredis.FakeAsyncRedis(server=server, decode_responses=True)
    cache = ViewerScopeCache(lambda: client)
    graph = Graph({"g1", "s2"})
    service = graph.service(cache)
    old = await service.resolve_now(ORG, USER)
    graph.admitted.discard("s2")  # the revoke, whose bump the lost counter takes with it
    await cache.bump(ORG)
    await client.flushall()  # failover to a replica without the counters (and without the entries, but see below)
    await client.set(f"{cache.KEY_PREFIX}:vs:{ORG}:{USER}:{old.version}", old.to_json())  # an entry that survived
    await asyncio.sleep(0.01)  # a real failover takes longer than this
    fresh = await service.resolve_now(ORG, USER)
    assert fresh.version != old.version and "s2" not in fresh.readable_scopes
    assert fresh.version > 1_000_000_000_000_000  # a base far above any counter a short-lived key could reach


async def test_each_lost_bump_has_its_own_deadline() -> None:
    cache = ViewerScopeCache(lambda: DownRedis(), ttl_seconds=1)  # type: ignore[arg-type,return-value]
    cache.BUMP_RETRY_DELAYS_SECONDS = (0.05,)
    await cache.bump("org-a")
    await asyncio.sleep(0.6)
    await cache.bump("org-b")
    assert set(cache._unbumped) == {"org-a", "org-b"}
    await asyncio.sleep(0.6)  # org-a's deadline (1 s after its failure) has passed; org-b's has not
    assert not cache.usable and set(cache._unbumped) == {"org-b"}
    await asyncio.sleep(0.6)
    assert set(cache._unbumped) == frozenset()
    await cache.aclose()


async def test_a_miss_is_legacy_at_once_and_the_cache_fills_once_in_the_background() -> None:
    graph = Graph({"g1", "s2"})
    service = graph.service(_cache())
    results = await asyncio.gather(*(service.scopes(ORG, USER) for _ in range(5)), return_exceptions=True)
    assert all(isinstance(r, ViewerScopesUnavailable) and r.reason == "miss" for r in results)
    assert graph.resolves == 0 or graph.resolves == 1  # nothing waited for the graph
    await service.wait_for_fills()
    assert graph.resolves == 1  # one fill for five searches
    hit = await service.scopes(ORG, USER)
    assert "s2" in hit.readable_scopes and graph.resolves == 1


async def test_without_a_usable_version_a_search_is_legacy_and_nothing_is_filled() -> None:
    graph = Graph({"g1"})
    service = graph.service(ViewerScopeCache(lambda: DownRedis()))  # type: ignore[arg-type,return-value]
    with pytest.raises(ViewerScopesUnavailable) as caught:
        await service.scopes(ORG, USER)
    assert caught.value.reason == "bypass"
    await service.wait_for_fills()
    assert graph.resolves == 0


async def test_the_gate_is_read_once_per_resolve() -> None:
    from types import SimpleNamespace

    from app.services.permissions.adapters import GraphPrincipalView

    reads: list[str] = []

    class Provider:
        async def get_knowledge_hub_access_v3(self, user_key, org_id):  # noqa: ANN202
            reads.append("v3")
            return {"grantee_ids": [user_key], "gated_app_ids": [APP], "by_connector": {APP: ["x"]}}

        async def check_access(self, user_key, org_id, *, node_ids, access):  # noqa: ANN202
            assert access is not None  # handed over, not read again
            return SimpleNamespace(node_ids=frozenset(node_ids), placeholder_ids=frozenset())

    view = GraphPrincipalView(Provider(), chunk=1)  # type: ignore[arg-type]
    who = await view.principal(ORG, USER)
    assert await view.readable(ORG, who, frozenset({"a", "b", "c"})) == {"a", "b", "c"}
    assert reads == ["v3"] and who.granted_ids == {"x"}


async def test_a_union_app_lists_only_scopes_restricted_by_rule_or_deletion() -> None:
    """A Drive account has a non-inheriting file for nearly everything it owns; listing them would be the set's bulk and
    no filter would read it. A limited-access folder (rule-restricted) or a deleted folder does hide what is below it,
    in every app. Confluence and Jira (Intersection) list all restricted scopes."""
    scopes = (ScopeNode("d-noinherit", "drive", True, rule_restricted=False), ScopeNode("d-rule", "drive", True),
              ScopeNode("c1", "conf", True, rule_restricted=False), ScopeNode("j1", "jira", True, rule_restricted=False))
    hierarchy = InMemoryHierarchyView(
        ORG, {}, scopes,
        {"drive": AppFacts(False, "DRIVE WORKSPACE"), "conf": AppFacts(False, "CONFLUENCE"), "jira": AppFacts(False, "JIRA DATA CENTER")})
    principals = InMemoryPrincipalView({(ORG, USER): Principal(TOKENS, frozenset({"drive", "conf", "jira"}))}, {})
    got = await ViewerScopeResolver(hierarchy, principals).resolve(ORG, USER)
    assert got.unreadable_restricted == {"d-rule", "c1", "j1"}


async def test_an_open_app_is_the_only_gated_app_a_container_filter_may_match() -> None:
    hierarchy = InMemoryHierarchyView(ORG, {}, (), {"kb": AppFacts(True, "KB"), "slack": AppFacts(False, "SLACK")})
    principals = InMemoryPrincipalView({(ORG, USER): Principal(TOKENS, frozenset({"kb", "slack"}))}, {})
    got = await ViewerScopeResolver(hierarchy, principals).resolve(ORG, USER)
    assert got.gated_apps == {"kb", "slack"} and got.open_apps == {"kb"} and got.readable_containers == {"kb"}


async def test_a_burst_of_misses_queues_nothing_and_a_bump_cancels_the_rest() -> None:
    """2,000 viewers miss at once: at most `max_background` fills run, none wait in a queue; and a fill whose version a
    permission event has moved past does not touch the graph."""
    graph = Graph({"g1"})
    cache = _cache()
    service = graph.service(cache)
    resolve = service._resolver.resolve
    started: list[str] = []

    async def slow(org: str, user: str, *, version: int = -1) -> ViewerScopes:
        started.append(user)
        await asyncio.sleep(0.05)
        return await resolve(org, user, version=version)

    service._resolver.resolve = slow  # type: ignore[method-assign]
    results = await asyncio.gather(*(service.scopes(ORG, f"user-{i}") for i in range(2000)), return_exceptions=True)
    assert all(isinstance(r, ViewerScopesUnavailable) for r in results)
    assert len(service._filling) <= 8
    await cache.bump(ORG)
    await service.wait_for_fills()
    assert len(started) <= 8  # the 1,992 other misses started nothing
    # fills that were running when the bump landed finish under the old version and are never read again
    assert await cache.get(ORG, "user-0", (await cache.version(ORG)) or 0) is None


async def test_a_fill_for_a_version_that_has_moved_does_not_resolve() -> None:
    graph = Graph({"g1"})
    cache = _cache()
    service = graph.service(cache)
    with pytest.raises(ViewerScopesUnavailable):
        await service.scopes(ORG, USER)  # starts the fill ...
    await cache.bump(ORG)  # ... which has not run yet when the version moves
    await service.wait_for_fills()
    assert graph.resolves == 0


async def test_a_legacy_app_is_not_a_gated_app() -> None:
    hierarchy = InMemoryHierarchyView(ORG, {"conf": frozenset({"g1"})}, (), {"conf": AppFacts(False, "CONFLUENCE", True), "slack": AppFacts(False, "SLACK")})
    principals = InMemoryPrincipalView({(ORG, USER): Principal(TOKENS, frozenset({"conf", "slack"}))}, {})
    got = await ViewerScopeResolver(hierarchy, principals).resolve(ORG, USER)
    assert got.legacy_apps == {"conf"} and got.gated_apps == {"slack"}


async def test_the_term_bound_trips_before_the_store_is_asked() -> None:
    hierarchy, principals = _views(frozenset({"g1"}))
    with pytest.raises(ViewerScopesUnavailable) as caught:
        await ViewerScopeResolver(hierarchy, principals, max_terms=2).resolve(ORG, USER)  # asks g1, g2, s2
    assert caught.value.reason.startswith("too_many_terms") and principals.calls == []
