import logging
from abc import ABC
from typing import Any, Dict

from app.connectors.core.interfaces.error.ierror import IErrorHandlingService


class BaseErrorHandlingService(IErrorHandlingService, ABC):
    """Base error handling service with common functionality"""

    def __init__(self, logger: logging.Logger) -> None:
        self.logger = logger

    def handle_api_error(self, error: Exception, context: Dict[str, Any]) -> Exception:
        """Handle API errors and convert to appropriate exceptions"""
        self.logger.error(f"API Error: {str(error)}", extra=context)
        return error

    def handle_rate_limit_error(self, error: Exception, context: Dict[str, Any]) -> Exception:
        """Handle rate limit errors"""
        self.logger.warning(f"Rate limit error: {str(error)}", extra=context)
        return error

    def handle_authentication_error(self, error: Exception, context: Dict[str, Any]) -> Exception:
        """Handle authentication errors"""
        self.logger.error(f"Authentication error: {str(error)}", extra=context)
        return error

    def log_error(self, error: Exception, operation: str, context: Dict[str, Any] = None) -> None:
        """Log error with context"""
        self.logger.error(f"Error: {str(error)}", extra=context)
