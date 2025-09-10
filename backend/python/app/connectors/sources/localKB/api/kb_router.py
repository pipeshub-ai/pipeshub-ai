# router.py fixes - Add return type annotations
from typing import Any, Dict, List, Optional, Union

from dependency_injector.wiring import Provide, inject
from fastapi import APIRouter, Depends, HTTPException, Query, status

from app.connectors.sources.localKB.api.models import (
    CreateFolderRequest,
    CreateFolderResponse,
    CreateKnowledgeBaseRequest,
    CreateKnowledgeBaseResponse,
    CreatePermissionRequest,
    CreatePermissionsResponse,
    CreateRecordsRequest,
    CreateRecordsResponse,
    DeleteRecordRequest,
    DeleteRecordResponse,
    ErrorResponse,
    FolderContentsResponse,
    KnowledgeBaseResponse,
    ListKnowledgeBaseResponse,
    ListPermissionsResponse,
    ListRecordsResponse,
    RemovePermissionRequest,
    RemovePermissionResponse,
    SuccessResponse,
    UpdateFolderRequest,
    UpdateKnowledgeBaseRequest,
    UpdatePermissionRequest,
    UpdatePermissionResponse,
    UpdateRecordRequest,
    UpdateRecordResponse,
    UploadRecordsInFolderRequest,
    UploadRecordsinFolderResponse,
    UploadRecordsInKBRequest,
    UploadRecordsinKBResponse,
)
from app.connectors.sources.localKB.handlers.kb_service import KnowledgeBaseService
from app.containers.connector import ConnectorAppContainer

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
)
@inject
async def create_knowledge_base(
    req: CreateKnowledgeBaseRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> CreateKnowledgeBaseResponse:
    try:
        result = await kb_service.create_knowledge_base(
            user_id=req.userId,
            org_id=req.orgId,
            name=req.name,
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
    "/user/{user_id}/org/{org_id}",
    response_model=ListKnowledgeBaseResponse,
    responses={
        500: {"model": ErrorResponse},
    }
)
@inject
async def list_user_knowledge_bases(
    user_id: str,
    org_id: str,
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(20, ge=1, le=100, description="Items per page"),
    search: Optional[str] = Query(None, description="Search by KB name"),
    permissions: Optional[str] = Query(None, description="Filter by permission roles"),
    sort_by: str = Query("name", description="Sort field"),
    sort_order: str = Query("asc", description="Sort order (asc/desc)"),
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[ListKnowledgeBaseResponse, Dict[str, Any]]:
    try:
        # Parse comma-separated string into list
        parsed_permissions = [item.strip() for item in permissions.split(',') if item.strip()] if permissions else None

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
    "/{kb_id}/user/{user_id}",
    response_model=KnowledgeBaseResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}
)
@inject
async def get_knowledge_base(
    kb_id: str,
    user_id: str,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[KnowledgeBaseResponse, Dict[str, Any]]:
    try :
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
    "/{kb_id}/user/{user_id}",
    response_model=SuccessResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse}
    }
)
@inject
async def update_knowledge_base(
    kb_id: str,
    user_id: str,
    req: UpdateKnowledgeBaseRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> SuccessResponse:
    try:
        result = await kb_service.update_knowledge_base(kb_id=kb_id, user_id=user_id, updates=req.dict(exclude_unset=True))
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
    "/{kb_id}/user/{user_id}",
    response_model=SuccessResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse}
    }
)
@inject
async def delete_knowledge_base(
    kb_id: str,
    user_id: str,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> SuccessResponse:
    try:
        result = await kb_service.delete_knowledge_base(kb_id=kb_id, user_id=user_id)
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
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
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}
)
@inject
async def create_records_in_kb(
    kb_id: str,
    req: CreateRecordsRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[CreateRecordsResponse, Dict[str, Any]]:
    try:
        result = await kb_service.create_records_in_kb(
            kb_id=kb_id,
            user_id=req.userId,
            records=req.records,
            file_records=req.fileRecords,
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
)
@inject
async def upload_records_to_kb(
    kb_id: str,
    req: UploadRecordsInKBRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[UploadRecordsinKBResponse, Dict[str, Any]]:
    """
    ⭐ UNIFIED: Upload records to KB root using consolidated service
    """
    try:
        # Input validation
        if not req.files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No files provided for upload"
            )

        if not req.userId or not req.orgId:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="userId and orgId are required"
            )

        # Convert Pydantic models to dicts for service layer
        files_data = []
        for file_data in req.files:
            files_data.append({
                "record": file_data.record,
                "fileRecord": file_data.fileRecord,
                "filePath": file_data.filePath,
                "lastModified": file_data.lastModified,
            })

        # Call unified service (KB root upload)
        result = await kb_service.upload_records_to_kb(
            kb_id=kb_id,
            user_id=req.userId,
            org_id=req.orgId,
            files=files_data,
        )

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR)) if result else HTTP_INTERNAL_SERVER_ERROR
            error_reason = result.get("reason", "Unknown error") if result else "Service error"
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

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
)
@inject
async def upload_records_to_folder(
    kb_id: str,
    folder_id: str,
    req: UploadRecordsInFolderRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[UploadRecordsinFolderResponse, Dict[str, Any]]:
    """
    ⭐ UNIFIED: Upload records to specific folder using consolidated service
    """
    try:
        # Input validation
        if not req.files:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No files provided for upload"
            )

        if not req.userId or not req.orgId:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="userId and orgId are required"
            )

        # Convert Pydantic models to dicts for service layer
        files_data = []
        for file_data in req.files:
            files_data.append({
                "record": file_data.record,
                "fileRecord": file_data.fileRecord,
                "filePath": file_data.filePath,
                "lastModified": file_data.lastModified,
            })

        # Call unified service (folder upload)
        result = await kb_service.upload_records_to_folder(
            kb_id=kb_id,
            folder_id=folder_id,
            user_id=req.userId,
            org_id=req.orgId,
            files=files_data,
        )

        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR)) if result else HTTP_INTERNAL_SERVER_ERROR
            error_reason = result.get("reason", "Unknown error") if result else "Service error"
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

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
    responses={403: {"model": ErrorResponse}, 400: {"model": ErrorResponse}}
)
@inject
async def create_folder_in_kb_root(
    kb_id: str,
    req: CreateFolderRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[CreateFolderResponse, Dict[str, Any]]:
    """Create folder in KB root"""
    try:
        result = await kb_service.create_folder_in_kb(
            kb_id=kb_id,
            name=req.name,
            user_id=req.userId,
            org_id=req.orgId
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
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}
)
@inject
async def create_nested_folder(
    kb_id: str,
    parent_folder_id: str,
    req: CreateFolderRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[CreateFolderResponse, Dict[str, Any]]:
    """Create folder inside another folder"""
    try:
        result = await kb_service.create_nested_folder(
            kb_id=kb_id,
            parent_folder_id=parent_folder_id,
            name=req.name,
            user_id=req.userId,
            org_id=req.orgId
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
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}
)
@inject
async def get_folder_contents(
    kb_id: str,
    folder_id: str,
    user_id: str,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[FolderContentsResponse, Dict[str, Any]]:
    try:

        result = await kb_service.get_folder_contents(kb_id=kb_id, folder_id=folder_id, user_id=user_id)
        print(f"Result of get folder content : {result}")
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
    "/{kb_id}/folder/{folder_id}/user/{user_id}",
    response_model=SuccessResponse,
    responses={403: {"model": ErrorResponse}}
)
@inject
async def update_folder(
    kb_id: str,
    folder_id: str,
    user_id: str,
    req: UpdateFolderRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> SuccessResponse:
    try:
        result = await kb_service.updateFolder(folder_id=folder_id, kb_id=kb_id, user_id=user_id, name=req.name)
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
    "/{kb_id}/folder/{folder_id}/user/{user_id}",
    response_model=SuccessResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}
)
@inject
async def delete_folder(
    kb_id: str,
    folder_id: str,
    user_id: str,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> SuccessResponse:
    try:
        result = await kb_service.delete_folder(kb_id=kb_id, folder_id=folder_id, user_id=user_id)
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )
        return SuccessResponse(message="Folder deleted successfully")

    except HTTPException as he:
        raise he

    except Exception as e:
        raise HTTPException(
            status_code=500,
            detail=f"Unexpected error: {str(e)}"
        )

@kb_router.get(
    "/{kb_id}/records/user/{user_id}/org/{org_id}",
    response_model=ListRecordsResponse
)
@inject
async def list_kb_records(
    kb_id: str,
    user_id: str,
    org_id: str,
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
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Dict[str, Any]:
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
    "/{kb_id}/children"
)
@inject
async def get_kb_children(
    kb_id: str,
    user_id: str,
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
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Dict[str, Any]:
    """
    Get KB root contents (folders and records) with pagination and filters
    """
    try:
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
)
@inject
async def get_folder_children(
    kb_id: str,
    folder_id: str,
    user_id: str,
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
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Dict[str, Any]:
    """
    Get folder contents (subfolders and records) with pagination and filters
    """
    try:
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
    responses={403: {"model": ErrorResponse}}
)
@inject
async def create_kb_permissions(
    kb_id: str,
    req: CreatePermissionRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[CreatePermissionsResponse, Dict[str, Any]]:
    try:
        result = await kb_service.create_kb_permissions(
            kb_id=kb_id,
            requester_id=req.requesterId,
            user_ids=req.userIds,
            team_ids=req.teamIds,
            role=req.role,
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
    responses={403: {"model": ErrorResponse}}
)
@inject
async def update_kb_permission(
    kb_id: str,
    req: UpdatePermissionRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[UpdatePermissionResponse, Dict[str, Any]]:
    try:

        result = await kb_service.update_kb_permission(
            kb_id=kb_id,
            requester_id=req.requesterId,
            user_ids=req.userIds,
            team_ids=req.teamIds,
            new_role=req.role,
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
    responses={403: {"model": ErrorResponse}}
)
@inject
async def remove_kb_permission(
    kb_id: str,
    req: RemovePermissionRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[RemovePermissionResponse, Dict[str, Any]]:
    """
    Remove permissions for users and teams from a knowledge base
    """
    try:
        result = await kb_service.remove_kb_permission(
            kb_id=kb_id,
            requester_id=req.requesterId,
            user_ids=req.userIds,
            team_ids=req.teamIds,
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
    "/{kb_id}/requester/{requester_id}/permissions",
    response_model=ListPermissionsResponse,
    responses={403: {"model": ErrorResponse}}
)
@inject
async def list_kb_permissions(
    kb_id: str,
    requester_id: str,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[ListPermissionsResponse, Dict[str, Any]]:
    try:
        result = await kb_service.list_kb_permissions(kb_id=kb_id, requester_id=requester_id)
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
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}}
)
@inject
async def create_records_in_folder(
    kb_id: str,
    folder_id: str,
    req: CreateRecordsRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[CreateRecordsResponse, Dict[str, Any]]:
    try:
        result = await kb_service.create_records_in_folder(
            kb_id=kb_id,
            folder_id=folder_id,
            user_id=req.userId,
            records=req.records,
            file_records=req.fileRecords,
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
    }
)
@inject
async def update_record(
    record_id: str,
    req: UpdateRecordRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[UpdateRecordResponse, Dict[str, Any]]:
    try:
        result = await kb_service.update_record(
            user_id=req.userId,
            record_id=record_id,
            updates=req.updates,
            file_metadata=req.fileMetadata,
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
    "/{kb_id}/records",
    response_model=DeleteRecordResponse,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse}
    }
)
@inject
async def delete_records_in_kb(
    kb_id: str,
    req: DeleteRecordRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[DeleteRecordResponse, Dict[str, Any]]:
    try:

        result = await kb_service.delete_records_in_kb(
            kb_id=kb_id,
            record_ids=req.recordIds,
            user_id=req.userId,
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
    }
)
@inject
async def delete_record_in_folder(
    kb_id: str,
    folder_id: str,
    req: DeleteRecordRequest,
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Union[DeleteRecordResponse, Dict[str, Any]]:
    try:

        result = await kb_service.delete_records_in_folder(
            kb_id=kb_id,
            folder_id=folder_id,
            record_ids=req.recordIds,
            user_id=req.userId,
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
    "/records/user/{user_id}/org/{org_id}",
    # response_model=ListAllRecordsResponse
)
@inject
async def list_all_records(
    user_id: str,
    org_id: str,
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
    kb_service: KnowledgeBaseService = Depends(Provide[ConnectorAppContainer.kb_service]),
) -> Dict[str, Any]:

    # Parse comma-separated strings into lists
    parsed_record_types = _parse_comma_separated_str(record_types)
    parsed_origins = _parse_comma_separated_str(origins)
    parsed_connectors = _parse_comma_separated_str(connectors)
    parsed_indexing_status = _parse_comma_separated_str(indexing_status)
    parsed_permissions = _parse_comma_separated_str(permissions)

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
