"""
Feature Flag Service Module

A singleton service for managing feature flags with extensible architecture.

Current Features:
- Reads from .env file
- Singleton pattern
- Simple flag checking

Future-Ready Architecture for:
- Multi-environment support (dev, stage, prod)
- HTTP header overrides
- etcd integration
- User/Org/Percentage based targeting

Usage:
    from app.services.featureflag.featureflag import FeatureFlagService
    from app.services.featureflag.config.config import CONFIG

    is_enabled = FeatureFlagService.get_service().is_feature_enabled(CONFIG.ENABLE_WORKFLOW_BUILDER)
"""

import os
from logging import Logger
from threading import Lock
from typing import Optional

from app.services.featureflag.interfaces.config import IConfigProvider
from app.services.featureflag.provider.env import EnvFileProvider
from app.services.featureflag.provider.etcd import EtcdProvider

DEFAULT_ENV_PATH = '../../../.env'

class FeatureFlagService:
    """
    Singleton service for managing feature flags
    """

    _instance: Optional['FeatureFlagService'] = None
    _lock: Lock = Lock()

    def __init__(self, provider: IConfigProvider) -> None:
        """
        Private constructor - use get_service() instead

        Args:
            provider: Configuration provider implementing IConfigProvider
        """
        if FeatureFlagService._instance is not None:
            raise RuntimeError("Use get_service() to get the singleton instance")

        self._provider = provider

    @classmethod
    def get_service(cls, provider: Optional[IConfigProvider] = None) -> 'FeatureFlagService':
        """
        Get or create the singleton instance (thread-safe)

        Args:
            provider: Optional provider for first-time initialization

        Returns:
            FeatureFlagService singleton instance
        """
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    # Default to EnvFileProvider if no provider specified
                    if provider is None:
                        # Get default .env path relative to this file
                        # This file is at: backend/python/app/services/featureflag/featureflag.py
                        # .env.template is at: backend/python/.env.template
                        default_env_path = os.path.join(
                            os.path.dirname(os.path.abspath(__file__)),
                            DEFAULT_ENV_PATH
                        )
                        env_path = os.getenv(
                            'FEATURE_FLAG_ENV_PATH',
                            default_env_path
                        )
                        provider = EnvFileProvider(env_path)

                    cls._instance = cls(provider)

        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset singleton instance (useful for testing)"""
        with cls._lock:
            cls._instance = None

    @classmethod
    async def init_with_etcd_provider(
        cls,
        provider: EtcdProvider,
        logger: Logger,
    ) -> 'FeatureFlagService':
        """
        Initialize the singleton to use EtcdProvider as the provider.

        The provider is refreshed once during initialization.
        """
        with cls._lock:
            try:
                await provider.refresh()
            except Exception as e:
                logger.debug(f"Feature flag provider refresh failed: {e}")
                pass
            cls._instance = cls(provider)
            return cls._instance

    def is_feature_enabled(self, flag_name: str, default: bool = False) -> bool:
        """
        Check if a feature flag is enabled

        Args:
            flag_name: Name of the feature flag (e.g., 'ENABLE_WORKFLOW_BUILDER')
            default: Default value if flag is not found

        Returns:
            bool: True if feature is enabled, False otherwise
        """
        value = self._provider.get_flag_value(flag_name)
        return value if value is not None else default

    async def refresh(self) -> None:
        """Refresh feature flags from the provider"""
        await self._provider.refresh()

    def set_provider(self, provider: IConfigProvider) -> None:
        """
        Set a new configuration provider (Dependency Injection)

        Allows runtime switching of providers for:
        - Testing
        - Migrating from .env to etcd
        - Adding header override layer
        Args:
            provider: New configuration provider
        """
        self._provider = provider
