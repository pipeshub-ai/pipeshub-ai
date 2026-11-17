"""The serving plan: off unless the mode is `scopes`; a connector is served only while its document says so now; any doubt is the legacy request;
and the cached scopes' pending roots and blocked connectors never decide."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.permissions.currency import (
    StampCurrency,
    app_is_exact,
    app_is_served,
)
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.mode import ENV_ACL_FILTER_MODE
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    set_resolved_generation,
)
from app.services.permissions.scopes import ViewerScopes
from app.services.permissions.serving import ServingPlanner, stamp_filter
from app.services.permissions.wiring import build_serving_planner
from app.services.vector_db import scope_expr as sx
from app.services.vector_db.scope_backfill import BACKFILL_KEYS as K
from app.services.vector_db.scope_backfill import DONE

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG, USER, GEN = "o" * 24, "u1", 3


@pytest.fixture(autouse=True)
def _generation() -> Iterator[None]:
    set_resolved_generation(GEN)
    yield
    set_resolved_generation(None)


def current(**over: object) -> dict:
    return {K["state"]: DONE, K["version"]: SCOPE_FIELDS_VERSION, K["generation"]: GEN, K["structure_generation"]: 5, "scopeProjectionGeneration": 5, **over}


def scopes(**kw: object) -> ViewerScopes:
    return ViewerScopes(ORG, USER, **{"gated_apps": frozenset({"c1", "c2"}), "readable_groups": frozenset({"g1"}), **kw})  # type: ignore[arg-type]


def graph(docs: dict[str, dict]) -> MagicMock:
    g = MagicMock()
    g.get_nodes_by_field_in = AsyncMock(side_effect=lambda coll, field, ids, **k: [{"_key": i, **docs[i]} for i in ids if i in docs])
    return g


def planner(docs: dict[str, dict], held: ViewerScopes | Exception | None = None) -> tuple[ServingPlanner, MagicMock]:
    svc = MagicMock()
    svc.scopes = AsyncMock(side_effect=held) if isinstance(held, Exception) else AsyncMock(return_value=held or scopes())
    g = graph(docs)
    return ServingPlanner(svc, StampCurrency(g)), g


async def test_the_default_mode_builds_no_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    g = MagicMock()
    for raw in (None, "", "off", "legacy", "shadow", "typo"):
        monkeypatch.delenv(ENV_ACL_FILTER_MODE, raising=False)
        if raw is not None:
            monkeypatch.setenv(ENV_ACL_FILTER_MODE, raw)
        assert build_serving_planner(g) is None, raw
    g.permission_hierarchy_view.assert_not_called()
    monkeypatch.setenv(ENV_ACL_FILTER_MODE, "scopes")
    assert build_serving_planner(g) is not None


@pytest.mark.parametrize(("name", "over"), [
    ("running", {K["state"]: "running"}), ("failed pass (a churned connector)", {K["state"]: "failed"}), ("moved on", {"scopeProjectionGeneration": 6}), ("unsafe", {"scopeProjectionUnsafe": True}),
    ("pending roots", {"scopeProjectionPendingRoots": '{"r":5}'}), ("unreadable roots", {"scopeProjectionPendingRoots": "x"}),
    ("an unclosed write", {"scopeProjectionOpened": 2, "scopeProjectionClosed": 1}), ("other generation", {K["generation"]: GEN - 1}),
    ("a sync running", {"status": "SYNCING"}), ("a full sync running", {"status": "FULL_SYNCING"}),
])
def test_a_connector_that_is_not_current_or_has_pending_roots_is_not_served(name: str, over: dict) -> None:
    assert app_is_served(current()) and not app_is_served(current(**over)), name


def test_an_idle_or_queued_connector_is_served() -> None:
    assert all(app_is_served(current(status=s)) for s in ("IDLE", "QUEUED", None))


def test_a_missing_document_is_not_served() -> None:
    assert not app_is_served(None) and not app_is_served({})


@pytest.mark.parametrize(("over", "exact"), [
    ({}, False), ({K["exact"]: False}, False), ({K["exact"]: True}, True), ({K["exact"]: "true"}, False), ({K["exact"]: 1}, False), ({K["exact"]: None}, False),
    ({K["exact"]: True, K["state"]: "running"}, False), ({K["exact"]: True, "scopeProjectionPendingRoots": '{"r":5}'}, False),
    ({K["exact"]: True, "status": "SYNCING"}, False), ({K["exact"]: True, "scopeProjectionGeneration": 6}, False)])
def test_a_connector_is_exact_only_when_it_is_served_and_its_pass_says_so(over: dict, exact: bool) -> None:
    assert app_is_exact(current(**over)) is exact and not app_is_exact(None) and not app_is_exact({K["exact"]: True})


async def test_the_plan_says_exact_only_when_every_gated_connector_is_served_and_exact() -> None:
    both = {"c1": current(**{K["exact"]: True}), "c2": current(**{K["exact"]: True})}
    for docs, want in ((both, True), ({**both, "c2": current()}, False), ({"c1": both["c1"]}, False),            # one not exact, one with no document
                       ({**both, "c2": current(scopeProjectionPendingRoots='{"r":5}', **{K["exact"]: True})}, False)):
        flt, exact = await planner(docs)[0].plan_exact(ORG, USER)
        assert (flt is not None, exact) == (True, want), docs
        assert (await planner(docs)[0].plan(ORG, USER)) == flt                                                     # `plan` is unchanged
    assert await planner({})[0].plan_exact(ORG, USER) == (None, False)


async def test_a_viewer_with_a_legacy_app_is_never_exact() -> None:
    both = {"c1": current(**{K["exact"]: True}), "c2": current(**{K["exact"]: True})}
    held = scopes(legacy_apps=frozenset({"c3"}))                            # c3's points are read by the arms and can be denied: the fetch must over-fetch
    flt, exact = await planner(both, held)[0].plan_exact(ORG, USER)
    assert flt is not None and exact is False


async def test_only_the_served_connectors_are_in_the_filter() -> None:
    p, _ = planner({"c1": current(), "c2": current(scopeProjectionPendingRoots='{"r":5}')})
    flt = await p.plan(ORG, USER)
    assert flt is not None and flt.current == {"c1"} and flt.defer and flt.version == SCOPE_FIELDS_VERSION and flt.generation == GEN


async def test_currency_is_read_for_every_search_and_kept_by_nobody() -> None:
    docs = {"c1": current()}
    p, g = planner(docs)
    assert (await p.plan(ORG, USER)).current == {"c1"}                      # served
    docs["c1"] = current(scopeProjectionGeneration=6)                         # a structural change commits: the generation moves
    assert await p.plan(ORG, USER) is None                                    # the very next search is the legacy request
    docs["c1"] = current(scopeProjectionGeneration=6, **{K["structure_generation"]: 6})      # the rewrite completes
    assert (await p.plan(ORG, USER)).current == {"c1"}                      # served again
    assert g.get_nodes_by_field_in.await_count == 3


@pytest.mark.parametrize("docs", [{}, {"c1": {}}, {"c1": current(**{K["state"]: None})}])
async def test_nothing_served_is_the_legacy_request(docs: dict) -> None:
    assert await planner(docs)[0].plan(ORG, USER) is None


async def test_an_unreadable_graph_is_the_legacy_request() -> None:
    p, g = planner({"c1": current()})
    g.get_nodes_by_field_in = AsyncMock(side_effect=RuntimeError("down"))
    assert await p.plan(ORG, USER) is None


async def test_scopes_that_are_not_cached_are_the_legacy_request_and_the_graph_is_not_asked() -> None:
    p, g = planner({"c1": current()}, held=ViewerScopesUnavailable("miss"))
    assert await p.plan(ORG, USER) is None
    g.get_nodes_by_field_in.assert_not_awaited()
    assert await planner({"c1": current()})[0].plan(ORG, "") is None


async def test_a_slow_plan_is_the_legacy_request() -> None:
    import asyncio

    p, g = planner({"c1": current()})
    p._budget = 0.01

    async def slow(*a: object, **k: object) -> list:
        await asyncio.sleep(1)
        return []

    g.get_nodes_by_field_in = slow
    assert await p.plan(ORG, USER) is None


async def test_the_cached_pending_and_blocked_do_not_decide() -> None:
    """Registering or clearing roots does not move the ACL version, so a cached entry keeps the roots it was resolved with. The filter ignores
    them: a connector with roots now is not served (its document), and one whose roots were rewritten is not hidden by the old ones."""
    stale = scopes(pending=frozenset({"x1"}), blocked=frozenset({"c1"}), readable_groups=frozenset({"g1"}), open_apps=frozenset())
    flt = stamp_filter(stale, frozenset({"c1"}), defer=True)
    assert flt.pending == frozenset() and flt.blocked == frozenset()
    container = {"connectorIds": ["c1"], "recordGroupIds": ["g1"], "rootRecordGroupIds": [], "scopeStamp": "container", "pathIds": [], "aclScope": [],
                 "scopeFieldsVersion": SCOPE_FIELDS_VERSION, "scopeGeneration": GEN}
    assert sx.evaluate(sx.admits(flt), container)                             # a cached blocked connector does not hide a container point (no over-hide)
    below = {**container, "scopeStamp": "union", "recordGroupIds": [], "pathIds": ["x1", "g1"], "aclScope": ["own"]}
    assert sx.evaluate(sx.admits(flt), below)                                 # a cached pending root does not hide a path
    p, _ = planner({"c1": current(scopeProjectionPendingRoots='{"x1":5}'), "c2": current()}, held=scopes(pending=frozenset(), blocked=frozenset()))
    served = await p.plan(ORG, USER)
    assert served.current == {"c2"}                                           # an empty cached entry does not admit a connector that has roots now (no over-admit)


async def test_a_viewer_with_long_lists_is_the_legacy_request_without_a_read_of_the_documents() -> None:
    from app.services.permissions.serving import MAX_SERVED_TERMS

    big = scopes(readable_groups=frozenset(f"g{i}" for i in range(MAX_SERVED_TERMS + 1)))
    p, g = planner({"c1": current()}, held=big)
    assert await p.plan(ORG, USER) is None
    g.get_nodes_by_field_in.assert_not_awaited()
    at_cap = scopes(readable_groups=frozenset(f"g{i}" for i in range(MAX_SERVED_TERMS)))
    assert await planner({"c1": current()}, held=at_cap)[0].plan(ORG, USER) is not None
