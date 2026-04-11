"""Outlook-specific constants shared by Outlook Personal and Outlook (team) connectors."""

from dataclasses import dataclass

from msgraph.generated.models.message import Message  # type: ignore


class OutlookConnectorNames:
    """Connector names for registry (used only in @ConnectorBuilder decorator)."""
    PERSONAL = "Outlook Personal"
    TEAM = "Outlook"


class OutlookOAuthRedirectURIs:
    """OAuth redirect URI path segments (relative to app base)."""
    PERSONAL = "connectors/oauth/callback/OutlookPersonal"


class OutlookDocs:
    """Documentation URLs."""
    AZURE_AD_SETUP_URL = "https://docs.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app"
    PIPESHUB_DOCS_URL_PERSONAL = "https://docs.pipeshub.com/connectors/microsoft-365/outlook-personal"
    PIPESHUB_DOCS_URL_TEAM = "https://docs.pipeshub.com/connectors/microsoft-365/outlook"


class OutlookThreadDetection:
    """Email thread detection (conversation index)."""
    ROOT_CONVERSATION_INDEX_LENGTH = 22
    CHILD_INDEX_SUFFIX_LENGTH = 5


class OutlookFolders:
    """Standard Outlook folder names."""
    STANDARD_FOLDERS: list[str] = [
        "Inbox",
        "Sent Items",
        "Drafts",
        "Deleted Items",
        "Junk Email",
        "Archive",
        "Outbox",
        "Conversation History",
    ]


class OutlookSyncConfig:
    """Sync and pagination configuration."""
    DEFAULT_SYNC_INTERVAL_MINUTES = 60
    MESSAGE_PAGE_SIZE = 100
    FOLDER_PAGE_SIZE = 50
    MAX_FOLDER_PAGE_SIZE = 100
    USER_GROUPS_SYNC_BATCH_SIZE = 10


class OutlookAPIFields:
    """Graph API $select field lists."""
    MESSAGE_SELECT_FIELDS: list[str] = [
        "id",
        "subject",
        "hasAttachments",
        "createdDateTime",
        "lastModifiedDateTime",
        "receivedDateTime",
        "webLink",
        "from",
        "toRecipients",
        "ccRecipients",
        "bccRecipients",
        "conversationId",
        "internetMessageId",
        "conversationIndex",
    ]
    
    FOLDER_SELECT_FIELDS: list[str] = ["id", "displayName"]
    
    # Team connector - Groups API select fields
    GROUP_SELECT_FIELDS: list[str] = [
        "id",
        "displayName",
        "description",
        "mail",
        "mailNickname",
        "groupTypes",
        "createdDateTime",
        "mailEnabled"
    ]
    
    # Team connector - Group members select fields
    GROUP_MEMBER_SELECT_FIELDS: list[str] = [
        "id",
        "displayName",
        "mail",
        "userPrincipalName"
    ]
    
    # Team connector - User groups select fields (minimal)
    USER_GROUP_SELECT_FIELDS: list[str] = ["id", "displayName"]
    
    # Minimal user ID select field (for user existence check)
    USER_ID_SELECT_FIELDS: list[str] = ["id"]
    
    # Team connector - Group conversation threads select fields
    THREAD_SELECT_FIELDS: list[str] = [
        "id",
        "topic",
        "lastDeliveredDateTime",
        "hasAttachments",
        "preview"
    ]
    
    # Team connector - Group conversation posts select fields
    POST_SELECT_FIELDS: list[str] = [
        "id",
        "body",
        "from",
        "receivedDateTime",
        "hasAttachments",
        "conversationId",
        "conversationThreadId"
    ]
    
    # Group info select fields (for URL construction)
    GROUP_INFO_SELECT_FIELDS: list[str] = ["mail", "mailNickname"]
    
    # User filter options select fields (for filter UI)
    USER_FILTER_SELECT_FIELDS: list[str] = [
        "id",
        "mail",
        "userPrincipalName",
        "displayName",
        "givenName",
        "surname"
    ]

    # Group filter options select fields (for filter UI)
    GROUP_FILTER_SELECT_FIELDS: list[str] = [
        "id",
        "displayName",
        "description",
        "mail",
        "mailNickname",
        "groupTypes",
        "mailEnabled",
    ]


class OutlookDefaults:
    """Fallback labels for records / folders."""
    SUBJECT = "No Subject"
    FOLDER_NAME = "Unnamed Folder"
    ATTACHMENT_NAME = "Unnamed Attachment"
    UNKNOWN_FOLDER_LABEL = "Unknown"
    UNKNOWN_FOLDER_OPTION = "Unknown Folder"
    UNKNOWN_USER_LABEL = "Unknown User"
    UNKNOWN_GROUP_LABEL = "Unknown Group"


class OutlookFilterKeys:
    """Connector filter registration keys for load_connector_filters."""
    INDIVIDUAL = "outlookindividual"
    TEAM = "outlook"


class OutlookOAuthConfig:
    """OAuth helper values."""
    CONNECTOR_TYPE_PERSONAL = "Outlook Personal"
    CONFIG_SCOPE_PERSONAL_DEFAULT = "PERSONAL"


class OutlookSyncPointKeys:
    """Sync point segment and payload keys."""
    SEGMENT_FOLDERS = "folders"
    DELTA_LINK = "delta_link"
    LAST_SYNC_TIMESTAMP = "last_sync_timestamp"
    FOLDER_ID = "folder_id"
    FOLDER_NAME = "folder_name"
    ENCRYPT_FIELD_DELTA_LINK = "delta_link"
    
    # Team connector - Group conversations sync point keys
    RECORD_TYPE_GROUP_CONVERSATIONS = "group_conversations"
    SEGMENT_GROUP = "group"


class OutlookODataFields:
    """OData / filter field names."""
    RECEIVED_DATE_TIME = "receivedDateTime"
    DISPLAY_NAME = "displayName"


class OutlookHTTPDetails:
    """HTTP detail strings for HTTPException (not log lines)."""
    CLIENT_NOT_INITIALIZED = "External Outlook client not initialized"
    NO_PARENT_MESSAGE = "No parent message ID stored for attachment"
    UNSUPPORTED_RECORD_TYPE = "Unsupported record type for streaming"
    MISSING_GROUP_ID_POST = "Missing group_id for group post"
    MISSING_GROUP_OR_THREAD_FOR_ATTACHMENT = "Missing group_id or thread_id for group attachment"
    MISSING_THREAD_ID_POST = (
        "Missing thread_id for group post. This may be an old record - please re-sync the "
        "connector to update group posts with required metadata."
    )
    USER_CONTEXT_UNKNOWN = "Could not determine user context for this record."


class OutlookMediaTypes:
    """Media types for streaming."""
    TEXT_HTML = "text/html"


@dataclass
class MessagesDeltaResult:
    """Result from delta sync of messages."""
    messages: list[Message]
    delta_link: str | None


@dataclass
class OutlookCredentials:
    """Outlook OAuth credentials shared by both Personal and Team connectors."""
    tenant_id: str
    client_id: str
    client_secret: str
    has_admin_consent: bool = False
