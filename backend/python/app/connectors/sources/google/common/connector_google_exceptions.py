from typing import Any, Dict, List, Optional


class GoogleConnectorError(Exception):
    """Base exception for Google connector errors"""

    def __init__(self, message: str, details: Optional[Dict[str, Any]] = None) -> None:
        self.message = message
        self.details = details or {}
        super().__init__(self.message)


class GoogleAuthError(GoogleConnectorError):
    """Raised when there's an authentication error with Google services"""

    pass


class GoogleDriveError(GoogleConnectorError):
    """Base exception for Google Drive specific errors"""

    pass


class GoogleMailError(GoogleConnectorError):
    """Base exception for Gmail specific errors"""

    pass


class DriveOperationError(GoogleDriveError):
    """Raised when a Drive operation fails"""

    pass


class DrivePermissionError(GoogleDriveError):
    """Raised when there's a permission issue with Drive operations"""

    pass


class DriveSyncError(GoogleDriveError):
    """Raised when Drive sync operations fail"""

    pass


class MailOperationError(GoogleMailError):
    """Raised when a Gmail operation fails"""

    pass


class MailSyncError(GoogleMailError):
    """Raised when Gmail sync operations fail"""

    pass


class MailThreadError(GoogleMailError):
    """Raised when Gmail thread operations fail"""

    pass


class AdminOperationError(GoogleConnectorError):
    """Raised when Google Admin operations fail"""

    pass


class UserOperationError(GoogleConnectorError):
    """Raised when user-related operations fail"""

    pass


class BatchOperationError(GoogleConnectorError):
    """Raised when batch operations fail"""

    def __init__(
        self,
        message: str,
        failed_items: List[Dict[str, Any]],
        details: Optional[Dict[str, Any]] = None,
    ) -> None:
        super().__init__(message, details)
        self.failed_items = failed_items


class AdminServiceError(GoogleConnectorError):
    """Base exception for admin service errors"""

    pass


class AdminAuthError(AdminServiceError):
    """Raised when there's an authentication error with admin services"""

    pass


class AdminListError(AdminServiceError):
    """Raised when listing resources (users, groups, domains) fails"""

    pass


class AdminDelegationError(AdminServiceError):
    """Raised when domain-wide delegation fails"""

    pass


class AdminQuotaError(AdminServiceError):
    """Raised when hitting API quotas"""

    pass
