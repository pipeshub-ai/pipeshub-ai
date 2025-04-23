import asyncio
import json
from typing import Any, Dict, Optional

from app.config.configuration_service import ConfigurationService


class BaseRedisService:
    """Service for handling Redis operations"""

    def __init__(self, logger, redis_client, config: ConfigurationService):
        self.logger = logger
        self.config = config
        self.redis_client = redis_client
        self.prefix = "drive_sync:"  # Namespace for our keys
        self._state_lock = asyncio.Lock()

    async def connect(self) -> bool:
        """Connect to Redis"""
        try:
            if self.redis_client is None:
                return False
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Redis: {str(e)}")
            return False

    async def disconnect(self):
        """Disconnect from Redis"""
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None

    async def set(self, key: str, value: Any, expire: int = 86400) -> bool:
        """Set a key with optional expiration (default 24 hours)"""
        try:
            full_key = f"{self.prefix}{key}"
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            await self.redis_client.set(full_key, value, ex=expire)
            return True
        except Exception as e:
            self.logger.error(f"Failed to set Redis key {key}: {str(e)}")
            return False

    async def get(self, key: str) -> Optional[Any]:
        """Get a key's value"""
        try:
            full_key = f"{self.prefix}{key}"
            value = await self.redis_client.get(full_key)
            if value and value.startswith("{") or value.startswith("["):
                return json.loads(value)
            return value
        except Exception as e:
            self.logger.error(f"Failed to get Redis key {key}: {str(e)}")
            return None

    async def delete(self, key: str) -> bool:
        """Delete a key"""
        try:
            full_key = f"{self.prefix}{key}"
            await self.redis_client.delete(full_key)
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete Redis key {key}: {str(e)}")
            return False

    async def store_progress(self, progress: Dict) -> bool:
        """Store sync progress"""
        return await self.set("sync_progress", progress)

    async def get_progress(self) -> Optional[Dict]:
        """Get sync progress"""
        return await self.get("sync_progress")
