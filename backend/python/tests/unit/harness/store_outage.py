"""The production ``ConfigurationService`` over a key-value store that is down.

A fake ``get_config`` that raises proves nothing: the real service answers ``default``
on a store failure unless the caller passes ``raise_on_error=True`` (F3 found two
readers whose ``except`` could never fire).
"""

from __future__ import annotations

import logging
import threading

from app.config.configuration_service import ConfigurationService


class DownStore:
    """As the encrypted stores report an outage: ``None`` unless asked to raise."""

    async def get_key(self, key: str, *, raise_on_error: bool = False) -> None:
        if raise_on_error:
            raise ConnectionError(f"kv down reading {key}")


def outage_configuration_service() -> ConfigurationService:
    config = object.__new__(ConfigurationService)
    config.logger = logging.getLogger("tests.store_outage")
    config.store = DownStore()  # pyright: ignore[reportAttributeAccessIssue]
    config.cache = {}  # pyright: ignore[reportAttributeAccessIssue]
    config._cache_lock = threading.Lock()  # main's ConfigurationService guards its LRU cache with a lock
    config._cached_at = {}  # and keeps when each entry was stored (max_age_seconds)
    return config
