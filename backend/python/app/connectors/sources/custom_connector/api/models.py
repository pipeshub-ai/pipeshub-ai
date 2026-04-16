"""Pydantic request/response models for the Custom Connector REST router.

Mirrors the shape of `localKB/api/models.py` for familiarity. SuccessResponse and
ErrorResponse are re-exported from the KB models to avoid duplication.
"""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, Field, model_validator

# Re-use KB's generic response models; identical shape.
from app.connectors.sources.localKB.api.models import ErrorResponse, SuccessResponse

__all__ = [
    "ErrorResponse",
    "SuccessResponse",
    # RecordGroup CRUD
    "CreateRecordGroupRequest",
    "RecordGroupResponse",
    "ListRecordGroupsResponse",
    "UpdateRecordGroupRequest",
    # Folder
    "CreateFolderRequest",
    "FolderResponse",
    "UpdateFolderRequest",
    # Records
    "PermissionPayload",
    "RecordPayload",
    "CreateRecordRequest",
    "CreateRecordsBatchRequest",
    "CreateRecordResponse",
    "CreateRecordsBatchResponse",
    "UpdateRecordRequest",
    "DeleteRecordsRequest",
    "MoveRecordRequest",
    "UploadFilesRequest",
    # Record relations
    # Permissions on any node
    "GrantPermissionRequest",
    "UpdatePermissionRequest",
    "RemovePermissionRequest",
    "NodePermissionsResponse",
    # Groups
    "CreateGroupRequest",
    "UpdateGroupRequest",
    "GroupResponse",
    "ListGroupsResponse",
    "GroupMembersRequest",
    # Roles
    "CreateRoleRequest",
    "UpdateRoleRequest",
    "RoleResponse",
    "ListRolesResponse",
    "RoleMembersRequest",
    "AssignRoleRequest",
]


# ============================================================================
# RecordGroup CRUD
# ============================================================================

class CreateRecordGroupRequest(BaseModel):
    name: str = Field(..., description="Record group name", min_length=1, max_length=255)
    group_type: str = Field(..., description="RecordGroup type enum value")
    connector_id: str = Field(..., description="Parent app/connector id — connector type is derived server-side")
    external_group_id: str = Field(
        ...,
        description="Caller-supplied external identifier (e.g. Jira project key). Required.",
    )
    parent_external_group_id: Optional[str] = Field(
        None,
        description="Optional parent recordGroup's external_group_id. If it resolves, PARENT_CHILD edge is created and INHERIT_PERMISSIONS (if enabled) targets the parent RG instead of the app.",
    )
    is_restricted: bool = Field(
        False,
        description="Stored on the recordGroup document. No edge behavior — reserved for future use.",
    )
    inherit_permissions: bool = Field(
        False,
        description="When true, creates INHERIT_PERMISSIONS edge — to parent recordGroup if parent_external_group_id resolves, else to parent app.",
    )


class RecordGroupResponse(BaseModel):
    success: bool = True
    id: str
    name: Optional[str] = None
    createdAtTimestamp: Optional[int] = None
    updatedAtTimestamp: Optional[int] = None
    userRole: Optional[str] = None

    class Config:
        extra = "allow"  # service returns additional fields on some paths


class ListRecordGroupsResponse(BaseModel):
    success: bool = True
    recordGroups: List[Dict[str, Any]] = Field(default_factory=list)
    pagination: Optional[Dict[str, Any]] = None
    filters: Optional[Dict[str, Any]] = None


class UpdateRecordGroupRequest(BaseModel):
    updates: Dict[str, Any] = Field(..., description="Fields to update")


# ============================================================================
# Folder
# ============================================================================

class CreateFolderRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    is_restricted: bool = Field(
        False,
        description="Stored on the folder document. No edge behavior — reserved for future use.",
    )
    inherit_permissions: bool = Field(
        True,
        description="When true (default), creates INHERIT_PERMISSIONS edge from this folder → immediate parent (parent folder if nested, else recordGroup).",
    )
    parent_id: Optional[str] = Field(None, description="Parent folder id (null = root)")


class FolderResponse(BaseModel):
    success: bool = True

    class Config:
        extra = "allow"


class UpdateFolderRequest(BaseModel):
    updates: Dict[str, Any] = Field(..., description="Fields to update")


# ============================================================================
# Records
# ============================================================================

class PermissionPayload(BaseModel):
    """Permission object accepted in create_record body.

    One of email (for USER entity) or external_id (for GROUP / ROLE / TEAM) must be set.
    """

    entity_type: str = Field(..., description="USER | GROUP | ROLE | TEAM")
    permission_type: str = Field(..., description="OWNER | WRITER | READER | COMMENTER | OTHERS")
    email: Optional[str] = None
    external_id: Optional[str] = None

    @model_validator(mode="after")
    def _check_identifier(self) -> "PermissionPayload":
        et = (self.entity_type or "").upper()
        if et == "USER":
            if not self.email:
                raise ValueError("email is required when entity_type=USER")
        elif et in {"GROUP", "ROLE", "TEAM"}:
            if not self.external_id:
                raise ValueError(f"external_id is required when entity_type={et}")
        else:
            raise ValueError(
                f"Unsupported entity_type '{self.entity_type}'. "
                "Expected one of: USER, GROUP, ROLE, TEAM"
            )
        return self


class RecordPayload(BaseModel):
    """Full Record body passed to create_record.

    `record` is free-form Dict because different RecordType subclasses have
    different typed fields. The processor validates the shape at upsert time.
    """

    record_type: str = Field(..., description="RecordType enum value (FILE, MAIL, TICKET, ...)")
    record: Dict[str, Any] = Field(..., description="Full Record JSON — base + type-specific fields")
    permissions: Optional[List[PermissionPayload]] = Field(default_factory=list)


class CreateRecordRequest(RecordPayload):
    pass


class CreateRecordsBatchRequest(BaseModel):
    records: List[RecordPayload] = Field(..., min_length=1)


class CreateRecordResponse(BaseModel):
    success: bool = True
    created: int = 0
    recordIds: List[str] = Field(default_factory=list)
    recordGroupId: Optional[str] = None


class CreateRecordsBatchResponse(CreateRecordResponse):
    pass


class UpdateRecordRequest(BaseModel):
    updates: Dict[str, Any]
    file_metadata: Optional[Dict[str, Any]] = None


class DeleteRecordsRequest(BaseModel):
    record_ids: List[str] = Field(..., min_length=1)
    parent_id: Optional[str] = None


class MoveRecordRequest(BaseModel):
    new_parent_id: Optional[str] = None


class UploadFilesRequest(BaseModel):
    """Legacy file-only upload — retained for parity with KB's upload path."""

    files: List[Dict[str, Any]] = Field(..., min_length=1)
    is_restricted: bool = False
    parent_id: Optional[str] = None


# ============================================================================
# Permissions on any node (record or recordGroup)
# ============================================================================

class GrantPermissionRequest(BaseModel):
    user_ids: List[str] = Field(default_factory=list)
    group_ids: List[str] = Field(default_factory=list)
    role_ids: List[str] = Field(default_factory=list)
    team_ids: List[str] = Field(default_factory=list)
    role: str = Field(..., description="OWNER | WRITER | READER | COMMENTER | ORGANIZER | FILEORGANIZER")
    node_collection: Literal["recordGroups", "records"] = Field(
        "recordGroups",
        description="Target collection for the node id",
    )


class UpdatePermissionRequest(GrantPermissionRequest):
    new_role: str = Field(..., description="New role for the entities listed")


class RemovePermissionRequest(BaseModel):
    user_ids: List[str] = Field(default_factory=list)
    group_ids: List[str] = Field(default_factory=list)
    role_ids: List[str] = Field(default_factory=list)
    team_ids: List[str] = Field(default_factory=list)
    node_collection: Literal["recordGroups", "records"] = "recordGroups"


class NodePermissionsResponse(BaseModel):
    success: bool = True
    permissions: List[Dict[str, Any]] = Field(default_factory=list)
    nodeId: str
    totalCount: int = 0


# ============================================================================
# Groups
# ============================================================================

class CreateGroupRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    connector_id: str
    source_group_id: str
    app_name: str = Field(..., description="Connectors enum value")
    description: Optional[str] = None


class UpdateGroupRequest(BaseModel):
    updates: Dict[str, Any]


class GroupResponse(BaseModel):
    success: bool = True

    class Config:
        extra = "allow"


class ListGroupsResponse(BaseModel):
    success: bool = True
    groups: List[Dict[str, Any]] = Field(default_factory=list)
    pagination: Optional[Dict[str, Any]] = None


class GroupMembersRequest(BaseModel):
    user_ids: List[str] = Field(..., min_length=1)
    role: Optional[str] = Field(
        "READER", description="Role on group for add_group_members; ignored for removal"
    )


# ============================================================================
# Roles
# ============================================================================

class CreateRoleRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    connector_id: str
    source_role_id: str
    app_name: str
    description: Optional[str] = None
    parent_role_id: Optional[str] = None


class UpdateRoleRequest(BaseModel):
    updates: Dict[str, Any]


class RoleResponse(BaseModel):
    success: bool = True

    class Config:
        extra = "allow"


class ListRolesResponse(BaseModel):
    success: bool = True
    roles: List[Dict[str, Any]] = Field(default_factory=list)
    pagination: Optional[Dict[str, Any]] = None


class RoleMembersRequest(BaseModel):
    user_ids: List[str] = Field(..., min_length=1)
    role: Optional[str] = Field(
        "READER", description="Role on role for add_role_members; ignored for removal"
    )


class AssignRoleRequest(RoleMembersRequest):
    pass
