"""One alert per level per cooldown and scope, kept in the configuration store.

The claim is ``create_config_if_absent`` with a TTL, which is atomic on Redis,
etcd and PostgreSQL, so restarts, a new leader or racing workers never send
the same level twice. A claim marked resolved (the back-to-normal notice was
sent) is taken over by the next alert, so a new overload is reported the
same day.
"""

from __future__ import annotations

import math
import socket
import time
from typing import TYPE_CHECKING, Any

from app.config.constants.service import config_node_constants

if TYPE_CHECKING:
    from collections.abc import Callable

    from app.config.configuration_service import ConfigurationService

STATE_CLAIMED = "claimed"
STATE_RESOLVED = "resolved"


class AlertCooldown:
    def __init__(
        self,
        config_service: ConfigurationService,
        scope: str,
        cooldown_s: int,
        *,
        clock: Callable[[], float] = time.time,
        host: str | None = None,
    ) -> None:
        self._config = config_service
        self._scope = scope
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._host = host or socket.gethostname()

    def key(self, level: str) -> str:
        return f"{config_node_constants.SANDBOX_ALERTS.value}/{self._scope}/{level}"

    async def claim(self, level: str, alert_id: str) -> bool:
        """Whether this call now owns *level* for the cooldown. Store failures
        raise: the caller must not send without a claim."""
        value = {
            "alertId": alert_id,
            "host": self._host,
            "level": level,
            "claimedAt": int(self._clock() * 1000),
            "cooldownS": self._cooldown_s,
            "state": STATE_CLAIMED,
        }
        key = self.key(level)
        if await self._config.create_config_if_absent(key, value, ttl_seconds=self._cooldown_s):
            return True
        current = await self.read(level)
        if current is None or current.get("state") != STATE_RESOLVED:
            return False
        # The store has no compare-and-set; only the leader of this scope
        # writes these keys, and the create below is still atomic.
        await self._config.delete_config(key)
        return await self._config.create_config_if_absent(key, value, ttl_seconds=self._cooldown_s)

    async def read(self, level: str) -> dict[str, Any] | None:
        value = await self._config.get_config(self.key(level), use_cache=False, raise_on_error=True)
        return value if isinstance(value, dict) else None

    async def release_if_ours(self, level: str, alert_id: str) -> bool:
        """Delete *level*'s claim if it is still *alert_id*'s, so the next
        evaluation can try again after a failed publish."""
        current = await self.read(level)
        if current is None or current.get("alertId") != alert_id:
            return False
        return await self._config.delete_config(self.key(level))

    async def mark_resolved(self, level: str, current: dict[str, Any]) -> bool:
        """Record that *level*'s alert was followed by a back-to-normal
        notice, keeping the claim's original expiry."""
        claimed_at = current.get("claimedAt")
        cooldown_s = current.get("cooldownS", self._cooldown_s)
        if not isinstance(claimed_at, (int, float)) or not isinstance(cooldown_s, (int, float)):
            remaining = self._cooldown_s
        else:
            remaining = claimed_at / 1000 + cooldown_s - self._clock()
        if remaining <= 0:
            return False
        value = {**current, "state": STATE_RESOLVED}
        return await self._config.set_config(self.key(level), value, ttl_seconds=math.ceil(remaining))
