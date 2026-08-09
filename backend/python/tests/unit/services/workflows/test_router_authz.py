"""Every workflow router mounted on the query service enforces a scope.

`tool_authoring` and `migration` were mounted with no `require_scopes`
dependency at all, and `tool_authoring` additionally read `org_id`/`user_id`
from `X-Org-Id`/`X-User-Id` request headers -- so any authenticated user could
act as another tenant by setting a header. This test fails if a new router is
added the same way.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.api.routes.workflows import router as workflows_router
from app.services.workflows.migration.api import router as migration_router
from app.services.workflows.tool_authoring.api import router as tool_authoring_router


def _has_scope_check(dependencies) -> bool:
    return any(
        "require_scopes" in getattr(dep.dependency, "__qualname__", "")
        for dep in dependencies
    )


@pytest.mark.parametrize(
    ("name", "router"),
    [
        ("tool_authoring", tool_authoring_router),
        ("migration", migration_router),
    ],
)
def test_router_requires_a_scope(name: str, router) -> None:
    assert _has_scope_check(router.dependencies), (
        f"{name} router is mounted without a require_scopes dependency"
    )


def test_workflows_router_checks_scopes_per_route() -> None:
    """`workflows.py` scopes each route individually rather than the router,
    since read and write endpoints need different scopes."""
    unscoped = [
        route.path
        for route in workflows_router.routes
        if not _has_scope_check(getattr(route, "dependencies", []))
    ]
    assert not unscoped, f"unscoped workflow routes: {unscoped}"


def test_tool_authoring_identity_ignores_spoofed_headers() -> None:
    """Headers naming another tenant, and no verified user on the request.

    The old handler returned that org id; the fixed one has nothing to trust
    and must refuse.
    """
    from fastapi import HTTPException

    from app.services.workflows.tool_authoring.api import _user_context

    spoofed = SimpleNamespace(
        headers={"X-Org-Id": "victim-org", "X-User-Id": "victim-user"},
        state=SimpleNamespace(),
    )

    with pytest.raises(HTTPException) as exc:
        _user_context(spoofed)
    assert exc.value.status_code == 401


def test_tool_authoring_identity_comes_from_the_verified_user() -> None:
    from app.services.workflows.tool_authoring.api import _user_context

    request = SimpleNamespace(
        headers={"X-Org-Id": "victim-org"},
        state=SimpleNamespace(user={"orgId": "real-org", "userId": "real-user"}),
    )

    assert _user_context(request) == ("real-org", "real-user")
