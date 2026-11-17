"""POST /api/v1/entity/internal/user-node: the connectors service writes a brand-new caller's graph user for the
query service. Only a USER_NODE_ENSURE service token reaches it, and only the user and org that token names are
written (through the registered EntityEventService.ensure_user, which reads Node's live records of them)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.middlewares.auth import AUTH_POLICY_ATTR
from app.api.routes.entity import ensure_user_node_internal, router
from app.config.constants.service import TokenScopes
from app.utils import user_lookup


@pytest.fixture
def ensure(monkeypatch: pytest.MonkeyPatch) -> AsyncMock:
    fn = AsyncMock(return_value=True)
    monkeypatch.setitem(user_lookup._ensurer, "caller_node", fn)
    return fn


async def test_writes_exactly_the_user_and_org_the_token_names(ensure: AsyncMock) -> None:
    resp = await ensure_user_node_internal(claims={"orgId": "org-1", "userId": "u1", "token_type": "scoped"})
    assert (resp.status_code, json.loads(resp.body)) == (200, {"written": True})
    ensure.assert_awaited_once_with("org-1", "u1")


async def test_reports_a_refusal(ensure: AsyncMock) -> None:
    ensure.return_value = False
    resp = await ensure_user_node_internal(claims={"orgId": "org-1", "userId": "u1"})
    assert json.loads(resp.body) == {"written": False}


@pytest.mark.parametrize(("claims", "status"), [({"userId": "u1"}, 401), ({"orgId": "org-1"}, 403)])
async def test_a_token_without_org_or_user_writes_nothing(ensure: AsyncMock, claims: dict[str, Any], status: int) -> None:
    with pytest.raises(Exception) as exc:
        await ensure_user_node_internal(claims=claims)
    assert getattr(exc.value, "status_code", None) == status
    ensure.assert_not_awaited()


async def test_without_an_ensurer_it_is_503() -> None:
    with pytest.raises(HTTPException) as exc:
        await ensure_user_node_internal(claims={"orgId": "org-1", "userId": "u1"})
    assert exc.value.status_code == 503


def test_only_a_user_node_ensure_service_token_reaches_it() -> None:
    (route,) = [r for r in router.routes if getattr(r, "path", "") == "/api/v1/entity/internal/user-node"]
    policies = [getattr(d.call, AUTH_POLICY_ATTR, None) for d in route.dependant.dependencies]
    (policy,) = [p for p in policies if p is not None]
    assert (policy.kind, policy.service_scopes) == ("service", frozenset({TokenScopes.USER_NODE_ENSURE.value}))
