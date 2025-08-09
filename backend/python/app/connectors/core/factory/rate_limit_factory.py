import logging
from typing import Dict, Optional, Type

from app.connectors.core.base.rate_limiter.no_op_rate_limiter import NoOpRateLimiter
from app.connectors.core.interfaces.rate_limiter.irate_limiter import IRateLimiter
from app.connectors.enums.enums import ConnectorType


class RateLimiterFactory:
    """
    Factory for creating rate limiters based on connector type and requirements.
    This factory follows the Factory pattern to create appropriate rate limiters
    for different connectors based on their specific needs.
    """

    def __init__(self, logger: logging.Logger) -> None:
        """
        Initialize the rate limiter factory.
        Args:
            logger (logging.Logger): Logger instance
        """
        self.logger = logger
        self._rate_limiter_registry: Dict[ConnectorType, Type[IRateLimiter]] = {}
        self._rate_limiter_configs: Dict[ConnectorType, Dict] = {}

    def register_rate_limiter(
        self,
        connector_type: ConnectorType,
        rate_limiter_class: Type[IRateLimiter],
        config: Optional[Dict] = None
    ) -> None:
        """
        Register a rate limiter implementation for a connector type.
        Args:
            connector_type (ConnectorType): The connector type
            rate_limiter_class (Type[IRateLimiter]): The rate limiter class
            config (Optional[Dict]): Configuration for the rate limiter
        """
        self._rate_limiter_registry[connector_type] = rate_limiter_class
        self._rate_limiter_configs[connector_type] = config or {}

        self.logger.info(
            f"Registered rate limiter '{rate_limiter_class.__name__}' "
            f"for connector type '{connector_type.value}'"
        )

    def create_rate_limiter(
        self,
        connector_type: ConnectorType,
        custom_config: Optional[Dict] = None
    ) -> IRateLimiter:
        """
        Create a rate limiter for the specified connector type.
        Args:
            connector_type (ConnectorType): The connector type
            custom_config (Optional[Dict]): Custom configuration to override defaults
        Returns:
            IRateLimiter: The created rate limiter instance
        """
        # Check if a specific rate limiter is registered for this connector type
        if connector_type in self._rate_limiter_registry:
            rate_limiter_class = self._rate_limiter_registry[connector_type]
            config = custom_config or self._rate_limiter_configs.get(connector_type, {})

            self.logger.info(
                f"Creating registered rate limiter '{rate_limiter_class.__name__}' "
                f"for connector type '{connector_type.value}'"
            )

            return rate_limiter_class(self.logger, **config)

        # Default to NoOpRateLimiter if no specific rate limiter is registered
        self.logger.info(
            f"No specific rate limiter registered for connector type '{connector_type.value}'. "
            f"Using NoOpRateLimiter"
        )

        return NoOpRateLimiter(logger=self.logger)

    def get_registered_rate_limiters(self) -> Dict[ConnectorType, Type[IRateLimiter]]:
        """
        Get all registered rate limiters.
        Returns:
            Dict[ConnectorType, Type[IRateLimiter]]: Dictionary of registered rate limiters
        """
        return self._rate_limiter_registry.copy()

    def is_rate_limiter_registered(self, connector_type: ConnectorType) -> bool:
        """
        Check if a rate limiter is registered for the connector type.
        Args:
            connector_type (ConnectorType): The connector type to check
        Returns:
            bool: True if registered, False otherwise
        """
        return connector_type in self._rate_limiter_registry

    def unregister_rate_limiter(self, connector_type: ConnectorType) -> bool:
        """
        Unregister a rate limiter for a connector type.
        Args:
            connector_type (ConnectorType): The connector type to unregister
        Returns:
            bool: True if unregistered, False if not found
        """
        if connector_type in self._rate_limiter_registry:
            del self._rate_limiter_registry[connector_type]
            if connector_type in self._rate_limiter_configs:
                del self._rate_limiter_configs[connector_type]

            self.logger.info(
                f"Unregistered rate limiter for connector type '{connector_type.value}'"
            )
            return True

        return False
