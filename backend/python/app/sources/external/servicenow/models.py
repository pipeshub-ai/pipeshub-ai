"""
Pydantic models for ServiceNow API responses.

These models provide type safety and validation for ServiceNow API interactions.
All models are based on actual API response data collected from ServiceNow instances.

Note: All reference fields are returned as strings (sys_ids) because the connector uses:
- sysparm_display_value=false (returns actual values, not display text)
- sysparm_exclude_reference_link=true (excludes reference link objects)

This ensures consistent, simple string types for all references.
"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ServiceNowAPIError(Exception):
    """Raised when ServiceNow API returns an error.

    Attributes:
        status_code: HTTP status code from the error response
        message: Human-readable error message
        details: Additional error details from the API response
    """

    def __init__(
        self, status_code: int, message: str, details: str | None = None
    ) -> None:
        self.status_code = status_code
        self.message = message
        self.details = details
        super().__init__(f"ServiceNow API Error {status_code}: {message}")


class TableAPIRecord(BaseModel):
    """Generic record from ServiceNow Table API.

    ServiceNow returns records as flat dictionaries with various field types.
    Common patterns observed:
    - sys_id: string (unique identifier)
    - sys_created_on: string (datetime in ISO format)
    - sys_updated_on: string (datetime in ISO format)
    - Reference fields: Can be either:
        * Simple string containing sys_id
        * Dict with {"value": "sys_id", "link": "url"}
    - Display fields vary based on sysparm_display_value parameter

    This model uses extra='allow' to handle any table's fields dynamically
    while still providing some type safety through helper methods.
    """

    model_config = ConfigDict(extra="allow")

    def get(self, key: str, default: Any = None) -> Any:
        """Dict-like get method for backward compatibility.

        Args:
            key: Field name to retrieve
            default: Default value if field doesn't exist

        Returns:
            Field value or default
        """
        return getattr(self, key, default)

    def __getitem__(self, key: str) -> Any:
        """Dict-like indexing for backward compatibility.

        Args:
            key: Field name to retrieve

        Returns:
            Field value

        Raises:
            AttributeError: If field doesn't exist
        """
        return getattr(self, key)


class TableAPIResponse(BaseModel):
    """Response from ServiceNow Table API GET request.

    Standard ServiceNow Table API response structure:
    {
        "result": [array of table records],
        // Optional metadata fields that may vary by API version
    }

    The result field contains an array of records matching the query.
    Records are kept as dictionaries for flexibility since each table has different fields.
    Additional metadata fields are allowed and preserved.

    Note: While result uses List[TableAPIRecord] for type hints, the actual records
    are dictionaries that get validated by table-specific models when accessed.
    """

    result: list[TableAPIRecord] = Field(
        default_factory=list,
        description="Array of table records returned by the query"
    )

    model_config = ConfigDict(extra="allow")


class AttachmentMetadata(BaseModel):
    """Metadata for an attachment record from sys_attachment table.

    Represents a single attachment's metadata retrieved from the
    sys_attachment table. Does not include the file content itself.
    """

    sys_id: str = Field(description="Unique identifier for the attachment")
    file_name: str = Field(description="Original filename of the attachment")
    content_type: str = Field(description="MIME type of the file")
    size_bytes: str = Field(description="File size in bytes (as string)")
    table_sys_id: str = Field(description="sys_id of the parent record this attachment belongs to")
    table_name: str | None = Field(default=None, description="Name of the parent table")
    sys_created_on: str = Field(description="Creation timestamp")
    sys_updated_on: str = Field(description="Last update timestamp")

    model_config = ConfigDict(extra="allow")


class SysUser(BaseModel):
    """Model for sys_user table records.

    Represents a ServiceNow user account.
    Based on actual API response from sys_user table.
    """

    sys_id: str = Field(description="Unique identifier")
    user_name: str | None = Field(default=None, description="Username")
    email: str | None = Field(default=None, description="Email address")
    first_name: str | None = Field(default=None, description="First name")
    last_name: str | None = Field(default=None, description="Last name")
    name: str | None = Field(default=None, description="Full name")
    title: str | None = Field(default=None, description="Job title")
    active: str | None = Field(default=None, description="Whether user is active (string 'true' or 'false')")
    department: str | None = Field(default=None, description="Department sys_id reference (cmn_department)")
    location: str | None = Field(default=None, description="Location sys_id reference (cmn_location)")
    company: str | None = Field(default=None, description="Company sys_id reference (core_company)")
    cost_center: str | None = Field(default=None, description="Cost center sys_id reference (cmn_cost_center)")
    sys_created_on: str | None = Field(default=None, description="Creation timestamp")
    sys_updated_on: str | None = Field(default=None, description="Update timestamp")

    model_config = ConfigDict(extra="allow")


class SysUserGroup(BaseModel):
    """Model for sys_user_group table records.

    Represents a ServiceNow user group.
    Based on actual API response from sys_user_group table.
    """

    sys_id: str = Field(description="Unique identifier")
    name: str | None = Field(default=None, description="Group name")
    description: str | None = Field(default=None, description="Group description")
    parent: str | None = Field(default=None, description="Parent group sys_id reference (sys_user_group)")
    manager: str | None = Field(default=None, description="Group manager sys_id reference (sys_user)")
    active: str | None = Field(default=None, description="Whether group is active (string 'true' or 'false')")
    sys_created_on: str | None = Field(default=None, description="Creation timestamp")
    sys_updated_on: str | None = Field(default=None, description="Update timestamp")

    model_config = ConfigDict(extra="allow")


class SysUserGroupMembership(BaseModel):
    """Model for sys_user_grmember table records.

    Represents a user's membership in a group.
    Based on actual API response from sys_user_grmember table.
    """

    sys_id: str = Field(description="Unique identifier")
    user: str = Field(description="User sys_id reference (sys_user)")
    group: str = Field(description="Group sys_id reference (sys_user_group)")
    sys_created_on: str | None = Field(default=None, description="Creation timestamp")
    sys_updated_on: str | None = Field(default=None, description="Update timestamp")

    model_config = ConfigDict(extra="allow")


class SysUserRole(BaseModel):
    """Model for sys_user_role table records.

    Represents a ServiceNow role definition.
    """

    sys_id: str = Field(description="Unique identifier")
    name: str | None = Field(default=None, description="Role name")
    description: str | None = Field(default=None, description="Role description")
    sys_created_on: str | None = Field(default=None, description="Creation timestamp")
    sys_updated_on: str | None = Field(default=None, description="Update timestamp")

    model_config = ConfigDict(extra="allow")


class SysUserRoleAssignment(BaseModel):
    """Model for sys_user_has_role table records.

    Represents a role assignment to a user.
    Based on actual API response from sys_user_has_role table.
    """

    sys_id: str = Field(description="Unique identifier")
    user: str = Field(description="User sys_id reference (sys_user)")
    role: str = Field(description="Role sys_id reference (sys_user_role)")
    sys_created_on: str | None = Field(default=None, description="Creation timestamp")
    sys_updated_on: str | None = Field(default=None, description="Update timestamp")

    model_config = ConfigDict(extra="allow")


class SysUserRoleContains(BaseModel):
    """Model for sys_user_role_contains table records.

    Represents role hierarchy - which roles contain other roles.
    Based on actual API response from sys_user_role_contains table.
    """

    sys_id: str = Field(description="Unique identifier")
    role: str = Field(description="Parent role sys_id reference (sys_user_role)")
    contains: str = Field(description="Child role sys_id reference (sys_user_role)")

    model_config = ConfigDict(extra="allow")


class KBKnowledgeBase(BaseModel):
    """Model for kb_knowledge_base table records.

    Represents a ServiceNow knowledge base container.
    Based on actual API response from kb_knowledge_base table.
    """

    sys_id: str = Field(description="Unique identifier")
    title: str | None = Field(default=None, description="Knowledge base title")
    description: str | None = Field(default=None, description="KB description")
    owner: str | None = Field(default=None, description="KB owner sys_id reference (sys_user)")
    kb_managers: str | None = Field(default=None, description="KB managers sys_id reference (sys_user_group)")
    active: str | None = Field(default=None, description="Whether KB is active (string 'true' or 'false')")
    sys_created_on: str | None = Field(default=None, description="Creation timestamp")
    sys_updated_on: str | None = Field(default=None, description="Update timestamp")

    model_config = ConfigDict(extra="allow")


class KBCategory(BaseModel):
    """Model for kb_category table records.

    Represents a knowledge base category in the hierarchy.
    Based on actual API response from kb_category table.
    """

    sys_id: str = Field(description="Unique identifier")
    label: str | None = Field(default=None, description="Category label")
    value: str | None = Field(default=None, description="Category value/slug")
    parent_id: str | None = Field(default=None, description="Parent sys_id reference (kb_knowledge_base or kb_category)")
    parent_table: str | None = Field(default=None, description="Parent table name (kb_knowledge_base or kb_category)")
    kb_knowledge_base: str | None = Field(default=None, description="Knowledge base sys_id reference (kb_knowledge_base)")
    active: str | None = Field(default=None, description="Whether category is active (string 'true' or 'false')")
    sys_created_on: str | None = Field(default=None, description="Creation timestamp")
    sys_updated_on: str | None = Field(default=None, description="Update timestamp")

    model_config = ConfigDict(extra="allow")


class KBKnowledge(BaseModel):
    """Model for kb_knowledge table records.

    Represents a ServiceNow knowledge base article.
    Based on actual API response from kb_knowledge table.
    """

    sys_id: str = Field(description="Unique identifier")
    short_description: str | None = Field(default=None, description="Article short description")
    text: str | None = Field(default=None, description="Article content/text (HTML)")
    number: str | None = Field(default=None, description="Article number (e.g., KB0000011)")
    kb_knowledge_base: str | None = Field(default=None, description="Knowledge base sys_id reference (kb_knowledge_base)")
    kb_category: str | None = Field(default=None, description="Category sys_id reference (kb_category)")
    author: str | None = Field(default=None, description="Author sys_id reference (sys_user)")
    workflow_state: str | None = Field(default=None, description="Workflow state (e.g., published, draft)")
    published: str | None = Field(default=None, description="Publication date")
    valid_to: str | None = Field(default=None, description="Expiration date")
    active: str | None = Field(default=None, description="Whether article is active (string 'true' or 'false')")
    can_read_user_criteria: str | None = Field(default=None, description="Read permission criteria sys_id reference (user_criteria)")
    sys_created_on: str | None = Field(default=None, description="Creation timestamp")
    sys_updated_on: str | None = Field(default=None, description="Update timestamp")

    model_config = ConfigDict(extra="allow")


class UserCriteria(BaseModel):
    """Model for user_criteria table records.

    Represents user criteria for permissions. Can specify permissions based on:
    - Specific users
    - Groups (can be comma-separated list)
    - Roles
    - Organizational entities (department, location, company, cost_center)

    Based on actual API response from user_criteria table.
    """

    sys_id: str = Field(description="Unique identifier")
    name: str | None = Field(default=None, description="Criteria name")
    user: str | None = Field(default=None, description="Specific user sys_id reference (sys_user)")
    group: str | None = Field(default=None, description="Group sys_id(s), can be comma-separated list of sys_ids")
    role: str | None = Field(default=None, description="Role sys_id reference (sys_user_role)")
    department: str | None = Field(default=None, description="Department sys_id reference (cmn_department)")
    location: str | None = Field(default=None, description="Location sys_id reference (cmn_location)")
    company: str | None = Field(default=None, description="Company sys_id reference (core_company)")
    cost_center: str | None = Field(default=None, description="Cost center sys_id reference (cmn_cost_center)")
    sys_created_on: str | None = Field(default=None, description="Creation timestamp")
    sys_updated_on: str | None = Field(default=None, description="Update timestamp")

    model_config = ConfigDict(extra="allow")


class OrganizationalEntity(BaseModel):
    """Generic model for organizational entities.

    Used for core_company, cmn_department, cmn_location, cmn_cost_center tables.
    Based on actual API responses from organizational entity tables.
    """

    sys_id: str = Field(description="Unique identifier")
    name: str | None = Field(default=None, description="Entity name")
    parent: str | None = Field(default=None, description="Parent entity sys_id reference (for hierarchy)")
    company: str | None = Field(default=None, description="Company sys_id reference (core_company, for locations)")
    active: str | None = Field(default=None, description="Whether entity is active (string 'true' or 'false')")
    sys_created_on: str | None = Field(default=None, description="Creation timestamp")
    sys_updated_on: str | None = Field(default=None, description="Update timestamp")

    model_config = ConfigDict(extra="allow")


class KBPermissionMapping(BaseModel):
    """Model for kb_uc_can_read_mtom and kb_uc_can_contribute_mtom table records.

    Represents many-to-many mapping between knowledge bases and user criteria.
    - kb_uc_can_read_mtom: Read permissions
    - kb_uc_can_contribute_mtom: Contribute/write permissions

    Based on actual API responses from permission mapping tables.
    """

    user_criteria: str = Field(description="User criteria sys_id reference (user_criteria)")

    model_config = ConfigDict(extra="allow")


# Internal connector models (not from ServiceNow API directly)

class RawPermission(BaseModel):
    """Internal model for raw permission data extracted from user_criteria.

    This represents a single permission assignment before conversion to the app's Permission model.
    Used as an intermediate format between ServiceNow data and application Permission objects.
    """

    entity_type: Literal["USER", "GROUP"] = Field(description="Type of entity: USER or GROUP")
    source_sys_id: str = Field(description="ServiceNow sys_id of the user or group")
    role: Literal["READER", "WRITER", "OWNER"] = Field(description="Permission role type")

    model_config = ConfigDict(extra="forbid")
