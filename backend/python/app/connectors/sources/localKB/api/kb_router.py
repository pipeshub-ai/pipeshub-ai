# router.py fixes - Add return type annotations
from typing import Any, Dict, List, Optional, Union

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status

from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes
from app.connectors.services.kafka_service import KafkaService
from app.connectors.sources.localKB.api.models import (
    CreateFolderResponse,
    CreateKnowledgeBaseResponse,
    CreatePermissionsResponse,
    CreateRecordsResponse,
    DeleteRecordResponse,
    ErrorResponse,
    FolderContentsResponse,
    KnowledgeBaseResponse,
    ListKnowledgeBaseResponse,
    ListPermissionsResponse,
    ListRecordsResponse,
    RemovePermissionResponse,
    SuccessResponse,
    UpdatePermissionResponse,
    UpdateRecordResponse,
    UploadRecordsinFolderResponse,
    UploadRecordsinKBResponse,
)
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.containers.connector import ConnectorAppContainer
from app.utils.time_conversion import get_epoch_timestamp_in_ms


async def get_kb_service(request: Request) -> KnowledgeBaseService:
    """
    Resolve KnowledgeBaseService with graph_provider and kafka_service.
    KB service is created per-request since it depends on async graph_provider.
    """
    container: ConnectorAppContainer = request.app.container
    logger = container.logger()
    graph_provider = request.app.state.graph_provider
    kafka_service = container.kafka_service()
    return KnowledgeBaseService(logger=logger, graph_provider=graph_provider, kafka_service=kafka_service)


async def get_kafka_service(request: Request) -> KafkaService:
    """Get KafkaService from container."""
    container: ConnectorAppContainer = request.app.container
    return container.kafka_service()


# Constants for HTTP status codes
HTTP_MIN_STATUS = 100
HTTP_MAX_STATUS = 600
HTTP_INTERNAL_SERVER_ERROR = 500

kb_router = APIRouter(prefix="/api/v1/kb", tags=["Knowledge Base"])

def _parse_comma_separated_str(value: Optional[str]) -> Optional[List[str]]:
    """Parses a comma-separated string into a list of strings, filtering out empty items."""
    if not value:
        return None
    return [item.strip() for item in value.split(',') if item.strip()]

@kb_router.post(
    "/",
    response_model=CreateKnowledgeBaseResponse,
    responses={
        400: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def create_knowledge_base(
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> CreateKnowledgeBaseResponse:
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )

        # Validate required field - accept both "name" and "kbName" for compatibility
        name = body.get("name") or body.get("kbName")
        if not name or not isinstance(name, str) or not name.strip():
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Knowledge base name is required (use 'name' or 'kbName' field)"
            )

        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        result = await kb_service.create_knowledge_base(
            user_id=user_id,
            org_id=org_id,
            name=name.strip(),
        )

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        return CreateKnowledgeBaseResponse(**result)

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error: {str(e)}"
        )


@kb_router.get(
    "/",
    response_model=ListKnowledgeBaseResponse,
    responses={
        500: {"model": ErrorResponse},
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
@inject
async def list_user_knowledge_bases(
    request: Request,
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by KB name"),
    permissions: Optional[str] = Query(None, description="Filter by permission roles"),
    sort_by: str = Query("name", description="Sort field"),
    sort_order: str = Query("asc", description="Sort order (asc/desc)"),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[ListKnowledgeBaseResponse, Dict[str, Any]]:
    try:
        # Parse comma-separated string into list
        parsed_permissions = [item.strip() for item in permissions.split(',') if item.strip()] if permissions else None

        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        result = await kb_service.list_user_knowledge_bases(
            user_id=user_id,
            org_id=org_id,
            page=page,
            limit=limit,
            search=search,
            permissions=parsed_permissions,
            sort_by=sort_by,
            sort_order=sort_order
        )
        if isinstance(result, dict) and result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@kb_router.get(
    "/{kb_id}",
    response_model=KnowledgeBaseResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
@inject
async def get_knowledge_base(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[KnowledgeBaseResponse, Dict[str, Any]]:
    try :
        user_id = request.state.user.get("userId")
        result = await kb_service.get_knowledge_base(kb_id=kb_id, user_id=user_id)

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return result

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@kb_router.put(
    "/{kb_id}",
    response_model=SuccessResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse}
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def update_knowledge_base(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> SuccessResponse:
    try:
        user_id = request.state.user.get("userId")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        result = await kb_service.update_knowledge_base(kb_id=kb_id, user_id=user_id, updates=body)
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return SuccessResponse(message="Knowledge base updated successfully")

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@kb_router.delete(
    "/{kb_id}",
    response_model=SuccessResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse}
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_DELETE))],
)
@inject
async def delete_knowledge_base(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> SuccessResponse:
    try:
        container = request.app.container
        logger = container.logger()
        user_id = request.state.user.get("userId")
        result = await kb_service.delete_knowledge_base(kb_id=kb_id, user_id=user_id)
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        # Publish batch deletion events
        event_data = result.get("eventData")
        if event_data and event_data.get("payloads"):
            try:
                timestamp = get_epoch_timestamp_in_ms()
                successful_events = 0
                for payload in event_data["payloads"]:
                    try:
                        event = {
                            "eventType": event_data["eventType"],
                            "timestamp": timestamp,
                            "payload": payload
                        }
                        await kafka_service.publish_event(event_data["topic"], event)
                        successful_events += 1
                    except Exception as e:
                        logger.error(f"❌ Failed to publish deletion event: {str(e)}")
                logger.info(f"✅ Published {successful_events}/{len(event_data['payloads'])} deletion events")
            except Exception as e:
                logger.error(f"❌ Failed to publish deletion events: {str(e)}")

        return SuccessResponse(message="Knowledge base deleted successfully")

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@kb_router.post(
    "/{kb_id}/records",
    response_model=CreateRecordsResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def create_records_in_kb(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[CreateRecordsResponse, Dict[str, Any]]:
    try:
        user_id = request.state.user.get("userId")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        result = await kb_service.create_records_in_kb(
            kb_id=kb_id,
            user_id=user_id,
            records=body.get("records"),
            file_records=body.get("fileRecords"),
        )
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return result

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@kb_router.post(
    "/{kb_id}/upload",
    response_model=UploadRecordsinKBResponse,
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_UPLOAD))],
)
@inject
async def upload_records_to_kb(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> Union[UploadRecordsinKBResponse, Dict[str, Any]]:
    """
    ⭐ UNIFIED: Upload records to KB root using consolidated service
    """
    try:
        # Input validation
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )

        if not body.get("files"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No files provided for upload"
            )

        if not user_id or not org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="userId and orgId are required"
            )

        # Convert Pydantic models to dicts for service layer
        files_data = []
        for file_data in body.get("files"):
            files_data.append({
                "record": file_data.get("record"),
                "fileRecord": file_data.get("fileRecord"),
                "filePath": file_data.get("filePath"),
                "lastModified": file_data.get("lastModified"),
            })

        # Call unified service (KB root upload)
        result = await kb_service.upload_records_to_kb(
            kb_id=kb_id,
            user_id=user_id,
            org_id=org_id,
            files=files_data,
        )

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR)) if result else HTTP_INTERNAL_SERVER_ERROR
            error_reason = result.get("reason", "Unknown error") if result else "Service error"
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        # Publish batch events
        container = request.app.container
        logger = container.logger()
        event_data = result.get("eventData")
        if event_data and event_data.get("payloads"):
            try:
                timestamp = get_epoch_timestamp_in_ms()
                successful_events = 0
                record_ids_queued = []
                for payload in event_data["payloads"]:
                    try:
                        event = {
                            "eventType": event_data["eventType"],
                            "timestamp": timestamp,
                            "payload": payload
                        }
                        await kafka_service.publish_event(event_data["topic"], event)
                        successful_events += 1
                        record_id = payload.get("recordId")
                        if record_id:
                            record_ids_queued.append(record_id)
                    except Exception as e:
                        logger.error(f"❌ Failed to publish event for record: {str(e)}")
                if record_ids_queued:
                    await kb_service.set_records_indexing_status_queued(record_ids_queued)
                logger.info(f"✅ Published {successful_events}/{len(event_data['payloads'])} upload events")
            except Exception as e:
                logger.error(f"❌ Failed to publish upload events: {str(e)}")

        # Return unified response
        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error during KB upload: {str(e)}"
        )

@kb_router.post(
    "/{kb_id}/folder/{folder_id}/upload",
    response_model=UploadRecordsinFolderResponse,
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_UPLOAD))],
)
@inject
async def upload_records_to_folder(
    kb_id: str,
    folder_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> Union[UploadRecordsinFolderResponse, Dict[str, Any]]:
    """
    ⭐ UNIFIED: Upload records to specific folder using consolidated service
    """
    try:
        # Input validation
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        if not body.get("files"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No files provided for upload"
            )

        if not user_id or not org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="userId and orgId are required"
            )

        # Convert Pydantic models to dicts for service layer
        files_data = []
        for file_data in body.get("files"):
            files_data.append({
                "record": file_data.get("record"),
                "fileRecord": file_data.get("fileRecord"),
                "filePath": file_data.get("filePath"),
                "lastModified": file_data.get("lastModified"),
            })

        # Call unified service (folder upload)
        result = await kb_service.upload_records_to_folder(
            kb_id=kb_id,
            folder_id=folder_id,
            user_id=user_id,
            org_id=org_id,
            files=files_data,
        )

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR)) if result else HTTP_INTERNAL_SERVER_ERROR
            error_reason = result.get("reason", "Unknown error") if result else "Service error"
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        # Publish batch events
        container = request.app.container
        logger = container.logger()
        event_data = result.get("eventData")
        if event_data and event_data.get("payloads"):
            try:
                timestamp = get_epoch_timestamp_in_ms()
                successful_events = 0
                record_ids_queued = []
                for payload in event_data["payloads"]:
                    try:
                        event = {
                            "eventType": event_data["eventType"],
                            "timestamp": timestamp,
                            "payload": payload
                        }
                        await kafka_service.publish_event(event_data["topic"], event)
                        successful_events += 1
                        record_id = payload.get("recordId")
                        if record_id:
                            record_ids_queued.append(record_id)
                    except Exception as e:
                        logger.error(f"❌ Failed to publish event for record: {str(e)}")
                if record_ids_queued:
                    await kb_service.set_records_indexing_status_queued(record_ids_queued)
                logger.info(f"✅ Published {successful_events}/{len(event_data['payloads'])} upload events")
            except Exception as e:
                logger.error(f"❌ Failed to publish upload events: {str(e)}")

        # Return unified response
        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Unexpected error during folder upload: {str(e)}"
        )

@kb_router.post(
    "/{kb_id}/folder",
    response_model=CreateFolderResponse,
    responses={403: {"model": ErrorResponse}, 400: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def create_folder_in_kb_root(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[CreateFolderResponse, Dict[str, Any]]:
    """Create folder in KB root"""
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        # Accept both "name" and "folderName" for compatibility
        folder_name = body.get("name") or body.get("folderName")
        if not folder_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Folder name is required (use 'name' or 'folderName' field)"
            )
        result = await kb_service.create_folder_in_kb(
            kb_id=kb_id,
            name=folder_name,
            user_id=user_id,
            org_id=org_id
        )

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@kb_router.post(
    "/{kb_id}/folder/{parent_folder_id}/subfolder",
    response_model=CreateFolderResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def create_nested_folder(
    kb_id: str,
    parent_folder_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[CreateFolderResponse, Dict[str, Any]]:
    """Create folder inside another folder"""
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        # Accept both "name" and "folderName" for compatibility
        folder_name = body.get("name") or body.get("folderName")
        if not folder_name:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Folder name is required (use 'name' or 'folderName' field)"
            )
        result = await kb_service.create_nested_folder(
            kb_id=kb_id,
            parent_folder_id=parent_folder_id,
            name=folder_name,
            user_id=user_id,
            org_id=org_id
        )

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Unexpected error: {str(e)}")

@kb_router.get(
    "/{kb_id}/folder/{folder_id}/user/{user_id}",
    response_model=FolderContentsResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
@inject
async def get_folder_contents(
    kb_id: str,
    folder_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[FolderContentsResponse, Dict[str, Any]]:
    try:
        user_id = request.state.user.get("userId")
        result = await kb_service.get_folder_contents(kb_id=kb_id, folder_id=folder_id, user_id=user_id)
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return result

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@kb_router.put(
    "/{kb_id}/folder/{folder_id}",
    response_model=SuccessResponse,
    responses={403: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def update_folder(
    kb_id: str,
    folder_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> SuccessResponse:
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        user_id = request.state.user.get("userId")
        result = await kb_service.updateFolder(folder_id=folder_id, kb_id=kb_id, user_id=user_id, name=body.get("name"))
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return SuccessResponse(message="Folder updated successfully")

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@kb_router.delete(
    "/{kb_id}/folder/{folder_id}",
    response_model=SuccessResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_DELETE))],
)
@inject
async def delete_folder(
    kb_id: str,
    folder_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> SuccessResponse:
    try:
        container = request.app.container
        logger = container.logger()
        user_id = request.state.user.get("userId")
        result = await kb_service.delete_folder(kb_id=kb_id, folder_id=folder_id, user_id=user_id)
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        # Publish batch deletion events
        event_data = result.get("eventData")
        logger.info(f"🗑️ Event data: {event_data}")
        if event_data and event_data.get("payloads"):
            try:
                timestamp = get_epoch_timestamp_in_ms()
                successful_events = 0
                for payload in event_data["payloads"]:
                    try:
                        event = {
                            "eventType": event_data["eventType"],
                            "timestamp": timestamp,
                            "payload": payload
                        }
                        await kafka_service.publish_event(event_data["topic"], event)
                        successful_events += 1
                    except Exception as e:
                        logger.error(f"❌ Failed to publish deletion event: {str(e)}")
                logger.info(f"✅ Published {successful_events}/{len(event_data['payloads'])} deletion events for folder {folder_id}")
            except Exception as e:
                logger.error(f"❌ Failed to publish folder deletion events: {str(e)}")

        return SuccessResponse(message="Folder deleted successfully")

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@kb_router.get(
    "/{kb_id}/records",
    response_model=ListRecordsResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
@inject
async def list_kb_records(
    kb_id: str,
    request: Request,
    page: int = 1,
    limit: int = 20,
    search: Optional[str] = None,
    record_types: Optional[List[str]] = None,
    origins: Optional[List[str]] = None,
    connectors: Optional[List[str]] = None,
    indexing_status: Optional[List[str]] = None,
    date_from: Optional[int] = None,
    date_to: Optional[int] = None,
    sort_by: str = "createdAtTimestamp",
    sort_order: str = "desc",
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Dict[str, Any]:
    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")
    return await kb_service.list_kb_records(
        kb_id=kb_id,
        user_id=user_id,
        org_id=org_id,
        page=page,
        limit=limit,
        search=search,
        record_types=record_types,
        origins=origins,
        connectors=connectors,
        indexing_status=indexing_status,
        date_from=date_from,
        date_to=date_to,
        sort_by=sort_by,
        sort_order=sort_order,
    )

@kb_router.get(
    "/{kb_id}/children",
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
@inject
async def get_kb_children(
    kb_id: str,
    request: Request,
    page: int = 1,
    limit: int = 20,
    level: int = 1,
    search: Optional[str] = None,
    record_types: Optional[List[str]] = None,
    origins: Optional[List[str]] = None,
    connectors: Optional[List[str]] = None,
    indexing_status: Optional[List[str]] = None,
    sort_by: str = "name",
    sort_order: str = "asc",
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Dict[str, Any]:
    """
    Get KB root contents (folders and records) with pagination and filters
    """
    try:
        user_id = request.state.user.get("userId")
        result = await kb_service.get_kb_children(
            kb_id=kb_id,
            user_id=user_id,
            page=page,
            limit=limit,
            level=level,
            search=search,
            record_types=record_types,
            origins=origins,
            connectors=connectors,
            indexing_status=indexing_status,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR)) if result else HTTP_INTERNAL_SERVER_ERROR
            error_reason = result.get("reason", "Unknown error") if result else "Service error"
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@kb_router.get(
    "/{kb_id}/folder/{folder_id}/children",
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
@inject
async def get_folder_children(
    kb_id: str,
    folder_id: str,
    request: Request,
    page: int = 1,
    limit: int = 20,
    level: int = 1,
    search: Optional[str] = None,
    record_types: Optional[List[str]] = None,
    origins: Optional[List[str]] = None,
    connectors: Optional[List[str]] = None,
    indexing_status: Optional[List[str]] = None,
    sort_by: str = "name",
    sort_order: str = "asc",
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Dict[str, Any]:
    """
    Get folder contents (subfolders and records) with pagination and filters
    """
    try:
        user_id = request.state.user.get("userId")
        result = await kb_service.get_folder_children(
            kb_id=kb_id,
            folder_id=folder_id,
            user_id=user_id,
            page=page,
            limit=limit,
            level=level,
            search=search,
            record_types=record_types,
            origins=origins,
            connectors=connectors,
            indexing_status=indexing_status,
            sort_by=sort_by,
            sort_order=sort_order,
        )

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR)) if result else HTTP_INTERNAL_SERVER_ERROR
            error_reason = result.get("reason", "Unknown error") if result else "Service error"
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@kb_router.post(
    "/{kb_id}/permissions",
    response_model=CreatePermissionsResponse,
    responses={403: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def create_kb_permissions(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[CreatePermissionsResponse, Dict[str, Any]]:
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        user_id = request.state.user.get("userId")
        # Role is required for users, but optional for teams (teams don't have roles)
        role = body.get("role")
        user_ids = body.get("userIds") or []
        team_ids = body.get("teamIds") or []

        # Validate: role is required if users are provided
        if user_ids and not role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role is required when adding users"
            )

        result = await kb_service.create_kb_permissions(
            kb_id=kb_id,
            requester_id=user_id,
            user_ids=user_ids,
            team_ids=team_ids,
            role=role,
        )
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return result

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@kb_router.put(
    "/{kb_id}/permissions",
    response_model=UpdatePermissionResponse,
    responses={403: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def update_kb_permission(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[UpdatePermissionResponse, Dict[str, Any]]:
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        user_id = request.state.user.get("userId")
        # Teams don't have roles, so we can only update user permissions
        user_ids = body.get("userIds") or []
        team_ids = body.get("teamIds") or []
        new_role = body.get("role")

        # Validate: role is required
        if not new_role:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Role is required"
            )

        # Teams don't have roles - reject at router level for faster feedback
        if team_ids:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Teams do not have roles. Only user permissions can be updated."
            )

        result = await kb_service.update_kb_permission(
            kb_id=kb_id,
            requester_id=user_id,
            user_ids=user_ids,
            team_ids=team_ids,
            new_role=new_role,
        )
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return result

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@kb_router.delete(
    "/{kb_id}/permissions",
    response_model=RemovePermissionResponse,
    responses={403: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_DELETE))],
)
@inject
async def remove_kb_permission(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[RemovePermissionResponse, Dict[str, Any]]:
    """
    Remove permissions for users and teams from a knowledge base
    """
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        user_id = request.state.user.get("userId")
        result = await kb_service.remove_kb_permission(
            kb_id=kb_id,
            requester_id=user_id,
            user_ids=body.get("userIds"),
            team_ids=body.get("teamIds"),
        )
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return result

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@kb_router.get(
    "/{kb_id}/permissions",
    response_model=ListPermissionsResponse,
    responses={403: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
@inject
async def list_kb_permissions(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[ListPermissionsResponse, Dict[str, Any]]:
    try:
        user_id = request.state.user.get("userId")
        result = await kb_service.list_kb_permissions(kb_id=kb_id, requester_id=user_id)
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return result

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@kb_router.post(
    "/{kb_id}/folder/{folder_id}/records",
    response_model=CreateRecordsResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def create_records_in_folder(
    kb_id: str,
    folder_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[CreateRecordsResponse, Dict[str, Any]]:
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        user_id = request.state.user.get("userId")
        result = await kb_service.create_records_in_folder(
            kb_id=kb_id,
            folder_id=folder_id,
            user_id=user_id,
            records=body.get("records"),
            file_records=body.get("fileRecords"),
        )
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return result

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@kb_router.put(
    "/record/{record_id}",
    response_model=UpdateRecordResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse}
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def update_record(
    record_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> Union[UpdateRecordResponse, Dict[str, Any]]:
    try:
        container = request.app.container
        logger = container.logger()
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        user_id = request.state.user.get("userId")
        result = await kb_service.update_record(
            user_id=user_id,
            record_id=record_id,
            updates=body.get("updates"),
            file_metadata=body.get("fileMetadata"),
        )
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        # Publish update event
        event_data = result.get("eventData")
        if event_data and event_data.get("payload"):
            try:
                timestamp = get_epoch_timestamp_in_ms()
                event = {
                    "eventType": event_data["eventType"],
                    "timestamp": timestamp,
                    "payload": event_data["payload"]
                }
                await kafka_service.publish_event(event_data["topic"], event)
                logger.info(f"✅ Published {event_data['eventType']} event for record {record_id}")
                # Set indexing status to QUEUED just after event is published
                await kb_service.set_record_indexing_status_queued(record_id)
            except Exception as e:
                logger.error(f"❌ Failed to publish update event: {str(e)}")

        # Enrich response with required fields for UpdateRecordResponse
        graph_provider = request.app.state.graph_provider
        try:
            # Get KB context for the record
            kb_context = await graph_provider._get_kb_context_for_record(record_id)
            if not kb_context:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Knowledge base not found for record"
                )

            kb_id = kb_context.get("kb_id")
            if not kb_id:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail="Knowledge base ID not found for record"
                )

            # Get user permission
            user_key = user_id
            user = await graph_provider.get_user_by_user_id(user_id)
            if user:
                user_key = user.get("_key") or user.get("id") or user_id

            user_permission = await graph_provider.get_user_kb_permission(kb_id, user_key)
            if not user_permission:
                user_permission = "NONE"

            # Get KB information
            kb_info = await graph_provider.get_knowledge_base(kb_id, user_key)
            if not kb_info:
                # Fallback to basic KB info from context
                kb_info = {
                    "id": kb_id,
                    "name": kb_context.get("kb_name", ""),
                    "createdAtTimestamp": None,
                    "updatedAtTimestamp": None,
                    "createdBy": None,
                    "userRole": user_permission,
                    "folders": []
                }

            # Determine location (folder or kb_root)
            folder_mime_types = ["application/vnd.google-apps.folder", "application/x-directory"]
            parent_info = await graph_provider.get_knowledge_hub_parent_node(
                record_id,
                folder_mime_types=folder_mime_types
            )

            location = "kb_root"
            if parent_info and parent_info.get("nodeType") == "folder":
                location = "folder"

            # Determine if file was updated
            file_updated = body.get("fileMetadata") is not None

            # Build enriched response with only required fields
            enriched_result = {
                **result,
                "fileUpdated": file_updated,
                "location": location,
                "kb": kb_info,
                "userPermission": user_permission,
            }

            return enriched_result

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Failed to enrich update record response: {str(e)}")
            # Return with required fields even if enrichment fails
            return {
                **result,
                "fileUpdated": body.get("fileMetadata") is not None,
                "location": "kb_root",
                "kb": {},
                "userPermission": "NONE",
            }

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@kb_router.delete(
    "/{kb_id}/records",
    response_model=DeleteRecordResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse}
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_DELETE))],
)
@inject
async def delete_records_in_kb(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[DeleteRecordResponse, Dict[str, Any]]:
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        user_id = request.state.user.get("userId")
        result = await kb_service.delete_records_in_kb(
            kb_id=kb_id,
            record_ids=body.get("recordIds"),
            user_id=user_id,
        )
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        return result

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@kb_router.delete(
    "/{kb_id}/folder/{folder_id}/records",
    response_model=DeleteRecordResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse}
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_DELETE))],
)
@inject
async def delete_record_in_folder(
    kb_id: str,
    folder_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[DeleteRecordResponse, Dict[str, Any]]:
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        user_id = request.state.user.get("userId")
        result = await kb_service.delete_records_in_folder(
            kb_id=kb_id,
            folder_id=folder_id,
            record_ids=body.get("recordIds"),
            user_id=user_id,
        )
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        return result

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )


@kb_router.get(
    "/records",
    # response_model=ListAllRecordsResponse
    dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))],
)
@inject
async def list_all_records(
    request: Request,
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
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Dict[str, Any]:

    # Parse comma-separated strings into lists
    parsed_record_types = _parse_comma_separated_str(record_types)
    parsed_origins = _parse_comma_separated_str(origins)
    parsed_connectors = _parse_comma_separated_str(connectors)
    parsed_indexing_status = _parse_comma_separated_str(indexing_status)
    parsed_permissions = _parse_comma_separated_str(permissions)

    user_id = request.state.user.get("userId")
    org_id = request.state.user.get("orgId")

    return await kb_service.list_all_records(
        user_id=user_id,
        org_id=org_id,
        page=page,
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


# ========================================================================
# Move Record API
# ========================================================================

@kb_router.put(
    "/{kb_id}/record/{record_id}/move",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))],
)
@inject
async def move_record(
    kb_id: str,
    record_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> SuccessResponse:
    """
    Move a record (file or folder) to a different location within the same KB.

    Request body:
        - newParentId: Target folder ID, or null to move to KB root
    """
    try:
        try:
            body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )

        user_id = request.state.user.get("userId")
        new_parent_id = body.get("newParentId")  # None for KB root, folder_id for folder

        result = await kb_service.move_record(
            kb_id=kb_id,
            record_id=record_id,
            new_parent_id=new_parent_id,
            user_id=user_id,
        )

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        return SuccessResponse(message="Record moved successfully")

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )
