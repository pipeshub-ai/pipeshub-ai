"""The shadow comparison: off unless the mode is `shadow`, bounded, and never part of an answer. It counts, per record, whether the filtered
search and the post-check agree; a record the filter admits and the post-check refuses is an alert. Metrics and logs hold counts and the
requesting organisation and viewer, never a record id."""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core import org_context
from app.services.permissions import shadow as shadow_mod
from app.services.permissions.errors import ViewerScopesUnavailable
from app.services.permissions.mode import ENV_ACL_FILTER_MODE
from app.services.permissions.scope_fields import (
    SCOPE_FIELDS_VERSION,
    set_resolved_generation,
)
from app.services.permissions.scopes import ViewerScopes
from app.services.permissions.shadow import ShadowComparator, ShadowIncomplete
from app.services.permissions.wiring import build_shadow_comparator
from app.services.vector_db.filters import ScopeFilter, ScopeFilterTooLarge

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG, USER = "o" * 24, "user-1"
GEN = 3


class Counter:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def inc(self, *labels: str, value: float = 1.0) -> None:
        self.calls.append((labels, value))

    def observe(self, *labels: str, value: float) -> None:
        self.calls.append((labels, value))

    def total(self, label: str) -> float:
        return sum(v for (labels, v) in self.calls if labels == (label,))


@pytest.fixture(autouse=True)
def _generation_and_metrics(monkeypatch: pytest.MonkeyPatch) -> Iterator[SimpleMetrics]:
    set_resolved_generation(GEN)
    compared, skipped, seconds = Counter(), Counter(), Counter()
    monkeypatch.setattr(shadow_mod, "SHADOW_COMPARED", compared)
    monkeypatch.setattr(shadow_mod, "SHADOW_SKIPPED", skipped)
    monkeypatch.setattr(shadow_mod, "SHADOW_SECONDS", seconds)
    yield SimpleMetrics(compared, skipped, seconds)
    set_resolved_generation(None)


class SimpleMetrics:
    def __init__(self, compared: Counter, skipped: Counter, seconds: Counter) -> None:
        self.compared, self.skipped, self.seconds = compared, skipped, seconds


def scopes(**kw: object) -> ViewerScopes:
    return ViewerScopes(ORG, USER, **{"gated_apps": frozenset({"c1", "c2"}), "open_apps": frozenset({"c1"}), **kw})  # type: ignore[arg-type]


def graph(docs: dict[str, dict | None]) -> MagicMock:
    g = MagicMock()
    g.get_document = AsyncMock(side_effect=lambda key, coll, **k: docs.get(key))
    return g


def done(**kw: object) -> dict:
    return {"scopeBackfillState": "done", "scopeBackfillVersion": SCOPE_FIELDS_VERSION, "scopeBackfillGeneration": GEN, **kw}


def comparator(docs: dict[str, dict | None] | None = None, *, scope_value: ViewerScopes | Exception | None = None, **kw: object) -> ShadowComparator:
    svc = MagicMock()
    svc.scopes = AsyncMock(side_effect=scope_value) if isinstance(scope_value, Exception) else AsyncMock(return_value=scope_value or scopes())
    return ShadowComparator(svc, graph(docs if docs is not None else {"c1": done(), "c2": done()}), **{"sample_rate": 1.0, **kw})  # type: ignore[arg-type]


def result(*vrids: str) -> list[dict]:
    return [{"metadata": {"virtualRecordId": v}} for v in vrids]


async def run(c: ShadowComparator, returned: set[str], admitted: set[str], scoped: list[str], checks: set[str] | None = None) -> dict[str, int]:
    check_calls: list[set[str]] = []

    async def run_scoped(flt: ScopeFilter) -> list[dict]:
        assert isinstance(flt, ScopeFilter)
        assert org_context.current_org_id() == ORG          # the search runs for the viewer's organisation, bound by the comparator
        return result(*scoped)

    async def check(vrids: set[str]) -> set[str]:
        check_calls.append(set(vrids))
        return (checks or set()) & vrids

    out = await c.compare(ORG, USER, returned, admitted, run_scoped, check)
    run.check_calls = check_calls  # type: ignore[attr-defined]
    return out


async def test_it_counts_agreement_over_admits_and_what_the_recall_loss(_generation_and_metrics) -> None:
    c = comparator()
    # the real search found a, b, c and the post-check allowed a and b; the filtered search returned a (agree), c (refused: over-admit) and x
    # (never post-checked: asked now, allowed); b is missed
    counts = await run(c, {"a", "b", "c"}, {"a", "b"}, ["a", "c", "x"], checks={"x"})
    assert counts == {"agree": 2, "over_admit": 1, "recall_loss": 1}
    assert run.check_calls == [{"x"}]                               # only a record the real search never judged is asked
    m = _generation_and_metrics.compared
    assert (m.total("agree"), m.total("over_admit"), m.total("recall_loss")) == (2, 1, 1)


async def test_a_record_the_filter_returns_that_the_post_check_refuses_is_an_alert_without_any_record_id(caplog, _generation_and_metrics) -> None:
    caplog.set_level(logging.DEBUG)
    c = comparator()
    counts = await run(c, {"secret-vrid-1"}, set(), ["secret-vrid-1", "other-org-vrid-9"], checks=set())
    assert counts["over_admit"] == 2
    alert = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert len(alert) == 1 and "over_admit" in alert[0].getMessage() and ORG in alert[0].getMessage()
    text = " ".join(r.getMessage() for r in caplog.records)
    assert "secret-vrid-1" not in text and "other-org-vrid-9" not in text
    assert not any("vrid" in str(labels) for labels, _ in _generation_and_metrics.compared.calls)           # labels are outcomes, never ids


async def test_a_record_the_real_path_admits_and_the_filter_hides_is_recall_loss_not_an_alert(caplog, _generation_and_metrics) -> None:
    caplog.set_level(logging.DEBUG)
    counts = await run(comparator(), {"a", "b", "refused"}, {"a", "b"}, ["a"])          # b is missed; "refused" was never admitted, so missing it is right
    assert counts == {"agree": 1, "over_admit": 0, "recall_loss": 1}
    m = _generation_and_metrics.compared
    assert (m.total("over_admit"), m.total("recall_loss")) == (0, 1) and not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_a_record_the_filter_admits_that_the_real_path_refuses_is_an_alert_not_recall_loss(caplog, _generation_and_metrics) -> None:
    caplog.set_level(logging.DEBUG)
    counts = await run(comparator(), {"a", "refused"}, {"a"}, ["a", "refused"])         # the filter returns everything the real path admits, and one more
    assert counts == {"agree": 1, "over_admit": 1, "recall_loss": 0}
    m = _generation_and_metrics.compared
    assert (m.total("over_admit"), m.total("recall_loss")) == (1, 0) and len([r for r in caplog.records if r.levelno >= logging.ERROR]) == 1


async def test_a_clean_comparison_raises_no_alert(caplog) -> None:
    caplog.set_level(logging.DEBUG)
    counts = await run(comparator(), {"a", "b"}, {"a"}, ["a"])
    assert counts["over_admit"] == 0 and not [r for r in caplog.records if r.levelno >= logging.ERROR]


async def test_it_asks_the_post_check_nothing_when_the_real_search_judged_every_record() -> None:
    await run(comparator(), {"a", "b"}, {"a"}, ["a", "b"])
    assert run.check_calls == []


@pytest.mark.parametrize(("exc", "reason"), [
    (NotImplementedError("no"), "unsupported"), (ScopeFilterTooLarge("big"), "too_large"), (TimeoutError(), "timeout"), (RuntimeError("boom"), "error")])
async def test_a_failing_comparison_is_counted_as_skipped_and_never_raises(exc: Exception, reason: str, _generation_and_metrics) -> None:
    c = comparator()

    async def run_scoped(flt: ScopeFilter) -> list[dict]:
        raise exc

    assert await c.compare(ORG, USER, {"a"}, {"a"}, run_scoped, AsyncMock()) == {}
    assert _generation_and_metrics.skipped.total(reason) == 1 and _generation_and_metrics.compared.calls == []


async def test_a_slow_step_times_out_instead_of_lingering(_generation_and_metrics) -> None:
    c = comparator(step_timeout_seconds=0.05)

    async def slow(flt: ScopeFilter) -> list[dict]:
        await asyncio.sleep(5)
        return []

    assert await c.compare(ORG, USER, {"a"}, {"a"}, slow, AsyncMock()) == {}
    assert _generation_and_metrics.skipped.total("timeout") == 1


async def test_unavailable_scopes_and_no_current_connector_skip_before_any_search(_generation_and_metrics) -> None:
    never = AsyncMock()
    assert await comparator(scope_value=ViewerScopesUnavailable("timeout")).compare(ORG, USER, {"a"}, {"a"}, never, never) == {}
    assert _generation_and_metrics.skipped.total("scopes_unavailable") == 1
    for docs in ({}, {"c1": None}, {"c1": done(scopeBackfillState="running")}, {"c1": done(scopeBackfillVersion=SCOPE_FIELDS_VERSION - 1)},
                 {"c1": done(scopeBackfillGeneration=GEN - 1)}, {"c1": done(scopeProjectionUnsafe=True)}):
        assert await comparator(docs).compare(ORG, USER, {"a"}, {"a"}, never, never) == {}
    assert _generation_and_metrics.skipped.total("no_current_connector") == 6
    never.assert_not_called()


async def test_only_apps_that_are_finished_current_and_not_unsafe_are_used() -> None:
    seen: list[ScopeFilter] = []

    async def run_scoped(flt: ScopeFilter) -> list[dict]:
        seen.append(flt)
        return []

    c = comparator({"c1": done(), "c2": done(scopeProjectionUnsafe=True)})
    await c.compare(ORG, USER, {"a"}, {"a"}, run_scoped, AsyncMock())
    assert seen[0].current == frozenset({"c1"}) and seen[0].version == SCOPE_FIELDS_VERSION and seen[0].generation == GEN
    set_resolved_generation(-2)                                         # the shared state could not be read: no stamp is current
    assert await comparator().compare(ORG, USER, {"a"}, {"a"}, AsyncMock(), AsyncMock()) == {}


async def test_it_samples_and_bounds_what_runs_at_once(_generation_and_metrics) -> None:
    gate = asyncio.Event()

    async def run_scoped(flt: ScopeFilter) -> list[dict]:
        await gate.wait()
        return []

    c = comparator(sample_rate=1.0, max_inflight=2)
    for _ in range(5):
        c.submit(ORG, USER, {"a"}, {"a"}, run_scoped, AsyncMock())
    assert _generation_and_metrics.skipped.total("busy") == 3 and len(c._inflight) == 2        # the rest are dropped, not queued
    gate.set()
    await c.wait()
    assert len(c._inflight) == 0
    never = comparator(sample_rate=0.0)
    never.submit(ORG, USER, {"a"}, {"a"}, run_scoped, AsyncMock())
    assert _generation_and_metrics.skipped.total("sampled_out") == 1 and not never._inflight
    some = comparator(sample_rate=0.5, rng=random.Random(7))
    some.submit(ORG, USER, set(), {"a"}, run_scoped, AsyncMock())          # nothing returned: nothing to compare, nothing counted
    assert not some._inflight


async def test_submitting_returns_nothing_and_never_raises() -> None:
    c = comparator(sample_rate=1.0)
    assert not asyncio.iscoroutinefunction(c.submit)                    # the search never awaits it
    assert c.submit(ORG, USER, {"a"}, {"a"}, AsyncMock(side_effect=RuntimeError("boom")), AsyncMock()) is None
    await c.wait()


def test_the_comparator_exists_only_in_shadow_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    g = MagicMock()
    for mode in (None, "", "off", "legacy", "scopes", "typo"):
        if mode is None:
            monkeypatch.delenv(ENV_ACL_FILTER_MODE, raising=False)
        else:
            monkeypatch.setenv(ENV_ACL_FILTER_MODE, mode)
        assert build_shadow_comparator(g) is None, mode
    g.permission_hierarchy_view.assert_not_called()                     # nothing is built, nothing queried
    monkeypatch.setenv(ENV_ACL_FILTER_MODE, "shadow")
    assert isinstance(build_shadow_comparator(g), ShadowComparator)


async def test_a_partly_failed_shadow_search_is_skipped_as_an_error_not_counted_as_recall_loss(_generation_and_metrics) -> None:
    async def run_scoped(flt: ScopeFilter) -> list[dict]:
        raise ShadowIncomplete

    assert await comparator().compare(ORG, USER, {"a", "b"}, {"a", "b"}, run_scoped, AsyncMock()) == {}
    assert _generation_and_metrics.skipped.total("error") == 1 and _generation_and_metrics.compared.total("recall_loss") == 0


async def test_without_a_user_nothing_is_resolved_or_compared(_generation_and_metrics) -> None:
    c = comparator()
    c.submit(ORG, "", {"a"}, {"a"}, AsyncMock(), AsyncMock())
    assert _generation_and_metrics.skipped.total("no_user") == 1 and not c._inflight
    c._scopes.scopes.assert_not_called()


async def test_a_cancelled_comparison_is_counted_as_cancelled(_generation_and_metrics) -> None:
    started = asyncio.Event()

    async def run_scoped(flt: ScopeFilter) -> list[dict]:
        started.set()
        await asyncio.sleep(60)
        return []

    c = comparator(sample_rate=1.0)
    c.submit(ORG, USER, {"a"}, {"a"}, run_scoped, AsyncMock())
    await started.wait()
    [task] = c._inflight
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert _generation_and_metrics.skipped.total("cancelled") == 1


@pytest.mark.parametrize(("name", "bad"), [
    ("ACL_SHADOW_SAMPLE", "often"), ("ACL_SHADOW_SAMPLE", "-1"), ("ACL_SHADOW_SAMPLE", "5"), ("ACL_SHADOW_SAMPLE", "nan"),
    ("ACL_SHADOW_MAX_INFLIGHT", "0"), ("ACL_SHADOW_MAX_INFLIGHT", "-2"), ("ACL_SHADOW_MAX_INFLIGHT", "1.5"), ("ACL_SHADOW_MAX_INFLIGHT", "x"),
    ("ACL_SHADOW_STEP_TIMEOUT_S", "0"), ("ACL_SHADOW_STEP_TIMEOUT_S", "-1"), ("ACL_SHADOW_STEP_TIMEOUT_S", "nan"), ("ACL_SHADOW_STEP_TIMEOUT_S", "soon"),
    ("ACL_SHADOW_MAX_APPS", "0"), ("ACL_SHADOW_MAX_APPS", "many"),
])
def test_an_invalid_setting_stops_start_up_naming_the_setting(monkeypatch: pytest.MonkeyPatch, name: str, bad: str) -> None:
    monkeypatch.setenv(ENV_ACL_FILTER_MODE, "shadow")
    monkeypatch.setenv(name, bad)
    with pytest.raises(ValueError, match=name):
        build_shadow_comparator(MagicMock())
    monkeypatch.setenv(ENV_ACL_FILTER_MODE, "legacy")
    assert build_shadow_comparator(MagicMock()) is None                 # outside shadow mode the settings are not read


def test_valid_settings_are_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(ENV_ACL_FILTER_MODE, "shadow")
    for name, value in (("ACL_SHADOW_SAMPLE", "0"), ("ACL_SHADOW_MAX_INFLIGHT", "2"), ("ACL_SHADOW_STEP_TIMEOUT_S", "1.5"), ("ACL_SHADOW_MAX_APPS", "7")):
        monkeypatch.setenv(name, value)
    c = build_shadow_comparator(MagicMock())
    assert (c._rate, c._max_inflight, c._timeout, c._max_apps) == (0.0, 2, 1.5, 7)


async def test_the_viewer_scope_cache_registered_after_the_comparator_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.services.cache import invalidation_hooks as hooks

    monkeypatch.setenv(ENV_ACL_FILTER_MODE, "shadow")
    hooks.reset_viewer_scope_version()
    c = build_shadow_comparator(MagicMock())                             # built first, as a service constructor may be
    with pytest.raises(ViewerScopesUnavailable, match="no_cache"):
        await c._scopes.scopes(ORG, USER)
    cache = MagicMock()
    cache.version = AsyncMock(return_value=None)
    monkeypatch.setitem(hooks._scope_cache, "cache", cache)              # then register_viewer_scope_version runs
    with pytest.raises(ViewerScopesUnavailable, match="bypass"):         # the cache is consulted now
        await c._scopes.scopes(ORG, USER)
    cache.version.assert_awaited()
