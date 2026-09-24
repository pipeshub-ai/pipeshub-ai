import asyncio
import json
import logging
from typing import Dict, Optional

from app.config.configuration_service import ConfigurationService
from app.services.key_value.interface.key_value import IKeyValueService
from app.services.redis.config import ClientOptions, RedisConnectionConfig
from app.services.redis.connection_provider_factory import get_redis_provider


class RedisService(IKeyValueService):
    """Service for handling Redis operations"""

    def __init__(self, logger: logging.Logger, redis_client, config: ConfigurationService) -> None:
        self.logger = logger
        self.config = config
        self.redis_client = redis_client
        self.prefix = "redis_service:"  # Namespace for our keys
        self._state_lock = asyncio.Lock()

    @classmethod
    async def create(cls, logger: logging.Logger, config_service: ConfigurationService) -> 'RedisService':
        """
        Factory method to create and initialize a RedisService instance.
        Args:
            logger: Logger instance
            config_service: ConfigurationService instance
        Returns:
            RedisService: Initialized RedisService instance
        """
        try:
            # Get typed Redis configuration and build a client through the
            # connection provider -- never `redis.asyncio.from_url()` directly,
            # so REDIS_MODE=cluster (or an EE MemoryDB mode) works with no
            # change to this class.
            redis_config = await config_service.get_redis_config()
            provider = get_redis_provider(
                RedisConnectionConfig.from_host_port(
                    host=redis_config.host,
                    port=redis_config.port,
                    password=redis_config.password,
                    db=redis_config.db,
                    tls=redis_config.tls,
                )
            )
            redis_client = provider.create_client(ClientOptions(decode_responses=True))
            service = cls(logger, redis_client, config_service)
            connected = await service.connect()
            if not connected:
                raise Exception("Failed to connect to Redis")

            return service

        except Exception as e:
            logger.error(f"Failed to create RedisService: {str(e)}")
            raise

    async def connect(self) -> bool:
        """Connect to Redis"""
        try:
            if self.redis_client is None:
                return False
            # Test connection by pinging
            await self.redis_client.ping()
            self.logger.info("✅ Successfully connected to Redis")
            return True
        except Exception as e:
            self.logger.error(f"Failed to connect to Redis: {str(e)}")
            return False

    async def disconnect(self) -> bool:
        """Disconnect from Redis"""
        if self.redis_client:
            await self.redis_client.close()
            self.redis_client = None
            self.logger.info("✅ Disconnected from Redis")
            return True
        return False

    async def set(self, key: str, value: str, expire: int = 86400) -> bool:
        """Set a key with optional expiration (default 24 hours)"""
        try:
            if self.redis_client is None:
                raise ValueError("Redis client is not connected")
            full_key = f"{self.prefix}{key}"
            if isinstance(value, (dict, list)):
                value = json.dumps(value)
            await self.redis_client.set(full_key, value, ex=expire)
            return True
        except Exception as e:
            self.logger.error(f"Failed to set Redis key {key}: {str(e)}")
            return False

    async def get(self, key: str) -> Optional[str]:
        """Get a key's value"""
        try:
            if self.redis_client is None:
                raise ValueError("Redis client is not connected")
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
            if self.redis_client is None:
                raise ValueError("Redis client is not connected")
            full_key = f"{self.prefix}{key}"
            await self.redis_client.delete(full_key)
            return True
        except Exception as e:
            self.logger.error(f"Failed to delete Redis key {key}: {str(e)}")
            return False

    async def store_progress(self, progress: Dict) -> bool:
        """Store sync progress"""
        return await self.set("sync_progress", json.dumps(progress))

    async def get_progress(self) -> Optional[Dict]:
        """Get sync progress"""
        progress = await self.get("sync_progress")
        if progress:
            return json.loads(progress)
        return None
