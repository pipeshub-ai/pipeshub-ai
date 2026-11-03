"""OpenAI-compatible model catalog for models configured in this organization."""

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.config.constants.service import config_node_constants
from app.services.ai_models.openai_catalog import (
    find_openai_model,
    openai_model_list,
    openai_model_not_found,
)

router = APIRouter(tags=["models"])


async def _stored_models(request: Request) -> dict:
    config_service = request.app.container.config_service()
    stored = await config_service.get_config(
        config_node_constants.AI_MODELS.value,
        use_cache=True,
    )
    return stored if isinstance(stored, dict) else {}


@router.get("/models")
async def list_models(request: Request) -> dict:
    """List configured models in the OpenAI ``GET /v1/models`` shape."""
    return openai_model_list(await _stored_models(request))


@router.get("/models/{model_id:path}", response_model=None)
async def retrieve_model(request: Request, model_id: str) -> dict | JSONResponse:
    """Return one configured model, or the OpenAI not-found error body."""
    found = find_openai_model(await _stored_models(request), model_id)
    if found is None:
        return JSONResponse(status_code=404, content=openai_model_not_found(model_id))
    return found
