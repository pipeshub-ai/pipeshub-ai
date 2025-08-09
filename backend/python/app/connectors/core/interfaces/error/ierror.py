from abc import ABC, abstractmethod
from typing import Any, Dict


class IErrorHandlingService(ABC):
    """Base interface for error handling"""

    @abstractmethod
    def handle_api_error(self, error: Exception, context: Dict[str, Any]) -> Exception:
        """Handle API errors and convert to appropriate exceptions"""
        pass

    @abstractmethod
    def handle_rate_limit_error(self, error: Exception, context: Dict[str, Any]) -> Exception:
        """Handle rate limit errors"""
        pass

    @abstractmethod
    def handle_authentication_error(self, error: Exception, context: Dict[str, Any]) -> Exception:
        """Handle authentication errors"""
        pass

    @abstractmethod
    def log_error(self, error: Exception, operation: str, context: Dict[str, Any] = None) -> None:
        """Log error with context"""
        pass


