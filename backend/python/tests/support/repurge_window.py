"""Fakes for the coalesced re-purge tests: a Redis (fakeredis, real Lua) that refuses cross-slot scripts like a Redis
Cluster, a broker that records what is published and when, and the discrete-event world the property test drives."""

from __future__ import annotations

import heapq
import random
import re
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

import fakeredis

from app.services.paid_cache import repurge, repurge_window
from app.services.paid_cache.repurge_window import (
    APPENDED,
    BUSY,
    Opened,
    RedisWindowStore,
)

_TAG = re.compile(r"\{([^}]*)\}")


class CrossSlot(Exception):
    """What a Redis Cluster answers a script whose keys are in different slots."""


class SlotCheckedRedis:
    """A client that rejects ``eval``/multi-key commands over keys with different hash tags, as a cluster does."""

    def __init__(self) -> None:
        self.inner = fakeredis.FakeAsyncRedis(decode_responses=True)
        self.evals = 0

    @staticmethod
    def _check(keys: tuple[str, ...]) -> None:
        tags = {m.group(1) if (m := _TAG.search(k)) else k for k in keys}
        if len(tags) > 1:
            raise CrossSlot(f"keys hash to different slots: {sorted(keys)}")

    async def eval(self, script: str, numkeys: int, *args: Any) -> Any:  # noqa: ANN401
        self._check(tuple(str(a) for a in args[:numkeys]))
        self.evals += 1
        return await self.inner.eval(script, numkeys, *args)

    async def zrangebyscore(self, key: str, *a: Any, **k: Any) -> Any:  # noqa: ANN401
        return await self.inner.zrangebyscore(key, *a, **k)

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self.inner, name)


def redis_store(client: Any = None, namespace: str = "ns") -> RedisWindowStore:  # noqa: ANN401
    return RedisWindowStore(client or SlotCheckedRedis(), lambda name: f"{namespace}:{name}")


class Broker:
    """A producer: remembers every event, and can be made to fail like a broker outage."""

    def __init__(self, clock: list[float] | None = None) -> None:
        self.clock = clock
        self.sent: list[SimpleNamespace] = []
        self.down = False
        self.fail_next = 0

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        if self.down or self.fail_next:
            self.fail_next = max(0, self.fail_next - 1)
            raise ConnectionError("broker down")
        self.sent.append(SimpleNamespace(topic=topic, type=event_type, payload=payload, key=key, at=self.clock[0] if self.clock else 0.0))
        return True

    def of(self, event_type: str) -> list[SimpleNamespace]:
        return [m for m in self.sent if m.type == event_type]


def first_hop(org: str, rid: str, now: float) -> dict[str, Any]:
    return repurge.repurge_event_payload(org, rid, now=now)


# -- the property test's world -----------------------------------------------------------------------------------

BACKOFF = [15, 60, 240]          # the consumer's re-queue backoff of a failed event


class Crash(BaseException):
    """The process died here (nothing after it runs, no ``finally`` of ours may be relied on by the world)."""


class TrustingStore:
    """Negative control: the round-1 rule, a live claim of another deleter is trusted and the id is only listed."""

    def __init__(self, inner: RedisWindowStore) -> None:
        self.inner = inner

    async def open_or_append(self, org_id: str, window: int, token: str, epoch: str, rid: str, now_ms: int) -> Opened:
        res = await self.inner.open_or_append(org_id, window, token, epoch, rid, now_ms)
        if res.state == BUSY:
            own, ids, _s, _d = self.inner._keys(org_id, window)
            await self.inner._client.rpush(ids, rid)
            return Opened(APPENDED)
        return res

    async def release_claim(self, org_id: str, window: int, token: str) -> None:
        return None                                        # round 1 left a failed claimer's claim live for its whole life

    def __getattr__(self, name: str) -> Any:  # noqa: ANN401
        return getattr(self.inner, name)


@dataclass
class World:
    seed: int
    rate: float
    horizon: float
    outages: int = 0
    outage_s: float = 0.0
    p_fail: float = 0.0
    p_stale: float = 0.0
    p_crash: float = 0.0
    #: A claimer that crashes never comes back (its event is lost to the world, as a dead-lettered one is).
    die: bool = False
    flag_off_at: float | None = None
    store_factory: Any = None
    client: Any = None                      # a real Redis client; the default is fakeredis (real Lua, no server)
    namespace: str = "ns"
    org: str = "org-1"
    covered: set[str] = field(default_factory=set)
    acked: set[str] = field(default_factory=set)
    dead: set[str] = field(default_factory=set)
    placed: dict[str, int] = field(default_factory=dict)
    bodies: dict[str, dict] = field(default_factory=dict)
    dead_seal_windows: set[int] = field(default_factory=set)

    async def run(self, monkeypatch: Any) -> dict[str, int]:  # noqa: ANN401
        rnd = random.Random(self.seed)
        clock = [0.0]
        monkeypatch.setattr(repurge, "time", SimpleNamespace(time=lambda: clock[0]))
        outages = [(s, s + self.outage_s) for s in (rnd.uniform(0, self.horizon) for _ in range(self.outages))]
        store = (self.store_factory or (lambda s: s))(redis_store(self.client, self.namespace))
        heap: list[tuple[float, int, int, str, Any]] = []
        counter = [0]

        def push(at: float, kind: str, data: Any) -> None:  # noqa: ANN401
            counter[0] += 1
            heapq.heappush(heap, (at, 0 if kind == "seal" else 1, counter[0], kind, data))

        mute = {"release": False}
        broker = _WorldBroker(self, rnd, outages, clock, push, mute)
        t, n = 0.0, 0
        while True:
            t += rnd.expovariate(self.rate)
            if t > self.horizon:
                break
            n += 1
            push(t, "hop1", (f"r{n}", 1))
        real_release = store.release_claim

        async def release(org_id: str, window: int, token: str) -> None:
            if not mute["release"]:
                await real_release(org_id, window, token)
        if not isinstance(store, TrustingStore):
            store.release_claim = release  # type: ignore[method-assign]

        while heap:
            at, _p, _c, kind, data = heapq.heappop(heap)
            clock[0] = at
            if kind == "seal":
                payload_, attempt_ = data if isinstance(data, tuple) else (data, 1)
                try:
                    await repurge_window.handle_seal(broker, self.org, payload_, store=store)
                except Exception:
                    if attempt_ < 1 + len(BACKOFF):
                        push(at + BACKOFF[attempt_ - 1], "seal", (payload_, attempt_ + 1))
                    else:
                        self.dead_seal_windows.add(int(payload_["window"]))       # in the DLQ, replayable: visible
                continue
            rid, attempt = data
            # one message: a redelivery carries the very same body (so the same claim token)
            payload = self.bodies.setdefault(rid, first_hop(self.org, rid, at - repurge.REPURGE_HOP_S))
            ok = True
            try:
                mute["release"] = False
                if self.flag_off_at is not None and at >= self.flag_off_at:
                    ok = await self._per_record(broker, payload)
                else:
                    broker.crash_next = rnd.random() < self.p_crash
                    covered = await repurge_window.coalesce_successor(broker, self.org, payload, store=store)
                    if not covered:
                        ok = await self._per_record(broker, payload)
                    else:
                        self.placed[rid] = repurge_window.window_of(at)
            except Crash:
                mute["release"] = True
                ok = False
                if self.die:
                    self.dead.add(rid)
                    continue
            except Exception:
                ok = False
            if ok:
                self.acked.add(rid)
            elif attempt < 1 + len(BACKOFF):
                push(at + BACKOFF[attempt - 1], "hop1", (rid, attempt + 1))
            else:
                self.dead.add(rid)
        lost = {r for r in self.acked - self.covered if self.placed.get(r) not in self.dead_seal_windows}
        return {"deletes": n, "acked": len(self.acked), "dead": len(self.dead), "lost": len(lost)}

    async def _per_record(self, broker: Any, payload: dict) -> bool:  # noqa: ANN401
        try:
            await repurge.schedule_next_hop(broker, self.org, payload)
        except Exception:
            return False
        return True


class _WorldBroker:
    def __init__(self, world: World, rnd: random.Random, outages: list, clock: list[float], push: Any, mute: dict) -> None:  # noqa: ANN401
        self.world, self.rnd, self.outages, self.clock, self.push, self.mute = world, rnd, outages, clock, push, mute
        self.crash_next = False

    async def send_event(self, topic: str, event_type: str, payload: dict, key: str | None = None) -> bool:
        now = self.clock[0]
        if any(a <= now < b for a, b in self.outages) or self.rnd.random() < self.world.p_fail:
            raise ConnectionError("broker down")
        if self.crash_next and event_type == "purgePaidResultsBatch" and payload.get("phase") == "seal":
            self.crash_next = False
            self.mute["release"] = True        # the process is dead: the handler's own cleanup never runs
            raise Crash
        if event_type == "purgePaidResults":
            if payload.get("recordId"):
                self.world.covered.add(payload["recordId"])
            return True
        if payload.get("phase") == "seal":
            if self.rnd.random() >= self.world.p_stale:
                self.push(float(payload["_retry_not_before"]), "seal", payload)
            return True
        for rid in payload.get("recordIds", []):
            self.world.covered.add(rid)
        return True


class MemoryWindowStore:
    """The window store in a dict, over the same pure decisions the PostgreSQL store uses (a parity test ties it to the
    Redis scripts). Fast enough for a 100k-delete message count."""

    def __init__(self) -> None:
        self.own: dict[tuple[str, int], str] = {}
        self.ids: dict[tuple[str, int], list[str]] = {}
        self.sealed: set[tuple[str, int]] = set()
        self.done: set[tuple[str, int]] = set()
        self.open: dict[str, dict[tuple[int, str], int]] = {}

    async def open_or_append(self, org_id: str, window: int, token: str, epoch: str, rid: str, now_ms: int) -> Opened:
        k = (org_id, window)
        if k in self.sealed:
            return Opened(repurge_window.CLOSED)
        action, ep = repurge_window.decide_open(self.own.get(k), token, epoch, now_ms)
        if action == "busy":
            return Opened(BUSY)
        if action == "claim":
            self.own[k] = f"claim:{token}:{ep}:{now_ms + repurge_window.CLAIM_TTL_MS}"
            return Opened(repurge_window.CLAIMED, ep)
        if len(self.ids.setdefault(k, [])) >= repurge_window.WINDOW_CAP:
            return Opened(repurge_window.FULL)
        self.ids[k].append(rid)
        return Opened(APPENDED)

    async def mark_published(self, org_id: str, window: int, token: str, epoch: str, due_ms: int, now_ms: int = 0) -> bool:
        k = (org_id, window)
        parsed = repurge_window.parse_own(self.own.get(k))
        if parsed is None or parsed[0] != "claim" or parsed[1] != token or parsed[2] != epoch or parsed[3] <= now_ms:
            return False
        self.own[k] = f"published:{epoch}:{due_ms}"
        self.open.setdefault(org_id, {})[(window, epoch)] = due_ms
        return True

    async def release_claim(self, org_id: str, window: int, token: str) -> None:
        parsed = repurge_window.parse_own(self.own.get((org_id, window)))
        if parsed is not None and parsed[0] == "claim" and parsed[1] == token:
            del self.own[(org_id, window)]

    async def seal_read(self, org_id: str, window: int, epoch: str, now_ms: int) -> repurge_window.SealRead:
        k = (org_id, window)
        verdict = repurge_window.decide_seal(self.own.get(k), epoch, now_ms)
        if verdict != repurge_window.SEAL_OK:
            return repurge_window.SealRead(verdict, (), len(self.ids.get(k, [])))
        if k in self.done:
            return repurge_window.SealRead(repurge_window.SEAL_DONE, (), len(self.ids.get(k, [])))
        self.sealed.add(k)
        return repurge_window.SealRead(repurge_window.SEAL_OK, tuple(self.ids.get(k, [])), expected=len(self.ids.get(k, [])))

    async def mark_done(self, org_id: str, window: int, epoch: str) -> None:
        self.done.add((org_id, window))
        self.open.get(org_id, {}).pop((window, epoch), None)

    async def overdue(self, org_id: str, before_ms: int) -> list[tuple[int, str]]:
        return [k for k, due in self.open.get(org_id, {}).items() if due < before_ms]
