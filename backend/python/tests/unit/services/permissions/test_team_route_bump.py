"""A team route's `finally` must never replace the route's own result with an error from finding the org."""

from __future__ import annotations

import fakeredis

from app.api.routes import entity as routes
from app.services.cache import invalidation_hooks as hooks
from app.services.permissions.cache import ViewerScopeCache


async def test_the_bump_after_a_team_write_survives_an_unbound_org() -> None:
    client = fakeredis.FakeAsyncRedis(server=fakeredis.FakeServer(), decode_responses=True)
    cache = ViewerScopeCache(lambda: client)
    hooks.init_viewer_scope_version(cache)
    try:
        before = await cache.version("org-1")
        await routes._principals_changed()          # no org bound in this test: the global counter moves
        assert await cache.version("org-1") == before + 1
    finally:
        hooks.reset_viewer_scope_version()


async def test_a_rejected_team_update_does_not_move_the_version(monkeypatch) -> None:
    """A request that fails before any member is written must not reset the organisation's cached scopes."""
    import json
    from unittest.mock import AsyncMock, MagicMock

    import pytest
    from fastapi import HTTPException

    graph = MagicMock()
    graph.update_node = AsyncMock(return_value=None)               # the team does not exist: 404
    services = {"graph_provider": graph, "logger": MagicMock()}
    bumped = AsyncMock()
    monkeypatch.setattr(routes, "get_services", AsyncMock(return_value=services))
    monkeypatch.setattr(routes, "get_user_or_503", AsyncMock(return_value={"_key": "u"}))
    monkeypatch.setattr(routes, "current_org_id", lambda: "org-1")
    monkeypatch.setattr(routes, "_caller_is_an_administrator", AsyncMock(return_value=True))
    monkeypatch.setattr(routes, "_authorize_team_change", AsyncMock(return_value=None))
    monkeypatch.setattr(routes, "_refuse_taken_team_name", AsyncMock())
    monkeypatch.setattr(routes, "_principals_changed", bumped)
    request = MagicMock()
    request.state.user = {"userId": "m", "orgId": "org-1"}
    request.body = AsyncMock(return_value=json.dumps({"name": "x"}).encode())

    with pytest.raises(HTTPException) as err:
        await routes.update_team.__wrapped__(request, "team-1") if hasattr(routes.update_team, "__wrapped__") else await routes.update_team(request, "team-1")
    assert err.value.status_code == 404
    bumped.assert_not_awaited()
