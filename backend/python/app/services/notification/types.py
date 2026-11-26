from enum import Enum


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
    OAUTH_APP_DELETED = "OAUTH_APP_DELETED"
    # Agent toolsets ("Actions") — distinct from connectors.
    TOOLSET_AUTH_ERROR = "TOOLSET_AUTH_ERROR"
    ARTIFACT_SHARED = "ARTIFACT_SHARED"
    # Code runs in chat kept waiting for a sandbox, were refused, or recovered.
    SANDBOX_CAPACITY_WARNING = "SANDBOX_CAPACITY_WARNING"
    SANDBOX_CAPACITY_CRITICAL = "SANDBOX_CAPACITY_CRITICAL"
    SANDBOX_CAPACITY_RESOLVED = "SANDBOX_CAPACITY_RESOLVED"
    # Indexing: a model provider key put on hold after several documents kept failing on it, and its end.
    INDEXING_PROVIDER_ON_HOLD = "INDEXING_PROVIDER_ON_HOLD"
    INDEXING_PROVIDER_HOLD_RELEASED = "INDEXING_PROVIDER_HOLD_RELEASED"

class NotificationStatus(str, Enum):
    UNREAD = "unread"
    READ = "read"
    ARCHIVED = "archived"

class NotificationRecipientRole(str, Enum):
    ADMIN = "admin"
    STANDARD = "standard"
    EVERYONE = "everyone"

class NotificationAudience(str, Enum):
    """Who an event is for when it names no org. Node resolves the users."""

    PLATFORM_ADMINS = "platformAdmins"

class NotificationOutcome(str, Enum):
    """What ``BaseConnector.notify_and_wait`` did with a notification."""

    SENT = "sent"
    SUPPRESSED = "suppressed"
    SKIPPED = "skipped"
    FAILED = "failed"
