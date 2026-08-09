"""`IRateLimiter` -- generic request-rate gate. First real caller is the
webhook ingress endpoint (Part E of the plan: "Python has neither [rate
limiter nor tightened CORS] today"), but the port itself is deliberately
generic (a `key` + `limit`/`window_seconds`) so it isn't webhook-specific.
"""
from __future__ import annotations

from abc import ABC, abstractmethod


class IRateLimiter(ABC):
    @abstractmethod
    async def allow(self, key: str, *, limit: int, window_seconds: int) -> bool:
        """Fixed-window rate check: returns `True` if `key` has made fewer
        than `limit` calls to this method within the current
        `window_seconds`-wide window, `False` (and still counts the call)
        otherwise. Must be atomic under concurrent callers."""
        ...
