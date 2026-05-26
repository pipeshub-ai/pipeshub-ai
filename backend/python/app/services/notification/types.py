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
    CONNECTOR_SYNC_ERROR = "CONNECTOR_SYNC_ERROR"
    CONNECTOR_USER_SYNC_ERROR = "CONNECTOR_USER_SYNC_ERROR"
    CONNECTOR_GROUP_SYNC_ERROR = "CONNECTOR_GROUP_SYNC_ERROR"
    CONNECTOR_ROLE_SYNC_ERROR = "CONNECTOR_ROLE_SYNC_ERROR"
    CONNECTOR_RG_SYNC_ERROR = "CONNECTOR_RG_SYNC_ERROR"
    CONNECTOR_RECORD_SYNC_ERROR = "CONNECTOR_RECORD_SYNC_ERROR"
    CONNECTOR_STREAM_ERROR = "CONNECTOR_STREAM_ERROR"
    CONNECTOR_INFO = "CONNECTOR_INFO"
    CONNECTOR_WARNING = "CONNECTOR_WARNING"
    CONNECTOR_SUCCESS = "CONNECTOR_SUCCESS"