"""FastAPI routes for migrating Agent Builder configs to Workflow SDK code.

Endpoint:
    POST /api/v1/workflows/migration/agent-config
        Body: { "config": { ...agent_builder_json... }, "dry_run": false }
        Returns: { "source": "...", "warnings": [...], "verified": bool }
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes
from app.services.workflows.migration.agent_config_transformer import (
    AgentBuilderToSDKTransformer,
    UntranslatableAgentConfig,
)

router = APIRouter(
    prefix="/api/v1/workflows/migration",
    tags=["workflows-migration"],
    dependencies=[Depends(require_scopes(OAuthScopes.WORKFLOW_WRITE))],
)


class MigrateRequest(BaseModel):
    config: dict
    dry_run: bool = True


class MigrateResponse(BaseModel):
    source: str
    warnings: list[str]
    verified: bool
    verification_errors: list[dict] = []


@router.post("/agent-config", response_model=MigrateResponse)
async def migrate_agent_config(body: MigrateRequest) -> MigrateResponse:
    """Convert an Agent Builder config JSON into Workflow SDK Python source."""
    transformer = AgentBuilderToSDKTransformer()
    try:
        result = transformer.transform(body.config)
    except UntranslatableAgentConfig as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        )

    # Optionally verify the generated source
    verified = False
    verification_errors: list[dict] = []
    try:
        from app.services.workflows.codegen.verifier import verify_workflow_source
        vr = verify_workflow_source(result.source)
        verified = vr.ok
        verification_errors = [e.to_dict() for e in vr.errors]
    except ImportError:
        pass

    return MigrateResponse(
        source=result.source,
        warnings=result.warnings,
        verified=verified,
        verification_errors=verification_errors,
    )
