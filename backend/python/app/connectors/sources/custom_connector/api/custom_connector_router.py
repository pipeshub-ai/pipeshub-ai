"""FastAPI router exposing CustomConnectorService as REST endpoints.

Mirrors the patterns from `localKB/api/kb_router.py`:
  - per-request service DI via `get_custom_connector_service`
  - JWT auth applied globally by middleware (all /api/v1/... paths)
  - scope-based authorization via `require_scopes(OAuthScopes.CONNECTOR_*)`
  - service returns dict → translated to HTTPException on `success=False`
"""

from typing import Any, Dict, List, Literal, Optional

from dependency_injector.wiring import inject
from fastapi import APIRouter, Depends, HTTPException, Query, Request

from app.api.middlewares.auth import require_scopes
from app.config.constants.service import OAuthScopes
from app.connectors.sources.custom_connector.api.models import (
    AssignRoleRequest,
    CreateFolderRequest,
    CreateGroupRequest,
    CreateRecordGroupRequest,
    CreateRecordRequest,
    CreateRecordResponse,
    CreateRecordsBatchRequest,
    CreateRecordsBatchResponse,
    CreateRoleRequest,
    DeleteRecordsRequest,
    ErrorResponse,
    FolderResponse,
    GrantPermissionRequest,
    GroupMembersRequest,
    GroupResponse,
    ListGroupsResponse,
    ListRecordGroupsResponse,
    ListRolesResponse,
    MoveRecordRequest,
    NodePermissionsResponse,
    RecordGroupResponse,
    RemovePermissionRequest,
    RoleMembersRequest,
    RoleResponse,
    SuccessResponse,
    UpdateFolderRequest,
    UpdateGroupRequest,
    UpdatePermissionRequest,
    UpdateRecordGroupRequest,
    UpdateRecordRequest,
    UpdateRoleRequest,
    UploadFilesRequest,
)
from app.connectors.sources.custom_connector.handlers.custom_connector_service import (
    CustomConnectorService,
)
from app.config.constants.arangodb import (
    CollectionNames,
    Connectors,
    MimeTypes,
    OriginTypes,
    RecordTypes,
)
from app.containers.connector import ConnectorAppContainer
from app.models.entities import (
    CommentRecord,
    DealRecord,
    FileRecord,
    LinkRecord,
    MailRecord,
    MeetingRecord,
    MessageRecord,
    ProductRecord,
    ProjectRecord,
    PullRequestRecord,
    Record,
    SharePointDocumentLibraryRecord,
    SharePointListItemRecord,
    SharePointListRecord,
    SharePointPageRecord,
    TicketRecord,
    WebpageRecord,
)
from app.models.permission import EntityType, Permission, PermissionType
from app.utils.time_conversion import get_epoch_timestamp_in_ms


async def get_custom_connector_service(request: Request) -> CustomConnectorService:
    """Resolve CustomConnectorService with its dependencies."""
    container: ConnectorAppContainer = request.app.container
    logger = container.logger()
    graph_provider = request.app.state.graph_provider
    kafka_service = container.kafka_service()
    return CustomConnectorService(
        logger=logger, graph_provider=graph_provider, kafka_service=kafka_service
    )


# HTTP status helpers — error codes only (must be 4xx/5xx)
HTTP_MIN = 400
HTTP_MAX = 600
HTTP_500 = 500


def _raise_on_error(result: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Translate a service {success: False, code, reason} dict to HTTPException.

    Returns the result dict unchanged on success.
    """
    if not result or result.get("success") is False:
        try:
            code = int(result.get("code", HTTP_500)) if result else HTTP_500
        except (ValueError, TypeError):
            code = HTTP_500
        code = code if HTTP_MIN <= code < HTTP_MAX else HTTP_500
        reason = (result or {}).get("reason", "Unknown error")
        raise HTTPException(status_code=code, detail=reason)
    return result  # type: ignore[return-value]


# RecordType string -> Record subclass.
RECORD_TYPE_CLASSES = {
    # FILE family
    "FILE": FileRecord,
    # MAIL family
    "MAIL": MailRecord,
    "GROUP_MAIL": MailRecord,
    # WEBPAGE family
    "WEBPAGE": WebpageRecord,
    "DATABASE": WebpageRecord,
    "DATASOURCE": WebpageRecord,
    "CONFLUENCE_PAGE": WebpageRecord,
    "CONFLUENCE_BLOGPOST": WebpageRecord,
    # TICKET family
    "TICKET": TicketRecord,
    "CASE": TicketRecord,
    "TASK": TicketRecord,
    # COMMENT family
    "COMMENT": CommentRecord,
    "INLINE_COMMENT": CommentRecord,
    # Others with their own subclass
    "LINK": LinkRecord,
    "PROJECT": ProjectRecord,
    "PRODUCT": ProductRecord,
    "DEAL": DealRecord,
    "PULL_REQUEST": PullRequestRecord,
    "MEETING": MeetingRecord,
    # MESSAGE has a subclass but no type-specific collection (stored only in `records`)
    "MESSAGE": MessageRecord,
    # SharePoint variants
    "SHAREPOINT_PAGE": SharePointPageRecord,
    "SHAREPOINT_LIST": SharePointListRecord,
    "SHAREPOINT_LIST_ITEM": SharePointListItemRecord,
    "SHAREPOINT_DOCUMENT_LIBRARY": SharePointDocumentLibraryRecord,
}


# Default mime_type per record type. Used when the caller doesn't provide one —
RECORD_TYPE_DEFAULT_MIME = {
    "TICKET": MimeTypes.BLOCKS.value,
    "CASE": MimeTypes.BLOCKS.value,
    "TASK": MimeTypes.BLOCKS.value,
    "MAIL": MimeTypes.BLOCKS.value,
    "GROUP_MAIL": MimeTypes.BLOCKS.value,
    "WEBPAGE": MimeTypes.HTML.value,
    "CONFLUENCE_PAGE": MimeTypes.BLOCKS.value,
    "CONFLUENCE_BLOGPOST": MimeTypes.BLOCKS.value,
    "DATABASE": MimeTypes.BLOCKS.value,
    "DATASOURCE": MimeTypes.BLOCKS.value,
    "COMMENT": MimeTypes.PLAIN_TEXT.value,
    "INLINE_COMMENT": MimeTypes.PLAIN_TEXT.value,
    "MESSAGE": MimeTypes.BLOCKS.value,
    "LINK": MimeTypes.HTML.value,
    "PROJECT": MimeTypes.BLOCKS.value,
    "DEAL": MimeTypes.BLOCKS.value,
    "PRODUCT": MimeTypes.BLOCKS.value,
    "PULL_REQUEST": MimeTypes.BLOCKS.value,
    "MEETING": MimeTypes.BLOCKS.value,
    "SHAREPOINT_PAGE": MimeTypes.HTML.value,
    "SHAREPOINT_LIST": MimeTypes.BLOCKS.value,
    "SHAREPOINT_LIST_ITEM": MimeTypes.BLOCKS.value,
    "SHAREPOINT_DOCUMENT_LIBRARY": MimeTypes.FOLDER.value,
    # FILE intentionally omitted — caller must supply its real mime_type (PDF, DOCX, etc.)
}


async def _fetch_rg_context(
    svc: CustomConnectorService, record_group_id: str
) -> Dict[str, Any]:
    """Pull connector_id / connector_name / org_id off the parent recordGroup.

    These are auto-filled onto every record the caller sends so they don't have
    to (and shouldn't be able to) spoof them.
    """
    rg = await svc.graph_provider.get_document(
        record_group_id, CollectionNames.RECORD_GROUPS.value
    )
    if not rg:
        raise HTTPException(status_code=404, detail=f"RecordGroup {record_group_id} not found")
    return {
        "org_id": rg.get("orgId"),
        "connector_id": rg.get("connectorId"),
        "connector_name": rg.get("connectorName"),
    }


def _build_record(
    record_type: str,
    payload: Dict[str, Any],
    record_group_id: str,
    rg_ctx: Dict[str, Any],
) -> Record:
    """Construct the right Record subclass from a RecordType string + dict payload.

    Auto-fills internal/derived fields from the parent recordGroup context so the
    caller sends only semantic content:
      - id            → generated UUID (if not supplied)
      - org_id        → from RG
      - connector_id  → from RG
      - connector_name→ from RG
      - origin        → CONNECTOR
      - version       → 0
      - created_at    → now
      - updated_at    → now
      - mime_type     → per-type default (BLOCKS / HTML / PLAIN_TEXT / …)
      - external_record_group_id → record_group_id

    Unknown types raise 400 — base `Record` cannot be safely upserted because it
    lacks `to_arango_record()`.
    """
    import uuid
    normalized = record_type.upper()
    cls = RECORD_TYPE_CLASSES.get(normalized)
    if cls is None:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unsupported record_type '{record_type}'. "
                f"Supported: {', '.join(sorted(RECORD_TYPE_CLASSES.keys()))}"
            ),
        )

    now = get_epoch_timestamp_in_ms()
    enriched = dict(payload)  # shallow copy — don't mutate caller's dict

    # Auto-fill derived fields if the caller didn't send them.
    enriched.setdefault("id", str(uuid.uuid4()))
    enriched.setdefault("org_id", rg_ctx["org_id"])
    enriched.setdefault("connector_id", rg_ctx["connector_id"])
    enriched.setdefault("origin", OriginTypes.CONNECTOR.value)
    enriched.setdefault("version", 0)
    enriched.setdefault("created_at", now)
    enriched.setdefault("updated_at", now)
    enriched.setdefault("external_record_group_id", record_group_id)

    # Inherit permissions from immediate parent by default. Caller can override with `false`.
    enriched.setdefault("inherit_permissions", True)
    # is_restricted is purely stored — not tied to edges. Default false; caller controls.
    enriched.setdefault("is_restricted", False)

    # record_type on the Record itself must match the discriminator
    enriched.setdefault("record_type", normalized)

    # connector_name: caller-allowed override, else from RG
    enriched.setdefault("connector_name", rg_ctx["connector_name"])

    # mime_type: caller-provided always wins (e.g. FILE needs real mime);
    # otherwise use per-type default, else UNKNOWN.
    if not enriched.get("mime_type"):
        enriched["mime_type"] = RECORD_TYPE_DEFAULT_MIME.get(
            normalized, MimeTypes.UNKNOWN.value
        )

    try:
        return cls(**enriched)
    except Exception as e:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid record payload for type {record_type}: {e}",
        ) from e


def _build_permissions(payloads: List) -> List[Permission]:
    """Convert PermissionPayload objects to Permission entities."""
    result: List[Permission] = []
    for p in payloads or []:
        try:
            entity_type = EntityType(p.entity_type.upper())
        except ValueError as e:
            raise HTTPException(400, f"Invalid entity_type: {p.entity_type}") from e
        try:
            permission_type = PermissionType(p.permission_type.upper())
        except ValueError as e:
            raise HTTPException(400, f"Invalid permission_type: {p.permission_type}") from e
        result.append(
            Permission(
                entity_type=entity_type,
                type=permission_type,
                email=p.email,
                external_id=p.external_id,
            )
        )
    return result


def _user_ctx(request: Request) -> tuple[str, str]:
    """Extract (user_id, org_id) from JWT state. Raises 401 if missing."""
    user = request.state.user
    user_id = user.get("userId")
    org_id = user.get("orgId")
    if not user_id or not org_id:
        raise HTTPException(status_code=401, detail="User not authenticated")
    return user_id, org_id


custom_connector_router = APIRouter(
    prefix="/api/v1/custom-connector",
    tags=["Custom Connector"],
)


# ============================================================================
# Record Groups
# ============================================================================

@custom_connector_router.post(
    "/recordGroups",
    response_model=RecordGroupResponse,
    responses={400: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def create_record_group(
    request: Request,
    body: CreateRecordGroupRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> RecordGroupResponse:
    user_id, org_id = _user_ctx(request)
    result = await svc.create_record_group(
        user_id=user_id,
        org_id=org_id,
        name=body.name,
        group_type=body.group_type,
        connector_id=body.connector_id,
        external_group_id=body.external_group_id,
        parent_external_group_id=body.parent_external_group_id,
        is_restricted=body.is_restricted,
        inherit_permissions=body.inherit_permissions,
    )
    return RecordGroupResponse(**_raise_on_error(result))


@custom_connector_router.get(
    "/recordGroups",
    response_model=ListRecordGroupsResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def list_record_groups(
    request: Request,
    group_type: Optional[str] = Query(
        None,
        description="Optional RecordGroup type filter. Omit to list all types — useful when combined with connector_id to fetch every RG under one app.",
    ),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None),
    permissions: Optional[str] = Query(None, description="Comma-separated permission roles to filter"),
    sort_by: str = Query("name"),
    sort_order: str = Query("asc"),
    connector_id: Optional[str] = Query(
        None, description="Restrict results to record groups under a specific app instance"
    ),
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> ListRecordGroupsResponse:
    user_id, org_id = _user_ctx(request)
    perms_list = [p.strip() for p in permissions.split(",")] if permissions else None
    result = await svc.list_user_record_groups(
        user_id=user_id,
        org_id=org_id,
        group_type=group_type,
        page=page,
        limit=limit,
        search=search,
        permissions=perms_list,
        sort_by=sort_by,
        sort_order=sort_order,
        connector_id=connector_id,
    )
    return ListRecordGroupsResponse(**_raise_on_error(result))


@custom_connector_router.get(
    "/recordGroups/{record_group_id}",
    response_model=RecordGroupResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def get_record_group(
    request: Request,
    record_group_id: str,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> RecordGroupResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.get_record_group(
        record_group_id=record_group_id, user_id=user_id
    )
    return RecordGroupResponse(**_raise_on_error(result))


@custom_connector_router.put(
    "/recordGroups/{record_group_id}",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def update_record_group(
    request: Request,
    record_group_id: str,
    body: UpdateRecordGroupRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.update_record_group(
        record_group_id=record_group_id, user_id=user_id, updates=body.updates
    )
    _raise_on_error(result)
    return SuccessResponse(success=True, message="Record group updated")


@custom_connector_router.delete(
    "/recordGroups/{record_group_id}",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))],
)
@inject
async def delete_record_group(
    request: Request,
    record_group_id: str,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.delete_record_group(record_group_id=record_group_id, user_id=user_id)
    _raise_on_error(result)
    return SuccessResponse(success=True, message="Record group and contents deleted")


# ============================================================================
# Folders
# ============================================================================

@custom_connector_router.post(
    "/recordGroups/{record_group_id}/folders",
    response_model=FolderResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def create_folder(
    request: Request,
    record_group_id: str,
    body: CreateFolderRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> FolderResponse:
    user_id, org_id = _user_ctx(request)
    result = await svc.create_folder(
        instance_id=record_group_id,
        name=body.name,
        user_id=user_id,
        org_id=org_id,
        is_restricted=body.is_restricted,
        inherit_permissions=body.inherit_permissions,
        parent_id=body.parent_id,
    )
    return FolderResponse(**_raise_on_error(result))


@custom_connector_router.get(
    "/recordGroups/{record_group_id}/folders/{folder_id}/contents",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def get_folder_contents(
    request: Request,
    record_group_id: str,
    folder_id: str,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, _ = _user_ctx(request)
    # Note: pagination is not yet plumbed through the provider's `get_folder_contents`
    # query — it returns all children of the folder.
    result = await svc.get_folder_contents(
        instance_id=record_group_id, folder_id=folder_id, user_id=user_id
    )
    return _raise_on_error(result)


@custom_connector_router.put(
    "/recordGroups/{record_group_id}/folders/{folder_id}",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def update_folder(
    request: Request,
    record_group_id: str,
    folder_id: str,
    body: UpdateFolderRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    new_name = body.updates.get("name") if isinstance(body.updates, dict) else None
    if not new_name:
        raise HTTPException(
            status_code=400, detail="updates.name is required (only 'name' is supported today)"
        )
    result = await svc.update_folder(
        instance_id=record_group_id, folder_id=folder_id, user_id=user_id, name=new_name
    )
    _raise_on_error(result)
    return SuccessResponse(success=True, message="Folder updated")


@custom_connector_router.delete(
    "/recordGroups/{record_group_id}/folders/{folder_id}",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))],
)
@inject
async def delete_folder(
    request: Request,
    record_group_id: str,
    folder_id: str,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.delete_folder(instance_id=record_group_id, folder_id=folder_id, user_id=user_id)
    _raise_on_error(result)
    return SuccessResponse(success=True, message="Folder and contents deleted")


# ============================================================================
# Records (generic)
# ============================================================================

@custom_connector_router.post(
    "/recordGroups/{record_group_id}/records",
    response_model=CreateRecordResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def create_record(
    request: Request,
    record_group_id: str,
    body: CreateRecordRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> CreateRecordResponse:
    user_id, _ = _user_ctx(request)
    rg_ctx = await _fetch_rg_context(svc, record_group_id)
    record = _build_record(body.record_type, body.record, record_group_id, rg_ctx)
    perms = _build_permissions(body.permissions or [])
    result = await svc.create_record(
        instance_id=record_group_id, record=record, user_id=user_id, permissions=perms
    )
    result = _raise_on_error(result)
    # Service speaks internal `instance_id`/`instanceId`; API surfaces it as
    # `recordGroupId`. Read both keys so a future service rename doesn't
    # silently return None here.
    return CreateRecordResponse(
        success=result.get("success", True),
        created=result.get("created", 0),
        recordIds=result.get("recordIds", []),
        recordGroupId=result.get("recordGroupId") or result.get("instanceId"),
    )


@custom_connector_router.post(
    "/recordGroups/{record_group_id}/records/batch",
    response_model=CreateRecordsBatchResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def create_records_batch(
    request: Request,
    record_group_id: str,
    body: CreateRecordsBatchRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> CreateRecordsBatchResponse:
    user_id, _ = _user_ctx(request)
    rg_ctx = await _fetch_rg_context(svc, record_group_id)
    records_with_permissions = []
    for rp in body.records:
        rec = _build_record(rp.record_type, rp.record, record_group_id, rg_ctx)
        perms = _build_permissions(rp.permissions or [])
        records_with_permissions.append((rec, perms))
    result = await svc.create_records(
        instance_id=record_group_id,
        records_with_permissions=records_with_permissions,
        user_id=user_id,
    )
    result = _raise_on_error(result)
    return CreateRecordsBatchResponse(
        success=result.get("success", True),
        created=result.get("created", 0),
        recordIds=result.get("recordIds", []),
        recordGroupId=result.get("recordGroupId") or result.get("instanceId"),
    )


@custom_connector_router.post(
    "/recordGroups/{record_group_id}/files",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def upload_files(
    request: Request,
    record_group_id: str,
    body: UploadFilesRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, org_id = _user_ctx(request)
    result = await svc.upload_files(
        instance_id=record_group_id,
        files=body.files,
        user_id=user_id,
        org_id=org_id,
        is_restricted=body.is_restricted,
        parent_id=body.parent_id,
    )
    return _raise_on_error(result)


@custom_connector_router.put(
    "/records/{record_id}",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def update_record(
    request: Request,
    record_id: str,
    body: UpdateRecordRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.update_record(
        record_id=record_id,
        user_id=user_id,
        updates=body.updates,
        file_metadata=body.file_metadata,
    )
    _raise_on_error(result)
    return SuccessResponse(success=True, message="Record updated")


@custom_connector_router.delete(
    "/recordGroups/{record_group_id}/records",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))],
)
@inject
async def delete_records(
    request: Request,
    record_group_id: str,
    body: DeleteRecordsRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.delete_records(
        record_ids=body.record_ids,
        instance_id=record_group_id,
        user_id=user_id,
        parent_id=body.parent_id,
    )
    result = _raise_on_error(result)
    deleted = result.get("deleted", result.get("deletedCount", len(body.record_ids)))
    return SuccessResponse(success=True, message=f"Deleted {deleted} records")


@custom_connector_router.put(
    "/recordGroups/{record_group_id}/records/{record_id}/move",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def move_record(
    request: Request,
    record_group_id: str,
    record_id: str,
    body: MoveRecordRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.move_record(
        record_id=record_id,
        instance_id=record_group_id,
        new_parent_id=body.new_parent_id,
        user_id=user_id,
    )
    _raise_on_error(result)
    return SuccessResponse(success=True, message="Record moved")


@custom_connector_router.get(
    "/records/{record_id}/stream",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def stream_record(
    request: Request,
    record_id: str,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, org_id = _user_ctx(request)
    result = await svc.stream_record(record_id=record_id, user_id=user_id, org_id=org_id)
    return _raise_on_error(result)


# ============================================================================
# Permissions on any node (record or recordGroup)
# ============================================================================

@custom_connector_router.post(
    "/nodes/{node_id}/permissions",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def add_node_permissions(
    request: Request,
    node_id: str,
    body: GrantPermissionRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, _ = _user_ctx(request)
    result = await svc.add_permissions_to_node(
        node_id=node_id,
        requester_id=user_id,
        user_ids=body.user_ids,
        team_ids=body.team_ids,
        role=body.role,
        group_ids=body.group_ids,
        role_ids=body.role_ids,
        node_collection=body.node_collection,
    )
    return _raise_on_error(result)


@custom_connector_router.put(
    "/nodes/{node_id}/permissions",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def update_node_permissions(
    request: Request,
    node_id: str,
    body: UpdatePermissionRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, _ = _user_ctx(request)
    result = await svc.update_node_permissions(
        node_id=node_id,
        requester_id=user_id,
        user_ids=body.user_ids,
        team_ids=body.team_ids,
        new_role=body.new_role,
        group_ids=body.group_ids,
        role_ids=body.role_ids,
        node_collection=body.node_collection,
    )
    return _raise_on_error(result)


@custom_connector_router.delete(
    "/nodes/{node_id}/permissions",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))],
)
@inject
async def remove_node_permissions(
    request: Request,
    node_id: str,
    body: RemovePermissionRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, _ = _user_ctx(request)
    result = await svc.remove_node_permissions(
        node_id=node_id,
        requester_id=user_id,
        user_ids=body.user_ids,
        team_ids=body.team_ids,
        group_ids=body.group_ids,
        role_ids=body.role_ids,
        node_collection=body.node_collection,
    )
    return _raise_on_error(result)


@custom_connector_router.get(
    "/nodes/{node_id}/permissions",
    response_model=NodePermissionsResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def list_node_permissions(
    request: Request,
    node_id: str,
    node_collection: Literal["recordGroups", "records"] = Query(
        "recordGroups", description="Target collection for the node id"
    ),
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> NodePermissionsResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.list_node_permissions(
        node_id=node_id, requester_id=user_id, node_collection=node_collection
    )
    return NodePermissionsResponse(**_raise_on_error(result))


# ============================================================================
# Groups
# ============================================================================

@custom_connector_router.post(
    "/groups",
    response_model=GroupResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def create_group(
    request: Request,
    body: CreateGroupRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> GroupResponse:
    user_id, org_id = _user_ctx(request)
    result = await svc.create_group(
        user_id=user_id,
        org_id=org_id,
        name=body.name,
        connector_id=body.connector_id,
        source_group_id=body.source_group_id,
        app_name=body.app_name,
        description=body.description,
    )
    return GroupResponse(**_raise_on_error(result))


@custom_connector_router.get(
    "/groups",
    response_model=ListGroupsResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def list_groups(
    request: Request,
    connector_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None),
    sort_by: str = Query("name"),
    sort_order: str = Query("asc"),
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> ListGroupsResponse:
    user_id, org_id = _user_ctx(request)
    result = await svc.list_groups(
        user_id=user_id,
        org_id=org_id,
        connector_id=connector_id,
        page=page,
        limit=limit,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return ListGroupsResponse(**_raise_on_error(result))


@custom_connector_router.get(
    "/groups/{group_id}",
    response_model=GroupResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def get_group(
    request: Request,
    group_id: str,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> GroupResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.get_group(group_id=group_id, user_id=user_id)
    return GroupResponse(**_raise_on_error(result))


@custom_connector_router.put(
    "/groups/{group_id}",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def update_group(
    request: Request,
    group_id: str,
    body: UpdateGroupRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.update_group(group_id=group_id, user_id=user_id, updates=body.updates)
    _raise_on_error(result)
    return SuccessResponse(success=True, message="Group updated")


@custom_connector_router.delete(
    "/groups/{group_id}",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))],
)
@inject
async def delete_group(
    request: Request,
    group_id: str,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.delete_group(group_id=group_id, user_id=user_id)
    _raise_on_error(result)
    return SuccessResponse(success=True, message="Group deleted")


@custom_connector_router.post(
    "/groups/{group_id}/members",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def add_group_members(
    request: Request,
    group_id: str,
    body: GroupMembersRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, _ = _user_ctx(request)
    result = await svc.add_group_members(
        group_id=group_id, user_ids=body.user_ids, requester_id=user_id, role=body.role or "READER"
    )
    return _raise_on_error(result)


@custom_connector_router.delete(
    "/groups/{group_id}/members",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))],
)
@inject
async def remove_group_members(
    request: Request,
    group_id: str,
    body: GroupMembersRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, _ = _user_ctx(request)
    result = await svc.remove_group_members(
        group_id=group_id, user_ids=body.user_ids, requester_id=user_id
    )
    return _raise_on_error(result)


# Note: to grant a group permission on a record/recordGroup, use
# POST /nodes/{node_id}/permissions with `group_ids: [...]` in the body.


# ============================================================================
# Roles
# ============================================================================

@custom_connector_router.post(
    "/roles",
    response_model=RoleResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def create_role(
    request: Request,
    body: CreateRoleRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> RoleResponse:
    user_id, org_id = _user_ctx(request)
    result = await svc.create_role(
        user_id=user_id,
        org_id=org_id,
        name=body.name,
        connector_id=body.connector_id,
        source_role_id=body.source_role_id,
        app_name=body.app_name,
        description=body.description,
        parent_role_id=body.parent_role_id,
    )
    return RoleResponse(**_raise_on_error(result))


@custom_connector_router.get(
    "/roles",
    response_model=ListRolesResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def list_roles(
    request: Request,
    connector_id: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None),
    sort_by: str = Query("name"),
    sort_order: str = Query("asc"),
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> ListRolesResponse:
    user_id, org_id = _user_ctx(request)
    result = await svc.list_roles(
        user_id=user_id,
        org_id=org_id,
        connector_id=connector_id,
        page=page,
        limit=limit,
        search=search,
        sort_by=sort_by,
        sort_order=sort_order,
    )
    return ListRolesResponse(**_raise_on_error(result))


@custom_connector_router.get(
    "/roles/{role_id}",
    response_model=RoleResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def get_role(
    request: Request,
    role_id: str,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> RoleResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.get_role(role_id=role_id, user_id=user_id)
    return RoleResponse(**_raise_on_error(result))


@custom_connector_router.put(
    "/roles/{role_id}",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def update_role(
    request: Request,
    role_id: str,
    body: UpdateRoleRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.update_role(role_id=role_id, user_id=user_id, updates=body.updates)
    _raise_on_error(result)
    return SuccessResponse(success=True, message="Role updated")


@custom_connector_router.delete(
    "/roles/{role_id}",
    response_model=SuccessResponse,
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))],
)
@inject
async def delete_role(
    request: Request,
    role_id: str,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> SuccessResponse:
    user_id, _ = _user_ctx(request)
    result = await svc.delete_role(role_id=role_id, user_id=user_id)
    _raise_on_error(result)
    return SuccessResponse(success=True, message="Role deleted")


@custom_connector_router.post(
    "/roles/{role_id}/members",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_WRITE))],
)
@inject
async def add_role_members(
    request: Request,
    role_id: str,
    body: AssignRoleRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, _ = _user_ctx(request)
    result = await svc.add_role_members(
        role_id=role_id,
        user_ids=body.user_ids,
        requester_id=user_id,
        membership_role=body.role or "READER",
    )
    return _raise_on_error(result)


@custom_connector_router.delete(
    "/roles/{role_id}/members",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_DELETE))],
)
@inject
async def remove_role_members(
    request: Request,
    role_id: str,
    body: RoleMembersRequest,
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, _ = _user_ctx(request)
    result = await svc.remove_role_members(
        role_id=role_id, user_ids=body.user_ids, requester_id=user_id
    )
    return _raise_on_error(result)


# ============================================================================
# Listings
# ============================================================================

@custom_connector_router.get(
    "/records",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def list_all_records(
    request: Request,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None),
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, org_id = _user_ctx(request)
    result = await svc.list_all_records(
        user_id=user_id, org_id=org_id, page=page, limit=limit, search=search
    )
    return _raise_on_error(result)


@custom_connector_router.get(
    "/recordGroups/{record_group_id}/records/all",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def list_record_group_records(
    request: Request,
    record_group_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, _ = _user_ctx(request)
    result = await svc.list_record_group_records(
        record_group_id=record_group_id, user_id=user_id, page=page, limit=limit
    )
    return _raise_on_error(result)


@custom_connector_router.get(
    "/nodes/{node_id}/children",
    dependencies=[Depends(require_scopes(OAuthScopes.CONNECTOR_READ))],
)
@inject
async def get_node_children(
    request: Request,
    node_id: str,
    node_type: str = Query(..., description="recordGroup | record"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=200),
    svc: CustomConnectorService = Depends(get_custom_connector_service),
) -> Dict[str, Any]:
    user_id, _ = _user_ctx(request)
    result = await svc.get_node_children(
        node_id=node_id, node_type=node_type, user_id=user_id, page=page, limit=limit
    )
    return _raise_on_error(result)
