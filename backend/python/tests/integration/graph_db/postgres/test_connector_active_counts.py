"""The connector_active gauge's count on PostgreSQL against a real database: every org's active connector instances under a system context,
one count per org, as the gauge loop asks for it from a detached task with no org bound.

The database is shared, so each test gives its apps types no other test holds and asserts on those keys only; ``unknown`` is a delta.

Needs a migrated database: PG_CORE_URL (a `pipeshub_connectors_login` DSN), PG_POOLER=none.
"""
from __future__ import annotations

import asyncio
import contextvars
import logging
import uuid
from typing import TYPE_CHECKING, Any

import pytest

import app.connectors_main as connectors_main
from tests.support.platform_event_capture import replace_event_listener
from app.core.org_context import MissingOrgContext, current_org_id, for_org, system
from app.utils.logger import log_record_fields

from .test_legacy_migrations import orgs as orgs

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from .test_access import World

pytestmark = pytest.mark.integration


async def unbound(coro: Coroutine[Any, Any, Any]) -> Any:  # noqa: ANN401
    """Run as a detached loop runs it: no org context at all."""
    return await asyncio.create_task(coro, context=contextvars.Context())


async def count_in_system(provider: Any) -> dict[str, int]:  # noqa: ANN401
    with system("connector_metrics"):
        return await provider.count_active_apps_by_type()


def _app(app_type: str | None, *, active: bool = True) -> dict:
    return {"_key": f"app-{uuid.uuid4().hex[:12]}", **({"type": app_type} if app_type is not None else {}), "isActive": active}


async def _seed(orgs: Callable[[], World], org_ctx, apps_per_org: list[list[dict]]) -> list[World]:
    worlds = []
    for apps in apps_per_org:
        w = orgs()
        with org_ctx(w.org):
            await w.nodes("organizations", {"_key": w.org, "isActive": True})
            await w.nodes("apps", *apps)
        worlds.append(w)
    return worlds


async def test_every_orgs_active_apps_are_counted_once_under_a_system_context(orgs, org_ctx) -> None:
    ta, tb, shared = (f"t-{uuid.uuid4().hex[:10]}" for _ in range(3))
    a, b = await _seed(orgs, org_ctx, [
        [_app(ta), _app(ta), _app(ta, active=False), _app(shared)],
        [_app(tb), _app(shared), _app(shared, active=False)],
    ])

    got = await unbound(count_in_system(a.p))

    # Each app once: two orgs do not double either org's count, and an inactive app is not counted.
    assert {t: got.get(t) for t in (ta, tb, shared)} == {ta: 2, tb: 1, shared: 2}

    async def in_org(org: str) -> dict[str, int]:
        with for_org(org, job="test"):
            return await a.p.count_active_apps_by_type()

    in_a, in_b = await unbound(in_org(a.org)), await unbound(in_org(b.org))
    assert {t: in_a.get(t) for t in (ta, tb, shared)} == {ta: 2, tb: None, shared: 1}      # an org context counts that org only
    assert {t: in_b.get(t) for t in (ta, tb, shared)} == {ta: None, tb: 1, shared: 1}
    with pytest.raises(MissingOrgContext):
        await unbound(a.p.count_active_apps_by_type())                                       # no context at all still fails closed


async def test_an_app_with_no_type_is_counted_as_unknown(orgs, org_ctx, pg_provider) -> None:
    before = (await unbound(count_in_system(pg_provider))).get("unknown", 0)
    await _seed(orgs, org_ctx, [[_app(None), _app("")], [_app(None), _app(None, active=False)]])

    assert (await unbound(count_in_system(pg_provider))).get("unknown", 0) - before == 3


async def test_one_orgs_failure_does_not_stop_the_others_and_is_raised_after(orgs, org_ctx, monkeypatch, caplog) -> None:
    ta, tb = (f"t-{uuid.uuid4().hex[:10]}" for _ in range(2))
    bad, good = await _seed(orgs, org_ctx, [[_app(ta)], [_app(tb)]])
    p = bad.p
    real_q, counted = p._q, []

    async def q(fn, transaction, *args: object, **kw: object):  # noqa: ANN202
        org = current_org_id()
        if org == bad.org:
            raise RuntimeError("org down")
        rows = await real_q(fn, transaction, *args, **kw)
        counted.append(org)
        return rows

    monkeypatch.setattr(p, "_q", q)
    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError, match="org down") as exc:
        await unbound(count_in_system(p))

    assert good.org in counted                                                     # the later orgs still ran
    assert any(bad.org in note and "failed in this pass" in note for note in exc.value.__notes__)
    # The provider's per-org line names the org that failed, and only that one; matched by its fields, not its wording.
    failed = [(r.levelno, f.get("error_type")) for r in caplog.records
              if (f := log_record_fields(r)).get("op") == "_sweep" and f.get("outcome") == "failure" and f.get("target_org_id") == bad.org]
    assert failed and all(level >= logging.WARNING and error_type == "RuntimeError" for level, error_type in failed), failed
    assert not [r for r in caplog.records if log_record_fields(r).get("op") == "_sweep" and log_record_fields(r).get("target_org_id") == good.org]


async def test_the_gauge_loop_publishes_every_orgs_counts_from_a_detached_task(orgs, org_ctx, monkeypatch, caplog) -> None:
    ta, tb = (f"t-{uuid.uuid4().hex[:10]}" for _ in range(2))
    a, _b = await _seed(orgs, org_ctx, [[_app(ta), _app(ta)], [_app(tb)]])
    published: list[dict] = []
    replace_event_listener(monkeypatch, "connector.inventory.counted", published.append, "active_by_type")

    async def stop(_s: float) -> None:
        raise asyncio.CancelledError      # one refresh, then leave the loop

    monkeypatch.setattr(connectors_main.asyncio, "sleep", stop)
    log = logging.getLogger("connector-active-it")
    with caplog.at_level(logging.WARNING, logger=log.name), pytest.raises(asyncio.CancelledError):
        await unbound(connectors_main.refresh_connector_metrics(a.p, log, interval_s=1))

    assert not [r.getMessage() for r in caplog.records if r.name == log.name]
    assert len(published) == 1 and (published[0].get(ta), published[0].get(tb)) == (2, 1), published
