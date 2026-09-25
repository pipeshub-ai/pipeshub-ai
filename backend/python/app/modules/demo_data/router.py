"""Each person's switch for the bundled Acme Corp demo data."""

from __future__ import annotations

from dataclasses import replace

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field

from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes
from app.modules.demo_data.access import demo_data_status, write_preference

demo_data_router = APIRouter(prefix="/api/v1/demo-data", tags=["Demo data"])


class DemoDataStatusResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    has_demo: bool = Field(alias="hasDemo")
    include: bool
    chosen: bool | None
    real_data: bool = Field(alias="realData")
    demo_connector_ids: list[str] = Field(alias="demoConnectorIds")


class DemoDataPreference(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # True or False to choose; None to go back to the default.
    include: bool | None


def _caller(request: Request) -> tuple[str, str]:
    user = getattr(request.state, "user", None) or {}
    org_id, user_id = user.get("orgId"), user.get("userId")
    if not org_id or not user_id:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not signed in")
    return org_id, user_id


@demo_data_router.get(
    "/status",
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
    response_model=DemoDataStatusResponse,
    response_model_by_alias=True,
)
async def get_demo_data_status(request: Request) -> dict:
    org_id, user_id = _caller(request)
    container = request.app.container
    result = await demo_data_status(
        request.app.state.graph_provider, container.config_service(), org_id, user_id
    )
    return result.to_dict()


@demo_data_router.put(
    "/preference",
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
    response_model=DemoDataStatusResponse,
    response_model_by_alias=True,
)
async def set_demo_data_preference(request: Request, body: DemoDataPreference) -> dict:
    org_id, user_id = _caller(request)
    container = request.app.container
    config_service = container.config_service()
    # Read first, so nothing that can fail runs after the choice is saved.
    before = await demo_data_status(request.app.state.graph_provider, config_service, org_id, user_id)
    await write_preference(config_service, org_id, user_id, include=body.include)
    return replace(before, chosen=body.include if before.has_demo else None).to_dict()
