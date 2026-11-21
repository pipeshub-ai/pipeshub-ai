"""GET /agent/{agent_id} admits a token that may write the agent.

The gateway may read the agent as the caller before an update or delete, to know
whether it is a service agent and whether org-wide sharing changes. Those routes
need agent:write, so a write-only token must be able to make that read.
"""

import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import pytest
from fastapi import HTTPException
from fastapi.routing import APIRoute

import app.edition_config  # noqa: F401  (edition seams load before any router)
from app.api.routes.agent import router


def _scope_check() -> Callable[[Any], Coroutine[Any, Any, None]]:
    route = next(
        r for r in router.routes
        if isinstance(r, APIRoute) and r.path == "/{agent_id}" and "GET" in r.methods
    )
    checks = [d.call for d in route.dependant.dependencies if d.call.__name__ == "_check_scopes"]
    assert len(checks) == 1
    return checks[0]


def _request(scopes: list[str]) -> object:
    class FakeRequest:
        class state:
            user = {"userId": "u1", "orgId": "o1", "isOAuth": True, "oauthScopes": scopes}

    return FakeRequest()


@pytest.mark.parametrize("scopes", [["agent:read"], ["agent:write"]])
def test_read_or_write_scope_may_read_an_agent(scopes: list[str]) -> None:
    asyncio.run(_scope_check()(_request(scopes)))


def test_other_scopes_may_not_read_an_agent() -> None:
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_scope_check()(_request(["conversation:chat"])))
    assert exc.value.status_code == 403
