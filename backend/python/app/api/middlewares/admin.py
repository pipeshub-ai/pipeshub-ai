"""Shared "is this caller an org admin" dependency.

Extracted from ``app.api.routes.toolsets`` (``_check_user_is_admin`` /
``_get_user_context``), which had the only admin-check implementation in the
codebase before this — see CLAUDE.md DRY guidance. ``toolsets.py`` now
delegates to :func:`check_user_is_admin` instead of keeping its own copy.

Distinct from :func:`app.api.middlewares.auth.require_scopes`: that only
enforces anything for OAuth-token callers (a no-op for a regular session
JWT — see its docstring). :func:`require_admin` checks the real user/session
against the Node.js CM backend's admin-role record, so it closes the gap for
normal logged-in (non-OAuth) admins-only routes such as the KG governance API.
"""

from typing import Any

import httpx
from fastapi import HTTPException, Request, status

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import DefaultEndpoints


def get_user_context(request: Request) -> dict[str, Any]:
    """Extract and validate user context from request."""
    user = getattr(request.state, "user", {})
    user_id = user.get("userId") or request.headers.get("X-User-Id")
    org_id = user.get("orgId") or request.headers.get("X-Organization-Id")

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Please provide valid user credentials.",
        )

    return {"user_id": user_id, "org_id": org_id}


async def check_user_is_admin(
    user_id: str,
    request: Request,
    config_service: ConfigurationService,
) -> bool:
    """Check if the current user is an admin by calling the Node.js CM backend.

    Calls GET /api/v1/users/{userId}/adminCheck with the user's auth token.
    Returns True if 200 (admin), False if 400/403 (not admin) or on error —
    fail-closed to non-admin so a CM-backend outage never widens access.
    """
    try:
        try:
            endpoints = await config_service.get_config(
                "/services/endpoints", use_cache=False
            )
            nodejs_url = (
                endpoints.get("nodejs", {}).get("endpoint")
                if isinstance(endpoints, dict)
                else None
            ) or DefaultEndpoints.NODEJS_ENDPOINT.value
        except Exception:
            nodejs_url = DefaultEndpoints.NODEJS_ENDPOINT.value

        auth_headers: dict[str, str] = {}
        for header_name in ("authorization", "x-organization-id", "cookie"):
            val = request.headers.get(header_name)
            if val:
                auth_headers[header_name] = val

        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(
                f"{nodejs_url}/api/v1/users/{user_id}/adminCheck",
                headers=auth_headers,
            )
            return resp.status_code == status.HTTP_200_OK

    except Exception:
        return False


async def require_admin(request: Request) -> None:
    """FastAPI dependency: 401 if unauthenticated, 403 if not an org admin.

    Pulls ``config_service`` straight off ``request.app.container`` (mirrors
    the pattern already used in ``app.api.routes.entity_sync._get_services``)
    rather than requiring per-service ``dependency_injector`` wiring, so this
    one dependency works unchanged on every FastAPI app in this repo.
    """
    user_context = get_user_context(request)
    config_service = request.app.container.config_service()
    is_admin = await check_user_is_admin(user_context["user_id"], request, config_service)
    if not is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator privileges required.",
        )


__all__ = ["check_user_is_admin", "get_user_context", "require_admin"]
