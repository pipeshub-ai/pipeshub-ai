"""``for_each_org``: the org list under ``system``, each org's work under ``for_org``, one org's failure contained."""
from __future__ import annotations

import asyncio
import contextvars
import logging

import pytest

from app.core import org_context
from app.core.org_jobs import for_each_org

_LOG = logging.getLogger("test-org-jobs")


class _Orgs:
    """A store whose org list records the context it was read under."""

    def __init__(self, orgs: list[dict] | None, error: Exception | None = None) -> None:
        self.orgs, self.error, self.calls = orgs, error, []

    async def get_all_orgs(self, **kwargs: object) -> list[dict] | None:
        ctx = org_context._ctx.get()
        self.calls.append((ctx.kind if ctx else None, ctx.source if ctx else None, kwargs))
        if self.error:
            raise self.error
        return self.orgs


async def _detached(graph: _Orgs, raising: frozenset[str] = frozenset()) -> tuple[list[tuple], org_context.OrgContext | None]:
    """Run for_each_org in an empty context, as a spawn_detached job does; the binding each work call saw, and what is bound after."""
    seen: list[tuple] = []

    async def work(org_id: str) -> None:
        ctx = org_context.current()
        seen.append((org_id, ctx.org_id, ctx.kind, ctx.source))
        if org_id in raising:
            raise RuntimeError("secret detail")

    async def run() -> org_context.OrgContext | None:
        await for_each_org(graph, "demo_job", work, _LOG)
        return org_context._ctx.get()

    return seen, await asyncio.get_running_loop().create_task(run(), context=contextvars.Context())


async def test_lists_once_under_system_every_internal_org_active_or_not() -> None:
    graph = _Orgs([{"_key": "a"}])
    await _detached(graph)
    assert graph.calls == [("system", "system:demo_job", {"active": False})]


async def test_active_lists_only_active_orgs() -> None:
    graph = _Orgs([])

    async def work(_org_id: str) -> None: ...

    await asyncio.get_running_loop().create_task(for_each_org(graph, "demo_job", work, _LOG, active=True), context=contextvars.Context())
    assert graph.calls == [("system", "system:demo_job", {"active": True})]


async def test_runs_work_once_per_org_under_that_org() -> None:
    seen, _ = await _detached(_Orgs([{"_key": "a"}, {"id": "b"}]))
    assert seen == [("a", "a", "job", "job:demo_job"), ("b", "b", "job", "job:demo_job")]


async def test_an_org_without_id_is_skipped() -> None:
    seen, _ = await _detached(_Orgs([{"name": "no id"}, {"_key": "b"}]))
    assert [s[0] for s in seen] == ["b"]


async def test_one_orgs_failure_is_one_warning_without_its_text_and_the_next_org_runs(caplog) -> None:
    seen, after = await _detached(_Orgs([{"_key": "a"}, {"_key": "b"}]), raising=frozenset({"a"}))
    assert [r.getMessage() for r in caplog.records if r.levelno >= logging.WARNING] == [
        "demo_job failed org=a error=RuntimeError"]
    assert [s[0] for s in seen] == ["a", "b"]
    assert after is None


async def test_an_org_list_failure_propagates_to_the_caller() -> None:
    with pytest.raises(RuntimeError, match="db down"):
        await _detached(_Orgs(None, error=RuntimeError("db down")))


@pytest.mark.parametrize("orgs", [None, []])
async def test_no_orgs_means_no_work(orgs, caplog) -> None:
    seen, _ = await _detached(_Orgs(orgs))
    assert seen == []
    assert not caplog.records


async def test_nothing_is_left_bound() -> None:
    _, after = await _detached(_Orgs([{"_key": "a"}]))
    assert after is None
