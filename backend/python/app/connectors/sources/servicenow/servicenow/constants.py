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
    # Inline images: sys_attachment rows hidden from the article's attachment list
    INLINE_IMAGE = "ZZ_YYkb_knowledge"
    SYS_AUDIT_DELETE = "sys_audit_delete"
    USER_CRITERIA = "user_criteria"
    SYS_PROPERTIES = "sys_properties"
    SP_PORTAL = "sp_portal"
    KB_UC_CAN_READ_MTOM = "kb_uc_can_read_mtom"
    KB_UC_CANNOT_READ_MTOM = "kb_uc_cannot_read_mtom"
    KB_UC_CAN_CONTRIBUTE_MTOM = "kb_uc_can_contribute_mtom"
    CORE_COMPANY = "core_company"
    CMN_DEPARTMENT = "cmn_department"
    CMN_LOCATION = "cmn_location"
    KB_UC_CANNOT_CONTRIBUTE_MTOM = "kb_uc_cannot_contribute_mtom"
    KB_FEEDBACK = "kb_feedback"
    # Plugins and whether each is active (see ServiceNowConnector._explicit_roles_active)
    V_PLUGIN = "v_plugin"


class ServiceNowFields:
    """ServiceNow field names"""
    SYS_ID = "sys_id"
    SYS_CREATED_ON = "sys_created_on"
    SYS_UPDATED_ON = "sys_updated_on"
    SYS_MOD_COUNT = "sys_mod_count"
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
    ROLE_NAME = "role.name"
    # v_plugin
    ID = "id"
    OWNER = "owner"
    KB_MANAGERS = "kb_managers"
    KB_KNOWLEDGE_BASE = "kb_knowledge_base"
    KB_CATEGORY = "kb_category"
    SHORT_DESCRIPTION = "short_description"
    TEXT = "text"
    # A wiki article (article_type=wiki) keeps its body in wiki, not text
    WIKI = "wiki"
    ARTICLE_TYPE = "article_type"
    # kb_feedback
    ARTICLE = "article"
    ARTICLE_NUMBER = "article.number"
    USER_DISPLAY_NAME = "user.name"
    COMMENTS = "comments"
    USEFUL = "useful"
    RATING = "rating"
    FLAGGED = "flagged"
    PARENT_COMMENT = "parent_comment"
    ROOT_FEEDBACK = "root_feedback"
    NUMBER = "number"
    CAN_READ_USER_CRITERIA = "can_read_user_criteria"
    CANNOT_READ_USER_CRITERIA = "cannot_read_user_criteria"
    MATCH_ALL = "match_all"
    ADVANCED = "advanced"
    USER_CRITERIA = "user_criteria"
    USER_NAME = "user_name"
    FIRST_NAME = "first_name"
    LAST_NAME = "last_name"
    DEPARTMENT = "department"
    COMPANY = "company"
    LOCATION = "location"
    SCRIPT = "script"
    OWNERSHIP_GROUP = "ownership_group"
    STATE = "state"
    MANAGER = "manager"
    FILE_NAME = "file_name"
    CONTENT_TYPE = "content_type"
    SIZE_BYTES = "size_bytes"
    # sys_attachment: a SHA-256 of the file's bytes, kept when a new article version copies the file
    HASH = "hash"
    TABLE_NAME = "table_name"
    TABLE_SYS_ID = "table_sys_id"
    CONTAINS = "contains"
    PARENT_TABLE = "parent_table"
    URL_SUFFIX = "url_suffix"
    DEFAULT = "default"
    VALID_TO = "valid_to"
    TABLENAME = "tablename"
    DISPLAY_VALUE = "display_value"


class ServiceNowQueryValues:
    """ServiceNow API query parameter values"""
    DISPLAY_VALUE_FALSE = "false"
    DISPLAY_VALUE_TRUE = "true"
    NO_COUNT_TRUE = "true"
    EXCLUDE_REFERENCE_LINK_TRUE = "true"
    # Keyset order of every listing (see ServiceNowConnector._iter_table).
    ORDER_BY_KEYSET = "ORDERBYsys_updated_on^ORDERBYsys_id"
    CURRENT_USER = "sys_id=javascript:gs.getUserID()"


class ServiceNowURLPatterns:
    """ServiceNow URL patterns for constructing web URLs.

    The knowledge pages live in a Service Portal, and the instance decides
    which portal that is. See ServiceNowConnector._resolve_kb_portal_suffix.
    """
    KB_BASE = "{instance_url}/{portal}?id=kb_home&kb={sys_id}"
    # By number: opens the article's current version, whichever row that is.
    KB_ARTICLE = "{instance_url}/{portal}?id=kb_article_view&sysparm_article={number}"
    KB_CATEGORY = "{instance_url}/{portal}?id=kb_category&kb_category={sys_id}"


class ServiceNowPrefixes:
    """Prefixes for organizational entity names"""
    COMPANY = "COMPANY_"
    DEPARTMENT = "DEPARTMENT_"
    LOCATION = "LOCATION_"
    ROLE = "ROLE_"


class ServiceNowDefaults:
    """Default values for ServiceNow API operations"""
    BATCH_SIZE = 100
    # Page size of the listings that read ids only
    ID_PAGE_SIZE = 1000
    DEFAULT_LIMIT = "1"
    # Seconds the connection test waits for the instance's first answer
    CONNECTION_TEST_TIMEOUT_SECONDS = 20
    DATETIME_FORMAT = "%Y-%m-%d %H:%M:%S"
    DATE_FORMAT = "%Y-%m-%d"
    # Stored articles are checked against the full list of current ones at least this often
    # (and on every full sync): a deleted article leaves no row a delta could see.
    RECONCILE_INTERVAL_SECONDS = 24 * 3600
    MAX_URL_LENGTH = 2000
    # An incremental read starts this far before its checkpoint. Rows written in
    # the same second as the checkpoint, or committed late, would otherwise be
    # skipped for good; reading them again is an idempotent upsert.
    DELTA_OVERLAP_SECONDS = 120
    # Most rows kept as synced in the overlap; past it the next delta syncs them again
    OVERLAP_KEYS_MAX = 2000
    CONNECTOR_TYPE = "SERVICENOW"
    # An inline image is embedded as a data: URI (a third larger) in the streamed article.
    MAX_INLINE_IMAGE_BYTES = 10 * 1024 * 1024
    # Past either budget an article's further inline images stay links.
    MAX_INLINE_IMAGES = 20
    MAX_INLINE_IMAGES_TOTAL_BYTES = 20 * 1024 * 1024
    # Content types that say nothing about the file, so its name decides.
    UNRELIABLE_MIME_TYPES = frozenset({"application/unknown", "application/octet-stream", "application/binary"})
    # The out-of-box Knowledge Portal. Used only when the instance tells us nothing.
    KB_PORTAL_SUFFIX = "kb"
    KB_PORTAL_PROPERTY = "sn_km_portal.glide.knowman.serviceportal.portal_url"
    # When true, contributors are held to an article's own Can Read and Cannot Read too. Readers
    # always are: an article's Can Read narrows its knowledge base, whatever this says.
    ARTICLE_READ_CRITERIA_PROPERTY = "glide.knowman.apply_article_read_criteria"
    # When true (and when it cannot be read or is unset), a knowledge base with no user criteria
    # is readable only by its owner, managers and knowledge admins.
    BLOCK_NO_CRITERIA_PROPERTY = "glide.knowman.block_access_with_no_user_criteria"
    # The out-of-box "Guest User" criterion every new knowledge base gets as Cannot Read and
    # Cannot Contribute: it denies only the user named guest. Its script, evaluated as that user
    # only while it reads exactly so (see ServiceNowConnector._is_guest):
    GUEST_USER_CRITERIA_SCRIPT = 'answer = user_id == GlideUser.getUser("guest").getID();'
    GUEST_USER_NAME = "guest"
    # The out-of-box "Has atleast one role apart from snc_internal KB" criterion every new
    # knowledge base gets as Can Contribute. Its script, evaluated only while it reads exactly so:
    HAS_ROLE_CRITERIA = "f9397919f79d52507787d3e543bfd6cd"
    HAS_ROLE_CRITERIA_SCRIPT = (
        "var all_roles = [];\n"
        "var userHasRoleGR = new GlideRecord('sys_user_has_role');\n"
        "userHasRoleGR.addQuery('user',user_id);\n"
        "userHasRoleGR.query();\n"
        "while(userHasRoleGR.next())\n"
        "all_roles.push(userHasRoleGR.getDisplayValue('role'));\n"
        "answer = GlidePluginManager.isActive('com.glide.explicit_roles') ? "
        "all_roles.includes ('snc_internal') && all_roles.length >=2:all_roles.length >=1;"
    )
    EXPLICIT_ROLES_PLUGIN = "com.glide.explicit_roles"


class ServiceNowConfigPaths:
    """Configuration path patterns"""
    CONNECTOR_CONFIG = "/services/connectors/{connector_id}/config"


class ServiceNowRoles:
    """ServiceNow role names"""
    ADMIN = "admin"
    # Reads every knowledge base and article without user criteria (the knowledge_manager role does not).
    KNOWLEDGE_ADMIN = "knowledge_admin"
    SNC_INTERNAL = "snc_internal"


class ServiceNowSyncPointKeys:
    """Keys for sync point storage"""
    USERS = "users"
    CATEGORIES = "categories"
    ARTICLES = "articles"
    # In the articles sync point while a full read is unfinished: the (sys_updated_on, sys_id) of the
    # last row it wrote, where the next run continues (see ServiceNowConnector._sync_articles)
    FULL_RESUME_AFTER = "full_resume_after"
    LAST_SYNC_TIME = "last_sync_time"
    # Beside a checkpoint: the "<sys_updated_on>|<sys_id>" of the rows already synced that the next
    # delta's overlap reads again, so it skips them (see ServiceNowConnector._overlap_keys)
    SYNCED_IN_OVERLAP = "synced_in_overlap"
    # Set once articles are stored under their number (they were under a version's sys_id)
    ARTICLE_KEY_V2 = "article_key/v2"
    # Set once attachments are stored under a key that survives a new article version (they
    # were under their own sys_id, which each version's copy changes)
    ATTACHMENT_KEY_V2 = "attachment_key/v2"
    ARTICLES_RECONCILE = "articles_reconcile/last"
    ARTICLES_EXPIRY = "articles_expiry"
    INLINE_IMAGES = "attachments"
    FEEDBACK = "feedback"
    AUDIT_DELETE = "audit_delete"
    KB_SCOPE = "kb_scope"
    # Set once a run has read every user, so the users deactivated before it lose access too
    USERS_V2 = "users/v2"
    CATEGORY_SCOPE = "category_scope"
    # What the tables behind article grants looked like when the re-read pass last ran (see
    # ServiceNowConnector._access_reread_due)
    ACCESS_FINGERPRINT = "access_fingerprint"
    # When the stored users were last checked against the active ones with an email (see _sync_users)
    USERS_RECONCILE = "users_reconcile/last"
    # The admins and knowledge admins of the last listing ServiceNow showed in full (see _get_admin_users)
    ADMINS = "admins"


class ServiceNowDictKeys:
    """Keys used in internal dict structures"""
    READ = "read"
    CANNOT_READ = "cannot_read"
    WRITE = "write"
    CANNOT_WRITE = "cannot_write"
    IDS = "ids"
    DONE = "done"
    KEPT = "kept"
    FINGERPRINT = "fingerprint"
    COUNT = "count"
    GRANTED = "granted"


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
}

