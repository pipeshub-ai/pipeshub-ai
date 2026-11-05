"""The retrieval side of the shadow comparison: a real search never carries a scope filter, the comparator gets copies and a plan of its own,
its search is the only one that does carry the filter, and nothing it does can fail or change a search."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import app.modules.retrieval.retrieval_service as mod
from app.modules.retrieval.retrieval_service import RetrievalService, _QueryPlan, _SHADOW_SCOPE_FILTER
from app.services.vector_db.filters import ScopeFilter


def service(shadow: object | None) -> RetrievalService:
    s = RetrievalService.__new__(RetrievalService)
    s.logger = MagicMock()
    s._shadow = shadow
    s.graph_provider = MagicMock()
    s.graph_provider.check_access = AsyncMock(return_value=SimpleNamespace(records_by_vrid={"v1": "r1"}))
    return s


def flt() -> ScopeFilter:
    return ScopeFilter(current=frozenset({"c1"}), version=5, generation=3)


def submit(s: RetrievalService, plan: _QueryPlan, returned: set[str], accessible: dict[str, str]) -> None:
    s._submit_shadow(["q"], "FILTER", 20, "org", "user", "ukey", frozenset({"c1"}), (), plan, returned, accessible)


async def test_the_comparator_runs_the_same_search_with_the_filter_and_a_plan_of_its_own() -> None:
    seen: dict = {}
    shadow = MagicMock()
    s = service(shadow)
    real_plan = _QueryPlan(dense=[[0.1]], sparse=[[1]], collections=["c"])

    async def search(queries, filter, limit, org_id, user_id, *, plan=None, record_cut=None):  # noqa: A002, ANN001, ANN202
        seen.update(queries=queries, filter=filter, limit=limit, org=org_id, plan=plan, scope=_SHADOW_SCOPE_FILTER.get())
        plan.failed_collections.append("shadow-only")
        return [{"metadata": {"virtualRecordId": "v1"}}]

    s._execute_parallel_searches = search
    submit(s, real_plan, {"v1", "v2"}, {"v1": "r1"})
    org, user, returned, admitted, run_scoped, check = shadow.submit.call_args.args
    assert (org, user, returned, admitted) == ("org", "ukey", {"v1", "v2"}, {"v1"})
    assert _SHADOW_SCOPE_FILTER.get() is None                          # not set here, in the real search's context
    assert await run_scoped(flt()) == [{"metadata": {"virtualRecordId": "v1"}}]
    assert seen["scope"] == flt() and seen["queries"] == ["q"] and seen["filter"] == "FILTER" and seen["limit"] == 20 and seen["org"] == "org"
    assert seen["plan"] is not real_plan and seen["plan"].dense == [[0.1]] and seen["plan"].collections == ["c"]       # the embeddings are reused
    assert real_plan.failed_collections == []                           # and nothing the comparator's search records reaches the real plan
    assert _SHADOW_SCOPE_FILTER.get() is None                          # the filter does not outlive the shadow search
    assert await check({"v1"}) == {"v1"}
    kw = s.graph_provider.check_access.call_args
    assert kw.args == ("ukey", "org") and kw.kwargs["virtual_record_ids"] == {"v1"} and kw.kwargs["connector_ids"] == frozenset({"c1"})


def test_the_comparator_gets_copies_not_the_searchs_own_collections() -> None:
    shadow = MagicMock()
    returned, accessible = {"v1"}, {"v1": "r1"}
    submit(service(shadow), _QueryPlan(dense=[[0.1]]), returned, accessible)
    sent_returned, sent_admitted = shadow.submit.call_args.args[2:4]
    assert sent_admitted is not accessible and sent_admitted == {"v1"}


def test_a_comparator_that_fails_cannot_fail_the_search() -> None:
    shadow = MagicMock()
    shadow.submit.side_effect = RuntimeError("boom")
    s = service(shadow)
    assert submit(s, _QueryPlan(dense=[[0.1]]), {"v1"}, {"v1": "r1"}) is None
    s.logger.warning.assert_called_once()


async def test_a_real_search_request_carries_no_scope_filter_and_the_shadow_search_does() -> None:
    s = service(None)
    s._capabilities = SimpleNamespace(supports_sparse_vectors=False, supports_server_side_text_search=False)
    s.cache_service = None
    seen: list = []

    async def fan_out(collections, requests, *a, **k):  # noqa: ANN001, ANN202
        seen.extend(requests)
        return [[] for _ in requests]

    s._fan_out_searches = fan_out
    s._resolve_search_collections = AsyncMock(return_value=["c"])
    s._cut_batches = MagicMock(return_value=[])
    plan = _QueryPlan()
    await s._run_searches(["q"], "F", 5, "org", "user", [[0.1]], [[1]], plan=plan)
    assert [r.scope_filter for r in seen] == [None]
    token = _SHADOW_SCOPE_FILTER.set(flt())
    try:
        await s._run_searches(["q"], "F", 5, "org", "user", [[0.1]], [[1]], plan=_QueryPlan())
    finally:
        _SHADOW_SCOPE_FILTER.reset(token)
    assert [r.scope_filter for r in seen] == [None, flt()]


def test_no_comparator_is_built_unless_the_mode_is_shadow(monkeypatch) -> None:
    monkeypatch.delenv("ACL_FILTER_MODE", raising=False)
    s = RetrievalService.__new__(RetrievalService)
    s.logger = MagicMock()
    assert s._build_shadow(MagicMock()) is None
    assert mod.RetrievalService._shadow is None                         # the class default: an instance built without __init__ has none either
