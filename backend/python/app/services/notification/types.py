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
    # Agent toolsets ("Actions") — distinct from connectors.
    TOOLSET_AUTH_ERROR = "TOOLSET_AUTH_ERROR"
    # Task Scheduling Engine (app.services.tasks) — see
    # app/services/tasks/adapters/messaging/notifier.py.
    TASK_RUN_FAILED = "TASK_RUN_FAILED"
    TASK_RUN_SUCCEEDED = "TASK_RUN_SUCCEEDED"
    TASK_AWAITING_INPUT = "TASK_AWAITING_INPUT"
    TASK_PREREQUISITE_MISSING = "TASK_PREREQUISITE_MISSING"
    TASK_AUTO_DISABLED = "TASK_AUTO_DISABLED"
    TASK_DLQ = "TASK_DLQ"
    WORKFLOW_RUN_STARTED = "WORKFLOW_RUN_STARTED"
    WORKFLOW_RUN_SUCCEEDED = "WORKFLOW_RUN_SUCCEEDED"
    WORKFLOW_RUN_FAILED = "WORKFLOW_RUN_FAILED"
    WORKFLOW_AWAITING_APPROVAL = "WORKFLOW_AWAITING_APPROVAL"

class NotificationStatus(str, Enum):
    UNREAD = "unread"
    READ = "read"
    ARCHIVED = "archived"

class NotificationRecipientRole(str, Enum):
    ADMIN = "admin"
    STANDARD = "standard"
    EVERYONE = "everyone"