"""Key holds: the hold ledger and the admin notices, run by the recovery leader.

A key goes on hold when three distinct jobs on it are capped within two hours (``fleet_breaker``). While
a hold lasts, jobs that wait for it must not have that time counted against their 24 h wall, so the leader
advances ``sg:hold:ledger:{<pk>:<cls>}`` every ``TICK_SECONDS`` by the Redis-clock time elapsed. A pause
state records the ledger when it first paused and subtracts the difference (``pause_budget``), so a job
that slept through a weekend hold has the weekend subtracted without being woken.

The leader also raises two notices once per hold episode, through the claim-key path the S8 notifier
contract defines (``<ns>:pipeshub:notice:{hold:<episode>}:{enter|release}``, ``SET NX``, 30 d):
``INDEXING_PROVIDER_ON_HOLD`` and ``INDEXING_PROVIDER_HOLD_RELEASED`` (only after the first was sent). The
payload names the provider's readable label and never its credential.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.services.llm_gateway.fleet_breaker import (
    REDIS_OP_TIMEOUT_S,
    FleetBreaker,
    FleetBreakerUnavailable,
)
from app.services.notification.types import (
    NotificationOrigin,
    NotificationRecipientRole,
    NotificationSeverity,
    NotificationStatus,
    NotificationType,
)

if TYPE_CHECKING:
    from app.services.llm_gateway.fleet_redis import FleetRedis

TICK_SECONDS = 10.0
# Every this many ticks (and on the first) the leader also scans Redis for holds the index lost: the index entry is
# written after the script that opens a hold, so a failure in between would otherwise leave a hold nobody ticks.
RECONCILE_EVERY_TICKS = 60
SCAN_COUNT = 500
NOTICE_TTL_SECONDS = 30 * 24 * 3600
# How long a notice claim stays pending while its send is in flight; it lapses if the sender never confirms.
PENDING_CLAIM_SECONDS = 300
_PENDING, _SENT = "pending", "sent"

# Removes an index entry only while it still holds the value the caller read: a newer hold episode noted at the
# same scope while a tick awaited Redis or the notifier stays indexed.
# KEYS: index  ARGV: scope, value read
_REMOVE_IF_UNCHANGED = """
if redis.call('HGET', KEYS[1], ARGV[1]) == ARGV[2] then return redis.call('HDEL', KEYS[1], ARGV[1]) end
return 0
"""

# Sends one notification document to the notification topic. Returns whether the broker took it.
NoticeSender = Callable[[dict[str, Any]], Awaitable[bool]]


@dataclass(frozen=True)
class HoldEntry:
    pk: str
    cls: str
    episode: str
    label: str
    since_ms: int
    org_id: str
    redriven_jobs: int
    # The index value this entry was read from: a removal that finds another value there leaves it.
    stored: str = field(default="", compare=False)

    @property
    def scope(self) -> str:
        return f"{self.pk}:{self.cls}"


def _s(value: Any) -> str:  # noqa: ANN401
    return value.decode() if isinstance(value, bytes) else str(value)


class HoldKeeper:
    """The index of keys on hold, the ledger advance and the notices. Safe to tick from any replica: the
    ledger advance reads its own last tick from Redis and the notices are claimed with ``SET NX``."""

    def __init__(
        self,
        fleet: FleetRedis,
        breaker: FleetBreaker,
        *,
        sender: NoticeSender | None = None,
        logger: logging.Logger | None = None,
        clock_ms: Callable[[], int] | None = None,
    ) -> None:
        self._fleet = fleet
        self._breaker = breaker
        self._sender = sender
        self._logger = logger or logging.getLogger(__name__)
        self._clock_ms = clock_ms or (lambda: int(time.time() * 1000))
        self._ticks = 0

    def _index_key(self) -> str:
        return self._fleet.key("sg:holds")

    def _claim_key(self, episode: str, edge: str) -> str:
        return self._fleet.key(f"pipeshub:notice:{{hold:{episode}}}:{edge}")

    async def note_hold(self, entry: HoldEntry) -> None:
        """A key went on hold: index it, and start its ledger from now."""
        client = self._fleet.client()
        await self._breaker.start_ledger(entry.scope)
        await client.hset(
            self._index_key(),
            entry.scope,
            json.dumps({
                "pk": entry.pk, "cls": entry.cls, "episode": entry.episode, "label": entry.label,
                "since_ms": entry.since_ms, "notifyOrg": entry.org_id, "redriven": entry.redriven_jobs,
            }),
        )

    async def entries(self) -> list[HoldEntry]:
        raw = await self._fleet.client().hgetall(self._index_key())
        found: list[HoldEntry] = []
        for value in raw.values():
            try:
                stored = _s(value)
                data = json.loads(stored)
                found.append(HoldEntry(
                    data["pk"], data["cls"], data["episode"], data["label"], int(data["since_ms"]),
                    data.get("notifyOrg", ""), int(data.get("redriven", 0)), stored,
                ))
            except (ValueError, KeyError, TypeError):
                continue
        return found

    async def reconcile(self) -> int:
        """Index every hold Redis has that the index lost (``scan_keys``). Returns how many were added back."""
        client = self._fleet.client()
        added = 0
        async for key in scan_keys(self._fleet, "sg:{p:", "}:hold"):
            scope = _scope_of(key, "sg:{p:", "}:hold")
            if scope is None or await client.hexists(self._index_key(), scope):
                continue
            data = {_s(k): _s(v) for k, v in (await client.hgetall(key)).items()}
            pk, _, cls = scope.rpartition(":")
            if not data.get("episode") or not pk:
                continue
            value = json.dumps({
                "pk": pk, "cls": cls, "episode": data["episode"], "label": data.get("label", ""),
                "since_ms": int(data.get("since_ms") or 0), "notifyOrg": data.get("org", ""), "redriven": 0,
            })
            if await client.hsetnx(self._index_key(), scope, value):
                added += 1
                self._logger.warning("re-indexed a provider hold the index had lost (%s)", data.get("label", ""))
        return added

    async def tick(self) -> int:
        """One leader pass over the keys on hold. Returns how many are still held."""
        if self._ticks % RECONCILE_EVERY_TICKS == 0:
            try:
                await self.reconcile()
            except FleetBreakerUnavailable:
                # The index is a fast path: a scan that cannot run must not stop the holds it lists from being kept.
                self._logger.warning("could not scan for provider holds the index lost; using the index as it is", exc_info=True)
        self._ticks += 1
        held = 0
        client = self._fleet.client()
        for entry in await self.entries():
            # Also refreshes an open breaker's TTL, and releases a hold whose breaker is no longer open.
            if await self._breaker.keep_hold(entry.pk, entry.cls) == 1:
                held += 1
                await self._breaker.advance_ledger(entry.scope, held=True, since_ms=entry.since_ms)
                await self._notify(entry, "enter")
                continue
            await self._breaker.advance_ledger(entry.scope, held=False)
            # Ended: the release notice goes out only if the hold notice did, so an admin never
            # sees a release without the hold it ends.
            if await self._delivered(entry.episode, "enter"):
                await self._notify(entry, "release")
            await client.eval(_REMOVE_IF_UNCHANGED, 1, self._index_key(), entry.scope, entry.stored)
        return held

    async def _delivered(self, episode: str, edge: str) -> bool:
        """Whether the broker took this episode's notice: a claim still pending (its sender is in flight, or died)
        is not a delivery."""
        value = await self._fleet.client().get(self._claim_key(episode, edge))
        return value is not None and _s(value) == _SENT

    async def _notify(self, entry: HoldEntry, edge: str) -> None:
        if self._sender is None:
            return
        client = self._fleet.client()
        claim = self._claim_key(entry.episode, edge)
        # Pending until the broker takes it, then sent. A pending claim lapses on its own, so a sender that died or
        # was cancelled mid-send does not suppress the notice for good.
        if not await client.set(claim, _PENDING, nx=True, ex=PENDING_CLAIM_SECONDS):
            return
        sent = False
        try:
            sent = await self._sender(notice_document(entry, edge, now_ms=self._clock_ms()))
        except asyncio.CancelledError:
            await asyncio.shield(client.delete(claim))
            raise
        except Exception:
            self._logger.warning("could not send the provider hold notice", exc_info=True)
        if sent:
            await client.set(claim, _SENT, ex=NOTICE_TTL_SECONDS)
        else:
            # Not taken: free the claim so the next tick tries again.
            await client.delete(claim)


def _glob_escape(text: str) -> str:
    return "".join(f"\\{c}" if c in "*?[]\\" else c for c in text)


async def scan_keys(fleet: FleetRedis, prefix: str, suffix: str) -> AsyncIterator[str]:
    """Every key of the namespace named ``<prefix>…<suffix>`` (a SCAN, so it never blocks Redis; on a cluster the client
    walks every primary). Each step is bounded by ``REDIS_OP_TIMEOUT_S`` and a Redis error ends the scan with
    ``FleetBreakerUnavailable``, like every other read here."""
    pattern = _glob_escape(fleet.key(prefix)) + "*" + _glob_escape(suffix)
    pages = fleet.client().scan_iter(match=pattern, count=SCAN_COUNT).__aiter__()
    try:
        while True:
            try:
                key = await asyncio.wait_for(pages.__anext__(), REDIS_OP_TIMEOUT_S)
            except StopAsyncIteration:
                return
            except Exception as exc:
                raise FleetBreakerUnavailable(f"{type(exc).__name__}: {exc}") from exc
            yield _s(key)
    finally:
        closer = getattr(pages, "aclose", None)
        if closer is not None:
            await closer()


def _scope_of(key: str, prefix: str, suffix: str) -> str | None:
    """The ``<pk>:<cls>`` scope inside a key named ``…<prefix><scope><suffix>``."""
    start = key.rfind(prefix)
    if start < 0 or not key.endswith(suffix):
        return None
    scope = key[start + len(prefix): len(key) - len(suffix)]
    return scope or None


def notice_document(entry: HoldEntry, edge: str, *, now_ms: int) -> dict[str, Any]:
    """The notification document of a hold (``enter``) or its release. No credential, no provider key digest."""
    entering = edge == "enter"
    size = "large" if entry.cls == "l" else "small"
    minutes = max(0, int((now_ms - entry.since_ms) / 60000))
    notice_type = NotificationType.INDEXING_PROVIDER_ON_HOLD if entering else NotificationType.INDEXING_PROVIDER_HOLD_RELEASED
    return {
        "orgId": entry.org_id,
        "type": notice_type.value,
        "severity": (NotificationSeverity.WARNING if entering else NotificationSeverity.SUCCESS).value,
        "status": NotificationStatus.UNREAD.value,
        "originService": NotificationOrigin.INDEXING.value,
        "title": "Model provider on hold" if entering else "Model provider back in service",
        "message": (
            f"Indexing is waiting for {entry.label}: several documents kept failing on it. "
            "They will be retried automatically."
            if entering
            else f"{entry.label} is serving requests again and indexing has resumed."
        ),
        "redirectLink": None,
        "payload": {
            "v": 1,
            "episodeId": entry.episode,
            "providerLabel": entry.label,
            "sizeClass": size,
            "sinceMin": minutes,
            "redrivenJobs": entry.redriven_jobs,
        },
        "recipientUserIds": [],
        "recipientRoles": [NotificationRecipientRole.ADMIN.value],
        "isDeleted": False,
    }


async def run_hold_leader(
    keeper: HoldKeeper,
    is_leader: Callable[[], Awaitable[bool]],
    *,
    logger: logging.Logger,
    interval_s: float = TICK_SECONDS,
    after_tick: Callable[[], Awaitable[object]] | None = None,
) -> None:
    """Tick every ``interval_s`` while this replica holds the recovery lease. Runs until cancelled. ``after_tick`` is the
    leader's other recovery work on the same cadence (the redrives holds still owe)."""
    while True:
        try:
            if await is_leader():
                await keeper.tick()
                if after_tick is not None:
                    await after_tick()
        except asyncio.CancelledError:
            raise
        except FleetBreakerUnavailable as exc:
            logger.warning("provider hold tick skipped: %s", exc)
        except Exception:
            logger.warning("provider hold tick failed", exc_info=True)
        await asyncio.sleep(interval_s)
