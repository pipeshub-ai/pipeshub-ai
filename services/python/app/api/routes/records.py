from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Request
from app.setups.query_setup import AppContainer
from app.modules.retrieval.retrieval_arango import ArangoService
from typing import Optional, Dict, Any, List


router = APIRouter()

async def get_arango_service(request: Request) -> ArangoService:
    container: AppContainer = request.app.container
    arango_service = await container.arango_service()
    return arango_service

@router.get("/records/{record_id}")
@inject
async def check_record_access(
    record_id: str,
    request: Request,
    arango_service: ArangoService = Depends(get_arango_service)
) -> Optional[Dict]:
    """
    Check if the current user has access to a specific record
    """
    try:
        container = request.app.container
        logger = container.logger()
        has_access = await arango_service.check_record_access_with_details(
            user_id=request.state.user.get('userId'),
            org_id=request.state.user.get('orgId'),
            record_id=record_id
        )
        return has_access
    except Exception as e:
        logger.error(f"Error checking record access: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail="Failed to check record access"
        )
