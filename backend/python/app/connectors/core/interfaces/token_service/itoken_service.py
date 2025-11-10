from abc import ABC, abstractmethod
from enum import Enum
from typing import Any


class TokenType(Enum):
    """Enumeration of token types"""

    OAUTH2 = "OAUTH2"
    API_KEY = "API_KEY"
    BEARER_TOKEN = "BEARER_TOKEN"
    BASIC_AUTH = "BASIC_AUTH"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT"
    SAML = "SAML"
    LDAP = "LDAP"


class ITokenService(ABC):
    """Base interface for token services"""

    @abstractmethod
    async def authenticate(self, credentials: dict[str, Any]) -> bool:
        """Authenticate with the service and return a token"""

    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> dict[str, Any]:
        """Refresh a token"""

    @abstractmethod
    async def validate_token(self, token: str) -> bool:
        """Validate a token"""

    @abstractmethod
    async def revoke_token(self, token: str) -> bool:
        """Revoke a token"""

    @abstractmethod
    def get_auth_headers(self) -> dict[str, str]:
        """Get headers for API calls"""

    @abstractmethod
    def get_service(self) -> object | None:
        """Get the underlying service instance"""
