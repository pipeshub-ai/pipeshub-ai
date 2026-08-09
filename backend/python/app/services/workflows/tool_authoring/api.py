"""FastAPI routes for tool authoring (Phase 7)."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request

from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes
from app.services.workflows.tool_authoring.openapi_drafter import OpenAPIDrafter
from app.services.workflows.tool_authoring.workflow_drafter import WorkflowDrafter

logger = logging.getLogger(__name__)
router = APIRouter(
    prefix="/api/v1/workflows/tools",
    tags=["workflow-tool-authoring"],
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))],
)


def _user_context(request: Request) -> tuple[str, str]:
    """(org_id, user_id) from the JWT the auth middleware verified.

    These handlers previously read `X-Org-Id`/`X-User-Id` from the request,
    which any authenticated caller can set to another tenant's id. Mirrors
    `api/routes/workflows._get_user_context`.
    """
    user = getattr(request.state, "user", None) or {}
    org_id = user.get("orgId")
    user_id = user.get("userId")
    if not org_id or not user_id:
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please provide valid credentials.",
        )
    return org_id, user_id


@router.post("/draft/openapi")
async def draft_from_openapi(request: Request) -> dict[str, Any]:
    """Draft ToolDefinitions from an OpenAPI spec."""
    org_id, user_id = _user_context(request)
    body = await request.json()
    spec = body.get("spec", {})

    if not spec:
        raise HTTPException(status_code=400, detail="'spec' field is required")

    drafter = OpenAPIDrafter()
    results = await drafter.draft(spec, org_id=org_id, user_id=user_id)
    return {
        "tools": [r.model_dump() for r in results],
        "count": len(results),
    }


@router.post("/draft/workflow")
async def draft_workflow_as_tool(request: Request) -> dict[str, Any]:
    """Draft a tool definition from an existing workflow."""
    org_id, user_id = _user_context(request)
    body = await request.json()

    drafter = WorkflowDrafter()
    results = await drafter.draft(body, org_id=org_id, user_id=user_id)
    return {
        "tools": [r.model_dump() for r in results],
        "count": len(results),
    }
