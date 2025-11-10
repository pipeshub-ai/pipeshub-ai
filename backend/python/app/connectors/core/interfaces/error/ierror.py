from abc import ABC, abstractmethod
from typing import Any


class IErrorHandlingService(ABC):
    """Base interface for error handling"""

    @abstractmethod
    def handle_api_error(self, error: Exception, context: dict[str, Any]) -> Exception:
        """Handle API errors and convert to appropriate exceptions"""

    @abstractmethod
    def handle_rate_limit_error(
        self, error: Exception, context: dict[str, Any]
    ) -> Exception:
        """Handle rate limit errors"""

    @abstractmethod
    def handle_authentication_error(
        self, error: Exception, context: dict[str, Any]
    ) -> Exception:
        """Handle authentication errors"""

    @abstractmethod
    def log_error(
        self, error: Exception, operation: str, context: dict[str, Any] = None
    ) -> None:
        """Log error with context"""
