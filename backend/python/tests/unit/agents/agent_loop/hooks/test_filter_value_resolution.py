"""Unit tests for the PRE_TOOL_USE `filter_value_resolution` middleware:
identity substitution ONLY — no more display-name/record-group resolution
or ambiguity DENYs (that entire mechanism is gone along with `FilterSpec`).

Covers: unrelated tool paths pass through untouched; the graph lookup is
skipped entirely when the query has no self-reference token; a resolvable
self-reference rewrites `tool_input` in place; an unresolvable one denies
rather than risk answering as the wrong person; and the content_query/limit
handoff to the POST hook via `ctx.metadata` still happens either way."""

import importlib
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent_loop_lib.hooks.middleware.context import ToolCallContext
from app.agent_loop_lib.hooks.middleware.decisions import PreDecision

# `app.agents.agent_loop.hooks` (the package `__init__.py`) re-exports the
# FUNCTION `filter_value_resolution`, overwriting the package's attribute
# of the same name that submodule import normally sets — so
# `import app.agents.agent_loop.hooks.filter_value_resolution as fvr_module`
# (which resolves via attribute traversal) would silently bind `fvr_module`
# to that function, not the submodule. `importlib.import_module` fetches
# the submodule straight out of `sys.modules` instead, sidestepping that.
fvr_module = importlib.import_module("app.agents.agent_loop.hooks.filter_value_resolution")
from app.agents.actions.filtered_search.adapters.jira import JiraFilterAdapter  # noqa: E402
from app.agents.agent_loop.hooks.filter_value_resolution import filter_value_resolution  # noqa: E402

pytestmark = pytest.mark.asyncio


def _context(state: dict) -> SimpleNamespace:
    return SimpleNamespace(tool_state=state)


def _ctx(tool_input: dict, tool_path: str = "/tools/filtered_search/search_jira_issues") -> ToolCallContext:
    return ToolCallContext(tool_path=tool_path, tool_input=tool_input)


@pytest.fixture(autouse=True)
def _patch_registry(monkeypatch):
    monkeypatch.setattr(fvr_module.FilterAdapterRegistry, "get", classmethod(lambda cls, ct: JiraFilterAdapter))
    yield


@pytest.fixture(autouse=True)
def _patch_connector_type(monkeypatch):
    monkeypatch.setattr(fvr_module, "resolve_connector_type", AsyncMock(return_value="JIRA"))
    yield


async def _run_next(middleware, ctx) -> bool:
    called = False

    async def _next():
        nonlocal called
        called = True

    await middleware(ctx, _next)
    return called


async def test_missing_connector_id_passes_through_untouched() -> None:
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({"jql": "assignee = currentUser()"})

    called = await _run_next(middleware, ctx)

    assert called is True
    assert ctx.decision == PreDecision.ALLOW


async def test_unrelated_tool_path_passes_through_untouched() -> None:
    """A tool not in NATIVE_QUERY_PARAM_BY_PATH must cost nothing — no
    connector resolution, no graph lookup."""
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({"connector_id": "c1", "jql": "assignee = currentUser()"}, tool_path="/tools/jira/get_issue")

    called = await _run_next(middleware, ctx)

    assert called is True
    assert ctx.decision == PreDecision.ALLOW


async def test_missing_query_value_passes_through_untouched() -> None:
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({"connector_id": "c1"})

    called = await _run_next(middleware, ctx)

    assert called is True


async def test_query_without_self_reference_skips_graph_lookup_entirely(monkeypatch) -> None:
    """The whole point of `has_self_reference` — a plain filter query costs
    nothing beyond the connector-type lookup."""
    resolve_self = AsyncMock()
    monkeypatch.setattr(fvr_module, "resolve_self_identity", resolve_self)
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({"connector_id": "c1", "jql": 'project = "ES"'})

    called = await _run_next(middleware, ctx)

    assert called is True
    assert ctx.decision == PreDecision.ALLOW
    assert ctx.tool_input["jql"] == 'project = "ES"'
    resolve_self.assert_not_called()


async def test_unknown_connector_id_denies(monkeypatch) -> None:
    monkeypatch.setattr(fvr_module, "resolve_connector_type", AsyncMock(return_value=None))
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({"connector_id": "bogus", "jql": "assignee = currentUser()"})

    await middleware(ctx, AsyncMock())

    assert ctx.decision == PreDecision.DENY
    assert "bogus" in ctx.decision_reason


async def test_connector_type_without_registered_adapter_denies(monkeypatch) -> None:
    monkeypatch.setattr(fvr_module.FilterAdapterRegistry, "get", classmethod(lambda cls, ct: None))
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({"connector_id": "c1", "jql": "assignee = currentUser()"})

    await middleware(ctx, AsyncMock())

    assert ctx.decision == PreDecision.DENY
    assert "does not support filter search" in ctx.decision_reason


async def test_resolvable_self_reference_rewrites_tool_input(monkeypatch) -> None:
    monkeypatch.setattr(fvr_module, "resolve_self_identity", AsyncMock(return_value="acc-123"))
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({"connector_id": "c1", "jql": "assignee = currentUser()"})

    await middleware(ctx, AsyncMock())

    assert ctx.decision == PreDecision.ALLOW
    assert ctx.tool_input["jql"] == 'assignee = "acc-123"'


async def test_unresolvable_self_reference_denies_never_executes_as_wrong_person(monkeypatch) -> None:
    monkeypatch.setattr(fvr_module, "resolve_self_identity", AsyncMock(return_value=None))
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({"connector_id": "c1", "jql": "assignee = currentUser()"})
    next_fn = AsyncMock()

    await middleware(ctx, next_fn)

    assert ctx.decision == PreDecision.DENY
    assert ctx.tool_input["jql"] == "assignee = currentUser()"  # left untouched
    next_fn.assert_not_called()


async def test_identity_lookup_exception_denies_rather_than_raises(monkeypatch) -> None:
    monkeypatch.setattr(fvr_module, "resolve_self_identity", AsyncMock(side_effect=RuntimeError("graph down")))
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({"connector_id": "c1", "jql": "assignee = currentUser()"})

    await middleware(ctx, AsyncMock())  # must not raise

    assert ctx.decision == PreDecision.DENY


async def test_content_query_and_limit_are_stashed_into_metadata_for_post_hook(monkeypatch) -> None:
    monkeypatch.setattr(fvr_module, "resolve_self_identity", AsyncMock(return_value="acc-123"))
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({
        "connector_id": "c1", "jql": "assignee = currentUser()",
        "content_query": "token refresh", "limit": 25,
    })

    await middleware(ctx, AsyncMock())

    assert ctx.metadata["filtered_search_call"] == {"content_query": "token refresh", "limit": 25}


async def test_no_content_query_still_stashes_none_for_plain_filter_query() -> None:
    middleware = filter_value_resolution(_context({"graph_provider": object()}))
    ctx = _ctx({"connector_id": "c1", "jql": 'project = "ES"'})

    await middleware(ctx, AsyncMock())

    assert ctx.metadata["filtered_search_call"] == {"content_query": None, "limit": None}
