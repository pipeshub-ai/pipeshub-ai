"""The agent-usage routes with the real graph providers: a failed store read is a refusal, never ``200`` with no agents.

Node deletes an AI model or a web search provider when these routes answer no agents, so a read that could not be made must not
look like one. The store-level raise (and that the provider logs nothing) is pinned in
``tests/unit/services/graph_db/test_provider_failure_logs_no_driver_text.py``.
"""
from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.api.routes.agent import get_model_usage, get_web_search_provider_usage
from app.core.org_context import OrgContext, OrgMismatch, bind
from tests.unit.services.graph_db.test_provider_failure_logs_no_driver_text import (
    _driver_error,
    _provider,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

ORG = "org-1"
BACKENDS = ["neo4j", "arango", "postgres"]
ROUTES = [
    # the model route already answered 500 for a raised read; the web search one answered 400 (either way Node refuses)
    pytest.param(get_model_usage, "model-1", 500, id="model"),
    pytest.param(get_web_search_provider_usage, "serper", 400, id="web_search"),
]


@pytest.fixture(autouse=True)
def _org() -> Iterator[None]:
    with bind(OrgContext(ORG, "u1", "user", "jwt", "t")):
        yield


@pytest.mark.asyncio
@pytest.mark.parametrize("backend", BACKENDS)
@pytest.mark.parametrize(("route", "arg", "least_status"), ROUTES)
async def test_a_failed_usage_read_is_refused_not_answered_with_no_agents(backend, route, arg, least_status) -> None:
    services = {"graph_provider": _provider(backend, _driver_error(stamped=True)), "logger": MagicMock()}
    with patch("app.api.routes.agent.get_services", new_callable=AsyncMock, return_value=services), \
         patch("app.api.routes.agent._get_user_context", return_value={"userId": "u1", "orgId": ORG}), \
         pytest.raises(HTTPException) as refused:
        answer = await route(MagicMock(), arg)
        pytest.fail(f"answered {answer.status_code} {answer.body!r} for a failed read")
    assert refused.value.status_code >= least_status


@pytest.mark.asyncio
async def test_on_postgres_another_org_reads_nothing_without_asking_the_store(monkeypatch) -> None:
    # A caller bug, not a failed read: the route always passes the token's org, which is the ambient one.
    provider = _provider("postgres", _driver_error(stamped=True))
    with pytest.raises(OrgMismatch):                                          # enforce, the default, refuses it outright
        await provider.get_agents_by_model_key("org-2", "model-1")
    monkeypatch.setenv("ORG_CONTEXT_MODE", "shadow")                          # in shadow the provider's own guard answers nothing
    assert await provider.get_agents_by_model_key("org-2", "model-1") == []
    assert await provider.get_agents_by_web_search_provider("org-2", "serper") == []
    provider._q.assert_not_awaited()
