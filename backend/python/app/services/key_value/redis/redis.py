import asyncio
import json
import logging
from typing import Dict, Optional, Union

from redis.asyncio import Redis
from redis.asyncio.cluster import RedisCluster

from app.config.configuration_service import ConfigurationService
from app.config.constants.service import config_node_constants
from app.services.key_value.interface.key_value import IKeyValueService
from app.utils.redis_util import create_redis_client, is_cluster_mode


class RedisService(IKeyValueService):
    """Service for handling Redis operations.
    
    Supports both standalone Redis and Redis Cluster modes.
    """

    def __init__(
        self,
        logger: logging.Logger,
        redis_client: Union[Redis, RedisCluster],
        config: ConfigurationService,
        cluster_mode: bool = False,
    ) -> None:
        self.logger = logger
        self.config = config
        self.redis_client = redis_client
        self.cluster_mode = cluster_mode
        self.prefix = "redis_service:"  # Namespace for our keys
        self._state_lock = asyncio.Lock()

    @classmethod
    async def create(cls, logger: logging.Logger, config_service: ConfigurationService) -> 'RedisService':
        """
        Factory method to create and initialize a RedisService instance.
        
        Supports both standalone Redis and Redis Cluster modes based on configuration.
        
        Args:
            logger: Logger instance
            config_service: ConfigurationService instance
        Returns:
            RedisService: Initialized RedisService instance
        """
        try:
            # Get Redis configuration
            redis_config = await config_service.get_config(config_node_constants.REDIS.value)
            if not redis_config or not isinstance(redis_config, dict):
                raise ValueError("Redis configuration not found")
            
            # Create Redis client (handles both standalone and cluster)
            cluster_mode = is_cluster_mode(redis_config)
            redis_client = await create_redis_client(
                redis_config,
                decode_responses=True,
                encoding="utf-8",
            )
            
            service = cls(logger, redis_client, config_service, cluster_mode)
            connected = await service.connect()
            if not connected:
                raise Exception("Failed to connect to Redis")

            mode_str = "Cluster" if cluster_mode else "Standalone"
            logger.info(f"✅ RedisService created in {mode_str} mode")
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

