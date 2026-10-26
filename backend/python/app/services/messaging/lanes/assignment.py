"""Assigned lanes: one recorded lane per connector instead of a hash.

Hashing a connector id onto eight lanes ignores which lanes are already in
use, so two connectors share a lane far more often than intuition suggests
(about four installs in five with five connectors), and the consumer reads a
lane strictly in order: a small connector behind a large one's backlog waits
for all of it. Here each connector is given a lane once, the least-loaded one
by the edition's rule (``assignment_policy.py``), and the choice is written to
a Redis hash on the broker's own Redis:

``{<topic>}:lane-map``
    connector id (or ``__default__``) -> ``v1|lane|class|state|prevLane|movedAtMs|fenceMs``.
    ``prevLane`` is set while a moved connector may still have events on its
    old lane; ``fenceMs`` is written once no new event can reach that lane.
``{<topic>}:lane-meta``
    ``laneCount`` (written by the indexing consumer, which owns the lanes),
    ``version`` (bumped by every write a placement depends on), per-lane
    ``large:N`` / ``small:N`` counts, and the per-lane ``busy:N`` / ``busyAt``
    reading the indexing service keeps.

Both keys carry the ``{<topic>}`` hash tag, so on Redis Cluster they share a
slot and one script may touch both.

Producers look a connector's lane up through a per-process cache. A hit is a
dictionary read; a miss is one script call; only the very first miss for a
connector runs the rule and commits it. The commit is a script that refuses to
write if any other placement changed the meta hash since the rule saw it, so
two processes assigning at once cannot both take the same free lane; the loser
gets the fresh snapshot back and asks the rule again.

If Redis cannot answer, the producer uses a cached lane even past its
lifetime, and otherwise falls back to the hash lane: refusing to publish
would turn a lookup failure into failed syncs, and the hash lane is one the
consumer reads and the stranded-record sweep always counts.
"""
from __future__ import annotations

import asyncio
import threading
import time
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from redis.exceptions import NoScriptError

from app.services.messaging.lanes.assignment_policy import (
    BUSY_READING_MAX_AGE_MS,
    KEEP_CURRENT,
    ConnectorClass,
    LaneLoad,
    LaneRequest,
    LaneRequestReason,
    LaneSnapshot,
    admin_lane_move_allowed,
    choose_connector_lane,
    is_large_class,
)
from app.services.messaging.lanes.hash_router import RedisLaneRouter, stable_lane
from app.services.messaging.lanes.interface import DEFAULT_LANE_KEY, LaneHint
from app.services.redis.config import ClientOptions
from app.services.redis.loop_clients import LoopBoundClients
from app.telemetry.modules import scheduling_metrics as metrics

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from logging import Logger

    from app.services.messaging.lanes.assignment_policy import LaneChoice
    from app.services.redis.connection_provider import (
        IRedisConnectionProvider,
        RedisClient,
    )

__all__ = [
    "AssignedRedisLaneRouter",
    "LaneAssignments",
    "LaneEntry",
    "LaneMapUnavailableError",
    "LaneMoveRefusedError",
    "UpkeepResult",
    "lane_assignments_in_use",
    "lane_map_key",
    "lane_meta_key",
    "read_lane_map",
    "shared_lane_assignments",
    "write_lane_count",
]

ENTRY_VERSION: Final = "v1"
STATE_LIVE: Final = "live"
STATE_DELETED: Final = "deleted"

# Placements are rare (once per connector, ever), so a commit that keeps
# losing races is a sign of something wrong, not of load. Bounded so a
# producer never spins: it falls back to the hash lane instead.
_MAX_COMMIT_ATTEMPTS = 25
# One warning per connector per this long when a lookup falls back.
_FALLBACK_WARNING_INTERVAL_SECONDS = 60.0
# After a lookup fails, connectors with nothing cached go straight to their
# hash lane for this long instead of each waiting on Redis again.
_UNAVAILABLE_HOLD_SECONDS = 5.0


def lane_map_key(topic: str) -> str:
    return f"{{{topic}}}:lane-map"


def lane_meta_key(topic: str) -> str:
    return f"{{{topic}}}:lane-meta"


@dataclass(frozen=True)
class LaneEntry:
    """One connector's row in the lane map."""

    lane: int
    connector_class: str
    state: str = STATE_LIVE
    prev_lane: int | None = None
    moved_at_ms: int | None = None
    fence_ms: int | None = None

    @property
    def is_live(self) -> bool:
        return self.state == STATE_LIVE

    def encode(self) -> str:
        def text(value: int | None) -> str:
            return "" if value is None else str(value)

        return "|".join(
            (
                ENTRY_VERSION,
                str(self.lane),
                self.connector_class,
                self.state,
                text(self.prev_lane),
                text(self.moved_at_ms),
                text(self.fence_ms),
            )
        )

    @classmethod
    def parse(cls, raw: object) -> LaneEntry | None:
        """None for anything that is not a well-formed entry."""
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8", "replace")
        if not isinstance(raw, str):
            return None
        fields = raw.split("|")
        if len(fields) != 7 or fields[0] != ENTRY_VERSION:
            return None

        def number(value: str) -> int | None:
            return int(value) if value.isdigit() else None

        lane = number(fields[1])
        if lane is None:
            return None
        return cls(
            lane=lane,
            connector_class=fields[2],
            state=fields[3] or STATE_LIVE,
            prev_lane=number(fields[4]),
            moved_at_ms=number(fields[5]),
            fence_ms=number(fields[6]),
        )


# Shared by the scripts below: Redis's clock in ms, an entry split on "|",
# and the count field a class is kept under (only "team" is large).
_LUA_HELPERS = """
local function now_ms()
    local t = redis.call("TIME")
    return tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
end

local function split(s)
    local out, start = {}, 1
    while true do
        local i = string.find(s, "|", start, true)
        if not i then
            table.insert(out, string.sub(s, start))
            return out
        end
        table.insert(out, string.sub(s, start, i - 1))
        start = i + 1
    end
end

local function size_of(c)
    if c == "team" then return "large:" end
    return "small:"
end
"""

# KEYS: map, meta. ARGV: connector id, fallback lane count, "1" to always
# return the snapshot. A valid entry comes back alone; otherwise the snapshot
# the rule needs comes with it, so a first assignment costs no extra round trip.
_LOOKUP_SCRIPT = """
local entry = redis.call("HGET", KEYS[1], ARGV[1])
local lane_count = tonumber(redis.call("HGET", KEYS[2], "laneCount")) or tonumber(ARGV[2])
if entry and ARGV[3] ~= "1" then
    local lane = tonumber(string.match(entry, "^v1|(%d+)|"))
    if lane and lane < lane_count then
        return {entry, lane_count}
    end
end
local t = redis.call("TIME")
local now_ms = tonumber(t[1]) * 1000 + math.floor(tonumber(t[2]) / 1000)
return {entry or "", lane_count, now_ms, redis.call("HGETALL", KEYS[2])}
"""

# KEYS: map, meta.
# ARGV: connector id, lane, class, expected meta version, mode ("assign" or
# "move"), the lane its events may be on from before the map ("" if none),
# fallback lane count, "1" if the class is known for sure.
#
# "assign" never touches a connector that already has a lane inside the lane
# count; "move" does, unless a previous move is still settling. An entry
# outside the lane count is replaced, unless it too is still settling. Either
# way the write is refused, and the current snapshot returned, if the meta
# version is no longer the one the rule saw.
_COMMIT_SCRIPT = _LUA_HELPERS + """
local map, meta = KEYS[1], KEYS[2]
local id, lane, class = ARGV[1], tonumber(ARGV[2]), ARGV[3]
local expected, mode, legacy = tonumber(ARGV[4]), ARGV[5], tonumber(ARGV[6])
local lane_count = tonumber(redis.call("HGET", meta, "laneCount")) or tonumber(ARGV[7])

local raw = redis.call("HGET", map, id)
local old = nil
if raw then
    local f = split(raw)
    if #f == 7 and f[1] == "v1" and tonumber(f[2]) then old = f end
end
local old_lane = nil
if old then old_lane = tonumber(old[2]) end

if old_lane and old_lane < lane_count then
    if mode ~= "move" or old_lane == lane then
        -- The caller knows the class for sure (creation): a first publish may
        -- have guessed it, so correct the entry and its count in place.
        if ARGV[8] == "1" and old[3] ~= class and old[4] == "live" then
            redis.call("HINCRBY", meta, size_of(old[3]) .. old_lane, -1)
            redis.call("HINCRBY", meta, size_of(class) .. old_lane, 1)
            old[3] = class
            raw = table.concat(old, "|")
            redis.call("HSET", map, id, raw)
            redis.call("HINCRBY", meta, "version", 1)
        end
        return {"existing", raw}
    end
    if old[5] ~= "" then return {"settling", raw} end
elseif old_lane and old[5] ~= "" then
    -- Outside the lane count but still settling a move: replacing prevLane
    -- would lose the lane its earlier events wait on. It keeps publishing to
    -- its current lane, which the consumer adopted, until the move settles.
    return {"settling", raw}
end

-- Unless the caller knows the class, the stored one stands: a class
-- corrected since the caller's lookup must not be written back over.
if old and ARGV[8] ~= "1" then class = old[3] end

local version = tonumber(redis.call("HGET", meta, "version")) or 0
if version ~= expected then
    return {"conflict", lane_count, now_ms(), redis.call("HGETALL", meta)}
end
if not lane or lane < 0 or lane >= lane_count then
    return redis.error_reply("lane " .. tostring(ARGV[2]) .. " is outside 0.." .. (lane_count - 1))
end

local prev = ""
if old_lane and old_lane ~= lane then
    prev = tostring(old_lane)
elseif not old_lane and legacy and legacy ~= lane then
    prev = tostring(legacy)
end
local now = now_ms()
local moved = ""
if prev ~= "" then moved = tostring(now) end
local state = "live"
if old and old[4] ~= "" then state = old[4] end

local entry = table.concat({"v1", tostring(lane), class, state, prev, moved, ""}, "|")
redis.call("HSET", map, id, entry)
if old_lane and old[4] == "live" then
    redis.call("HINCRBY", meta, size_of(old[3]) .. old_lane, -1)
end
if state == "live" then
    redis.call("HINCRBY", meta, size_of(class) .. lane, 1)
end
redis.call("HINCRBY", meta, "version", 1)
return {
    "assigned", entry,
    tonumber(redis.call("HGET", meta, "large:" .. lane)) or 0,
    tonumber(redis.call("HGET", meta, "small:" .. lane)) or 0,
}
"""

# KEYS: meta. ARGV: lane count. Bumps the version only when it changes, so a
# restart does not invalidate snapshots for nothing.
_SET_LANE_COUNT_SCRIPT = """
if redis.call("HGET", KEYS[1], "laneCount") == ARGV[1] then
    return 0
end
redis.call("HSET", KEYS[1], "laneCount", ARGV[1])
redis.call("HINCRBY", KEYS[1], "version", 1)
return 1
"""


# KEYS: map, meta. ARGV: connector id.
# A deleted connector's entry stays for good, so its late events still go to
# the same lane, but it stops counting at once so the lane can go to the next
# connector. Its delete time is kept where a move keeps its time; upkeep
# fences it like a move and lets its old lane go, but never deletes the row.
_RELEASE_SCRIPT = _LUA_HELPERS + """
local raw = redis.call("HGET", KEYS[1], ARGV[1])
if not raw then return 0 end
local f = split(raw)
if #f ~= 7 or f[1] ~= "v1" or not tonumber(f[2]) or f[4] ~= "live" then return 0 end
f[4], f[6], f[7] = "deleted", tostring(now_ms()), ""
redis.call("HSET", KEYS[1], ARGV[1], table.concat(f, "|"))
redis.call("HINCRBY", KEYS[2], size_of(f[3]) .. f[2], -1)
redis.call("HINCRBY", KEYS[2], "version", 1)
return 1
"""

# KEYS: map, meta. ARGV: connector id, the entry as it was read, new class.
# Only if the entry has not changed since it was read; never moves anyone.
_SET_CLASS_SCRIPT = _LUA_HELPERS + """
local raw = redis.call("HGET", KEYS[1], ARGV[1])
if raw ~= ARGV[2] then return 0 end
local f = split(raw)
if #f ~= 7 or f[3] == ARGV[3] then return 0 end
if f[4] == "live" then
    redis.call("HINCRBY", KEYS[2], size_of(f[3]) .. f[2], -1)
    redis.call("HINCRBY", KEYS[2], size_of(ARGV[3]) .. f[2], 1)
end
f[3] = ARGV[3]
redis.call("HSET", KEYS[1], ARGV[1], table.concat(f, "|"))
redis.call("HINCRBY", KEYS[2], "version", 1)
return 1
"""

# KEYS: map, meta. ARGV: fence delay ms, busy-after ms, fallback lane count,
# "1" if the backlog below was read, then lane/oldest-unfinished-ms pairs for
# every lane that has unfinished work.
#
# One pass over the map, in one step so no placement can land in between:
# fences the moves and deletes whose producers' caches have run out, clears
# a move once its old lane has finished everything up to the fence, lets a
# deleted entry's old lane go the same way (the entry itself stays, so a late
# event keeps its lane), rebuilds the per-lane counts, and writes the busy
# flags. Nothing that needs the backlog is decided without it.
_UPKEEP_SCRIPT = _LUA_HELPERS + """
local map, meta = KEYS[1], KEYS[2]
local fence_delay, busy_after = tonumber(ARGV[1]), tonumber(ARGV[2])
local lane_count = tonumber(redis.call("HGET", meta, "laneCount")) or tonumber(ARGV[3])
local known = ARGV[4] == "1"
local oldest = {}
for i = 5, #ARGV, 2 do oldest[tonumber(ARGV[i])] = tonumber(ARGV[i + 1]) end
local now = now_ms()

local function finished_up_to(lane, fence)
    if not known then return false end
    local o = oldest[tonumber(lane)]
    return o == nil or o > fence
end

local changed = false
local fenced, cleared = 0, 0
local counts = {}
local entries = redis.call("HGETALL", map)
for i = 1, #entries, 2 do
    local id, f = entries[i], split(entries[i + 1])
    if #f == 7 and f[1] == "v1" and tonumber(f[2]) then
        local dirty = false
        local deleted = f[4] == "deleted"
        if deleted and f[6] == "" then
            f[6], dirty = tostring(now), true
        end
        if f[6] ~= "" and (deleted or f[5] ~= "") then
            if f[7] == "" then
                if now >= tonumber(f[6]) + fence_delay then
                    f[7], dirty, fenced = tostring(now), true, fenced + 1
                end
            else
                local fence = tonumber(f[7])
                if deleted then
                    -- The row stays for good: a late event (a retried cleanup,
                    -- a sweep) must find it and stay on this lane, not be given
                    -- a new live one. Only its old lane is let go once drained.
                    if f[5] ~= "" and finished_up_to(f[5], fence) then
                        f[5], dirty, cleared = "", true, cleared + 1
                    end
                elseif finished_up_to(f[5], fence) then
                    f[5], f[6], f[7], dirty, cleared = "", "", "", true, cleared + 1
                end
            end
        end
        if dirty then
            redis.call("HSET", map, id, table.concat(f, "|"))
            changed = true
        end
        if f[4] == "live" then
            local field = size_of(f[3]) .. f[2]
            counts[field] = (counts[field] or 0) + 1
        end
    end
end

local fields = redis.call("HGETALL", meta)
for i = 1, #fields, 2 do
    local name = fields[i]
    if string.match(name, "^large:%d+$") or string.match(name, "^small:%d+$") then
        if counts[name] == nil then counts[name] = 0 end
    end
end
for name, n in pairs(counts) do
    if tonumber(redis.call("HGET", meta, name)) ~= n then
        redis.call("HSET", meta, name, n)
        changed = true
    end
end

if known then
    for lane = 0, lane_count - 1 do
        local o, busy = oldest[lane], "0"
        if o and now - o > busy_after then busy = "1" end
        if redis.call("HGET", meta, "busy:" .. lane) ~= busy then
            redis.call("HSET", meta, "busy:" .. lane, busy)
            changed = true
        end
    end
    redis.call("HSET", meta, "busyAt", now)
end
if changed then redis.call("HINCRBY", meta, "version", 1) end
return {fenced, cleared, now}
"""

def _text(value: object) -> str:
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value)


def _pairs(flat: object) -> dict[str, str]:
    """HGETALL's reply as a dict, whether it came back flat (from a script) or not."""
    if isinstance(flat, dict):
        return {_text(k): _text(v) for k, v in flat.items()}
    items = list(flat or [])  # type: ignore[call-overload]
    return {_text(items[i]): _text(items[i + 1]) for i in range(0, len(items) - 1, 2)}


def _count(fields: Mapping[str, str], name: str) -> int:
    try:
        return max(0, int(fields.get(name, "0")))
    except ValueError:
        return 0


def snapshot_from_meta(
    fields: Mapping[str, str], lane_count: int, now_ms: int
) -> LaneSnapshot:
    """The rule's view of every lane, from the meta hash."""
    try:
        busy_at = int(fields.get("busyAt", ""))
    except ValueError:
        busy_at = None
    busy_is_recent = busy_at is not None and now_ms - busy_at <= BUSY_READING_MAX_AGE_MS
    try:
        version = int(fields.get("version", "0"))
    except ValueError:
        version = 0
    return LaneSnapshot(
        lane_count=lane_count,
        lanes=tuple(
            LaneLoad(
                lane=lane,
                large=_count(fields, f"large:{lane}"),
                small=_count(fields, f"small:{lane}"),
                busy=busy_is_recent and fields.get(f"busy:{lane}") == "1",
            )
            for lane in range(lane_count)
        ),
        version=version,
        now_ms=now_ms,
        fields=dict(fields),
    )


def _without(snapshot: LaneSnapshot, entry: LaneEntry) -> LaneSnapshot:
    """The snapshot as it would be without this connector, so a move is
    judged on everyone else's load."""
    if not entry.is_live:
        return snapshot
    large = is_large_class(entry.connector_class)
    lanes = tuple(
        replace(
            load,
            large=max(0, load.large - 1) if large else load.large,
            small=load.small if large else max(0, load.small - 1),
        )
        if load.lane == entry.lane
        else load
        for load in snapshot.lanes
    )
    return replace(snapshot, lanes=lanes)


@dataclass(frozen=True)
class UpkeepResult:
    fenced: int
    cleared: int
    now_ms: int


class LaneMapUnavailableError(RuntimeError):
    """The lane map could not be read just now; the caller falls back."""


class LaneMoveRefusedError(RuntimeError):
    """A move was asked for that this edition, or the map's state, does not allow."""


@dataclass
class _Cached:
    lane: int
    fetched_at: float


class LaneAssignments:
    """The lane map for one topic, with the per-process cache in front of it.

    Shared by every producer in a process (see ``shared_lane_assignments``):
    the connectors service builds one producer per connector processor, and
    they should not each keep a copy. Safe to call from any event loop: the
    Redis client is held per loop, and the cache is guarded by a thread lock
    because the indexing service publishes from two loops.
    """

    def __init__(
        self,
        logger: Logger,
        provider: IRedisConnectionProvider,
        *,
        topic: str,
        fallback_lane_count: int,
        cache_seconds: float = 60.0,
        choose: Callable[[LaneRequest, LaneSnapshot], LaneChoice] | None = None,
        admin_move_allowed: Callable[[str | None, str], bool] | None = None,
        operation_timeout_seconds: float = 2.0,
        max_connections: int = 8,
    ) -> None:
        self.logger = logger
        self.topic = topic
        self._provider = provider
        self._fallback_lane_count = max(1, fallback_lane_count)
        self._cache_seconds = max(0.0, cache_seconds)
        self._choose = choose or choose_connector_lane
        self._admin_move_allowed = admin_move_allowed or admin_lane_move_allowed
        self._map = lane_map_key(topic)
        self._meta = lane_meta_key(topic)
        timeout = max(0.1, operation_timeout_seconds)
        options = ClientOptions(
            decode_responses=True,
            max_connections=max(1, max_connections),
            socket_timeout_seconds=timeout,
            socket_connect_timeout_seconds=timeout,
            # A lookup that fails falls back to the hash lane, so one retry
            # (a pooled connection that went stale) is all it is worth.
            retry_attempts=1,
            blocking=True,
        )
        self._clients: LoopBoundClients[RedisClient] = LoopBoundClients(
            lambda: provider.create_client(options)
        )
        self._lock = threading.Lock()
        self._cache: dict[str, _Cached] = {}
        self._known_lane_count: int | None = None
        self._shas: dict[str, str] = {}
        # Monotonic time until which lookups are not tried after one failed.
        self._unavailable_until = 0.0
        # One placement at a time per loop: two of them racing each other's
        # commits from the same process would only cost round trips.
        self._placement_locks: dict[asyncio.AbstractEventLoop, asyncio.Lock] = {}

    @property
    def lane_count(self) -> int:
        """The lane count Redis last reported, or the configured one before that."""
        return self._known_lane_count or self._fallback_lane_count

    def hash_lane(self, connector_id: str) -> int:
        return stable_lane(connector_id, self.lane_count)

    def cached_lane(self, connector_id: str) -> int | None:
        """The cached lane, however old. None if this process never saw one."""
        with self._lock:
            cached = self._cache.get(connector_id)
        return cached.lane if cached else None

    async def aclose(self) -> None:
        await self._clients.aclose()

    async def lane_for(self, connector_id: str, hint: LaneHint | None = None) -> int:
        """The connector's lane, assigning one on first sight.

        Raises if Redis cannot answer and this process has never seen the
        connector; the caller decides what to fall back to.
        """
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(connector_id)
        if cached is not None and now - cached.fetched_at < self._cache_seconds:
            return cached.lane
        if now < self._unavailable_until:
            # Redis just failed a lookup: answer at once rather than make
            # every publish wait out its own timeout.
            if cached is not None:
                return cached.lane
            raise LaneMapUnavailableError("The lane map did not answer a moment ago")
        try:
            lane = await self._lookup_or_place(connector_id, hint or LaneHint(), is_new=False)
        except Exception:
            self._unavailable_until = time.monotonic() + _UNAVAILABLE_HOLD_SECONDS
            if cached is not None:
                # An entry almost never changes, so a stale lane is still
                # the best answer there is.
                return cached.lane
            raise
        return lane

    async def assign(
        self,
        connector_id: str,
        connector_class: ConnectorClass | str,
        *,
        org_id: str | None = None,
        connector_type: str | None = None,
        is_new: bool = False,
    ) -> int:
        """Give a connector a lane now, at creation, rather than on first publish.

        A connector that already has a lane keeps it. ``is_new`` says it
        cannot have events from before the map, so its hash lane is not kept
        as a lane it might still have work on.
        """
        hint = LaneHint(
            org_id=org_id,
            connector_class=str(connector_class),
            connector_type=connector_type,
        )
        return await self._lookup_or_place(
            connector_id, hint, is_new=is_new, class_is_known=True
        )

    async def move(
        self,
        connector_id: str,
        reason: LaneRequestReason,
        *,
        org_id: str | None = None,
    ) -> LaneEntry:
        """Ask the rule for a new lane for a connector that already has one.

        The old lane is kept as ``prevLane`` until housekeeping sees it
        drained past the fence. An admin move happens only if the edition's
        ``admin_lane_move_allowed`` says so; a move while an earlier one is
        still settling is refused.
        """
        if reason is LaneRequestReason.ADMIN and not self._admin_move_allowed(
            org_id, connector_id
        ):
            raise LaneMoveRefusedError(
                "Moving a connector to another lane is not available in this edition"
            )
        reply = await self._eval(
            _LOOKUP_SCRIPT, connector_id, self._fallback_lane_count, "1"
        )
        lane_count = self._note_lane_count(reply[1])
        entry = LaneEntry.parse(reply[0])
        if entry is None:
            raise LaneMoveRefusedError(f"Connector {connector_id} has no lane to move from")
        request = LaneRequest(
            connector_id=connector_id,
            connector_class=entry.connector_class,
            hash_lane=stable_lane(connector_id, lane_count),
            reason=reason,
            org_id=org_id,
            current_lane=entry.lane if entry.lane < lane_count else None,
        )
        status, value, occupancy = await self._commit_loop(
            request,
            entry,
            snapshot_from_meta(_pairs(reply[3]), lane_count, int(reply[2])),
            mode="move",
            legacy_lane=None,
        )
        if status == "settling":
            raise LaneMoveRefusedError(
                f"Connector {connector_id} is still settling an earlier move"
            )
        moved = LaneEntry.parse(value)
        if moved is None:
            raise RuntimeError(f"Lane map returned an unreadable entry for {connector_id}")
        if status == "assigned":
            self._log_assignment(connector_id, moved, LaneHint(org_id=org_id), occupancy)
        self._remember(connector_id, moved.lane)
        return moved

    async def read_map(self) -> dict[str, LaneEntry]:
        return await read_lane_map(self._client(), self.topic)

    async def read_meta(self) -> dict[str, str]:
        return _pairs(await self._client().hgetall(self._meta))  # type: ignore[misc]

    async def release(self, connector_id: str) -> bool:
        """Take a deleted connector off its lane's count at once.

        Its entry stays for good, so a late event still goes to the same
        lane, however late. True if it had a live entry. The shared
        ``__default__`` entry is never released.
        """
        if connector_id == DEFAULT_LANE_KEY:
            return False
        released = bool(await self._eval(_RELEASE_SCRIPT, connector_id))
        if released:
            self.logger.info(
                "Connector %s was deleted; its lane on %s is free for the next connector",
                connector_id,
                self.topic,
            )
        return released

    async def correct_class(
        self, connector_id: str, entry: LaneEntry, connector_class: str
    ) -> bool:
        """Record a connector's real class, read from the graph, if its entry
        is still the one that was read. Affects later placements only."""
        return bool(
            await self._eval(_SET_CLASS_SCRIPT, connector_id, entry.encode(), connector_class)
        )

    async def upkeep(
        self,
        oldest_waiting_ms: Mapping[int, float] | None,
        *,
        fence_delay_ms: int,
        busy_after_ms: int = BUSY_READING_MAX_AGE_MS,
    ) -> UpkeepResult:
        """Fence moves and deletes, clear the old lane of any that has drained
        (a deleted entry is never removed), rebuild the counts and write the
        busy flags, in one atomic step.

        ``oldest_waiting_ms`` is, per lane number, when its oldest unfinished
        event was published (lanes with none left out); None when the backlog
        could not be read, so nothing that depends on it is decided.
        """
        pairs: list[object] = []
        for lane, oldest in (oldest_waiting_ms or {}).items():
            pairs.extend((lane, int(oldest)))
        reply = await self._eval(
            _UPKEEP_SCRIPT,
            int(fence_delay_ms),
            int(busy_after_ms),
            self._fallback_lane_count,
            "1" if oldest_waiting_ms is not None else "",
            *pairs,
        )
        return UpkeepResult(
            fenced=int(reply[0]),
            cleared=int(reply[1]),
            now_ms=int(reply[2]),
        )

    async def _lookup_or_place(
        self,
        connector_id: str,
        hint: LaneHint,
        *,
        is_new: bool,
        class_is_known: bool = False,
    ) -> int:
        reply = await self._eval(_LOOKUP_SCRIPT, connector_id, self._fallback_lane_count, "")
        lane_count = self._note_lane_count(reply[1])
        entry = LaneEntry.parse(reply[0])
        if len(reply) == 2:
            if entry is None:
                raise RuntimeError(f"Unexpected lane map reply: {reply!r}")
            lane = entry.lane
            if (
                class_is_known
                and hint.connector_class
                and entry.connector_class != hint.connector_class
            ):
                # A first publish guessed the class; creation knows it. The
                # script answers with the row as it is now, which a move since
                # the lookup may have put on another lane.
                reply = await self._eval(
                    _COMMIT_SCRIPT,
                    connector_id,
                    lane,
                    hint.connector_class,
                    0,
                    "assign",
                    "",
                    self._fallback_lane_count,
                    "1",
                )
                current = LaneEntry.parse(reply[1]) if len(reply) > 1 else None
                if current is not None:
                    lane = current.lane
        else:
            lane = await self._place(
                connector_id,
                hint,
                entry,
                snapshot_from_meta(_pairs(reply[3]), lane_count, int(reply[2])),
                is_new=is_new,
                class_is_known=class_is_known,
            )
        self._remember(connector_id, lane)
        return lane

    def _note_lane_count(self, value: object) -> int:
        lane_count = max(1, int(value))  # type: ignore[call-overload]
        if self._known_lane_count is not None and lane_count < self._known_lane_count:
            # The consumer now reads fewer lanes: a cached lane past the new
            # count is looked up again rather than used until it expires.
            with self._lock:
                for connector_id in [
                    c for c, cached in self._cache.items() if cached.lane >= lane_count
                ]:
                    del self._cache[connector_id]
        self._known_lane_count = lane_count
        return lane_count

    async def _place(
        self,
        connector_id: str,
        hint: LaneHint,
        entry: LaneEntry | None,
        snapshot: LaneSnapshot,
        *,
        is_new: bool = False,
        class_is_known: bool = False,
    ) -> int:
        lock = self._placement_lock()
        async with lock:
            # A placement this process made while we waited is the answer,
            # unless this caller knows the class: then the commit below runs,
            # and corrects the class that placement guessed.
            with self._lock:
                cached = self._cache.get(connector_id)
            if (
                not class_is_known
                and cached is not None
                and time.monotonic() - cached.fetched_at < self._cache_seconds
            ):
                return cached.lane
            # An event says its class only for an upload, so a repaired entry
            # keeps the class it already has unless the caller knows better.
            connector_class = (
                ConnectorClass.SYSTEM.value
                if connector_id == DEFAULT_LANE_KEY
                else hint.connector_class
                or (entry.connector_class if entry is not None else ConnectorClass.TEAM.value)
            )
            reason = (
                LaneRequestReason.LANE_OUT_OF_RANGE
                if entry is not None
                else LaneRequestReason.FIRST_ASSIGNMENT
            )
            request = LaneRequest(
                connector_id=connector_id,
                connector_class=connector_class,
                hash_lane=stable_lane(connector_id, snapshot.lane_count),
                reason=reason,
                org_id=hint.org_id,
                connector_type=hint.connector_type,
            )
            legacy_lane = None if is_new else request.hash_lane
            status, value, occupancy = await self._commit_loop(
                request,
                entry,
                snapshot,
                mode="assign",
                legacy_lane=legacy_lane,
                class_is_known=class_is_known,
            )
            placed = LaneEntry.parse(value)
            if placed is None:
                raise RuntimeError(f"Lane map returned an unreadable entry for {connector_id}")
            if status == "assigned":
                self._log_assignment(connector_id, placed, hint, occupancy)
            return placed.lane

    async def _commit_loop(
        self,
        request: LaneRequest,
        entry: LaneEntry | None,
        snapshot: LaneSnapshot,
        *,
        mode: str,
        legacy_lane: int | None,
        choose: Callable[[LaneRequest, LaneSnapshot], LaneChoice] | None = None,
        class_is_known: bool = False,
    ) -> tuple[str, object, tuple[int, int]]:
        """Commit the rule's choice, asking it again on a fresh snapshot for as
        long as another placement keeps getting in first.

        Returns the script's status, the entry, and the (large, small) count
        on the entry's lane after an assignment.
        """
        for _attempt in range(_MAX_COMMIT_ATTEMPTS):
            view = (
                _without(snapshot, entry)
                if entry is not None and request.current_lane is not None
                else snapshot
            )
            choice = (choose or self._choose)(request, view)
            if choice is KEEP_CURRENT:
                if request.current_lane is None:
                    raise ValueError(
                        f"The lane rule kept connector {request.connector_id} "
                        "where it is, but it has no lane inside the lane count"
                    )
                choice = request.current_lane
            if not isinstance(choice, int) or not 0 <= choice < snapshot.lane_count:
                raise ValueError(
                    f"The lane rule chose {choice!r} for connector "
                    f"{request.connector_id}, outside 0..{snapshot.lane_count - 1}"
                )
            reply = await self._eval(
                _COMMIT_SCRIPT,
                request.connector_id,
                choice,
                request.connector_class,
                snapshot.version,
                mode,
                "" if legacy_lane is None else legacy_lane,
                self._fallback_lane_count,
                "1" if class_is_known else "",
            )
            status = _text(reply[0])
            if status == "assigned":
                return status, reply[1], (int(reply[2]), int(reply[3]))
            if status != "conflict":
                return status, reply[1], (0, 0)
            lane_count = self._note_lane_count(reply[1])
            snapshot = snapshot_from_meta(_pairs(reply[3]), lane_count, int(reply[2]))
            request = replace(request, hash_lane=stable_lane(request.connector_id, lane_count))
            if legacy_lane is not None:
                legacy_lane = request.hash_lane
        raise RuntimeError(
            f"Could not record a lane for connector {request.connector_id}: "
            f"the lane map changed under {_MAX_COMMIT_ATTEMPTS} attempts in a row"
        )

    def _log_assignment(
        self,
        connector_id: str,
        entry: LaneEntry,
        hint: LaneHint,
        occupancy: tuple[int, int],
    ) -> None:
        name = (
            f"{hint.connector_type} ({entry.connector_class}, {connector_id})"
            if hint.connector_type
            else f"{connector_id} ({entry.connector_class})"
        )
        large, small = occupancy
        moved = (
            f"; its earlier events may still be on lane {entry.prev_lane}"
            if entry.prev_lane is not None
            else ""
        )
        self.logger.info(
            "Connector %s assigned to lane %d of %s; lane %d now has %d large and "
            "%d small connector(s)%s",
            name,
            entry.lane,
            self.topic,
            entry.lane,
            large,
            small,
            moved,
        )

    def _remember(self, connector_id: str, lane: int) -> None:
        with self._lock:
            self._cache[connector_id] = _Cached(lane, time.monotonic())

    def _placement_lock(self) -> asyncio.Lock:
        loop = asyncio.get_running_loop()
        with self._lock:
            lock = self._placement_locks.get(loop)
            if lock is None:
                lock = asyncio.Lock()
                self._placement_locks[loop] = lock
            return lock

    def _client(self) -> RedisClient:
        return self._clients.get()

    async def _sha(self, body: str, *, reload: bool = False) -> str:
        sha = None if reload else self._shas.get(body)
        if sha is None:
            sha = await self._provider.load_script(body)
            self._shas[body] = sha
        return sha

    async def _eval(self, body: str, *args: object) -> list:
        """Run a script, reloading it once if Redis lost its script cache
        (a restart or failover drops it)."""
        client = self._client()
        keys = (self._map, self._meta)
        try:
            return await client.evalsha(await self._sha(body), 2, *keys, *args)  # type: ignore[misc]
        except NoScriptError:
            sha = await self._sha(body, reload=True)
            return await client.evalsha(sha, 2, *keys, *args)  # type: ignore[misc]


async def read_lane_map(redis: RedisClient, topic: str) -> dict[str, LaneEntry]:
    """Every well-formed entry in the topic's lane map; empty if there is no map."""
    raw = await redis.hgetall(lane_map_key(topic))  # type: ignore[misc]
    entries: dict[str, LaneEntry] = {}
    for key, value in _pairs(raw).items():
        entry = LaneEntry.parse(value)
        if entry is not None:
            entries[key] = entry
    return entries


async def write_lane_count(redis: RedisClient, topic: str, lane_count: int) -> bool:
    """Record the lane count the consumer reads, for producers to place by.

    True if it changed. The consumer owns the set of lanes, so its count is
    the one producers should agree with.
    """
    changed = await redis.eval(  # type: ignore[misc]
        _SET_LANE_COUNT_SCRIPT, 1, lane_meta_key(topic), str(int(lane_count))
    )
    return bool(changed)


class AssignedRedisLaneRouter(RedisLaneRouter):
    """Places each connector on the lane the map gives it.

    ``route`` (synchronous) is still the hash lane: it is what the producer
    falls back to, and what the sweep counts for events published before the
    map or during a fallback.
    """

    def __init__(self, assignments: LaneAssignments, logger: Logger) -> None:
        super().__init__(assignments.lane_count)
        self._assignments = assignments
        self.logger = logger
        self._warned_at: dict[str, float] = {}
        self._warn_lock = threading.Lock()

    @property
    def assignments(self) -> LaneAssignments:
        return self._assignments

    async def place(
        self, topic: str, lane_key: str | None, hint: LaneHint | None = None
    ) -> tuple[str, str | None]:
        if topic != self._assignments.topic:
            return self.route(topic, lane_key)
        key = lane_key or DEFAULT_LANE_KEY
        try:
            lane = await self._assignments.lane_for(key, hint)
        except Exception as e:
            lane = self._assignments.hash_lane(key)
            self._fall_back(key, lane, e)
        return self.lane_name(topic, lane), lane_key

    def _fall_back(self, key: str, lane: int, error: Exception) -> None:
        if isinstance(error, LaneMapUnavailableError):
            reason = "held"
        elif isinstance(error, (TimeoutError, asyncio.TimeoutError)):
            reason = "timeout"
        else:
            reason = "error"
        metrics.record_lane_assignment_fallback(reason)
        now = time.monotonic()
        with self._warn_lock:
            last = self._warned_at.get(key)
            if last is not None and now - last < _FALLBACK_WARNING_INTERVAL_SECONDS:
                return
            self._warned_at[key] = now
        self.logger.warning(
            "Could not look up the queue lane for connector %s, so its events go "
            "to its hashed lane %d for now: %s: %s",
            key,
            lane,
            type(error).__name__,
            error,
        )


_shared_lock = threading.Lock()
_shared: dict[tuple[int, str], LaneAssignments] = {}


def shared_lane_assignments(
    logger: Logger,
    provider: IRedisConnectionProvider,
    *,
    topic: str,
    fallback_lane_count: int,
    cache_seconds: float,
) -> LaneAssignments:
    """The process's one ``LaneAssignments`` for this Redis and topic.

    Module-level so every producer in the process shares one cache. The lane
    rule comes from the edition (``app.edition_services``), resolved here
    rather than imported at module load because that module imports most of
    the connectors stack.
    """
    key = (id(provider), topic)
    with _shared_lock:
        existing = _shared.get(key)
        if existing is not None:
            return existing
        from app import edition_services

        assignments = LaneAssignments(
            logger,
            provider,
            topic=topic,
            fallback_lane_count=fallback_lane_count,
            cache_seconds=cache_seconds,
            choose=edition_services.choose_connector_lane,
            admin_move_allowed=edition_services.admin_lane_move_allowed,
        )
        _shared[key] = assignments
        return assignments


def lane_assignments_in_use(topic: str) -> LaneAssignments | None:
    """The lane map this process's producers place ``topic`` by, if any.

    None unless a producer was built with assignment on, on Redis: the
    creation, delete and upkeep paths then have nothing to do.
    """
    with _shared_lock:
        return next((a for a in _shared.values() if a.topic == topic), None)
