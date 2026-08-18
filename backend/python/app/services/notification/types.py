from enum import Enum

# App-relative redirect links for notifications; the frontend notification panel
# prepends "/" and renders them as in-app links (see notification-row.tsx).
CONNECTOR_NOTIFICATION_LINK_PREFIX = "workspace/connectors/"  # + f"{scope}/?connectorType={type}"
ACTIONS_NOTIFICATION_LINK = "workspace/actions"
MCP_SERVERS_NOTIFICATION_LINK = "workspace/mcp-servers/team"


class NotificationSeverity(str, Enum):
    """Matches INotification.severity in backend/nodejs notification schema."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"
    SUCCESS = "success"

class NotificationOrigin(str, Enum):
    CONNECTOR = "Connector Service"
    INDEXING = "Indexing Service"
    AI = "AI Service"


class NotificationType(str, Enum):
    CONNECTOR_AUTH_ERROR = "CONNECTOR_AUTH_ERROR"
    CONNECTOR_NOT_ACCESSIBLE = "CONNECTOR_NOT_ACCESSIBLE"
    CONNECTOR_SYNC_ERROR = "CONNECTOR_SYNC_ERROR"
    CONNECTOR_USER_SYNC_ERROR = "CONNECTOR_USER_SYNC_ERROR"
    CONNECTOR_GROUP_SYNC_ERROR = "CONNECTOR_GROUP_SYNC_ERROR"
    CONNECTOR_ROLE_SYNC_ERROR = "CONNECTOR_ROLE_SYNC_ERROR"
    CONNECTOR_RECORD_GROUP_SYNC_ERROR = "CONNECTOR_RECORD_GROUP_SYNC_ERROR"
    CONNECTOR_RECORD_SYNC_ERROR = "CONNECTOR_RECORD_SYNC_ERROR"
    CONNECTOR_STREAM_ERROR = "CONNECTOR_STREAM_ERROR"
    CONNECTOR_INFO = "CONNECTOR_INFO"
    CONNECTOR_WARNING = "CONNECTOR_WARNING"
    CONNECTOR_SUCCESS = "CONNECTOR_SUCCESS"
    # Agent toolsets ("Actions") — distinct from connectors.
    TOOLSET_AUTH_ERROR = "TOOLSET_AUTH_ERROR"
    # MCP server instances — distinct from connectors and toolsets.
    MCP_AUTH_ERROR = "MCP_AUTH_ERROR"

class NotificationStatus(str, Enum):
    UNREAD = "unread"
    READ = "read"
    ARCHIVED = "archived"

class NotificationRecipientRole(str, Enum):
    ADMIN = "admin"
    STANDARD = "standard"
    EVERYONE = "everyone"