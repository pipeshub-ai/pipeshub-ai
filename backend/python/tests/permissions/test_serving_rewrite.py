"""A search served by the stamps returns what the legacy request returns, before, during and after a structural change.

While a change is being rewritten the connector is not served (its document says so), so its points are the legacy request's; once the rewrite
completes the connector is served again and its stamps decide. At every moment `arms AND filter AND post-check` equals `arms AND post-check`:
the filter never admits what the post-check refuses (it can only narrow), and never hides what the legacy request returns (the record that a
rewrite hid, or a not-current connector held back, is visible again when the rewrite completes).
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from app.services.permissions.currency import app_is_served
from app.services.permissions.serving import stamp_filter
from app.services.vector_db import scope_expr as sx

from .model import Mutation as M
from .rewrite_kit import PREFIX, Env, _prefixed
from .runner import OracleLayer
from .scenario import WorldStore
from .scope_layer import OracleScopeLayer
from .serving_corpus import legacy_returns

W = WorldStore()
CHANGES = {
    "move": (M("moved", "page:Handbook", "space:ALL", "space:ALL"),),
    "restrict": (M("rule_set", "page:Handbook", value="RESTRICTED"),),
    "no-op rule": (M("rule_set", "page:Onboarding", value="STRICT"),),
    "two moves": (M("moved", "page:Handbook", "space:ALL", "space:ALL"), M("moved", "page:Compensation", "space:ALL", "space:ALL")),
}


@pytest.fixture(autouse=True)
def _writers_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ACL_SCOPE_FIELDS", "on")


async def serve_audit(env: Env) -> tuple[int, int, int, set[str]]:
    """``(extra, missing, decided, served)``: viewer-record pairs the served search returns that the legacy request does not, the ones it drops that
    the legacy request returns, and the pairs a stamp decided."""
    world, layer, oracle = env.world, OracleScopeLayer(), OracleLayer()
    served = frozenset(a for a in env.apps if app_is_served(env.graph.app_docs.get(a)))
    vrids = env.vrids()
    of_vrid: dict[str, list[str]] = {}
    for r, v in vrids.items():
        n = world.node(r)
        if n.kind == "record" and not n.deleted and not n.placeholder:
            of_vrid.setdefault(v, []).append(r)
    pids = {p: v for v, ps in env.loaded.points.items() for p in ps}
    payloads = {str(p.id): p.payload for p in await env.vdb.retrieve_points(env.loaded.collection, list(pids))}
    ids = [n.id for n in world.nodes]
    extra = missing = decided = 0
    for viewer in world.users():
        scopes = _prefixed(await layer.viewer_scopes(world, viewer), (), ())
        flt = stamp_filter(scopes, served, defer=True)
        node = sx.admits(flt)
        truth = oracle.explain(world, viewer, ids)
        for pid, v in pids.items():
            payload = payloads.get(pid)
            if payload is None or v not in of_vrid:
                continue
            allowed = any(truth[r].admitted for r in of_vrid[v])
            arms = legacy_returns(scopes, of_vrid[v], payload)
            through = sx.evaluate(node, payload)
            decided += bool(set(payload.get("connectorIds") or []) <= served and payload.get("scopeStamp") != "legacy")
            extra += bool(through and arms and allowed) and not (arms and allowed)
            missing += bool(arms and allowed) and not (through and arms and allowed)
    return extra, missing, decided, set(served)


@pytest.mark.parametrize("name", sorted(CHANGES))
async def test_serving_equals_the_legacy_request_before_during_and_after_a_change(name: str) -> None:
    env = await Env.make(W.world("golden/confluence_hr"))
    app = PREFIX + "app"
    extra, missing, decided, served = await serve_audit(env)
    assert (extra, missing) == (0, 0) and served == {app} and decided > 0          # stamps decide a clean connector
    await env.change(CHANGES[name])
    extra, missing, decided, served = await serve_audit(env)
    assert (extra, missing, decided) == (0, 0, 0) and served == set()             # during: the legacy request alone, nothing hidden, nothing decided by an old stamp
    await env.settle()
    extra, missing, decided, served = await serve_audit(env)
    assert (extra, missing) == (0, 0) and served == {app} and decided > 0          # recovery: the rewrite completed, the stamps decide again
    assert not await env.problems()


async def test_a_connector_the_full_pass_has_not_redone_is_not_served_and_is_after_it() -> None:
    env = await Env.make(W.world("golden/confluence_hr"))
    app = PREFIX + "app"
    await env.change(CHANGES["move"], whole=True)          # a change nobody lists: only the full pass covers it
    extra, missing, decided, served = await serve_audit(env)
    assert (extra, missing, decided) == (0, 0, 0) and served == set()
    await env.settle()
    extra, missing, decided, served = await serve_audit(env)
    assert (extra, missing) == (0, 0) and served == {app} and decided > 0


async def test_the_pure_filter_would_have_hidden_a_pending_record() -> None:
    """The control: with the connector counted as current while its root is pending, the plain filter hides records the legacy request returns."""
    env = await Env.make(W.world("golden/confluence_hr"))
    await env.change((M("rule_set", "page:Onboarding", value="STRICT"),))          # a change that moves nobody's access, and still registers a root
    world, layer, oracle = env.world, OracleScopeLayer(), OracleLayer()
    roots = env.pending()
    assert roots
    hidden = 0
    pids = {p: v for v, ps in env.loaded.points.items() for p in ps}
    payloads = {str(p.id): p.payload for p in await env.vdb.retrieve_points(env.loaded.collection, list(pids))}
    ids = [n.id for n in world.nodes]
    for viewer in world.users():
        scopes = _prefixed(await layer.viewer_scopes(world, viewer), roots, [PREFIX + "app"])
        flt = stamp_filter(scopes, frozenset({PREFIX + "app"}), defer=False)
        flt = replace(flt, pending=frozenset(roots), blocked=frozenset({PREFIX + "app"}))
        truth = oracle.explain(world, viewer, ids)
        vr = {v: [r for r, x in env.vrids().items() if x == v] for v in env.loaded.points}
        for pid, v in pids.items():
            if any(truth[r].admitted for r in vr[v] if world.node(r).kind == "record") and legacy_returns(scopes, vr[v], payloads[pid]) and not sx.evaluate(sx.admits(flt), payloads[pid]):
                hidden += 1
    assert hidden > 0
