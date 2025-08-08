"""
Factory for creating key-value service instances.
This provides a centralized way to create different key-value services.
"""

import logging
from typing import Optional

from app.config.configuration_service import ConfigurationService
from app.services.key_value.interface.key_value import IKeyValueService
from app.services.key_value.redis.redis import RedisService


class KeyValueFactory:
    """Factory for creating key-value service instances"""

    @staticmethod
    async def create_redis_service(
        logger: logging.Logger,
        config_service: ConfigurationService
    ) -> RedisService:
        """
        Create a RedisService instance using the factory method.
        Args:
            logger: Logger instance
            config_service: ConfigurationService instance
        Returns:
            RedisService: Initialized RedisService instance
        """
        return await RedisService.create(logger, config_service)

    @staticmethod
    async def create_service(
        service_type: str,
        logger: logging.Logger,
        config_service: ConfigurationService
    ) -> Optional[IKeyValueService]:
        """
        Create a key-value service based on the service type.
        Args:
            service_type: Type of service to create ('redis', etc.)
            logger: Logger instance
            config_service: ConfigurationService instance
        Returns:
            IKeyValueService: Initialized key-value service instance
        """
        if service_type.lower() == "redis":
            return await KeyValueFactory.create_redis_service(logger, config_service)
        else:
            logger.error(f"Unsupported key-value service type: {service_type}")
            return None
