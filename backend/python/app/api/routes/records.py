from typing import Dict, List, Optional

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from app.containers.query import QueryAppContainer
from app.modules.retrieval.retrieval_arango import ArangoService

router = APIRouter()

class ReindexFailedRequest(BaseModel):
    connector: str  # GOOGLE_DRIVE, GOOGLE_MAIL, KNOWLEDGE_BASE
    origin: str     # CONNECTOR, UPLOAD


async def get_arango_service(request: Request) -> ArangoService:
    container: QueryAppContainer = request.app.container
    arango_service = await container.arango_service()
    return arango_service


def _parse_comma_separated_str(value: Optional[str]) -> Optional[List[str]]:
    """Parses a comma-separated string into a list of strings, filtering out empty items."""
    if not value:
        return None
    return [item.strip() for item in value.split(',') if item.strip()]


@router.get("/records")
@inject
async def get_records(
    request: Request,
    arango_service: ArangoService = Depends(get_arango_service),
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None,
    record_types: Optional[str] = Query(None, description="Comma-separated list of record types"),
    origins: Optional[str] = Query(None, description="Comma-separated list of origins"),
    connectors: Optional[str] = Query(None, description="Comma-separated list of connectors"),
    indexing_status: Optional[str] = Query(None, description="Comma-separated list of indexing statuses"),
    permissions: Optional[str] = Query(None, description="Comma-separated list of permissions"),
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    sort_by: str = "createdAtTimestamp",
    sort_order: str = "desc",
    source: str = "all",
) -> Optional[Dict]:
    """
    List all records the user can access (from all KBs, folders, and direct connector permissions), with filters.
    """
    try:
        container = request.app.container
        logger = container.logger()

        user_id=request.state.user.get("userId")
        org_id=request.state.user.get("orgId")

        logger.info(f"Looking up user by user_id: {user_id}")
        user = await arango_service.get_user_by_user_id(user_id=user_id)

        if not user:
            logger.warning(f"⚠️ User not found for user_id: {user_id}")
            return {
                "success": False,
                "code": 404,
                "reason": f"User not found for user_id: {user_id}"
            }
        user_key = user.get('_key')

        skip = (page - 1) * limit
        sort_order = sort_order.lower() if sort_order.lower() in ["asc", "desc"] else "desc"
        sort_by = sort_by if sort_by in [
            "recordName", "createdAtTimestamp", "updatedAtTimestamp", "recordType", "origin", "indexingStatus"
        ] else "createdAtTimestamp"

        # Parse comma-separated strings into lists
        parsed_record_types = _parse_comma_separated_str(record_types)
        parsed_origins = _parse_comma_separated_str(origins)
        parsed_connectors = _parse_comma_separated_str(connectors)
        parsed_indexing_status = _parse_comma_separated_str(indexing_status)
        parsed_permissions = _parse_comma_separated_str(permissions)

        records, total_count, available_filters = await arango_service.get_records(
            user_id=user_key,
            org_id=org_id,
            skip=skip,
            limit=limit,
            search=search,
            record_types=parsed_record_types,
            origins=parsed_origins,
            connectors=parsed_connectors,
            indexing_status=parsed_indexing_status,
            permissions=parsed_permissions,
            date_from=date_from,
            date_to=date_to,
            sort_by=sort_by,
            sort_order=sort_order,
            source=source,
        )

        total_pages = (total_count + limit - 1) // limit

        applied_filters = {
            k: v for k, v in {
                "search": search,
                "recordTypes": parsed_record_types,
                "origins": parsed_origins,
                "connectors": parsed_connectors,
                "indexingStatus": parsed_indexing_status,
                "source": source if source != "all" else None,
                "dateRange": {"from": date_from, "to": date_to} if date_from or date_to else None,
            }.items() if v
        }

        return {
            "records": records,
            "pagination": {
                "page": page,
                "limit": limit,
                "totalCount": total_count,
                "totalPages": total_pages,
            },
            "filters": {
                "applied": applied_filters,
                "available": available_filters,
            }
        }
    except Exception as e:
        logger.error(f"❌ Failed to list all records: {str(e)}")
        return {
            "records": [],
            "pagination": {"page": page, "limit": limit, "totalCount": 0, "totalPages": 0},
            "filters": {"applied": {}, "available": {}},
            "error": str(e),
        }

@router.get("/records/{record_id}")
@inject
async def get_record_by_id(
    record_id: str,
    request: Request,
    arango_service: ArangoService = Depends(get_arango_service),
) -> Optional[Dict]:
    """
    Check if the current user has access to a specific record
    """
    try:
        container = request.app.container
        logger = container.logger()
        has_access = await arango_service.check_record_access_with_details(
            user_id=request.state.user.get("userId"),
            org_id=request.state.user.get("orgId"),
            record_id=record_id,
        )
        logger.info(f"🚀 has_access: {has_access}")
        if has_access:
            return has_access
        else:
            raise HTTPException(
                status_code=404, detail="You do not have access to this record"
            )
    except Exception as e:
        logger.error(f"Error checking record access: {str(e)}")
        raise HTTPException(status_code=500, detail="Failed to check record access")

@router.delete("/records/{record_id}")
@inject
async def delete_record(
    record_id: str,
    request: Request,
    arango_service: ArangoService = Depends(get_arango_service),
) -> Dict:
    """
    Delete a specific record with permission validation
    """
    try:
        container = request.app.container
        logger = container.logger()

        logger.info(f"🗑️ Attempting to delete record {record_id}")

        result = await arango_service.delete_record(
            record_id=record_id,
            user_id=request.state.user.get("userId")
        )

        if result["success"]:
            logger.info(f"✅ Successfully deleted record {record_id}")
            return {
                "success": True,
                "message": f"Record {record_id} deleted successfully",
                "recordId": record_id,
                "connector": result.get("connector"),
                "timestamp": result.get("timestamp")
            }
        else:
            logger.error(f"❌ Failed to delete record {record_id}: {result.get('reason')}")
            raise HTTPException(
                status_code=result.get("code", 500),
                detail=result.get("reason", "Failed to delete record")
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error deleting record {record_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while deleting record: {str(e)}"
        )

@router.post("/records/{record_id}/reindex")
@inject
async def reindex_single_record(
    record_id: str,
    request: Request,
    arango_service: ArangoService = Depends(get_arango_service),
) -> Dict:
    """
    Reindex a single record with permission validation
    """
    try:
        container = request.app.container
        logger = container.logger()

        logger.info(f"🔄 Attempting to reindex record {record_id}")

        result = await arango_service.reindex_single_record(
            record_id=record_id,
            user_id=request.state.user.get("userId"),
            org_id=request.state.user.get("orgId")
        )

        if result["success"]:
            logger.info(f"✅ Successfully initiated reindex for record {record_id}")
            return {
                "success": True,
                "message": f"Reindex initiated for record {record_id}",
                "recordId": result.get("recordId"),
                "recordName": result.get("recordName"),
                "connector": result.get("connector"),
                "eventPublished": result.get("eventPublished"),
                "userRole": result.get("userRole")
            }
        else:
            logger.error(f"❌ Failed to reindex record {record_id}: {result.get('reason')}")
            raise HTTPException(
                status_code=result.get("code", 500),
                detail=result.get("reason", "Failed to reindex record")
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error reindexing record {record_id}: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while reindexing record: {str(e)}"
        )

@router.post("/records/reindex-failed")
@inject
async def reindex_failed_records(
    request_body: ReindexFailedRequest,
    request: Request,
    arango_service: ArangoService = Depends(get_arango_service),
) -> Dict:
    """
    Reindex all failed records for a specific connector with permission validation
    """
    try:
        container = request.app.container
        logger = container.logger()

        logger.info(f"🔄 Attempting to reindex failed {request_body.connector} records")

        result = await arango_service.reindex_failed_connector_records(
            user_id=request.state.user.get("userId"),
            org_id=request.state.user.get("orgId"),
            connector=request_body.connector,
            origin=request_body.origin
        )

        if result["success"]:
            logger.info(f"✅ Successfully initiated reindex for failed {request_body.connector} records")
            return {
                "success": True,
                "message": result.get("message"),
                "connector": result.get("connector"),
                "origin": result.get("origin"),
                "userPermissionLevel": result.get("user_permission_level"),
                "eventPublished": result.get("event_published")
            }
        else:
            logger.error(f"❌ Failed to reindex failed records: {result.get('reason')}")
            raise HTTPException(
                status_code=result.get("code", 500),
                detail=result.get("reason", "Failed to reindex failed records")
            )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"❌ Error reindexing failed records: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail=f"Internal server error while reindexing failed records: {str(e)}"
        )
