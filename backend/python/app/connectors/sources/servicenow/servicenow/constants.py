"""
ServiceNow-specific constants for the connector.

This module contains all hard-coded string literals and configuration values
specific to ServiceNow API and data model.
"""

from typing import TypedDict

from app.connectors.core.constants import CommonStrings


class ServiceNowConnectorMetadata:
    """Metadata for the ServiceNow connector"""
    NAME = "ServiceNow"


class ServiceNowTables:
    """ServiceNow table names"""
    KB_KNOWLEDGE_BASE = "kb_knowledge_base"
    KB_KNOWLEDGE = "kb_knowledge"
    KB_CATEGORY = "kb_category"
    SYS_USER = "sys_user"
    SYS_USER_GROUP = "sys_user_group"
    SYS_USER_GRMEMBER = "sys_user_grmember"
    SYS_USER_ROLE = "sys_user_role"
    SYS_USER_HAS_ROLE = "sys_user_has_role"
    SYS_USER_ROLE_CONTAINS = "sys_user_role_contains"
    SYS_ATTACHMENT = "sys_attachment"
    USER_CRITERIA = "user_criteria"
    KB_UC_CAN_READ_MTOM = "kb_uc_can_read_mtom"
    KB_UC_CAN_CONTRIBUTE_MTOM = "kb_uc_can_contribute_mtom"
    CORE_COMPANY = "core_company"
    CMN_DEPARTMENT = "cmn_department"
    CMN_LOCATION = "cmn_location"
    CMN_COST_CENTER = "cmn_cost_center"


class ServiceNowFields:
    """ServiceNow field names"""
    SYS_ID = "sys_id"
    SYS_CREATED_ON = "sys_created_on"
    SYS_UPDATED_ON = "sys_updated_on"
    NAME = "name"
    TITLE = "title"
    DESCRIPTION = "description"
    EMAIL = "email"
    ACTIVE = "active"
    PUBLISHED = "published"
    WORKFLOW_STATE = "workflow_state"
    PARENT = "parent"
    PARENT_ID = "parent_id"
    LABEL = "label"
    VALUE = "value"
    USER = "user"
    GROUP = "group"
    ROLE = "role"
    OWNER = "owner"
    KB_MANAGERS = "kb_managers"
    KB_KNOWLEDGE_BASE = "kb_knowledge_base"
    KB_CATEGORY = "kb_category"
    SHORT_DESCRIPTION = "short_description"
    TEXT = "text"
    NUMBER = "number"
    AUTHOR = "author"
    CAN_READ_USER_CRITERIA = "can_read_user_criteria"
    USER_CRITERIA = "user_criteria"
    USER_NAME = "user_name"
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    DEPARTMENT = "department"
    COMPANY = "company"
    LOCATION = "location"
    COST_CENTER = "cost_center"
    MANAGER = "manager"
    FILE_NAME = "file_name"
    CONTENT_TYPE = "content_type"
    SIZE_BYTES = "size_bytes"
    TABLE_NAME = "table_name"
    TABLE_SYS_ID = "table_sys_id"
    RESULT = "result"
    CONTAINS = "contains"
    PARENT_TABLE = "parent_table"


class ServiceNowQueryParams:
    """ServiceNow API query parameter names"""
    SYSPARM_QUERY = "sysparm_query"
    SYSPARM_FIELDS = "sysparm_fields"
    SYSPARM_LIMIT = "sysparm_limit"
    SYSPARM_OFFSET = "sysparm_offset"
    SYSPARM_DISPLAY_VALUE = "sysparm_display_value"
    SYSPARM_NO_COUNT = "sysparm_no_count"
    SYSPARM_EXCLUDE_REFERENCE_LINK = "sysparm_exclude_reference_link"


class ServiceNowQueryValues:
    """ServiceNow API query parameter values"""
    DISPLAY_VALUE_FALSE = "false"
    DISPLAY_VALUE_TRUE = "true"
    NO_COUNT_TRUE = "true"
    EXCLUDE_REFERENCE_LINK_TRUE = "true"
    ORDER_BY_UPDATED = "ORDERBYsys_updated_on"


class ServiceNowURLPatterns:
    """ServiceNow URL patterns for constructing web URLs"""
    KB_BASE = "{instance_url}kb?kb={sys_id}"
    KB_ARTICLE = "{instance_url}/sp?id=kb_article&sys_id={sys_id}"
    KB_CATEGORY = "{instance_url}sp?id=kb_category&kb_category={sys_id}"
    ATTACHMENT = "{instance_url}/sys_attachment.do?sys_id={sys_id}"


class ServiceNowPrefixes:
    """Prefixes for organizational entity names"""
    COMPANY = "COMPANY_"
    DEPARTMENT = "DEPARTMENT_"
    LOCATION = "LOCATION_"
    COST_CENTER = "COSTCENTER_"
    ROLE = "ROLE_"


class ServiceNowDefaults:
    """Default values for ServiceNow API operations"""
    BATCH_SIZE = 100
    PAGINATION_OFFSET = 0
    DEFAULT_LIMIT = "1"
    DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
    MAX_URL_LENGTH = 2000
    CONNECTOR_TYPE = "SERVICENOW"
    ADMIN_ROLE_NAME = "admin"
    DEFAULT_MIME_TYPE = "application/octet-stream"
    UNKNOWN_VALUE = "Unknown"


class ServiceNowConfigPaths:
    """Configuration path patterns"""
    CONNECTOR_CONFIG = "/services/connectors/{connector_id}/config"


class ServiceNowRoles:
    """ServiceNow role names"""
    ADMIN = "admin"


class ServiceNowSyncPointKeys:
    """Keys for sync point storage"""
    USERS = "users"
    GROUPS = "groups"
    KNOWLEDGE_BASES = "knowledge_bases"
    CATEGORIES = "categories"
    ARTICLES = "articles"
    ROLE_ASSIGNMENTS = "role_assignments"
    LAST_SYNC_TIME = "last_sync_time"


class ServiceNowDictKeys:
    """Keys used in internal dict structures"""
    ENTITY_TYPE = "entity_type"
    SOURCE_SYS_ID = "source_sys_id"
    ROLE = "role"
    READ = "read"
    WRITE = "write"
    USER_SYS_ID = "user_sys_id"
    ORG_SYS_ID = "org_sys_id"
    ORG_TYPE = "org_type"
    PARENT = "parent"


class OrganizationalEntityConfig(TypedDict):
    """Configuration for a single organizational entity type sync.

    Defines the table, fields, naming prefix, and sync point key for
    syncing organizational entities like companies, departments, locations, etc.
    """
    table: str  # ServiceNow table name
    fields: str  # Comma-separated field list
    prefix: str  # Name prefix for groups (e.g., "COMPANY_", "DEPARTMENT_")
    sync_point_key: str  # Key for sync point tracking (e.g., "companies", "departments")


# Organizational entity configuration
ORGANIZATIONAL_ENTITIES: dict[str, OrganizationalEntityConfig] = {
    "company": {
        "table": ServiceNowTables.CORE_COMPANY,
        "fields": CommonStrings.COMMA.join(
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.NAME,
                ServiceNowFields.PARENT,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ]
        ),
        "prefix": ServiceNowPrefixes.COMPANY,
        "sync_point_key": "companies",
    },
    "department": {
        "table": ServiceNowTables.CMN_DEPARTMENT,
        "fields": CommonStrings.COMMA.join(
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.NAME,
                ServiceNowFields.PARENT,
                ServiceNowFields.COMPANY,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ]
        ),
        "prefix": ServiceNowPrefixes.DEPARTMENT,
        "sync_point_key": "departments",
    },
    "location": {
        "table": ServiceNowTables.CMN_LOCATION,
        "fields": CommonStrings.COMMA.join(
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.NAME,
                ServiceNowFields.PARENT,
                ServiceNowFields.COMPANY,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ]
        ),
        "prefix": ServiceNowPrefixes.LOCATION,
        "sync_point_key": "locations",
    },
    "cost_center": {
        "table": ServiceNowTables.CMN_COST_CENTER,
        "fields": CommonStrings.COMMA.join(
            [
                ServiceNowFields.SYS_ID,
                ServiceNowFields.NAME,
                ServiceNowFields.PARENT,
                ServiceNowFields.SYS_CREATED_ON,
                ServiceNowFields.SYS_UPDATED_ON,
            ]
        ),
        "prefix": ServiceNowPrefixes.COST_CENTER,
        "sync_point_key": "cost_centers",
    },
}
