from __future__ import annotations

from typing import Optional

from app.config.configuration_service import ConfigurationService
from app.services.featureflag.interfaces.config import IConfigProvider
from app.utils.logger import create_logger

logger = create_logger(__name__)


class EtcdProvider(IConfigProvider):
    """
    Provider that reads feature flags from the distributed configuration store
    via ConfigurationService.

    Expects a JSON object at key '/services/platform/settings' with shape:
      {
        "fileUploadMaxSizeBytes": number,
        "featureFlags": { [flag: string]: boolean }
      }
    """

    SETTINGS_KEY = "/services/platform/settings"

    def __init__(self, config_service: ConfigurationService) -> None:
        self._config_service = config_service
        self._flags: dict[str, bool] = {}

    async def refresh(self) -> None:
        """Refresh feature flags from ConfigurationService."""
        try:
            settings = await self._config_service.get_config(self.SETTINGS_KEY, default={})
            logger.debug("Settings: %s", settings)
            # Extract and validate feature flags
            feature_flags = settings.get("featureFlags", {}) if isinstance(settings, dict) else {}
            logger.debug("Feature flags: %s", feature_flags)
            if isinstance(feature_flags, dict):
                # Normalize keys to upper-case for consistency
                self._flags = {str(k).upper(): bool(v) for k, v in feature_flags.items()}
                logger.debug("Feature flags refreshed: %s", list(self._flags.keys()))
            else:
                logger.warning("Invalid featureFlags format, expected dict, got %s", type(feature_flags))
                self._flags = {}

        except Exception as e:
            logger.error("Failed to refresh feature flags: %s", str(e), exc_info=True)
            # Keep existing flags on error rather than clearing them
            # This provides better availability during transient failures

    def get_flag_value(self, flag_name: str) -> Optional[bool]:
        """Get the value of a feature flag by name (case-insensitive)."""
        return self._flags.get(str(flag_name).upper())

    def get_all_flags(self) -> dict[str, bool]:
        """Get all feature flags as a dictionary."""
        return self._flags.copy()
