import logging
from typing import Any, Dict, List, Optional, Tuple

from app.connectors.core.base.user_service.user_service import BaseUserService
from app.connectors.core.interfaces.connector.iconnector_config import ConnectorConfig
from app.connectors.core.interfaces.rate_limiter.irate_limiter import IRateLimiter


class S3UserService(BaseUserService):
    """User service for S3 connector"""

    def __init__(self, logger: logging.Logger, rate_limiter: IRateLimiter, config: ConnectorConfig, credentials: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(logger, rate_limiter, config)
        self.credentials = credentials

    async def _perform_connection(self) -> bool:
        """Perform the actual connection to the S3 service"""
        return True

    async def _cleanup_service(self) -> None:
        """Clean up service resources"""
        pass

    async def _fetch_user_info(self, org_id: str) -> List[Dict[str, Any]]:
        """Fetch user information"""
        return []

    async def _setup_monitoring(self, token: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """Setup change monitoring"""
        return None

    async def _stop_monitoring(self, channel_id: str, resource_id: str) -> bool:
        """Stop change monitoring"""
        return True

    async def _fetch_changes(self, page_token: str) -> Tuple[List[Dict[str, Any]], Optional[str]]:
        """Fetch changes"""
        return [], None

    async def _fetch_start_page_token(self) -> Optional[str]:
        """Fetch start page token"""
        return None

    def get_service_info(self) -> Dict[str, Any]:
        """Get service information"""
        return {
            "connected": self._connected,
            "org_id": self._org_id,
            "user_id": self._user_id,
            "service_type": self.__class__.__name__,
            "rate_limiter_info": self.rate_limiter.get_rate_limit_info()
        }
