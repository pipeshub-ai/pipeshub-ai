from typing import Any


class GoogleConnectorError(Exception):
    """Base exception for Google connector errors"""

    def __init__(self, message: str, details: dict[str, Any] | None = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class GoogleAuthError(GoogleConnectorError):
    """Raised when there's an authentication error with Google services"""


class GoogleDriveError(GoogleConnectorError):
    """Base exception for Google Drive specific errors"""


class GoogleMailError(GoogleConnectorError):
    """Base exception for Gmail specific errors"""


class DriveOperationError(GoogleDriveError):
    """Raised when a Drive operation fails"""


class DrivePermissionError(GoogleDriveError):
    """Raised when there's a permission issue with Drive operations"""


class DriveSyncError(GoogleDriveError):
    """Raised when Drive sync operations fail"""


class MailOperationError(GoogleMailError):
    """Raised when a Gmail operation fails"""


class MailSyncError(GoogleMailError):
    """Raised when Gmail sync operations fail"""


class MailThreadError(GoogleMailError):
    """Raised when Gmail thread operations fail"""


class AdminOperationError(GoogleConnectorError):
    """Raised when Google Admin operations fail"""


class UserOperationError(GoogleConnectorError):
    """Raised when user-related operations fail"""


class BatchOperationError(GoogleConnectorError):
    """Raised when batch operations fail"""

    def __init__(
        self,
        message: str,
        failed_items: list[dict[str, Any]],
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message, details)
        self.failed_items = failed_items


class AdminServiceError(GoogleConnectorError):
    """Base exception for admin service errors"""


class AdminAuthError(AdminServiceError):
    """Raised when there's an authentication error with admin services"""


class AdminListError(AdminServiceError):
    """Raised when listing resources (users, groups, domains) fails"""


class AdminDelegationError(AdminServiceError):
    """Raised when domain-wide delegation fails"""


class AdminQuotaError(AdminServiceError):
    """Raised when hitting API quotas"""
