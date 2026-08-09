"""Workflow scope enforcement for Python routes.

Since requireScopes is a no-op for non-OAuth tokens (confirmed in code),
Python enforces scopes for all workflow routes via this middleware.
The Node proxy forwards X-Scopes (space-delimited) and X-User-Id headers.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import HTTPException, Request

__all__ = ["require_workflow_scope", "WORKFLOW_SCOPES"]

logger = logging.getLogger(__name__)

WORKFLOW_SCOPES = {
    "read": "workflow:read",
    "write": "workflow:write",
    "execute": "workflow:execute",
    "approve": "workflow:approve",
}


def require_workflow_scope(required_scope: str):
    """FastAPI dependency that enforces a workflow scope.

    Reads X-Scopes header (forwarded by the Node proxy). If the header
    contains 'internal' (for service-to-service calls) or the required
    scope, the request proceeds. Otherwise 403.

    This enforcement is in Python specifically because requireScopes in
    Node is a no-op for non-OAuth tokens.
    """
    async def _checker(request: Request) -> None:
        scopes_header = request.headers.get("X-Scopes", "")
        scopes = set(s.strip() for s in scopes_header.split() if s.strip())

        # Internal service-to-service calls bypass scope checks
        if "internal" in scopes or "service:internal" in scopes:
            return

        # OAuth2 tokens: check for required scope
        if required_scope not in scopes:
            logger.warning(
                "Scope enforcement: missing '%s' for user %s. Has: %s",
                required_scope,
                request.headers.get("X-User-Id", "unknown"),
                scopes_header,
            )
            raise HTTPException(
                status_code=403,
                detail=f"Missing required scope: {required_scope}",
            )
    return _checker
