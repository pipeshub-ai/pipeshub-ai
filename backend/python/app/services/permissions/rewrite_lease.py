"""`RewriteLease`: one rewriter per connector at a time, kept on the connector's app document.

The stores fence a held-up writer by time (`scopeFence`) and every request checks the connector's generation first; the lease is what
keeps a second rewriter of the same connector from starting at all (two would only undo each other's work, and one of them would fail its
roots). It is a lease, not a lock: it runs out, so a rewriter that died stops holding the connector. Expiry is read against the clock of
whoever asks, so skewed hosts can overlap near the deadline; the fence and the generation check are what keep that safe, the lease only keeps
it rare. A value that does not parse is taken over, never waited on.
"""

from __future__ import annotations

import json
import secrets
import time
from typing import TYPE_CHECKING, Any

from app.config.constants.arangodb import CollectionNames
from app.services.permissions.structure_generation import whole_number

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

REWRITE_LEASE_KEY = "scopeRewriteLease"
REWRITE_LEASE_SECONDS = 300  # renewed before every page; a page is bounded far below this (the virtual-record lock times out at 120 s)


class LeaseLost(Exception):
    """The connector's lease is held by another rewriter (it ran out and was taken): stop before the next write."""


def read_lease(raw: object) -> tuple[str, int] | None:
    """``(holder, deadline)`` of a stored lease, None for none or for a value that does not parse (which holds nothing)."""
    try:
        held = json.loads(raw) if isinstance(raw, str) and raw else None
        until = whole_number(held["u"]) if held else None
        return (str(held["h"]), until) if until is not None else None
    except (ValueError, TypeError, KeyError):
        return None


class RewriteLease:
    def __init__(self, graph: Any, connector_id: str, *, seconds: int = REWRITE_LEASE_SECONDS, clock: Callable[[], float] = time.time) -> None:  # noqa: ANN401
        self._graph, self._id, self._seconds, self._clock = graph, connector_id, seconds, clock
        self.token = secrets.token_hex(8)

    def _value(self) -> str:
        return json.dumps({"h": self.token, "u": int(self._clock()) + self._seconds}, separators=(",", ":"))

    async def _raw(self) -> object:
        return ((await self._graph.get_document(self._id, CollectionNames.APPS.value, raise_on_error=True)) or {}).get(REWRITE_LEASE_KEY) or None

    async def _swap(self, raw: object, value: str | None) -> bool:
        return await self._cas(raw, {REWRITE_LEASE_KEY: value})

    async def _cas(self, raw: object, updates: Mapping[str, Any]) -> bool:
        try:
            return bool(await self._graph.update_node_fields_if_match(self._id, CollectionNames.APPS.value, dict(updates), {REWRITE_LEASE_KEY: raw}))
        except Exception as exc:
            if getattr(self._graph, "is_write_conflict", lambda _e: False)(exc):
                return False
            raise

    async def acquire(self) -> bool:
        """False while another holder's lease is still running."""
        raw = await self._raw()
        held = read_lease(raw)
        if held and held[0] != self.token and held[1] > self._clock():
            return False
        return await self._swap(raw, self._value())

    async def renew(self) -> None:
        """Push the deadline out (outside the virtual-record locks: the app document is written only there). `LeaseLost` when another holds it."""
        for _ in range(3):
            raw = await self._raw()
            held = read_lease(raw)
            if not held or held[0] != self.token:
                raise LeaseLost(f"the rewrite lease of {self._id} is held by another rewriter")
            if await self._swap(raw, self._value()):
                return
        raise LeaseLost(f"the rewrite lease of {self._id} could not be renewed")

    async def update_app(self, updates: Mapping[str, Any]) -> None:
        """Write fields of the app document in one compare-and-set on this holder's lease value, so a holder that lost the lease writes nothing
        (a check before an unconditional write would leave a gap for the takeover). `LeaseLost` when another holds it."""
        for _ in range(3):
            raw = await self._raw()
            held = read_lease(raw)
            if not held or held[0] != self.token:
                raise LeaseLost(f"the rewrite lease of {self._id} is held by another rewriter")
            if await self._cas(raw, updates):
                return
        raise RuntimeError(f"the app document of {self._id} was not written")      # (the lease is still ours: the store refused it)

    def holds(self, app: Mapping[str, Any]) -> bool:
        """Read-only (safe under the locks): the app document still names this holder."""
        held = read_lease(app.get(REWRITE_LEASE_KEY))
        return bool(held and held[0] == self.token)

    async def release(self) -> None:
        """Best effort: a lease that is not given back runs out."""
        try:
            raw = await self._raw()
            if (held := read_lease(raw)) and held[0] == self.token:
                await self._swap(raw, None)
        except Exception:
            return
