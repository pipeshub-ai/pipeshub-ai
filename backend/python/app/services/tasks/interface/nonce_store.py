"""`INonceStore` -- replay protection for inbound webhook requests.

Separate from `IRateLimiter`: rate limiting bounds *volume* from a source,
nonce tracking rejects a *specific request being replayed* (e.g. a captured
request replayed by a network attacker, or a naive retry that resends the
exact same signed payload). Both are required per Part E of the plan;
neither substitutes for the other.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class INonceStore(ABC):
    @abstractmethod
    async def check_and_set(self, scope: str, nonce: str, *, ttl_seconds: int) -> bool:
        """Atomically records `nonce` within `scope` (e.g. a webhook_id) and
        returns `True` if this is the first time it has been seen within
        `ttl_seconds`, `False` if it's a replay. Must be atomic under
        concurrent callers -- a naive get-then-set race would let two
        simultaneous replays both pass."""
        ...
