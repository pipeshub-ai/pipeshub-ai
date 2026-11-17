"""One alert per level per cooldown and scope, kept in the configuration store.

The claim is ``create_config_if_absent`` with a TTL, which is atomic on Redis,
etcd and PostgreSQL, so restarts, a new leader or racing workers never send
the same level twice. A claim marked resolved (the back-to-normal notice was
sent) is taken over by the next alert, so a new overload is reported in-app
the same day. Email stays at most one per level per cooldown: the claim keeps
``emailedAt``, carried over when a resolved claim is taken over, and a
critical email also counts for the warning.

Every store call is bounded by ``timeout_s``; a call that runs out raises
``TimeoutError`` like any other store failure.
"""

from __future__ import annotations

import asyncio
import math
import socket
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, TypeVar

from app.config.constants.service import config_node_constants

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from app.config.configuration_service import ConfigurationService

_T = TypeVar("_T")

STATE_CLAIMED = "claimed"
STATE_RESOLVED = "resolved"
STORE_TIMEOUT_S = 5.0


@dataclass(frozen=True)
class AlertClaim:
    """A level this call now owns. ``email``: no email for this level (or a
    stronger one) went out within the cooldown, so this alert sends one.
    ``previous``: the resolved value it replaced, put back on release."""

    level: str
    alert_id: str
    email: bool
    emailed_at_ms: int
    previous: dict[str, Any] | None = None


def _ms(value: object) -> int | None:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


def emailed_at_ms(value: dict[str, Any] | None) -> int | None:
    """When the claim's level was last emailed. Older values without
    ``emailedAt`` were emailed when claimed."""
    if not value:
        return None
    emailed = _ms(value.get("emailedAt"))
    return emailed if emailed is not None else _ms(value.get("claimedAt"))


class AlertCooldown:
    def __init__(
        self,
        config_service: ConfigurationService,
        scope: str,
        cooldown_s: int,
        *,
        clock: Callable[[], float] = time.time,
        host: str | None = None,
        timeout_s: float = STORE_TIMEOUT_S,
    ) -> None:
        self._config = config_service
        self._scope = scope
        self._cooldown_s = cooldown_s
        self._clock = clock
        self._host = host or socket.gethostname()
        self._timeout_s = timeout_s

    def key(self, level: str) -> str:
        return f"{config_node_constants.SANDBOX_ALERTS.value}/{self._scope}/{level}"

    async def _call(self, awaitable: Awaitable[_T]) -> _T:
        return await asyncio.wait_for(awaitable, self._timeout_s)

    async def claim(self, level: str, alert_id: str) -> bool:
        """Whether this call now owns *level* for the cooldown. Store failures
        raise: the caller must not send without a claim."""
        return await self.claim_alert(level, alert_id) is not None

    async def claim_alert(
        self, level: str, alert_id: str, *, stronger_emailed_at_ms: int | None = None,
    ) -> AlertClaim | None:
        """Claim *level*, or ``None`` when it is already claimed. An email is
        due when neither this level's last email nor *stronger_emailed_at_ms*
        (a stronger level's) is within the cooldown."""
        now_ms = int(self._clock() * 1000)
        key = self.key(level)

        def value(previous: dict[str, Any] | None) -> tuple[dict[str, Any], AlertClaim]:
            known = [t for t in (emailed_at_ms(previous), stronger_emailed_at_ms) if t is not None]
            last = max(known) if known else None
            email = last is None or now_ms - last >= self._cooldown_s * 1000
            emailed = now_ms if email else last
            assert emailed is not None
            stored = {
                "alertId": alert_id,
                "host": self._host,
                "level": level,
                "claimedAt": now_ms,
                "cooldownS": self._cooldown_s,
                "state": STATE_CLAIMED,
                "emailedAt": emailed,
            }
            return stored, AlertClaim(level, alert_id, email, emailed, previous)

        stored, claim = value(None)
        if await self._call(self._config.create_config_if_absent(key, stored, ttl_seconds=self._cooldown_s)):
            return claim
        current = await self.read(level)
        if current is None or current.get("state") != STATE_RESOLVED:
            return None
        stored, claim = value(current)
        # The store has no compare-and-set; only the leader of this scope
        # writes these keys, and the create below is still atomic.
        await self._call(self._config.delete_config(key))
        created = await self._call(self._config.create_config_if_absent(key, stored, ttl_seconds=self._cooldown_s))
        return claim if created else None

    async def read(self, level: str) -> dict[str, Any] | None:
        value = await self._call(self._config.get_config(self.key(level), use_cache=False, raise_on_error=True))
        return value if isinstance(value, dict) else None

    async def release_if_ours(self, level: str, alert_id: str, previous: dict[str, Any] | None = None) -> bool:
        """Give up *level*'s claim if it is still *alert_id*'s, so the next
        evaluation can try again after a failed publish. A resolved value it
        replaced (*previous*) is put back with its remaining expiry, so the
        last email time is not lost."""
        current = await self.read(level)
        if current is None or current.get("alertId") != alert_id:
            return False
        if previous is not None and await self._restore(level, previous):
            return True
        if not await self._call(self._config.delete_config(self.key(level))):
            # The claim was there a moment ago: a delete that did not happen is
            # a store failure, which the caller retries.
            raise ConnectionError(f"could not delete the {level} claim")
        return True

    def _remaining_s(self, value: dict[str, Any]) -> float:
        claimed_at = value.get("claimedAt")
        cooldown_s = value.get("cooldownS", self._cooldown_s)
        if not isinstance(claimed_at, (int, float)) or not isinstance(cooldown_s, (int, float)):
            return self._cooldown_s
        return claimed_at / 1000 + cooldown_s - self._clock()

    async def _restore(self, level: str, value: dict[str, Any]) -> bool:
        remaining = self._remaining_s(value)
        if remaining <= 0:
            return False
        return await self._call(self._config.set_config(self.key(level), value, ttl_seconds=math.ceil(remaining)))

    async def mark_resolved(self, level: str, current: dict[str, Any]) -> bool:
        """Record that *level*'s alert was followed by a back-to-normal
        notice, keeping the claim's original expiry. A claim whose cooldown
        is already over is deleted instead, so it cannot hold back the next
        alert until the store expires it."""
        if self._remaining_s(current) <= 0:
            key = self.key(level)
            if await self._call(self._config.delete_config(key)):
                return True
            # False is both "already gone" and a store failure that did not
            # raise; only a key that is really absent counts as done.
            value = await self._call(self._config.get_config(key, use_cache=False, raise_on_error=True))
            return value is None
        return await self._restore(level, {**current, "state": STATE_RESOLVED})
