from __future__ import annotations

import asyncio
from typing import Any, Dict, Optional

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
        self._flags: Dict[str, bool] = {}

    def _blocking_fetch_settings(self) -> Optional[Dict[str, Any]]:
        """Fetch settings using the event loop in a safe blocking manner."""
        try:
            try:
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    # Running inside an event loop (e.g., FastAPI/uvicorn):
                    # create a new loop in a separate thread
                    result: Optional[Dict[str, Any]] = None

                    def run_in_new_loop() -> None:
                        nonlocal result
                        new_loop = asyncio.new_event_loop()
                        try:
                            asyncio.set_event_loop(new_loop)
                            result = new_loop.run_until_complete(
                                self._config_service.get_config(self.SETTINGS_KEY, default={})
                            )
                        finally:
                            new_loop.close()

                    import threading

                    t = threading.Thread(target=run_in_new_loop)
                    t.start()
                    t.join()
                    return result
                # If no loop running, we can run synchronously
                return loop.run_until_complete(
                    self._config_service.get_config(self.SETTINGS_KEY, default={})
                )
            except RuntimeError:
                # No current event loop
                return asyncio.run(self._config_service.get_config(self.SETTINGS_KEY, default={}))
        except Exception as e:
            logger.error("Failed to fetch platform settings for feature flags: %s", str(e))
            return None

    def refresh(self) -> None:
        settings = self._blocking_fetch_settings() or {}
        feature_flags = settings.get("featureFlags") if isinstance(settings, dict) else None
        if isinstance(feature_flags, dict):
            # Normalize keys to upper-case (compatible with env provider)
            self._flags = {str(k).upper(): bool(v) for k, v in feature_flags.items()}
        else:
            self._flags = {}
        logger.debug("Feature flags refreshed from config service: %s", list(self._flags.keys()))

    def get_flag_value(self, flag_name: str) -> Optional[bool]:
        return self._flags.get(str(flag_name).upper())


