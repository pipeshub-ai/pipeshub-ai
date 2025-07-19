import logging
from abc import ABC
from typing import Any, Dict, Optional

from app.connectors.core.interfaces.auth.iauth_service import IAuthenticationService
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig


class BaseAuthenticationService(IAuthenticationService, ABC):
    """Base authentication service with common functionality"""

    def __init__(self, logger: logging.Logger, config: ConnectorConfig) -> None:
        self.logger = logger
        self.config = config
        self._token = None
        self._refresh_token = None
        self._token_expiry = None

    async def authenticate(self, credentials: Dict[str, Any]) -> bool:
        """Authenticate with the service"""
        try:
            # This should be implemented by specific auth services
            self.logger.info("Authenticating with service")
            return True
        except Exception as e:
            self.logger.error(f"Authentication failed: {str(e)}")
            return False

    async def refresh_token(self, refresh_token: str) -> Dict[str, Any]:
        """Refresh authentication token"""
        try:
            # This should be implemented by specific auth services
            self.logger.info("Refreshing token")
            return {"access_token": "new_token", "refresh_token": refresh_token}
        except Exception as e:
            self.logger.error(f"Token refresh failed: {str(e)}")
            raise

    async def validate_token(self, token: str) -> bool:
        """Validate current token"""
        try:
            # This should be implemented by specific auth services
            return token is not None and len(token) > 0
        except Exception as e:
            self.logger.error(f"Token validation failed: {str(e)}")
            return False

    async def revoke_token(self, token: str) -> bool:
        """Revoke authentication token"""
        try:
            # This should be implemented by specific auth services
            self.logger.info("Revoking token")
            return True
        except Exception as e:
            self.logger.error(f"Token revocation failed: {str(e)}")
            return False

    def get_auth_headers(self) -> Dict[str, str]:
        """Get authentication headers for API calls"""
        if self._token:
            return {"Authorization": f"Bearer {self._token}"}
        return {}

    def get_service(self) -> Optional[object]:
        """Get the underlying service instance (to be implemented by subclasses)"""
        return None
