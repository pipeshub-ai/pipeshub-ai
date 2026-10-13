"""Five-minute discovery cache. The key is a hash so credentials are not stored in clear text."""

from __future__ import annotations

import hashlib
from typing import Any

from cachetools import TTLCache

TTL_SECONDS = 300
MAX_ENTRIES = 256


class DiscoveryCache:
    def __init__(self) -> None:
        self._entries: TTLCache[str, Any] = TTLCache(maxsize=MAX_ENTRIES, ttl=TTL_SECONDS)

    @staticmethod
    def key(
        provider: str,
        endpoint: str,
        credential: str,
        query: str,
        region: str = "",
        access_key_id: str = "",
    ) -> str:
        material = "\n".join((provider, endpoint, credential, region, access_key_id, query))
        return hashlib.sha256(material.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        return self._entries.get(key)

    def set(self, key: str, value: Any) -> None:
        self._entries[key] = value
