from abc import ABC, abstractmethod
from enum import Enum
from typing import Any, Dict, Optional


class AuthenticationType(Enum):
    """Enumeration of authentication types"""
    OAUTH2 = "OAUTH2"
    API_KEY = "API_KEY"
    BEARER_TOKEN = "BEARER_TOKEN"
    BASIC_AUTH = "BASIC_AUTH"
    SERVICE_ACCOUNT = "SERVICE_ACCOUNT"
    SAML = "SAML"
    LDAP = "LDAP"

class IAuthenticationService(ABC):
    """Base interface for authentication services"""

    @abstractmethod
    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        """Authenticate with the service"""
        pass

    @abstractmethod
    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh authentication token"""
        pass

    @abstractmethod
    async def validate_token(self, token: str) -> bool:
        """Validate current token"""
        pass

    @abstractmethod
    async def revoke_token(self, token: str) -> bool:
        """Revoke authentication token"""
        pass

    @abstractmethod
    def get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for API calls"""
        pass

    @abstractmethod
    def get_service(self) -> Optional[object]:
        """Get the underlying service instance (e.g., aioboto3.Session for AWS)"""
        pass

