# router.py fixes - Add return type annotations
import asyncio
import logging
from typing import Any, Dict, List, Optional, Union

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, FastAPI, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.requests import ClientDisconnect

from app.api.connection_limit import (
    hide_record_parents_outside_connection,
    hide_record_parents_unchecked,
    hide_source_paths,
    ids_outside_connection,
    ids_outside_connection_on_restore,
    keep_path_inside_connection,
    refuse_outside_connection,
)
from app.api.middlewares.auth import connection_limit, require_scopes
from app.config.constants.arangodb import FOLDER_MIME_TYPES, CollectionNames, Connectors
from app.config.constants.service import OAuthScopes
from app.connectors.core.base.data_processor.data_source_entities_processor import (
    DataSourceEntitiesProcessor,
)
from app.connectors.services.kafka_service import KafkaService
from app.connectors.sources.localKB.api.models import (
    CreateFolderResponse,
    CreateKnowledgeBaseResponse,
    CreatePermissionsResponse,
    DeleteRecordResponse,
    ErrorResponse,
    KnowledgeBaseResponse,
    ListKnowledgeBaseResponse,
    ListPermissionsResponse,
    ListRecordsResponse,
    ListTrashResponse,
    RemovePermissionResponse,
    RestoreRecordResponse,
    RestoreRecordsResponse,
    SuccessResponse,
    UpdateKnowledgeBaseRequest,
    UpdatePermissionResponse,
    UpdateRecordResponse,
    UploadRecordsinFolderResponse,
    UploadRecordsinKBResponse,
)
from app.connectors.sources.localKB.handlers.kb_service import (
    ConnectionCheck,
    KnowledgeBaseService,
)
from app.connectors.sources.localKB.push import admission
from app.connectors.sources.localKB.push import api as push_api
from app.connectors.sources.localKB.push.contract import PushRefusal
from app.connectors.sources.localKB.push.gate import GatedRoute, KbPushFlagMiddleware
from app.connectors.sources.localKB.push.service import KbPushService
from app.containers.connector import ConnectorAppContainer
from app.core import org_context
from app.modules.record_metadata.schemas import MetadataSchemaRegistry
from app.services.storage_documents import StorageDocuments
from app.utils.time_conversion import get_epoch_timestamp_in_ms
from app.utils.user_messages import action_failed

# Handlers bind their own ``logger`` from the container inside the request, so the
# module logger needs a name that a half-run handler cannot shadow.
_log = logging.getLogger(__name__)


async def get_kb_service(request: Request) -> KnowledgeBaseService:
    """
    Resolve KnowledgeBaseService with graph_provider and kafka_service.
    KB service is created per-request since it depends on async graph_provider.
    """
    container: ConnectorAppContainer = request.app.container
    logger = container.logger()
    graph_provider = request.app.state.graph_provider
    kafka_service = container.kafka_service()
    config_service = container.config_service()
    request_org_id = request.state.user.get("orgId")
    # Deferred: EventService pulls in every connector module via ConnectorFactory.
    from app.edition_services import EventService

    event_service = EventService(
        logger=logger, graph_provider=graph_provider, app_container=container
    )

    # Each KB is its own connector instance, created with the KB's org like any
    # other connector, so its processor stamps that org on every write.
    async def processor_for_kb(kb_id: str) -> DataSourceEntitiesProcessor:
        # The KB doc's orgId is the ownership record. Checked before building an
        # instance because lazy init falls back to the caller's org when it is
        # missing, which would bind the KB (and its org's shared processor) wrongly.
        kb_doc = await graph_provider.get_document(kb_id, CollectionNames.APPS.value)
        kb_org_id = (kb_doc or {}).get("orgId")
        if not kb_org_id or kb_org_id != request_org_id:
            raise PermissionError(
                f"Knowledge base {kb_id} is not owned by org {request_org_id} (owner: {kb_org_id!r})"
            )
        connector = await event_service.get_or_init_connector(
            Connectors.KNOWLEDGE_BASE.value.lower(), kb_id
        )
        if connector is None:
            raise LookupError(f"Knowledge base {kb_id} has no active connector instance")
        processor = connector.data_entities_processor
        if processor.org_id != request_org_id:
            raise PermissionError(
                f"Knowledge base {kb_id} belongs to org {processor.org_id}, not {request_org_id}"
            )
        return processor

    return KnowledgeBaseService(
        logger=logger,
        graph_provider=graph_provider,
        kafka_service=kafka_service,
        processor_for_kb=processor_for_kb,
        config_service=config_service,
    )


async def get_kafka_service(request: Request) -> KafkaService:
    """Get KafkaService from container."""
    container: ConnectorAppContainer = request.app.container
    return container.kafka_service()


# Constants for HTTP status codes
HTTP_MIN_STATUS = 100
HTTP_MAX_STATUS = 600
HTTP_INTERNAL_SERVER_ERROR = 500

kb_router = APIRouter(
    prefix="/api/v1/kb",
    tags=["Knowledge Base"],
    dependencies=[Depends(keep_path_inside_connection)],
)


def _listing_or_error(result: dict[str, Any]) -> dict[str, Any]:
    """Turn the service's error payload into an error status.

    The error payload is not a valid listing, so returned as-is it fails the
    response model and the caller gets a bare 500 without the message.
    """
    if result.get("success") is False:
        raise HTTPException(status_code=result.get("code") or 500, detail=result.get("reason"))
    if result.get("error"):
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=result["error"])
    return result

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

        raw_is_hidden = body.get("isHidden", False)
        if not isinstance(raw_is_hidden, bool):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="'isHidden' must be a boolean",
            )
        is_hidden = raw_is_hidden

        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")
        result = await kb_service.create_knowledge_base(
            user_id=user_id,
            org_id=org_id,
            name=name.strip(),
            is_hidden=is_hidden,
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
        _log.error("create_knowledge_base failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=action_failed("create this knowledge base")
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

        if isinstance(result, dict) and result.get("knowledgeBases"):
            # shortcut: hiding after paging can leave a page short and the totals
            # high; filter in the listing query if that matters to a client.
            outside = await ids_outside_connection(
                request, request.app.state.graph_provider, org_id, user_id,
                [kb.get("id") for kb in result["knowledgeBases"] if isinstance(kb, dict)],
            )
            if outside:
                result["knowledgeBases"] = [
                    kb for kb in result["knowledgeBases"] if not (isinstance(kb, dict) and kb.get("id") in outside)
                ]

        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        _log.error("list_user_knowledge_bases failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("load your knowledge bases")
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
        _log.error("get_knowledge_base failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("open this knowledge base")
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
            raw_body = await request.json()
        except Exception:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Invalid request body"
            )
        try:
            body = UpdateKnowledgeBaseRequest(**raw_body)
        except ValidationError as ve:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=ve.errors()
            )
        updates = body.model_dump(exclude_none=True)
        if not updates:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No fields to update"
            )
        result = await kb_service.update_knowledge_base(kb_id=kb_id, user_id=user_id, updates=updates)
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
        _log.error("update_knowledge_base failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("update this knowledge base")
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
        org_id = request.state.user.get("orgId")
        result = await kb_service.delete_knowledge_base(kb_id=kb_id, user_id=user_id, org_id=org_id)
        if not result or result.get("success") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR))
            error_reason = result.get("reason", "Unknown error")
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason
            )

        kb_connector = getattr(container, "connectors_map", {}).pop(kb_id, None)
        if kb_connector is not None:
            try:
                await kb_connector.cleanup()
            except Exception as e:
                logger.error(f"Error cleaning up connector instance for deleted KB {kb_id}: {e}")

        # Publish batch deletion events
        event_data = result.get("eventData")
        if event_data and event_data.get("payloads"):
            try:
                timestamp = get_epoch_timestamp_in_ms()
                results = await kafka_service.publish_events(
                    event_data["topic"],
                    [
                        {
                            "eventType": event_data["eventType"],
                            "timestamp": timestamp,
                            "payload": payload,
                        }
                        for payload in event_data["payloads"]
                    ],
                )
                logger.info(f"✅ Published {sum(results)}/{len(event_data['payloads'])} deletion events")
            except Exception as e:
                logger.error(f"❌ Failed to publish deletion events: {str(e)}")

        return SuccessResponse(message="Knowledge base deleted successfully")

    except HTTPException as he:
        raise he

    except Exception as e:
        _log.error("delete_knowledge_base failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("delete this knowledge base")
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
                results = await kafka_service.publish_events(
                    event_data["topic"],
                    [
                        {
                            "eventType": event_data["eventType"],
                            "timestamp": timestamp,
                            "payload": payload,
                        }
                        for payload in event_data["payloads"]
                    ],
                )
                logger.info(f"✅ Published {sum(results)}/{len(event_data['payloads'])} upload events")
            except Exception as e:
                logger.error(f"❌ Failed to publish upload events: {str(e)}")

        # Return unified response
        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        _log.error("upload_records_to_kb failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=action_failed("upload these files")
        )

@kb_router.post(
    "/{kb_id}/folder/{folder_id}/upload",
    response_model=UploadRecordsinFolderResponse,
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
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
                results = await kafka_service.publish_events(
                    event_data["topic"],
                    [
                        {
                            "eventType": event_data["eventType"],
                            "timestamp": timestamp,
                            "payload": payload,
                        }
                        for payload in event_data["payloads"]
                    ],
                )
                logger.info(f"✅ Published {sum(results)}/{len(event_data['payloads'])} upload events")
            except Exception as e:
                logger.error(f"❌ Failed to publish upload events: {str(e)}")

        # Return unified response
        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        _log.error("upload_records_to_folder failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=action_failed("upload these files")
        )

@kb_router.get(
    "/{kb_id}/folder/{folder_id}/validate",
    response_model=SuccessResponse,
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
        500: {"model": ErrorResponse},
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_UPLOAD))],
)
@inject
async def validate_folder_for_upload(
    kb_id: str,
    folder_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> Union[SuccessResponse, Dict[str, Any]]:
    """Validate that a folder exists and belongs to the given KB before upload."""
    try:
        user_id = request.state.user.get("userId")
        org_id = request.state.user.get("orgId")

        if not user_id or not org_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="userId and orgId are required",
            )

        result = await kb_service.validate_folder_for_upload(
            kb_id=kb_id,
            folder_id=folder_id,
            user_id=user_id,
            org_id=org_id,
        )

        if not result or result.get("valid") is False:
            error_code = int(result.get("code", HTTP_INTERNAL_SERVER_ERROR)) if result else HTTP_INTERNAL_SERVER_ERROR
            error_reason = result.get("reason", "Unknown error") if result else "Service error"
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=error_reason,
            )

        return SuccessResponse(message="Folder is valid for upload")

    except HTTPException as he:
        raise he
    except Exception as e:
        _log.error("validate_folder_for_upload failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=action_failed("check this folder before uploading"),
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
        if (
            "parentId" in body
            or "parent_id" in body
            or "folderId" in body
            or any(
                k in request.query_params
                for k in ("parentId", "parent_id", "folderId")
            )
        ):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    "Cannot nest folders via the root endpoint. Use the "
                    "/{kb_id}/folder/{parent_id}/subfolder route instead."
                )
            )

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
        _log.error("create_folder_in_kb_root failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("create this folder"))

@kb_router.post(
    "/{kb_id}/folder/{parent_folder_id}/subfolder",
    response_model=CreateFolderResponse,
    responses={403: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
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
        for param_name in ("parentId", "parent_id", "folderId"):
            for val in request.query_params.getlist(param_name):
                if val != parent_folder_id:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail=f"Conflicting {param_name} query parameter specified.",
                    )
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
        _log.error("create_nested_folder failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("create this folder"))

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
        _log.error("update_folder failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("rename this folder")
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
                results = await kafka_service.publish_events(
                    event_data["topic"],
                    [
                        {
                            "eventType": event_data["eventType"],
                            "timestamp": timestamp,
                            "payload": payload,
                        }
                        for payload in event_data["payloads"]
                    ],
                )
                logger.info(f"✅ Published {sum(results)}/{len(event_data['payloads'])} deletion events for folder {folder_id}")
            except Exception as e:
                logger.error(f"❌ Failed to publish folder deletion events: {str(e)}")

        return SuccessResponse(message="Folder deleted successfully")

    except HTTPException as he:
        raise he

    except Exception as e:
        _log.error("delete_folder failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("delete this folder")
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
    return _listing_or_error(await kb_service.list_kb_records(
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
    ))

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

        hide_source_paths(request, result, kb_id)
        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        _log.error("get_kb_children failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("open this knowledge base")
        )


async def _keep_update_inside_connection(request: Request, response: dict[str, Any], kb_id: str) -> None:
    """A connected app limited to part of a collection is told nothing of the
    rest of it: not the collection, its other folders, the updated record's
    parents outside the limit, or the file's source path."""
    if connection_limit(request.state.user) is None:
        return
    user = request.state.user
    graph_provider = request.app.state.graph_provider
    kb = response.get("kb") if isinstance(response.get("kb"), dict) else {}
    folders = [f for f in kb.get("folders") or [] if isinstance(f, dict)]
    outside = await ids_outside_connection(
        request, graph_provider, user.get("orgId"), user.get("userId"),
        [kb_id, *(f.get("id") for f in folders if f.get("id"))],
    )
    if kb_id in outside:
        response["kb"] = {}
    elif kb:
        kb["folders"] = [f for f in folders if f.get("id") not in outside]
    await hide_record_parents_outside_connection(request, graph_provider, user.get("orgId"), user.get("userId"), response)
    hide_source_paths(request, response, kb_id)


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

        hide_source_paths(request, result, kb_id)
        return result

    except HTTPException as he:
        raise he
    except Exception as e:
        _log.error("get_folder_children failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("open this folder")
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
        _log.error("create_kb_permissions failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("share this knowledge base")
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
        _log.error("update_kb_permission failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("update this person's access")
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
        _log.error("remove_kb_permission failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("remove this person's access")
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
        _log.error("list_kb_permissions failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("load who this knowledge base is shared with")
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
            except Exception as e:
                logger.error(f"❌ Failed to publish update event: {str(e)}")

        # Enrich response with required fields for UpdateRecordResponse
        graph_provider = request.app.state.graph_provider
        try:
            # Get KB context for the record. The write already succeeded, so a missing
            # context is an enrichment miss — degrade gracefully rather than turning a
            # completed update into a misleading 404.
            kb_context = await graph_provider._get_kb_context_for_record(record_id)
            kb_id = kb_context.get("kb_id") if kb_context else None
            if not kb_id:
                logger.warning(f"⚠️ KB context unavailable for updated record {record_id}; returning un-enriched response")
                fallback = {
                    **result,
                    "fileUpdated": body.get("fileMetadata") is not None,
                    "timestamp": get_epoch_timestamp_in_ms(),
                    "location": "kb_root",
                    "kb": {},
                    "userPermission": "NONE",
                }
                hide_record_parents_unchecked(request, fallback)
                return fallback

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
            parent_info = await graph_provider.get_knowledge_hub_parent_node(
                record_id,
                folder_mime_types=FOLDER_MIME_TYPES,
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
                "timestamp": get_epoch_timestamp_in_ms(),
                "location": location,
                "kb": kb_info,
                "userPermission": user_permission,
            }
            await _keep_update_inside_connection(request, enriched_result, kb_id)

            return enriched_result

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"❌ Failed to enrich update record response: {str(e)}")
            # Return with required fields even if enrichment fails. For a
            # connected app with a limit, the enrichment's own limit check may be
            # what failed: nothing above the record is named then.
            fallback = {
                **result,
                "fileUpdated": body.get("fileMetadata") is not None,
                "timestamp": get_epoch_timestamp_in_ms(),
                "location": "kb_root",
                "kb": {},
                "userPermission": "NONE",
            }
            hide_record_parents_unchecked(request, fallback)
            return fallback

    except HTTPException as he:
        raise he

    except Exception as e:
        _log.error("update_record failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("update this file")
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
        _log.error("delete_records_in_kb failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("delete these files")
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
        _log.error("delete_record_in_folder failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("delete this file")
        )


def _outside_connection(request: Request) -> ConnectionCheck | None:
    """For a connected app with a limit: which of a restore's ids it may not
    reach. A restore brings back a whole delete batch, not only the ids it names."""
    if connection_limit(request.state.user) is None:
        return None
    user = request.state.user
    return lambda kb_id, live_ids, trashed: ids_outside_connection_on_restore(
        request, request.app.state.graph_provider, user.get("orgId"), user.get("userId"), kb_id, live_ids, trashed,
    )


def _raise_refusal(result: dict[str, Any] | None) -> None:
    error_code = int((result or {}).get("code", HTTP_INTERNAL_SERVER_ERROR))
    raise HTTPException(
        status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
        detail=(result or {}).get("reason") or action_failed("restore this item"),
    )


@kb_router.post(
    "/record/{record_id}/restore",
    response_model=RestoreRecordResponse,
    response_model_exclude_none=True,
    responses={
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
        409: {"model": ErrorResponse},
    },
    dependencies=[Depends(require_scopes(OAuthScopes.KB_DELETE))],
)
@inject
async def restore_record(
    record_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> RestoreRecordResponse:
    try:
        result = await kb_service.restore_record(
            record_id=record_id,
            user_id=request.state.user.get("userId"),
            org_id=request.state.user.get("orgId"),
            outside=_outside_connection(request),
        )
        if not result or result.get("success") is False:
            _raise_refusal(result)
        return RestoreRecordResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        _log.error("restore_record failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("restore this item")) from e


@kb_router.post(
    "/records/restore",
    response_model=RestoreRecordsResponse,
    responses={400: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.KB_DELETE))],
)
@inject
async def restore_records(
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> RestoreRecordsResponse:
    try:
        try:
            body = await request.json()
        except Exception:
            body = None
        record_ids = body.get("recordIds") if isinstance(body, dict) else None
        if not isinstance(record_ids, list) or not all(isinstance(i, str) and i for i in record_ids):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Send the items to restore as recordIds, a list of record ids.",
            )
        result = await kb_service.restore_records(
            record_ids=record_ids,
            user_id=request.state.user.get("userId"),
            org_id=request.state.user.get("orgId"),
            outside=_outside_connection(request),
        )
        if result.get("code") == status.HTTP_400_BAD_REQUEST:
            _raise_refusal(result)
        return RestoreRecordsResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        _log.error("restore_records failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("restore these items")) from e


@kb_router.get(
    "/{kb_id}/trash",
    response_model=ListTrashResponse,
    responses={
        400: {"model": ErrorResponse},
        403: {"model": ErrorResponse},
        404: {"model": ErrorResponse},
    },
    # The same scope as restore: the list is what this caller may restore.
    dependencies=[Depends(require_scopes(OAuthScopes.KB_DELETE))],
)
@inject
async def list_trash(
    kb_id: str,
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(25, ge=1, le=100),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
) -> ListTrashResponse:
    try:
        result = await kb_service.list_trash(
            kb_id=kb_id,
            user_id=request.state.user.get("userId"),
            org_id=request.state.user.get("orgId"),
            page=page,
            limit=limit,
        )
        if not result or result.get("success") is False:
            error_code = int((result or {}).get("code", HTTP_INTERNAL_SERVER_ERROR))
            raise HTTPException(
                status_code=error_code if HTTP_MIN_STATUS <= error_code < HTTP_MAX_STATUS else HTTP_INTERNAL_SERVER_ERROR,
                detail=(result or {}).get("reason") or action_failed("load the recently deleted items"),
            )
        return ListTrashResponse(**result)
    except HTTPException:
        raise
    except Exception as e:
        _log.error("list_trash failed: %s", e, exc_info=True)
        raise HTTPException(status_code=500, detail=action_failed("load the recently deleted items")) from e


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
        409: {"model": ErrorResponse},
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
        await refuse_outside_connection(
            request, request.app.state.graph_provider, request.state.user.get("orgId"), user_id, new_parent_id or kb_id,
        )

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
        _log.error("move_record failed: %s", e, exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=action_failed("move this file")
        )


# ---- push: application/blocks records and the KB's property schema --------------------------------

# Its own router, included after ``kb_router``: its routes match only while ENABLE_KB_PUSH is on
# (``push/gate.py``; the app needs ``KbPushFlagMiddleware``), so the flag is checked before the
# connection check and any other dependency, and with it off the app answers as if they were absent.
kb_push_router = APIRouter(
    prefix="/api/v1/kb",
    tags=["Knowledge Base"],
    dependencies=[Depends(keep_path_inside_connection)],
    route_class=GatedRoute,
)


def install_push_routes(app: FastAPI) -> None:
    """Add the push routes and the middleware that reads ENABLE_KB_PUSH before routing, ahead of
    authentication and any graph call. Without the flag the routes do not match."""
    app.include_router(kb_push_router)
    app.add_middleware(KbPushFlagMiddleware)


def _refusal_response(r: PushRefusal) -> JSONResponse:
    return JSONResponse(status_code=r.status, content=push_api.error_body(r), headers=push_api.error_headers(r))


def _bound_org_id() -> str | None:
    """The org the request is bound to, or None when it is not bound (the push routes then refuse)."""
    try:
        return org_context.current_org_id()
    except (org_context.MissingOrgContext, org_context.SystemContextNotAllowed):
        return None


def _require_bound_org() -> str:
    org_id = _bound_org_id()
    if not org_id:
        raise PushRefusal(400, "ENVELOPE_INVALID", "userId and orgId are required.")
    return org_id


def _push_service(request: Request, kb_service: KnowledgeBaseService, kafka_service: KafkaService) -> KbPushService:
    container: ConnectorAppContainer = request.app.container
    runtime = push_api.runtime_for(request.app)
    config_service = container.config_service()

    async def publish(event: dict[str, Any]) -> bool:
        return await kafka_service.publish_event("record-events", event) is not False

    return KbPushService(
        graph_provider=request.app.state.graph_provider,
        kb_service=kb_service,
        processor_for_kb=kb_service.processor_for_kb,
        schema_registry=MetadataSchemaRegistry(config_service),
        storage=StorageDocuments(config_service, container.logger()),
        worker=runtime.worker,
        spool=runtime.spool,
        publish_event=publish,
    )


@kb_push_router.post(
    "/{kb_id}/records",
    dependencies=[Depends(require_scopes(OAuthScopes.KB_UPLOAD))],
)
async def push_record(
    kb_id: str,
    request: Request,
    folderId: str | None = Query(None),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> JSONResponse:
    """Create or update one ``application/blocks`` record by ``externalRecordId``.

    The endpoint declares no body parameter, so nothing is read before the caller's role is
    known; the body is then read by hand, bounded and timed (``push/admission.py``).
    """
    user = request.state.user
    user_id, org_id = user.get("userId"), _bound_org_id()
    try:
        if not user_id or not org_id:
            raise PushRefusal(400, "ENVELOPE_INVALID", "userId and orgId are required.")
        service = _push_service(request, kb_service, kafka_service)
        user_key = await service.authorize(kb_id, user_id)
        admission = push_api.runtime_for(request.app).admission
        admission.claim(user_key)  # one push at a time per caller per process: held until the push ends
        task: asyncio.Future[Any] | None = None
        try:
            body = await admission.read_body(request.headers, request.stream())
            # Once the body is in, the push finishes even if the client goes away; the caller's
            # slot is then released when the push itself ends.
            task = org_context.spawn_in_request(
                service.push(kb_id=kb_id, user_id=user_id, org_id=org_id, body=body, folder_id=folderId)
            )
            task.add_done_callback(lambda _t: admission.release(user_key))
            result = await asyncio.shield(task)
        finally:
            if task is None:
                admission.release(user_key)
        return JSONResponse(status_code=result.http_status, content=result.to_json())
    except PushRefusal as r:
        return _refusal_response(r)
    except ClientDisconnect:
        # The caller went away mid-body: nothing was read to the end and nothing is written.
        _log.info("push_record: the client disconnected before the body ended")
        return _refusal_response(PushRefusal(400, "CLIENT_DISCONNECTED", "The client closed the connection before the body ended."))
    except Exception as e:
        _log.error("push_record failed: %s", e, exc_info=True)
        return _refusal_response(PushRefusal(500, "INTERNAL_ERROR", action_failed("push this record")))


@kb_push_router.put("/{kb_id}/schema", dependencies=[Depends(require_scopes(OAuthScopes.KB_WRITE))])
async def put_kb_schema(
    kb_id: str,
    request: Request,
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> JSONResponse:
    user = request.state.user
    try:
        org_id = _require_bound_org()
        service = _push_service(request, kb_service, kafka_service)
        await service.authorize(kb_id, user.get("userId"), ["OWNER"])
        try:
            body = push_api.SchemaIn.model_validate_json(
                await admission.read_bounded(
                    request.headers, request.stream(), admission.MAX_SCHEMA_BYTES, admission.SCHEMA_READ_TIMEOUT_S,
                )
            )
        except ValidationError as e:
            first = e.errors()[0]
            raise PushRefusal(400, "ENVELOPE_INVALID", "The schema body is invalid.", ".".join(str(x) for x in first["loc"])) from None
        out = await push_api.put_schema(service.schemas, org_id, kb_id, body)
        return JSONResponse(status_code=200, content=out)
    except PushRefusal as r:
        return _refusal_response(r)
    except Exception as e:
        _log.error("put_kb_schema failed: %s", e, exc_info=True)
        return _refusal_response(PushRefusal(500, "INTERNAL_ERROR", action_failed("save this schema")))


@kb_push_router.get("/{kb_id}/schema", dependencies=[Depends(require_scopes(OAuthScopes.KB_READ))])
async def get_kb_schema(
    kb_id: str,
    request: Request,
    version: int | None = Query(None, ge=1),
    kb_service: KnowledgeBaseService = Depends(get_kb_service),
    kafka_service: KafkaService = Depends(get_kafka_service),
) -> JSONResponse:
    user = request.state.user
    try:
        org_id = _require_bound_org()
        service = _push_service(request, kb_service, kafka_service)
        await service.authorize(kb_id, user.get("userId"), None)
        out = await push_api.get_schema(service.schemas, org_id, kb_id, version)
        return JSONResponse(status_code=200, content=out)
    except PushRefusal as r:
        return _refusal_response(r)
    except Exception as e:
        _log.error("get_kb_schema failed: %s", e, exc_info=True)
        return _refusal_response(PushRefusal(500, "INTERNAL_ERROR", action_failed("read this schema")))
